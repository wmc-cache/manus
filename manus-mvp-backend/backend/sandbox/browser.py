"""浏览器服务 - Playwright 控制和截图（支持会话隔离）"""
import asyncio
import base64
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

from sandbox.event_bus import event_bus, SandboxEvent


@dataclass
class BrowserSession:
    """单会话浏览器状态（每个 conversation_id 独立）。"""

    context: Any
    page: Any
    current_url: str = ""
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class BrowserService:
    """浏览器服务 - 管理 Chromium 实例和会话级页面"""

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._sessions: Dict[str, BrowserSession] = {}
        self._current_url = ""
        self._is_ready = False
        self._init_lock = asyncio.Lock()

    @staticmethod
    def _session_key(conversation_id: Optional[str]) -> str:
        return conversation_id or "_default"

    async def _ensure_runtime(self):
        """确保浏览器运行时已启动"""
        if self._is_ready and self._browser:
            return

        async with self._init_lock:
            if self._is_ready and self._browser:
                return

            try:
                from playwright.async_api import async_playwright
            except ModuleNotFoundError as e:
                raise RuntimeError(
                    "Playwright 未安装。请在后端虚拟环境执行: pip install playwright && playwright install chromium"
                ) from e

            try:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-setuid-sandbox"],
                )
                self._is_ready = True
            except Exception as e:
                err_msg = str(e)
                if "Executable doesn't exist" in err_msg:
                    raise RuntimeError(
                        "Chromium 未安装。请在后端虚拟环境执行: playwright install chromium"
                    ) from e
                raise

    async def _ensure_session(self, conversation_id: Optional[str]) -> BrowserSession:
        """确保指定会话拥有独立的 browser context/page。"""
        await self._ensure_runtime()
        key = self._session_key(conversation_id)
        session = self._sessions.get(key)
        if session and session.page and not session.page.is_closed():
            return session

        context = await self._browser.new_context(viewport={"width": 1280, "height": 800})
        page = await context.new_page()
        session = BrowserSession(context=context, page=page)
        self._sessions[key] = session
        return session

    async def _take_screenshot(self, session: BrowserSession) -> str:
        """截取当前页面截图，返回 base64"""
        screenshot_bytes = await session.page.screenshot(type="jpeg", quality=70)
        return base64.b64encode(screenshot_bytes).decode("utf-8")

    async def navigate(self, url: str, conversation_id: Optional[str] = None) -> Dict[str, Any]:
        """导航到指定 URL"""
        session = await self._ensure_session(conversation_id)

        await event_bus.publish(SandboxEvent(
            "browser_navigating",
            {"url": url},
            window_id="browser",
            conversation_id=conversation_id,
        ))

        async with session.lock:
            try:
                response = await session.page.goto(url, wait_until="domcontentloaded", timeout=30000)
                session.current_url = session.page.url or url
                self._current_url = session.current_url
                title = await session.page.title()
                screenshot_b64 = await self._take_screenshot(session)

                await event_bus.publish(SandboxEvent(
                    "browser_navigated",
                    {
                        "url": session.current_url,
                        "title": title,
                        "status": response.status if response else 0,
                        "screenshot": screenshot_b64,
                    },
                    window_id="browser",
                    conversation_id=conversation_id,
                ))

                return {
                    "url": session.current_url,
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

    async def screenshot(self, conversation_id: Optional[str] = None) -> Dict[str, Any]:
        """获取当前页面截图"""
        session = await self._ensure_session(conversation_id)
        async with session.lock:
            screenshot_b64 = await self._take_screenshot(session)
            title = await session.page.title()
            url = session.current_url or session.page.url or ""

            await event_bus.publish(SandboxEvent(
                "browser_screenshot",
                {
                    "url": url,
                    "title": title,
                    "screenshot": screenshot_b64,
                },
                window_id="browser",
                conversation_id=conversation_id,
            ))

            return {
                "url": url,
                "title": title,
                "screenshot": screenshot_b64,
            }

    async def get_content(self, conversation_id: Optional[str] = None) -> str:
        """获取当前页面文本内容"""
        try:
            session = await self._ensure_session(conversation_id)
        except Exception as e:
            return f"获取页面内容失败: {str(e)}"

        async with session.lock:
            try:
                content = await session.page.evaluate("document.body.innerText")
                if len(content) > 5000:
                    content = content[:5000] + "\n... [内容被截断]"
                return content
            except Exception as e:
                return f"获取页面内容失败: {str(e)}"

    async def click(self, selector: str, conversation_id: Optional[str] = None) -> Dict[str, Any]:
        """点击页面元素"""
        session = await self._ensure_session(conversation_id)
        async with session.lock:
            try:
                await session.page.click(selector, timeout=5000)
                await session.page.wait_for_load_state("domcontentloaded", timeout=5000)
                screenshot_b64 = await self._take_screenshot(session)
                title = await session.page.title()
                url = session.page.url
                session.current_url = url
                self._current_url = url

                await event_bus.publish(SandboxEvent(
                    "browser_clicked",
                    {
                        "selector": selector,
                        "url": url,
                        "title": title,
                        "screenshot": screenshot_b64,
                    },
                    window_id="browser",
                    conversation_id=conversation_id,
                ))

                return {"success": True, "screenshot": screenshot_b64, "url": url, "title": title}
            except Exception as e:
                return {"success": False, "error": str(e)}

    async def click_by_coordinates(
        self,
        x: float,
        y: float,
        viewport_width: float,
        viewport_height: float,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """按截图坐标点击页面"""
        session = await self._ensure_session(conversation_id)

        if viewport_width <= 0 or viewport_height <= 0:
            return {"success": False, "error": "无效坐标参数"}

        async with session.lock:
            try:
                vp = session.page.viewport_size or {
                    "width": int(viewport_width),
                    "height": int(viewport_height),
                }
                target_x = max(0.0, min(float(vp["width"]) - 1, float(x) * float(vp["width"]) / float(viewport_width)))
                target_y = max(0.0, min(float(vp["height"]) - 1, float(y) * float(vp["height"]) / float(viewport_height)))

                await session.page.mouse.click(target_x, target_y)
                await asyncio.sleep(0.15)

                screenshot_b64 = await self._take_screenshot(session)
                title = await session.page.title()
                url = session.page.url
                session.current_url = url
                self._current_url = url

                await event_bus.publish(SandboxEvent(
                    "browser_clicked",
                    {
                        "x": target_x,
                        "y": target_y,
                        "url": url,
                        "title": title,
                        "screenshot": screenshot_b64,
                    },
                    window_id="browser",
                    conversation_id=conversation_id,
                ))

                return {
                    "success": True,
                    "url": url,
                    "title": title,
                    "screenshot": screenshot_b64,
                }
            except Exception as e:
                await event_bus.publish(SandboxEvent(
                    "browser_error",
                    {"error": str(e), "action": "click_by_coordinates"},
                    window_id="browser",
                    conversation_id=conversation_id,
                ))
                return {"success": False, "error": str(e)}

    async def type_text(
        self,
        text: str,
        submit: bool = False,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """向当前焦点元素输入文本"""
        session = await self._ensure_session(conversation_id)
        async with session.lock:
            try:
                if text:
                    await session.page.keyboard.type(text, delay=10)
                if submit:
                    await session.page.keyboard.press("Enter")

                await asyncio.sleep(0.15)
                screenshot_b64 = await self._take_screenshot(session)
                title = await session.page.title()
                url = session.page.url
                session.current_url = url
                self._current_url = url

                await event_bus.publish(SandboxEvent(
                    "browser_screenshot",
                    {
                        "url": url,
                        "title": title,
                        "screenshot": screenshot_b64,
                    },
                    window_id="browser",
                    conversation_id=conversation_id,
                ))

                return {"success": True, "url": url, "title": title, "screenshot": screenshot_b64}
            except Exception as e:
                await event_bus.publish(SandboxEvent(
                    "browser_error",
                    {"error": str(e), "action": "type_text"},
                    window_id="browser",
                    conversation_id=conversation_id,
                ))
                return {"success": False, "error": str(e)}

    async def scroll(
        self,
        delta_y: float,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """按指定距离滚动页面"""
        session = await self._ensure_session(conversation_id)
        async with session.lock:
            try:
                await session.page.mouse.wheel(0, delta_y)
                await asyncio.sleep(0.1)

                screenshot_b64 = await self._take_screenshot(session)
                title = await session.page.title()
                url = session.page.url
                session.current_url = url
                self._current_url = url

                await event_bus.publish(SandboxEvent(
                    "browser_screenshot",
                    {
                        "url": url,
                        "title": title,
                        "screenshot": screenshot_b64,
                    },
                    window_id="browser",
                    conversation_id=conversation_id,
                ))

                return {"success": True, "url": url, "title": title, "screenshot": screenshot_b64}
            except Exception as e:
                await event_bus.publish(SandboxEvent(
                    "browser_error",
                    {"error": str(e), "action": "scroll"},
                    window_id="browser",
                    conversation_id=conversation_id,
                ))
                return {"success": False, "error": str(e)}

    async def press_key(
        self,
        key: str,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """向页面发送按键"""
        session = await self._ensure_session(conversation_id)
        async with session.lock:
            try:
                await session.page.keyboard.press(key)
                await asyncio.sleep(0.1)

                screenshot_b64 = await self._take_screenshot(session)
                title = await session.page.title()
                url = session.page.url
                session.current_url = url
                self._current_url = url

                await event_bus.publish(SandboxEvent(
                    "browser_screenshot",
                    {
                        "url": url,
                        "title": title,
                        "screenshot": screenshot_b64,
                    },
                    window_id="browser",
                    conversation_id=conversation_id,
                ))

                return {"success": True, "url": url, "title": title, "screenshot": screenshot_b64}
            except Exception as e:
                await event_bus.publish(SandboxEvent(
                    "browser_error",
                    {"error": str(e), "action": "press_key", "key": key},
                    window_id="browser",
                    conversation_id=conversation_id,
                ))
                return {"success": False, "error": str(e)}

    def get_status(self) -> Dict[str, Any]:
        """返回浏览器运行状态（按会话聚合）。"""
        sessions: Dict[str, Dict[str, Any]] = {}
        for key, session in self._sessions.items():
            url = session.current_url
            try:
                if not url and session.page and not session.page.is_closed():
                    url = session.page.url or ""
            except Exception:
                url = session.current_url
            sessions[key] = {
                "current_url": url,
                "is_open": bool(session.page and not session.page.is_closed()),
            }

        return {
            "is_ready": self._is_ready,
            "session_count": len(self._sessions),
            "sessions": sessions,
        }

    async def close(self, conversation_id: Optional[str] = None):
        """关闭浏览器会话。传 conversation_id 只关闭该会话；不传则关闭全部。"""
        if conversation_id is not None:
            key = self._session_key(conversation_id)
            session = self._sessions.pop(key, None)
            if not session:
                return
            try:
                await session.page.close()
            except Exception:
                pass
            try:
                await session.context.close()
            except Exception:
                pass
            return

        for key in list(self._sessions.keys()):
            session = self._sessions.pop(key, None)
            if not session:
                continue
            try:
                await session.page.close()
            except Exception:
                pass
            try:
                await session.context.close()
            except Exception:
                pass

        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._is_ready = False
        self._browser = None
        self._playwright = None
        self._current_url = ""


# 全局浏览器服务
browser_service = BrowserService()
