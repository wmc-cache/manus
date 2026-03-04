"""
统一配置管理 — 集中管理所有环境变量和配置常量。

设计原则：
- 所有环境变量读取集中在此模块，其他模块通过 `settings` 单例访问
- 使用 dataclass 提供类型安全和默认值
- 消除各文件中重复的 `_read_env` / `os.environ.get` 调用
- 支持运行时通过 `settings.xxx = value` 覆盖（便于测试）
"""

import os
from dataclasses import dataclass, field
from typing import List, Set


# ---------------------------------------------------------------------------
# 通用环境变量解析工具
# ---------------------------------------------------------------------------

def _read_positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
        return value if value > 0 else default
    except ValueError:
        return default


def _read_positive_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
        return value if value > 0 else default
    except ValueError:
        return default


def _read_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _read_str(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def _read_str_list(name: str, default: str, sep: str = ",") -> List[str]:
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(sep) if item.strip()]


# ---------------------------------------------------------------------------
# 配置数据类
# ---------------------------------------------------------------------------

@dataclass
class AgentSettings:
    """Agent 核心循环配置"""
    max_iterations: int = field(default_factory=lambda: _read_positive_int("MANUS_MAX_ITERATIONS", 30))
    progress_heartbeat_seconds: float = field(default_factory=lambda: _read_positive_float("MANUS_PROGRESS_HEARTBEAT_SECONDS", 2.0))
    tool_loop_window: int = field(default_factory=lambda: _read_positive_int("MANUS_TOOL_LOOP_WINDOW", 8))
    tool_loop_repeat_threshold: int = field(default_factory=lambda: _read_positive_int("MANUS_TOOL_LOOP_REPEAT_THRESHOLD", 3))
    plan_use_llm: bool = field(default_factory=lambda: _read_bool("MANUS_PLAN_USE_LLM", True))
    max_context_messages: int = field(default_factory=lambda: _read_positive_int("MANUS_MAX_CONTEXT_MESSAGES", 40))
    max_recent_message_chars: int = field(default_factory=lambda: _read_positive_int("MANUS_MAX_RECENT_MESSAGE_CHARS", 4000))
    max_old_message_chars: int = field(default_factory=lambda: _read_positive_int("MANUS_MAX_OLD_MESSAGE_CHARS", 1200))
    max_conversation_title_chars: int = field(default_factory=lambda: _read_positive_int("MANUS_MAX_CONVERSATION_TITLE_CHARS", 50))
    conversations_file: str = field(default_factory=lambda: _read_str("MANUS_CONVERSATIONS_FILE", "/tmp/manus_workspace/conversations.json"))
    todo_filename: str = "todo.md"
    default_conversation_title: str = "新对话"
    continue_messages: Set[str] = field(default_factory=lambda: {"继续", "继续。", "continue", "continue.", "go on"})

    # 可安全并行执行的工具（只读/无副作用）
    parallel_safe_tools: Set[str] = field(default_factory=lambda: {
        "web_search", "read_file", "list_files", "find_files", "grep_files",
        "browser_screenshot", "browser_get_content", "data_analysis",
    })
    # 有副作用的工具，必须串行执行
    serial_only_tools: Set[str] = field(default_factory=lambda: {
        "write_file", "edit_file", "append_file", "shell_exec", "execute_code",
        "browser_navigate", "browser_click", "browser_input", "browser_scroll",
        "wide_research", "spawn_sub_agents",
    })
    # 默认可用工具列表
    default_tool_names: List[str] = field(default_factory=lambda: [
        "web_search", "wide_research", "spawn_sub_agents",
        "shell_exec", "execute_code", "expose_port",
        "browser_navigate", "browser_screenshot", "browser_get_content",
        "browser_click", "browser_input", "browser_scroll",
        "read_file", "write_file", "edit_file", "append_file",
        "list_files", "find_files", "grep_files", "data_analysis",
    ])


@dataclass
class ServerSettings:
    """服务器与 API 配置"""
    api_token: str = field(default_factory=lambda: _read_str("MANUS_API_TOKEN", ""))
    allowed_origins: List[str] = field(default_factory=lambda: _read_str_list(
        "MANUS_ALLOWED_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    ))
    allowed_origin_regex: str = field(default_factory=lambda: _read_str(
        "MANUS_ALLOWED_ORIGIN_REGEX",
        r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    ))
    host: str = "0.0.0.0"
    port: int = 8000
    version: str = "0.3.0"


@dataclass
class UploadSettings:
    """文件上传配置"""
    max_image_bytes: int = field(default_factory=lambda: _read_positive_int("MANUS_MAX_UPLOAD_IMAGE_BYTES", 6 * 1024 * 1024))
    max_image_count: int = field(default_factory=lambda: _read_positive_int("MANUS_MAX_UPLOAD_IMAGE_COUNT", 4))


@dataclass
class SandboxSettings:
    """沙箱容器配置"""
    image: str = field(default_factory=lambda: _read_str("MANUS_SANDBOX_IMAGE", "manus-sandbox:latest"))
    network_mode: str = field(default_factory=lambda: _read_str("MANUS_SANDBOX_NETWORK_MODE", "auto"))
    network_name: str = field(default_factory=lambda: _read_str("MANUS_SANDBOX_NETWORK", "manus-sandbox-net"))
    host_workspace_base: str = field(default_factory=lambda: _read_str("MANUS_HOST_WORKSPACE_BASE", "/tmp/manus_workspace"))
    container_workspace: str = "/tmp/workspace"
    container_home: str = "/home/ubuntu"
    container_user: str = "ubuntu"
    mem_limit: str = field(default_factory=lambda: _read_str("MANUS_CONTAINER_MEM_LIMIT", "512m"))
    cpu_quota: int = field(default_factory=lambda: _read_positive_int("MANUS_CONTAINER_CPU_QUOTA", 100000))
    cpu_period: int = field(default_factory=lambda: _read_positive_int("MANUS_CONTAINER_CPU_PERIOD", 100000))
    pids_limit: int = field(default_factory=lambda: _read_positive_int("MANUS_CONTAINER_PIDS_LIMIT", 256))
    exec_timeout: int = field(default_factory=lambda: _read_positive_int("MANUS_EXEC_TIMEOUT", 30))
    max_exec_timeout: int = field(default_factory=lambda: _read_positive_int("MANUS_MAX_EXEC_TIMEOUT", 300))
    idle_timeout: int = field(default_factory=lambda: _read_positive_int("MANUS_CONTAINER_IDLE_TIMEOUT", 600))


@dataclass
class AppSettings:
    """应用全局配置聚合"""
    agent: AgentSettings = field(default_factory=AgentSettings)
    server: ServerSettings = field(default_factory=ServerSettings)
    upload: UploadSettings = field(default_factory=UploadSettings)
    sandbox: SandboxSettings = field(default_factory=SandboxSettings)


# ---------------------------------------------------------------------------
# 全局配置单例
# ---------------------------------------------------------------------------
settings = AppSettings()
