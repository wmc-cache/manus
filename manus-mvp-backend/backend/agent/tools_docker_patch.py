"""
Docker 沙箱工具补丁模块

通过猴子补丁（monkey-patch）方式，将 tools.py / tools_extended.py 中的
进程级执行函数替换为 Docker 容器内执行版本。

使用方式：
    在 main.py 启动时调用 apply_docker_sandbox_patch()

设计原则：
- 零侵入：不修改原有 tools.py 的任何代码
- 可逆：通过环境变量 MANUS_DOCKER_SANDBOX=false 禁用
- 向后兼容：Docker 不可用时自动回退
"""

import logging
import os

logger = logging.getLogger("agent.tools_docker_patch")

DOCKER_SANDBOX_ENABLED = os.environ.get(
    "MANUS_DOCKER_SANDBOX", "true"
).strip().lower() in ("true", "1", "yes")


def apply_docker_sandbox_patch():
    """
    应用 Docker 沙箱补丁。

    替换 tools.py 中的以下函数：
    - shell_exec -> docker_shell_exec
    - execute_code -> docker_execute_code
    - _get_workspace -> docker_get_workspace_root (通过替换 get_workspace_root)

    同时替换 tools_extended.py 中的对应函数。
    """
    if not DOCKER_SANDBOX_ENABLED:
        logger.info("Docker 沙箱已禁用 (MANUS_DOCKER_SANDBOX=false)")
        return

    try:
        from sandbox.docker_tools_adapter import (
            docker_shell_exec,
            docker_execute_code,
            docker_get_workspace_root,
        )
    except ImportError as e:
        logger.warning("无法导入 Docker 适配层，跳过补丁: %s", e)
        return

    # ---- 补丁 tools.py ----
    try:
        import agent.tools as tools_module

        # 保存原始函数引用（用于回退）
        tools_module._original_shell_exec = tools_module.shell_exec
        tools_module._original_execute_code = tools_module.execute_code
        tools_module._original_get_workspace = tools_module._get_workspace

        # 替换 shell_exec
        async def patched_shell_exec(command: str) -> str:
            from contextvars import copy_context
            conv_id = tools_module._get_current_conversation_id()
            return await docker_shell_exec(command, conversation_id=conv_id)

        tools_module.shell_exec = patched_shell_exec

        # 替换 execute_code
        async def patched_execute_code(code: str) -> str:
            conv_id = tools_module._get_current_conversation_id()
            return await docker_execute_code(code, conversation_id=conv_id)

        tools_module.execute_code = patched_execute_code

        # 替换 _get_workspace
        def patched_get_workspace(conversation_id=None):
            cid = conversation_id or tools_module._get_current_conversation_id()
            return docker_get_workspace_root(cid)

        tools_module._get_workspace = patched_get_workspace

        # 更新工具注册表中的函数引用
        if hasattr(tools_module, "TOOL_REGISTRY"):
            registry = tools_module.TOOL_REGISTRY
            if "shell_exec" in registry:
                registry["shell_exec"]["func"] = patched_shell_exec
            if "execute_code" in registry:
                registry["execute_code"]["func"] = patched_execute_code

        logger.info("已为 tools.py 应用 Docker 沙箱补丁")

    except Exception as e:
        logger.error("补丁 tools.py 失败: %s", e)

    # ---- 补丁 tools_extended.py ----
    try:
        import agent.tools_extended as ext_module

        ext_module._original_get_workspace_from_conv = ext_module._get_workspace_from_conv

        def patched_ext_get_workspace(conversation_id=None):
            return docker_get_workspace_root(conversation_id)

        ext_module._get_workspace_from_conv = patched_ext_get_workspace

        logger.info("已为 tools_extended.py 应用 Docker 沙箱补丁")

    except Exception as e:
        logger.warning("补丁 tools_extended.py 失败（可能未安装）: %s", e)

    # ---- 补丁 tools_search.py ----
    try:
        import agent.tools_search as search_module

        search_module._original_get_workspace = search_module._get_workspace

        def patched_search_get_workspace(conversation_id=None):
            return docker_get_workspace_root(conversation_id)

        search_module._get_workspace = patched_search_get_workspace

        logger.info("已为 tools_search.py 应用 Docker 沙箱补丁")

    except Exception as e:
        logger.warning("补丁 tools_search.py 失败（可能未安装）: %s", e)

    # ---- 补丁 filesystem.py（使 API 层也使用 Docker workspace）----
    try:
        import sandbox.filesystem as fs_module

        fs_module._original_get_workspace_root = fs_module.get_workspace_root
        fs_module.get_workspace_root = docker_get_workspace_root

        logger.info("已为 filesystem.py 应用 Docker 沙箱补丁")

    except Exception as e:
        logger.warning("补丁 filesystem.py 失败: %s", e)

    # ---- 补丁 context_manager.py ----
    try:
        import agent.context_manager as ctx_module
        # context_manager 通过 from sandbox.filesystem import get_workspace_root 导入
        # 需要替换其本地引用
        if hasattr(ctx_module, "get_workspace_root"):
            ctx_module.get_workspace_root = docker_get_workspace_root
            logger.info("已为 context_manager.py 应用 Docker 沙箱补丁")
    except Exception as e:
        logger.warning("补丁 context_manager.py 失败: %s", e)

    logger.info("Docker 沙箱补丁全部应用完成")


def revert_docker_sandbox_patch():
    """
    撤销 Docker 沙箱补丁，恢复原始函数。
    """
    try:
        import agent.tools as tools_module
        if hasattr(tools_module, "_original_shell_exec"):
            tools_module.shell_exec = tools_module._original_shell_exec
            tools_module.execute_code = tools_module._original_execute_code
            tools_module._get_workspace = tools_module._original_get_workspace

            registry = tools_module.TOOL_REGISTRY
            if "shell_exec" in registry:
                registry["shell_exec"]["func"] = tools_module._original_shell_exec
            if "execute_code" in registry:
                registry["execute_code"]["func"] = tools_module._original_execute_code

            logger.info("已撤销 tools.py 的 Docker 沙箱补丁")
    except Exception as e:
        logger.warning("撤销 tools.py 补丁失败: %s", e)

    try:
        import agent.tools_extended as ext_module
        if hasattr(ext_module, "_original_get_workspace_from_conv"):
            ext_module._get_workspace_from_conv = ext_module._original_get_workspace_from_conv
            logger.info("已撤销 tools_extended.py 的 Docker 沙箱补丁")
    except Exception:
        pass

    try:
        import sandbox.filesystem as fs_module
        if hasattr(fs_module, "_original_get_workspace_root"):
            fs_module.get_workspace_root = fs_module._original_get_workspace_root
            logger.info("已撤销 filesystem.py 的 Docker 沙箱补丁")
    except Exception:
        pass
