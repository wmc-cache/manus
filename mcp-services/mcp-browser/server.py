"""
mcp-browser: 浏览器操作 MCP 服务
提供网页导航、截图、内容提取、点击、输入、滚动等能力
使用 Playwright 管理浏览器实例池，按 conversation_id 隔离会话
"""

import asyncio
import base64
import json
import logging
import os
import sys
from typing import Any, Dict, Optional

import uvicorn

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp-shared"))
from mcp_base import MCPService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 浏览器实例管理（按 conversation_id 隔离）
# ---------------------------------------------------------------------------

_playwright_instance = None
_browser_instance = None
_page_pool: Dict[str, object] = {}  # conversation_id -> Page
_pool_lock = asyncio.Lock()


async def _get_playwright():
    global _playwright_instance, _browser_instance
    if _playwright_instance is None:
        from playwright.async_api import async_playwright
        _playwright_instance = await async_playwright().start()
    needs_launch = _browser_instance is None
    if not needs_launch:
        try:
            needs_launch = not _browser_instance.is_connected()
        except Exception:
            needs_launch = True
    if needs_launch:
        _browser_instance = await _playwright_instance.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        logger.info("[mcp-browser] Playwright 浏览器已启动")
    return _browser_instance


async def _get_page(conversation_id: Optional[str]) -> object:
    """获取或创建指定会话的浏览器页面"""
    cid = conversation_id or "_default"
    async with _pool_lock:
        page = _page_pool.get(cid)
        if page is None or page.is_closed():
            browser = await _get_playwright()
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()
            _page_pool[cid] = page
            logger.info("[mcp-browser] 为会话 %s 创建新页面", cid)
        return page


async def _build_browser_payload(
    page: object,
    message: str,
    *,
    action: str,
    status: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """构造包含截图的标准浏览器结果。"""
    screenshot_bytes = await page.screenshot(type="jpeg", quality=70, full_page=False)
    payload: Dict[str, Any] = {
        "ok": True,
        "action": action,
        "url": page.url,
        "title": await page.title(),
        "screenshot": base64.b64encode(screenshot_bytes).decode("utf-8"),
        "mime_type": "image/jpeg",
        "message": message,
    }
    if status is not None:
        payload["status"] = status
    if extra:
        payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def _build_error_payload(
    action: str,
    error: Exception,
    *,
    message_prefix: str,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    payload: Dict[str, Any] = {
        "ok": False,
        "action": action,
        "error": str(error),
        "message": f"{message_prefix}: {error}",
    }
    if extra:
        payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------

async def browser_navigate(url: str, conversation_id: Optional[str] = None) -> str:
    """导航到指定 URL"""
    try:
        page = await _get_page(conversation_id)
        response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        current_url = page.url
        title = await page.title()
        status = response.status if response else 0
        return await _build_browser_payload(
            page,
            f"已导航到: {current_url}\n页面标题: {title}",
            action="navigate",
            status=status,
        )
    except Exception as e:
        return _build_error_payload("navigate", e, message_prefix="导航失败", extra={"url": url})


async def browser_screenshot(conversation_id: Optional[str] = None) -> str:
    """对当前页面截图，返回结构化 JSON 字符串"""
    try:
        page = await _get_page(conversation_id)
        current_url = page.url
        return await _build_browser_payload(
            page,
            f"截图成功（当前页面: {current_url}）",
            action="screenshot",
        )
    except Exception as e:
        return _build_error_payload("screenshot", e, message_prefix="截图失败")


async def browser_get_content(conversation_id: Optional[str] = None) -> str:
    """获取当前页面的文本内容"""
    try:
        page = await _get_page(conversation_id)
        # 尝试提取主要文本内容
        content = await page.evaluate("""() => {
            const body = document.body;
            if (!body) return '';
            // 移除脚本和样式标签
            const clone = body.cloneNode(true);
            clone.querySelectorAll('script, style, nav, footer, header').forEach(el => el.remove());
            return clone.innerText || clone.textContent || '';
        }""")
        if not content or not content.strip():
            content = await page.content()
        result = content.strip()
        if len(result) > 10000:
            result = result[:10000] + "\n... [内容被截断]"
        return result
    except Exception as e:
        return f"获取页面内容失败: {e}"


async def browser_click(
    selector: str = "",
    x: Optional[float] = None,
    y: Optional[float] = None,
    conversation_id: Optional[str] = None,
) -> str:
    """点击页面元素（通过 CSS 选择器或坐标）"""
    try:
        page = await _get_page(conversation_id)
        extra: Dict[str, Any] = {}
        if selector:
            await page.click(selector, timeout=10000)
            extra["selector"] = selector
            message = f"已点击元素: {selector}"
        elif x is not None and y is not None:
            await page.mouse.click(x, y)
            extra["x"] = x
            extra["y"] = y
            message = f"已点击坐标: ({x}, {y})"
        else:
            return json.dumps(
                {
                    "ok": False,
                    "action": "click",
                    "message": "请提供 selector 或 (x, y) 坐标",
                },
                ensure_ascii=False,
            )
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        await asyncio.sleep(0.2)
        return await _build_browser_payload(page, message, action="click", extra=extra)
    except Exception as e:
        return _build_error_payload(
            "click",
            e,
            message_prefix="点击失败",
            extra={"selector": selector, "x": x, "y": y},
        )


async def browser_input(
    text: str,
    selector: str = "",
    submit: bool = False,
    conversation_id: Optional[str] = None,
) -> str:
    """向指定元素或当前焦点元素输入文本"""
    try:
        page = await _get_page(conversation_id)
        if selector:
            await page.fill(selector, text, timeout=10000)
            message = f"已在 `{selector}` 中输入文本"
        else:
            if text:
                await page.keyboard.type(text, delay=10)
            message = "已向当前焦点元素输入文本"
        if submit:
            await page.keyboard.press("Enter")
            message += " 并提交"
        await asyncio.sleep(0.1)
        return await _build_browser_payload(
            page,
            message,
            action="input",
            extra={"selector": selector, "submit": submit},
        )
    except Exception as e:
        return _build_error_payload(
            "input",
            e,
            message_prefix="输入失败",
            extra={"selector": selector, "submit": submit},
        )


async def browser_scroll(
    direction: str = "down",
    amount: int = 500,
    conversation_id: Optional[str] = None,
) -> str:
    """滚动页面"""
    try:
        page = await _get_page(conversation_id)
        if direction == "down":
            await page.evaluate(f"window.scrollBy(0, {amount})")
        elif direction == "up":
            await page.evaluate(f"window.scrollBy(0, -{amount})")
        elif direction == "top":
            await page.evaluate("window.scrollTo(0, 0)")
        elif direction == "bottom":
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        else:
            return json.dumps(
                {
                    "ok": False,
                    "action": "scroll",
                    "message": f"不支持的滚动方向: {direction}，支持: up/down/top/bottom",
                    "direction": direction,
                    "amount": amount,
                },
                ensure_ascii=False,
            )
        await asyncio.sleep(0.1)
        return await _build_browser_payload(
            page,
            f"已向 {direction} 滚动 {amount}px",
            action="scroll",
            extra={"direction": direction, "amount": amount},
        )
    except Exception as e:
        return _build_error_payload(
            "scroll",
            e,
            message_prefix="滚动失败",
            extra={"direction": direction, "amount": amount},
        )


# ---------------------------------------------------------------------------
# 服务构建
# ---------------------------------------------------------------------------

service = MCPService(
    name="mcp-browser",
    version="1.0.0",
    description="Manus 浏览器操作服务，提供网页导航、截图、内容提取、交互操作等能力",
)

service.register_tool(
    name="browser_navigate",
    description="导航浏览器到指定 URL",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要访问的完整 URL，包含协议前缀（如 https://）"},
        },
        "required": ["url"],
    },
    func=browser_navigate,
)

service.register_tool(
    name="browser_screenshot",
    description="对当前浏览器页面进行截图",
    parameters={"type": "object", "properties": {}},
    func=browser_screenshot,
)

service.register_tool(
    name="browser_get_content",
    description="获取当前浏览器页面的文本内容",
    parameters={"type": "object", "properties": {}},
    func=browser_get_content,
)

service.register_tool(
    name="browser_click",
    description="点击页面中的元素（通过 CSS 选择器或坐标）",
    parameters={
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "CSS 选择器，例如 '#submit-btn'"},
            "x": {"type": "number", "description": "点击位置的 X 坐标（像素）"},
            "y": {"type": "number", "description": "点击位置的 Y 坐标（像素）"},
        },
    },
    func=browser_click,
)

service.register_tool(
    name="browser_input",
    description="在指定输入框或当前焦点元素中输入文本",
    parameters={
        "type": "object",
        "properties": {
            "text":     {"type": "string", "description": "要输入的文本内容"},
            "selector": {"type": "string", "description": "输入框的 CSS 选择器；不传时输入到当前焦点元素"},
            "submit":   {"type": "boolean", "description": "输入后是否按 Enter 提交"},
        },
        "required": ["text"],
    },
    func=browser_input,
)

service.register_tool(
    name="browser_scroll",
    description="滚动当前页面",
    parameters={
        "type": "object",
        "properties": {
            "direction": {
                "type": "string",
                "description": "滚动方向",
                "enum": ["up", "down", "top", "bottom"],
            },
            "amount": {"type": "integer", "description": "滚动像素数（默认 500）"},
        },
    },
    func=browser_scroll,
)

app = service.app


@app.on_event("shutdown")
async def shutdown_browser():
    global _playwright_instance, _browser_instance
    if _browser_instance:
        await _browser_instance.close()
    if _playwright_instance:
        await _playwright_instance.stop()
    logger.info("[mcp-browser] 浏览器已关闭")


if __name__ == "__main__":
    port = int(os.environ.get("MCP_BROWSER_PORT", "8103"))
    uvicorn.run(app, host="0.0.0.0", port=port)
