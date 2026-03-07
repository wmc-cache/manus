"""浏览器服务 - 控制 VNC 中的 Chromium（支持会话隔离）。"""

import asyncio
import base64
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from sandbox.docker_sandbox import sandbox_manager
from sandbox.event_bus import SandboxEvent, event_bus
from sandbox.raw_tcp_proxy import docker_exec_raw_tcp_proxy

logger = logging.getLogger("sandbox.browser")

_REMOTE_BROWSER_PORT = 9222
_REMOTE_BROWSER_PROFILE = "/tmp/manus-browser-profile"

_ENSURE_DESKTOP_COMMAND = (
    "pgrep -f 'x11vnc .*5900' >/dev/null && exit 0; "
    "if [ -x /usr/local/bin/start-desktop.sh ]; then "
    "  nohup /usr/local/bin/start-desktop.sh >/tmp/manus-desktop.log 2>&1 & "
    "else "
    "  export DISPLAY=:99; "
    "  pgrep -f 'Xvfb :99' >/dev/null || nohup Xvfb :99 -screen 0 1280x800x24 -ac +extension GLX +render -noreset >/tmp/xvfb.log 2>&1 & "
    "  sleep 1; "
    "  pgrep -x x11vnc >/dev/null || nohup x11vnc -display :99 -forever -nopw -shared -rfbport 5900 -xkb >/tmp/x11vnc.log 2>&1 & "
    "fi"
)

_DISPLAY_DISCOVERY_SNIPPET = (
    "DISPLAY_VALUE=\"$(ps -ef | sed -n 's/.*x11vnc .* -display \\(:[0-9][0-9]*\\).*/\\1/p' | head -n 1)\"\n"
    "if [ -z \"$DISPLAY_VALUE\" ]; then\n"
    "  if command -v xdpyinfo >/dev/null 2>&1 && DISPLAY=:1 xdpyinfo >/dev/null 2>&1; then\n"
    "    DISPLAY_VALUE=:1\n"
    "  elif command -v xdpyinfo >/dev/null 2>&1 && DISPLAY=:99 xdpyinfo >/dev/null 2>&1; then\n"
    "    DISPLAY_VALUE=:99\n"
    "  else\n"
    "    DISPLAY_VALUE=:99\n"
    "  fi\n"
    "fi\n"
)

_ENSURE_REMOTE_BROWSER_COMMAND = (
    _DISPLAY_DISCOVERY_SNIPPET
    + "export DISPLAY=\"$DISPLAY_VALUE\"\n"
    + "if command -v chromium-browser >/dev/null 2>&1; then\n"
    + "  BROWSER_BIN=chromium-browser\n"
    + "elif command -v chromium >/dev/null 2>&1; then\n"
    + "  BROWSER_BIN=chromium\n"
    + "else\n"
    + "  echo 'Chromium not found' >&2\n"
    + "  exit 127\n"
    + "fi\n"
    + f"BROWSER_PORT={_REMOTE_BROWSER_PORT}\n"
    + f"PROFILE_DIR={_REMOTE_BROWSER_PROFILE}\n"
    + "mkdir -p \"$PROFILE_DIR\"\n"
    + "if pgrep -af \"chromium.*--remote-debugging-port=${BROWSER_PORT}.*--user-data-dir=${PROFILE_DIR}\" >/dev/null 2>&1 || "
    + "pgrep -af \"chromium-browser.*--remote-debugging-port=${BROWSER_PORT}.*--user-data-dir=${PROFILE_DIR}\" >/dev/null 2>&1; then\n"
    + "  exit 0\n"
    + "fi\n"
    + "pkill -x chromium >/dev/null 2>&1 || true\n"
    + "pkill -x chromium-browser >/dev/null 2>&1 || true\n"
    + "nohup \"$BROWSER_BIN\" "
    + "--no-sandbox "
    + "--disable-gpu "
    + "--disable-dev-shm-usage "
    + "--no-first-run "
    + "--no-default-browser-check "
    + "--remote-debugging-address=127.0.0.1 "
    + "--remote-debugging-port=\"$BROWSER_PORT\" "
    + "--user-data-dir=\"$PROFILE_DIR\" "
    + "--window-size=1280,800 "
    + "--start-maximized "
    + "about:blank >/tmp/manus-chromium.log 2>&1 < /dev/null &\n"
)


@dataclass
class BrowserSession:
    """单会话浏览器状态。"""

    browser: Any
    context: Any
    page: Any
    tunnel_key: str
    local_port: int
    current_url: str = ""
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class BrowserService:
    """浏览器服务 - 将 Playwright 绑定到 VNC 中的 Chromium。"""

    def __init__(self):
        self._playwright = None
        self._sessions: Dict[str, BrowserSession] = {}
        self._current_url = ""
        self._is_ready = False
        self._init_lock = asyncio.Lock()

    @staticmethod
    def _session_key(conversation_id: Optional[str]) -> str:
        return conversation_id or "_default"

    @staticmethod
    def _tunnel_key(conversation_id: Optional[str]) -> str:
        return f"browser-cdp:{conversation_id or '_default'}"

    async def _ensure_runtime(self):
        """确保 Playwright 运行时已就绪。"""
        if self._is_ready and self._playwright:
            return

        async with self._init_lock:
            if self._is_ready and self._playwright:
                return

            try:
                from playwright.async_api import async_playwright
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "Playwright 未安装。请在后端虚拟环境执行: pip install playwright && playwright install chromium"
                ) from exc

            try:
                self._playwright = await async_playwright().start()
                self._is_ready = True
            except Exception as exc:
                err_msg = str(exc)
                if "Executable doesn't exist" in err_msg:
                    raise RuntimeError(
                        "Chromium 未安装。请在后端虚拟环境执行: playwright install chromium"
                    ) from exc
                raise

    async def _ensure_remote_browser(self, conversation_id: Optional[str]):
        """确保容器内桌面和 Chromium 可用。"""
        code, _, stderr = await sandbox_manager.exec_command(
            _ENSURE_DESKTOP_COMMAND,
            conversation_id=conversation_id,
            timeout=10,
        )
        if code != 0:
            raise RuntimeError(f"启动桌面环境失败: {stderr.strip() or 'unknown error'}")

        code, _, stderr = await sandbox_manager.exec_command(
            _ENSURE_REMOTE_BROWSER_COMMAND,
            conversation_id=conversation_id,
            timeout=15,
        )
        if code != 0:
            raise RuntimeError(f"启动 Chromium 失败: {stderr.strip() or 'unknown error'}")

    async def _connect_over_cdp(self, local_port: int):
        """连接宿主机本地隧道上的 Chromium CDP。"""
        endpoint = f"http://127.0.0.1:{local_port}"
        last_error = None
        for _ in range(40):
            try:
                return await self._playwright.chromium.connect_over_cdp(endpoint, timeout=2000)
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(0.25)
        raise RuntimeError(f"连接 Chromium 调试端口失败: {last_error}")

    async def _select_page(self, context: Any):
        """选择当前最可能处于前台的页面。"""
        pages = [page for page in context.pages if not page.is_closed()]
        if not pages:
            return await context.new_page()

        for page in reversed(pages):
            try:
                if await page.evaluate("document.hasFocus()"):
                    return page
            except Exception:
                continue

        return pages[-1]

    async def _refresh_session_targets(self, session: BrowserSession):
        """刷新连接中的 context/page 引用。"""
        contexts = list(session.browser.contexts)
        if not contexts:
            await asyncio.sleep(0.2)
            contexts = list(session.browser.contexts)
        if not contexts:
            raise RuntimeError("Chromium 未暴露可用的浏览器上下文")

        session.context = contexts[0]
        session.page = await self._select_page(session.context)
        session.current_url = session.page.url or session.current_url

    async def _create_session(self, conversation_id: Optional[str]) -> BrowserSession:
        """创建新的 CDP 会话。"""
        await self._ensure_runtime()
        await self._ensure_remote_browser(conversation_id)
        sandbox = await sandbox_manager.get_or_create(conversation_id)

        tunnel_key = self._tunnel_key(conversation_id)
        local_port = await docker_exec_raw_tcp_proxy.create_tunnel(
            sandbox.container_name,
            _REMOTE_BROWSER_PORT,
            tunnel_key,
        )

        try:
            browser = await self._connect_over_cdp(local_port)
            session = BrowserSession(
                browser=browser,
                context=None,
                page=None,
                tunnel_key=tunnel_key,
                local_port=local_port,
            )
            await self._refresh_session_targets(session)
            return session
        except Exception:
            docker_exec_raw_tcp_proxy.close_tunnel(tunnel_key)
            raise

    async def _dispose_session(self, session: BrowserSession):
        """释放会话资源。"""
        try:
            if session.browser:
                await session.browser.close()
        except Exception:
            pass
        docker_exec_raw_tcp_proxy.close_tunnel(session.tunnel_key)

    async def _ensure_session(self, conversation_id: Optional[str]) -> BrowserSession:
        """确保指定会话拥有绑定到 VNC Chromium 的页面。"""
        key = self._session_key(conversation_id)
        session = self._sessions.get(key)

        if session:
            try:
                if session.browser and session.browser.is_connected():
                    await self._refresh_session_targets(session)
                    return session
            except Exception:
                pass
            await self._dispose_session(session)

        session = await self._create_session(conversation_id)
        self._sessions[key] = session
        return session

    async def _take_screenshot(self, session: BrowserSession) -> str:
        """截取当前页面截图，返回 base64。"""
        screenshot_bytes = await session.page.screenshot(type="jpeg", quality=70)
        return base64.b64encode(screenshot_bytes).decode("utf-8")

    async def navigate(self, url: str, conversation_id: Optional[str] = None) -> Dict[str, Any]:
        """导航到指定 URL。"""
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
            except Exception as exc:
                await event_bus.publish(SandboxEvent(
                    "browser_error",
                    {"url": url, "error": str(exc)},
                    window_id="browser",
                    conversation_id=conversation_id,
                ))
                return {"url": url, "error": str(exc)}

    async def screenshot(self, conversation_id: Optional[str] = None) -> Dict[str, Any]:
        """获取当前页面截图。"""
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
        """获取当前页面文本内容。"""
        try:
            session = await self._ensure_session(conversation_id)
        except Exception as exc:
            return f"获取页面内容失败: {str(exc)}"

        async with session.lock:
            try:
                content = await session.page.evaluate("document.body.innerText")
                if len(content) > 5000:
                    content = content[:5000] + "\n... [内容被截断]"
                return content
            except Exception as exc:
                return f"获取页面内容失败: {str(exc)}"

    async def click(self, selector: str, conversation_id: Optional[str] = None) -> Dict[str, Any]:
        """点击页面元素。"""
        session = await self._ensure_session(conversation_id)
        async with session.lock:
            try:
                await session.page.click(selector, timeout=5000)
                await session.page.wait_for_load_state("domcontentloaded", timeout=5000)
                await self._refresh_session_targets(session)
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
            except Exception as exc:
                return {"success": False, "error": str(exc)}

    async def click_by_coordinates(
        self,
        x: float,
        y: float,
        viewport_width: float,
        viewport_height: float,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """按截图坐标点击页面。"""
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
                await asyncio.sleep(0.2)
                await self._refresh_session_targets(session)

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
            except Exception as exc:
                await event_bus.publish(SandboxEvent(
                    "browser_error",
                    {"error": str(exc), "action": "click_by_coordinates"},
                    window_id="browser",
                    conversation_id=conversation_id,
                ))
                return {"success": False, "error": str(exc)}

    async def type_text(
        self,
        text: str,
        submit: bool = False,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """向当前焦点元素输入文本。"""
        session = await self._ensure_session(conversation_id)
        async with session.lock:
            try:
                if text:
                    await session.page.keyboard.type(text, delay=10)
                if submit:
                    await session.page.keyboard.press("Enter")

                await asyncio.sleep(0.15)
                await self._refresh_session_targets(session)
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
            except Exception as exc:
                await event_bus.publish(SandboxEvent(
                    "browser_error",
                    {"error": str(exc), "action": "type_text"},
                    window_id="browser",
                    conversation_id=conversation_id,
                ))
                return {"success": False, "error": str(exc)}

    async def scroll(
        self,
        delta_y: float,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """按指定距离滚动页面。"""
        session = await self._ensure_session(conversation_id)
        async with session.lock:
            try:
                await session.page.mouse.wheel(0, delta_y)
                await asyncio.sleep(0.1)
                await self._refresh_session_targets(session)

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
            except Exception as exc:
                await event_bus.publish(SandboxEvent(
                    "browser_error",
                    {"error": str(exc), "action": "scroll"},
                    window_id="browser",
                    conversation_id=conversation_id,
                ))
                return {"success": False, "error": str(exc)}

    async def press_key(
        self,
        key: str,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """向页面发送按键。"""
        session = await self._ensure_session(conversation_id)
        async with session.lock:
            try:
                await session.page.keyboard.press(key)
                await asyncio.sleep(0.1)
                await self._refresh_session_targets(session)

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
            except Exception as exc:
                await event_bus.publish(SandboxEvent(
                    "browser_error",
                    {"error": str(exc), "action": "press_key", "key": key},
                    window_id="browser",
                    conversation_id=conversation_id,
                ))
                return {"success": False, "error": str(exc)}

    def get_status(self) -> Dict[str, Any]:
        """返回浏览器运行状态。"""
        sessions: Dict[str, Dict[str, Any]] = {}
        for key, session in self._sessions.items():
            url = session.current_url
            is_open = False
            try:
                is_open = bool(session.browser and session.browser.is_connected())
                if not url and session.page and not session.page.is_closed():
                    url = session.page.url or ""
            except Exception:
                is_open = False
            sessions[key] = {
                "current_url": url,
                "is_open": is_open,
            }

        return {
            "is_ready": self._is_ready,
            "session_count": len(self._sessions),
            "sessions": sessions,
        }

    async def close(self, conversation_id: Optional[str] = None):
        """关闭浏览器会话。"""
        if conversation_id is not None:
            key = self._session_key(conversation_id)
            session = self._sessions.pop(key, None)
            if session:
                await self._dispose_session(session)
            return

        for key in list(self._sessions.keys()):
            session = self._sessions.pop(key, None)
            if session:
                await self._dispose_session(session)

        if self._playwright:
            await self._playwright.stop()
        self._is_ready = False
        self._playwright = None
        self._current_url = ""


browser_service = BrowserService()
