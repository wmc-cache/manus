"""浏览器服务 - Playwright 控制和截图（支持会话隔离）"""
import asyncio
import base64
from typing import Optional, Dict, Any
from sandbox.event_bus import event_bus, SandboxEvent


class BrowserService:
    """浏览器服务 - 管理 Chromium 实例"""

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._page = None
        self._current_url = ""
        self._is_ready = False

    async def _ensure_browser(self):
        """确保浏览器已启动"""
        if self._is_ready and self._page:
            return

        from playwright.async_api import async_playwright
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        self._page = await self._browser.new_page(
            viewport={"width": 1280, "height": 800}
        )
        self._is_ready = True

    async def navigate(self, url: str, conversation_id: Optional[str] = None) -> Dict[str, Any]:
        """导航到指定 URL"""
        await self._ensure_browser()

        await event_bus.publish(SandboxEvent(
            "browser_navigating",
            {"url": url},
            window_id="browser",
            conversation_id=conversation_id,
        ))

        try:
            response = await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            self._current_url = url
            title = await self._page.title()

            # 截图
            screenshot_b64 = await self._take_screenshot()

            await event_bus.publish(SandboxEvent(
                "browser_navigated",
                {
                    "url": url,
                    "title": title,
                    "status": response.status if response else 0,
                    "screenshot": screenshot_b64,
                },
                window_id="browser",
                conversation_id=conversation_id,
            ))

            return {
                "url": url,
                "title": title,
                "status": response.status if response else 0,
                "screenshot": screenshot_b64,
            }
        except Exception as e:
            await event_bus.publish(SandboxEvent(
                "browser_error",
                {"url": url, "error": str(e)},
                window_id="browser",
                conversation_id=conversation_id,
            ))
            return {"url": url, "error": str(e)}

    async def _take_screenshot(self) -> str:
        """截取当前页面截图，返回 base64"""
        if not self._page:
            return ""
        screenshot_bytes = await self._page.screenshot(type="jpeg", quality=70)
        return base64.b64encode(screenshot_bytes).decode("utf-8")

    async def screenshot(self, conversation_id: Optional[str] = None) -> Dict[str, Any]:
        """获取当前页面截图"""
        await self._ensure_browser()
        screenshot_b64 = await self._take_screenshot()
        title = await self._page.title() if self._page else ""

        await event_bus.publish(SandboxEvent(
            "browser_screenshot",
            {
                "url": self._current_url,
                "title": title,
                "screenshot": screenshot_b64,
            },
            window_id="browser",
            conversation_id=conversation_id,
        ))

        return {
            "url": self._current_url,
            "title": title,
            "screenshot": screenshot_b64,
        }

    async def get_content(self) -> str:
        """获取当前页面文本内容"""
        if not self._page:
            return ""
        try:
            content = await self._page.evaluate("document.body.innerText")
            if len(content) > 5000:
                content = content[:5000] + "\n... [内容被截断]"
            return content
        except Exception as e:
            return f"获取页面内容失败: {str(e)}"

    async def click(self, selector: str, conversation_id: Optional[str] = None) -> Dict[str, Any]:
        """点击页面元素"""
        await self._ensure_browser()
        try:
            await self._page.click(selector, timeout=5000)
            await self._page.wait_for_load_state("domcontentloaded", timeout=5000)
            screenshot_b64 = await self._take_screenshot()
            title = await self._page.title()

            await event_bus.publish(SandboxEvent(
                "browser_clicked",
                {
                    "selector": selector,
                    "url": self._page.url,
                    "title": title,
                    "screenshot": screenshot_b64,
                },
                window_id="browser",
                conversation_id=conversation_id,
            ))

            return {"success": True, "screenshot": screenshot_b64}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def close(self):
        """关闭浏览器"""
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._is_ready = False
        self._page = None
        self._browser = None
        self._playwright = None


# 全局浏览器服务
browser_service = BrowserService()
