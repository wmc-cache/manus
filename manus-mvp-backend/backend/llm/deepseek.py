"""
DeepSeek API 封装 - 兼容 OpenAI SDK

Improvements:
1. Enhanced system prompt with structured instructions
2. Extended tool definitions (edit_file, list_files, data_analysis, etc.)
3. Retry with exponential backoff for transient errors
4. Request-level timeout protection
5. Token usage tracking
"""
import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import AsyncGenerator, List, Dict, Any, Optional, Tuple

import httpx
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


def _read_env_key_from_file(path: Path, key: str) -> str:
    if not path.exists():
        return ""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() != key:
                continue
            value = v.strip().strip('"').strip("'")
            if value:
                return value
    except Exception:
        return ""
    return ""


def _resolve_multi_key(keys: tuple[str, ...], default: str = "") -> str:
    for key_name in keys:
        env_value = os.environ.get(key_name, "").strip()
        if env_value:
            return env_value

    backend_env = Path(__file__).resolve().parents[1] / ".env"
    for key_name in keys:
        file_value = _read_env_key_from_file(backend_env, key_name)
        if file_value:
            return file_value

    frontend_env = Path(__file__).resolve().parents[3] / "manus-frontend" / ".env"
    for key_name in keys:
        file_value = _read_env_key_from_file(frontend_env, key_name)
        if file_value:
            return file_value

    return default


def _resolve_deepseek_api_key() -> str:
    # Anthropic-compatible env first, then Claude-compatible env, then legacy DeepSeek env.
    return _resolve_multi_key(
        ("ANTHROPIC_AUTH_TOKEN", "CLAUDE_API_KEY", "DEEPSEEK_API_KEY"),
        default="",
    )


# DeepSeek API 配置
DEEPSEEK_API_KEY = _resolve_deepseek_api_key()
DEEPSEEK_BASE_URL = _resolve_multi_key(
    ("ANTHROPIC_BASE_URL", "CLAUDE_BASE_URL", "DEEPSEEK_BASE_URL"),
    default="https://api.deepseek.com",
)
DEEPSEEK_MODEL = _resolve_multi_key(
    ("ANTHROPIC_DEFAULT_SONNET_MODEL", "CLAUDE_MODEL", "DEEPSEEK_MODEL"),
    default="deepseek-chat",
)
DEEPSEEK_FALLBACK_MODELS = [
    item.strip()
    for item in os.environ.get("DEEPSEEK_FALLBACK_MODELS", "deepseek-chat").split(",")
    if item.strip()
]


def _read_int_env(name: str, default: int, minimum: int = 1) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
        return value if value >= minimum else default
    except ValueError:
        return default


def _read_optional_bool_env(*names: str) -> Optional[bool]:
    for name in names:
        raw = os.environ.get(name, "").strip().lower()
        if not raw:
            continue
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off"}:
            return False
    return None


DEEPSEEK_MAX_TOKENS = _read_int_env("DEEPSEEK_MAX_TOKENS", 8192, minimum=256)
DEEPSEEK_MAX_TOKENS_FALLBACK = _read_int_env("DEEPSEEK_MAX_TOKENS_FALLBACK", 4096, minimum=256)
MAX_RETRIES = _read_int_env("DEEPSEEK_MAX_RETRIES", 3, minimum=1)
REQUEST_TIMEOUT = _read_int_env("DEEPSEEK_REQUEST_TIMEOUT", 120, minimum=10)

client = AsyncOpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
    timeout=REQUEST_TIMEOUT,
)
_anthropic_http_client: Optional[httpx.AsyncClient] = None


def _uses_anthropic_messages_api() -> bool:
    base_url = (DEEPSEEK_BASE_URL or "").strip().lower()
    if not base_url:
        return False
    if "/v1/openai" in base_url or "/openai/" in base_url:
        return False
    return "anthropic" in base_url


def llm_supports_vision() -> bool:
    explicit = _read_optional_bool_env(
        "MANUS_LLM_SUPPORTS_VISION",
        "CLAUDE_SUPPORTS_VISION",
        "DEEPSEEK_SUPPORTS_VISION",
    )
    if explicit is not None:
        return explicit

    base_url = (DEEPSEEK_BASE_URL or "").strip().lower()
    if "hone.vvvv.ee" in base_url:
        return False
    return True


def vision_capability_error() -> str:
    base_url = (DEEPSEEK_BASE_URL or "").strip()
    gateway_hint = f"\n当前网关: {base_url}" if base_url else ""
    return (
        "当前服务已收到图片，但当前配置的模型网关不支持图片理解。"
        f"{gateway_hint}"
        "\n请切换到支持视觉输入的模型/网关后重试，或先用文字描述图片内容。"
    )


def _anthropic_api_base_url() -> str:
    base_url = (DEEPSEEK_BASE_URL or "").strip().rstrip("/")
    if base_url.endswith("/v1"):
        return base_url
    return f"{base_url}/v1"


def _anthropic_headers() -> Dict[str, str]:
    headers = {
        "content-type": "application/json",
        "anthropic-version": os.environ.get("ANTHROPIC_VERSION", "2023-06-01"),
    }
    if DEEPSEEK_API_KEY:
        headers["x-api-key"] = DEEPSEEK_API_KEY
        headers["Authorization"] = f"Bearer {DEEPSEEK_API_KEY}"
    return headers


def _get_anthropic_http_client() -> httpx.AsyncClient:
    global _anthropic_http_client
    if _anthropic_http_client is None or _anthropic_http_client.is_closed:
        _anthropic_http_client = httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
        )
    return _anthropic_http_client


def _build_anthropic_payload(
    messages: List[Dict[str, Any]],
    *,
    use_tools: bool = True,
    allowed_tool_names: Optional[List[str]] = None,
    stream: bool = False,
) -> Dict[str, Any]:
    safe_messages = _sanitize_messages_for_api(messages)
    system_prompt, anthropic_messages = _convert_messages_to_anthropic(safe_messages)
    payload: Dict[str, Any] = {
        "model": DEEPSEEK_MODEL,
        "system": system_prompt,
        "messages": anthropic_messages,
        "temperature": 0.7,
        "max_tokens": DEEPSEEK_MAX_TOKENS,
    }
    selected_tools = _select_tools(allowed_tool_names)
    if use_tools and selected_tools:
        payload["tools"] = _convert_tools_to_anthropic(selected_tools)
    if stream:
        payload["stream"] = True
    return payload


async def _iter_anthropic_sse_events(
    response: httpx.Response,
) -> AsyncGenerator[Tuple[str, Dict[str, Any]], None]:
    event_name = ""
    data_lines: List[str] = []

    async for raw_line in response.aiter_lines():
        line = raw_line.rstrip("\r")
        if not line:
            if not data_lines:
                event_name = ""
                continue
            raw_data = "\n".join(data_lines).strip()
            data_lines = []
            if not raw_data or raw_data == "[DONE]":
                event_name = ""
                continue
            try:
                payload = json.loads(raw_data)
            except json.JSONDecodeError:
                logger.warning(
                    "Failed to decode Anthropic SSE event=%s data=%s",
                    event_name or "(unknown)",
                    raw_data[:300],
                )
                event_name = ""
                continue
            resolved_name = event_name or str(payload.get("type", "")).strip()
            event_name = ""
            if isinstance(payload, dict):
                yield resolved_name, payload
            continue

        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())

    if data_lines:
        raw_data = "\n".join(data_lines).strip()
        if raw_data and raw_data != "[DONE]":
            try:
                payload = json.loads(raw_data)
            except json.JSONDecodeError:
                logger.warning("Failed to decode trailing Anthropic SSE data=%s", raw_data[:300])
            else:
                resolved_name = event_name or str(payload.get("type", "")).strip()
                if isinstance(payload, dict):
                    yield resolved_name, payload


def _stringify_content(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _content_to_text_blocks(content: Any) -> List[Dict[str, str]]:
    if content is None:
        return []

    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []

    if isinstance(content, list):
        blocks: List[Dict[str, str]] = []
        for item in content:
            if isinstance(item, str):
                if item:
                    blocks.append({"type": "text", "text": item})
                continue
            if isinstance(item, dict):
                item_type = str(item.get("type", "")).strip()
                if item_type == "text" and isinstance(item.get("text"), str):
                    text = item.get("text", "")
                    if text:
                        blocks.append({"type": "text", "text": text})
                    continue
                if isinstance(item.get("text"), str):
                    text = item.get("text", "")
                    if text:
                        blocks.append({"type": "text", "text": text})
                    continue
            serialized = _stringify_content(item)
            if serialized:
                blocks.append({"type": "text", "text": serialized})
        return blocks

    serialized = _stringify_content(content)
    return [{"type": "text", "text": serialized}] if serialized else []


def _append_anthropic_message(
    messages: List[Dict[str, Any]],
    role: str,
    blocks: List[Dict[str, Any]],
) -> None:
    if not blocks:
        return
    if messages and messages[-1].get("role") == role and isinstance(messages[-1].get("content"), list):
        messages[-1]["content"].extend(blocks)
        return
    messages.append({"role": role, "content": blocks})


def _convert_tools_to_anthropic(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    anthropic_tools: List[Dict[str, Any]] = []
    for tool in tools:
        function_spec = tool.get("function", {})
        name = str(function_spec.get("name", "")).strip()
        if not name:
            continue
        anthropic_tools.append({
            "name": name,
            "description": function_spec.get("description", "") or "",
            "input_schema": function_spec.get("parameters") or {
                "type": "object",
                "properties": {},
            },
        })
    return anthropic_tools


def _convert_messages_to_anthropic(messages: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, Any]]]:
    system_parts = [_system_prompt()]
    anthropic_messages: List[Dict[str, Any]] = []

    index = 0
    while index < len(messages):
        message = messages[index]
        role = message.get("role")

        if role == "system":
            system_text = _stringify_content(message.get("content")).strip()
            if system_text:
                system_parts.append(system_text)
            index += 1
            continue

        if role == "user":
            _append_anthropic_message(
                anthropic_messages,
                "user",
                _content_to_text_blocks(message.get("content")),
            )
            index += 1
            continue

        if role == "assistant":
            blocks = _content_to_text_blocks(message.get("content"))
            raw_tool_calls = message.get("tool_calls")
            if isinstance(raw_tool_calls, list):
                for tool_call in raw_tool_calls:
                    if not isinstance(tool_call, dict):
                        continue
                    tool_call_id = str(tool_call.get("id", "")).strip()
                    function_spec = tool_call.get("function", {})
                    if not isinstance(function_spec, dict):
                        function_spec = {}
                    name = str(function_spec.get("name", "")).strip()
                    if not tool_call_id or not name:
                        continue
                    arguments, _, _ = _parse_tool_arguments(function_spec.get("arguments"))
                    blocks.append({
                        "type": "tool_use",
                        "id": tool_call_id,
                        "name": name,
                        "input": arguments,
                    })
            _append_anthropic_message(anthropic_messages, "assistant", blocks)
            index += 1
            continue

        if role == "tool":
            tool_result_blocks: List[Dict[str, Any]] = []
            while index < len(messages) and messages[index].get("role") == "tool":
                tool_message = messages[index]
                tool_call_id = str(tool_message.get("tool_call_id", "")).strip()
                if tool_call_id:
                    tool_result_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": tool_call_id,
                        "content": _stringify_content(tool_message.get("content")),
                    })
                index += 1
            _append_anthropic_message(anthropic_messages, "user", tool_result_blocks)
            continue

        index += 1

    return "\n\n".join(part for part in system_parts if part), anthropic_messages


def _parse_anthropic_response(payload: Dict[str, Any]) -> Dict[str, Any]:
    content_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []

    for block in payload.get("content", []) or []:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type", "")).strip()
        if block_type == "text":
            text = block.get("text", "")
            if isinstance(text, str) and text:
                content_parts.append(text)
            continue
        if block_type == "tool_use":
            tool_calls.append({
                "id": str(block.get("id", "")).strip(),
                "name": str(block.get("name", "")).strip(),
                "arguments": block.get("input") if isinstance(block.get("input"), dict) else {},
            })

    stop_reason = str(payload.get("stop_reason", "")).strip()
    finish_reason = ""
    if stop_reason == "tool_use":
        finish_reason = "tool_calls"
    elif stop_reason == "end_turn":
        finish_reason = "stop"
    elif stop_reason == "max_tokens":
        finish_reason = "length"
    elif stop_reason:
        finish_reason = stop_reason

    result: Dict[str, Any] = {
        "content": "".join(content_parts),
        "tool_calls": tool_calls,
    }
    if finish_reason:
        result["finish_reason"] = finish_reason
    return result


def _track_anthropic_usage(payload: Dict[str, Any]) -> None:
    global _total_prompt_tokens, _total_completion_tokens
    usage = payload.get("usage") or {}
    if not isinstance(usage, dict):
        return
    _total_prompt_tokens += int(usage.get("input_tokens") or 0)
    _total_completion_tokens += int(usage.get("output_tokens") or 0)


def _extract_anthropic_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        payload = None

    if isinstance(payload, dict):
        error_obj = payload.get("error")
        if isinstance(error_obj, dict):
            message = _stringify_content(error_obj.get("message")).strip()
            if message:
                return f"Error code: {response.status_code} - {message}"
        message = _stringify_content(payload.get("message")).strip()
        if message:
            return f"Error code: {response.status_code} - {message}"

    text = response.text.strip()
    if text:
        return f"Error code: {response.status_code} - {text[:300]}"
    return f"Error code: {response.status_code}"


async def _post_anthropic_messages(payload: Dict[str, Any]) -> Dict[str, Any]:
    started_at = time.perf_counter()
    response = await _get_anthropic_http_client().post(
        f"{_anthropic_api_base_url()}/messages",
        headers=_anthropic_headers(),
        json=payload,
    )
    elapsed = time.perf_counter() - started_at
    if response.status_code >= 400:
        raise RuntimeError(_extract_anthropic_error(response))
    logger.info(
        "Anthropic completion model=%s elapsed=%.3fs",
        payload.get("model") or DEEPSEEK_MODEL,
        elapsed,
    )
    return response.json()


async def _create_anthropic_completion_with_retry(
    payload: Dict[str, Any],
    max_retries: int = MAX_RETRIES,
) -> Dict[str, Any]:
    last_error = None

    requested_model = str(payload.get("model") or DEEPSEEK_MODEL).strip() or DEEPSEEK_MODEL
    candidate_models = [requested_model]
    for fallback in DEEPSEEK_FALLBACK_MODELS:
        if fallback not in candidate_models:
            candidate_models.append(fallback)

    for model_index, model_name in enumerate(candidate_models):
        model_payload = dict(payload)
        model_payload["model"] = model_name
        retry_budget = max_retries if model_index == 0 else max(1, min(2, max_retries))

        for attempt in range(retry_budget):
            try:
                return await _post_anthropic_messages(model_payload)
            except Exception as e:
                last_error = e
                err_text = str(e).lower()

                if (
                    attempt == 0
                    and model_payload.get("max_tokens") != DEEPSEEK_MAX_TOKENS_FALLBACK
                    and "max_tokens" in err_text
                ):
                    retry_payload = dict(model_payload)
                    retry_payload["max_tokens"] = DEEPSEEK_MAX_TOKENS_FALLBACK
                    try:
                        return await _post_anthropic_messages(retry_payload)
                    except Exception as e2:
                        last_error = e2
                        if _is_model_not_found(e2):
                            logger.warning(
                                "Anthropic model `%s` unavailable after max_tokens fallback, trying next model.",
                                model_name,
                            )
                            break
                        if not _is_retryable(e2):
                            raise

                if _is_model_not_found(e):
                    logger.warning(
                        "Anthropic model `%s` unavailable, trying fallback model.",
                        model_name,
                    )
                    break

                if _is_retryable(e) and attempt < retry_budget - 1:
                    delay = 2 ** attempt
                    logger.warning(
                        "Anthropic request failed (model=%s attempt %d/%d), retrying in %ds: %s",
                        model_name,
                        attempt + 1,
                        retry_budget,
                        delay,
                        str(e)[:200],
                    )
                    await asyncio.sleep(delay)
                    continue

                raise

    raise last_error  # type: ignore[misc]


async def _chat_completion_anthropic(
    messages: List[Dict[str, Any]],
    use_tools: bool = True,
    allowed_tool_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    payload = _build_anthropic_payload(
        messages,
        use_tools=use_tools,
        allowed_tool_names=allowed_tool_names,
        stream=False,
    )
    response_payload = await _create_anthropic_completion_with_retry(payload)
    _track_anthropic_usage(response_payload)
    return _parse_anthropic_response(response_payload)


async def _chat_completion_anthropic_stream(
    messages: List[Dict[str, Any]],
    use_tools: bool = True,
    allowed_tool_names: Optional[List[str]] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    payload = _build_anthropic_payload(
        messages,
        use_tools=use_tools,
        allowed_tool_names=allowed_tool_names,
        stream=True,
    )
    last_error = None

    requested_model = str(payload.get("model") or DEEPSEEK_MODEL).strip() or DEEPSEEK_MODEL
    candidate_models = [requested_model]
    for fallback in DEEPSEEK_FALLBACK_MODELS:
        if fallback not in candidate_models:
            candidate_models.append(fallback)

    def _build_tool_call_payload(block: Dict[str, Any]) -> Dict[str, Any]:
        args, parse_error, preview = _parse_tool_arguments(block.get("arguments", ""))
        item: Dict[str, Any] = {
            "id": str(block.get("id", "")).strip(),
            "name": str(block.get("name", "")).strip(),
            "arguments": args,
        }
        if parse_error:
            item["parse_error"] = parse_error
            if preview:
                item["raw_arguments_preview"] = preview
        return item

    for model_index, model_name in enumerate(candidate_models):
        base_model_payload = dict(payload)
        base_model_payload["model"] = model_name
        retry_budget = MAX_RETRIES if model_index == 0 else max(1, min(2, MAX_RETRIES))

        for attempt in range(retry_budget):
            model_payload = dict(base_model_payload)
            emitted_partial = False
            first_emit_at: Optional[float] = None
            started_at = time.perf_counter()
            content_parts: List[str] = []
            tool_calls: List[Dict[str, Any]] = []
            content_blocks: Dict[int, Dict[str, Any]] = {}
            usage: Dict[str, int] = {"input_tokens": 0, "output_tokens": 0}

            try:
                async with _get_anthropic_http_client().stream(
                    "POST",
                    f"{_anthropic_api_base_url()}/messages",
                    headers=_anthropic_headers(),
                    json=model_payload,
                ) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        raise RuntimeError(_extract_anthropic_error(response))

                    async for event_name, event_payload in _iter_anthropic_sse_events(response):
                        if not event_payload:
                            continue

                        if event_name == "message_start":
                            message_obj = event_payload.get("message") or {}
                            usage_obj = message_obj.get("usage") or {}
                            usage["input_tokens"] = int(usage_obj.get("input_tokens") or 0)
                            continue

                        if event_name == "message_delta":
                            usage_obj = event_payload.get("usage") or {}
                            usage["output_tokens"] = int(
                                usage_obj.get("output_tokens") or usage["output_tokens"]
                            )
                            continue

                        if event_name == "content_block_start":
                            index = int(event_payload.get("index") or 0)
                            block = event_payload.get("content_block") or {}
                            block_type = str(block.get("type", "")).strip()
                            if block_type == "text":
                                text = _stringify_content(block.get("text"))
                                content_blocks[index] = {"type": "text"}
                                if text:
                                    content_parts.append(text)
                                    emitted_partial = True
                                    if first_emit_at is None:
                                        first_emit_at = time.perf_counter()
                                    yield {"type": "content", "data": text}
                                continue

                            if block_type == "tool_use":
                                initial_input = block.get("input")
                                initial_json = ""
                                if isinstance(initial_input, dict) and initial_input:
                                    initial_json = json.dumps(initial_input, ensure_ascii=False)
                                content_blocks[index] = {
                                    "type": "tool_use",
                                    "id": str(block.get("id", "")).strip(),
                                    "name": str(block.get("name", "")).strip(),
                                    "arguments": initial_json,
                                    "emitted": False,
                                }
                            continue

                        if event_name == "content_block_delta":
                            index = int(event_payload.get("index") or 0)
                            delta = event_payload.get("delta") or {}
                            delta_type = str(delta.get("type", "")).strip()

                            if delta_type == "text_delta":
                                text = _stringify_content(delta.get("text"))
                                if text:
                                    content_parts.append(text)
                                    emitted_partial = True
                                    if first_emit_at is None:
                                        first_emit_at = time.perf_counter()
                                    yield {"type": "content", "data": text}
                                continue

                            if delta_type == "input_json_delta":
                                block = content_blocks.setdefault(
                                    index,
                                    {"type": "tool_use", "id": "", "name": "", "arguments": "", "emitted": False},
                                )
                                block["arguments"] = str(block.get("arguments", "")) + _stringify_content(
                                    delta.get("partial_json")
                                )
                            continue

                        if event_name == "content_block_stop":
                            index = int(event_payload.get("index") or 0)
                            block = content_blocks.get(index)
                            if not block or block.get("type") != "tool_use" or block.get("emitted"):
                                continue
                            payload_item = _build_tool_call_payload(block)
                            block["emitted"] = True
                            tool_calls.append(payload_item)
                            emitted_partial = True
                            if first_emit_at is None:
                                first_emit_at = time.perf_counter()
                            yield {"type": "tool_call", "data": payload_item}
                            continue

                        if event_name == "error":
                            error_obj = event_payload.get("error") or {}
                            error_message = _stringify_content(error_obj.get("message")).strip()
                            raise RuntimeError(error_message or "Anthropic stream error")

                        if event_name == "message_stop":
                            break

                for block in content_blocks.values():
                    if block.get("type") != "tool_use" or block.get("emitted"):
                        continue
                    payload_item = _build_tool_call_payload(block)
                    block["emitted"] = True
                    tool_calls.append(payload_item)
                    emitted_partial = True
                    if first_emit_at is None:
                        first_emit_at = time.perf_counter()
                    yield {"type": "tool_call", "data": payload_item}

                _track_anthropic_usage({"usage": usage})
                logger.info(
                    "Anthropic stream model=%s first_emit=%.3fs total=%.3fs tool_calls=%d",
                    model_name,
                    (first_emit_at - started_at) if first_emit_at is not None else -1.0,
                    time.perf_counter() - started_at,
                    len(tool_calls),
                )
                yield {
                    "type": "done",
                    "data": {
                        "content": "".join(content_parts),
                        "tool_calls": tool_calls,
                    },
                }
                return
            except Exception as e:
                last_error = e
                err_text = str(e).lower()

                if emitted_partial:
                    raise

                if (
                    attempt == 0
                    and model_payload.get("max_tokens") != DEEPSEEK_MAX_TOKENS_FALLBACK
                    and "max_tokens" in err_text
                ):
                    base_model_payload["max_tokens"] = DEEPSEEK_MAX_TOKENS_FALLBACK
                    continue

                if _is_model_not_found(e):
                    logger.warning(
                        "Anthropic stream model `%s` unavailable, trying fallback model.",
                        model_name,
                    )
                    break

                if _is_retryable(e) and attempt < retry_budget - 1:
                    delay = 2 ** attempt
                    logger.warning(
                        "Anthropic stream failed (model=%s attempt %d/%d), retrying in %ds: %s",
                        model_name,
                        attempt + 1,
                        retry_budget,
                        delay,
                        str(e)[:200],
                    )
                    await asyncio.sleep(delay)
                    continue

                raise

    raise last_error  # type: ignore[misc]


# ============ Enhanced System Prompt ============
# Use get_system_prompt() for a fresh date on every LLM call while keeping the
# prompt body stable for KV-cache reuse.
_FALLBACK_SYSTEM_PROMPT = "你是 Manus，一个强大的通用 AI Agent 助手。请使用工具完成用户任务。"

try:
    from llm.system_prompt import get_system_prompt as _get_system_prompt
except ImportError:
    _get_system_prompt = None  # type: ignore[assignment]


def _system_prompt() -> str:
    """Return the system prompt with today's date."""
    if _get_system_prompt is not None:
        return _get_system_prompt()
    return _FALLBACK_SYSTEM_PROMPT


# ============ Tool Definitions (OpenAI Function Calling format) ============

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "搜索互联网获取信息。用于查找最新资讯、新闻动态、事实验证、获取参考资料等。涉及时效信息时应优先使用本工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "wide_research",
            "description": "并行研究多个对象。会基于 query_template 对每个 item 执行搜索，并在工作目录 research/ 下产出分项结果和 summary.md。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query_template": {
                        "type": "string",
                        "description": "查询模板，支持 {item} 占位符，如 '{item} 公司 2026 最新动态'"
                    },
                    "items": {
                        "type": "array",
                        "description": "待研究对象列表（字符串数组）",
                        "items": {"type": "string"}
                    }
                },
                "required": ["query_template", "items"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_sub_agents",
            "description": "启动多个轻量子代理并行执行同质任务（仅深度研究模式开启时可用）。每个子代理在 multi_agent/agents/<agent_id>/ 产出 task/observation/result，并在 multi_agent/reduce_summary.md 做汇总。支持自动重试失败的子代理。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_template": {
                        "type": "string",
                        "description": "子代理任务模板，支持 {item} 占位符"
                    },
                    "items": {
                        "type": "array",
                        "description": "待并行处理对象列表（字符串数组）",
                        "items": {"type": "string"}
                    },
                    "reduce_goal": {
                        "type": "string",
                        "description": "可选，reduce 阶段的汇总目标说明"
                    },
                    "max_concurrency": {
                        "type": "integer",
                        "description": "可选，子代理并发数（正整数）"
                    },
                    "max_items": {
                        "type": "integer",
                        "description": "可选，本次最多处理条目数（正整数）"
                    },
                    "max_iterations": {
                        "type": "integer",
                        "description": "可选，单个子代理最大循环轮数（正整数）"
                    }
                },
                "required": ["task_template", "items"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "shell_exec",
            "description": "在终端中执行 shell 命令。用于系统操作、安装软件、文件管理等。命令会实时显示在用户的终端窗口中。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 shell 命令"
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_code",
            "description": "执行 Python 代码。用于数据处理、计算、生成图表等。代码会显示在编辑器窗口中。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "要执行的 Python 代码"
                    }
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_navigate",
            "description": "在浏览器中打开指定 URL。仅在需要网页交互（登录、点击、输入、滚动等）时使用；纯信息检索优先 web_search。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要打开的网页 URL"
                    }
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_get_content",
            "description": "获取当前浏览器页面的文本内容。需要先使用 browser_navigate 打开网页。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": "在浏览器页面上点击指定坐标。需要先用 browser_navigate 打开页面。",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {
                        "type": "number",
                        "description": "点击位置的 X 坐标（像素）"
                    },
                    "y": {
                        "type": "number",
                        "description": "点击位置的 Y 坐标（像素）"
                    }
                },
                "required": ["x", "y"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_input",
            "description": "在浏览器当前聚焦的输入框中输入文本。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "要输入的文本"
                    },
                    "submit": {
                        "type": "boolean",
                        "description": "输入后是否按回车提交（默认 false）"
                    }
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_scroll",
            "description": "滚动浏览器页面。",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down"],
                        "description": "滚动方向"
                    },
                    "amount": {
                        "type": "number",
                        "description": "滚动像素数（默认 300）"
                    }
                },
                "required": ["direction"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取指定路径的文件内容。使用相对路径（如 report.md），文件会在编辑器窗口中显示。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径（相对路径）"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "将内容写入指定路径的文件。使用相对路径（如 report.md），文件会自动保存到工作目录并在编辑器窗口中显示。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径（相对路径）"
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的内容"
                    }
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "对文件进行精确的查找替换编辑，比重写整个文件更高效。每个编辑包含 find（要查找的文本）和 replace（替换文本）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径（相对路径）"
                    },
                    "edits": {
                        "type": "array",
                        "description": "编辑操作列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "find": {
                                    "type": "string",
                                    "description": "要查找的文本"
                                },
                                "replace": {
                                    "type": "string",
                                    "description": "替换文本"
                                },
                                "all": {
                                    "type": "boolean",
                                    "description": "是否替换所有匹配（默认 false）"
                                }
                            },
                            "required": ["find", "replace"]
                        }
                    }
                },
                "required": ["path", "edits"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "append_file",
            "description": "向文件末尾追加内容，适合增量写入。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径（相对路径）"
                    },
                    "content": {
                        "type": "string",
                        "description": "要追加的内容"
                    }
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出目录内容，以树形结构显示文件和子目录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "目录路径（相对路径，默认 '.'）"
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "最大递归深度（默认 3）"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "data_analysis",
            "description": "执行 Python 数据分析代码。自动导入 pandas、numpy、matplotlib、seaborn，图表自动保存为 PNG。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python 数据分析代码"
                    },
                    "description": {
                        "type": "string",
                        "description": "分析目标描述（可选）"
                    }
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_files",
            "description": "使用 glob 模式匹配查找文件。支持 ** 递归匹配。例如: **/*.py 查找所有 Python 文件, src/**/*.ts 查找 src 下所有 TypeScript 文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob 匹配模式（如 **/*.py, *.md, src/**/*.ts）"
                    },
                    "path": {
                        "type": "string",
                        "description": "搜索起始目录（相对路径，默认 '.'）"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最大返回结果数（默认 50）"
                    }
                },
                "required": ["pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "expose_port",
            "description": "暴露沙箱内的 Web 服务端口，生成可从实体机浏览器直接访问的链接。在沙箱内启动 HTTP 服务后调用此工具，用户就可以在自己的浏览器中直接访问。",
            "parameters": {
                "type": "object",
                "properties": {
                    "port": {
                        "type": "integer",
                        "description": "要暴露的端口号（如 8080、3000 等）"
                    },
                    "label": {
                        "type": "string",
                        "description": "可选，服务描述标签（如 '我的网站'、'API 服务'）"
                    }
                },
                "required": ["port"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "grep_files",
            "description": "使用正则表达式搜索文件内容。支持上下文行显示。结果按文件修改时间倒序排列。适合在项目中查找特定代码、配置或文本。",
            "parameters": {
                "type": "object",
                "properties": {
                    "regex": {
                        "type": "string",
                        "description": "正则表达式搜索模式"
                    },
                    "scope": {
                        "type": "string",
                        "description": "文件范围的 glob 模式（如 **/*.py 只搜索 Python 文件，默认 **/*）"
                    },
                    "path": {
                        "type": "string",
                        "description": "搜索起始目录（相对路径，默认 '.'）"
                    },
                    "leading": {
                        "type": "integer",
                        "description": "匹配行前的上下文行数（默认 0）"
                    },
                    "trailing": {
                        "type": "integer",
                        "description": "匹配行后的上下文行数（默认 0）"
                    }
                },
                "required": ["regex"]
            }
        }
    },
]


# ============ Parsing Utilities ============

def _parse_tool_arguments(raw_arguments: Any) -> Tuple[Dict[str, Any], Optional[str], str]:
    """解析工具参数，避免把解析失败静默吞成 {}。"""
    if isinstance(raw_arguments, dict):
        return raw_arguments, None, ""

    if raw_arguments is None:
        # 一些模型对无参工具会返回 null / 空值，按空对象处理。
        return {}, None, ""

    if not isinstance(raw_arguments, str):
        preview = str(raw_arguments)[:300]
        return {}, f"参数类型异常: {type(raw_arguments).__name__}。", preview

    text = raw_arguments.strip()
    if not text:
        # 一些模型对无参工具会返回空字符串，按空对象处理。
        return {}, None, ""

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        preview = text[:300]
        return {}, f"参数 JSON 解析失败（位置 {e.pos}）: {e.msg}", preview

    if not isinstance(parsed, dict):
        if parsed is None:
            return {}, None, ""
        return {}, f"参数解析后不是 JSON 对象，而是 {type(parsed).__name__}。", str(parsed)[:300]

    return parsed, None, ""


def _normalize_tool_calls(raw_tool_calls: Any) -> List[Dict[str, Any]]:
    tool_calls: List[Dict[str, Any]] = []
    if not raw_tool_calls:
        return tool_calls

    for tc in raw_tool_calls:
        function = getattr(tc, "function", None)
        raw_arguments = getattr(function, "arguments", None)
        parsed_args, parse_error, preview = _parse_tool_arguments(raw_arguments)

        item: Dict[str, Any] = {
            "id": getattr(tc, "id", "") or "",
            "name": getattr(function, "name", "") or "",
            "arguments": parsed_args,
        }
        if parse_error:
            item["parse_error"] = parse_error
            if preview:
                item["raw_arguments_preview"] = preview

        tool_calls.append(item)

    return tool_calls


def _select_tools(allowed_tool_names: Optional[List[str]]) -> List[Dict[str, Any]]:
    """按名称筛选可用工具；为空时返回全部工具。"""
    if allowed_tool_names is None:
        return TOOLS
    allow = {name.strip() for name in allowed_tool_names if isinstance(name, str) and name.strip()}
    if not allow:
        return []
    return [
        tool
        for tool in TOOLS
        if tool.get("function", {}).get("name") in allow
    ]


def _sanitize_messages_for_api(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    修复消息序列，避免 `role=tool` 与 `assistant.tool_calls` 失配导致 400。

    规则：
    1. 丢弃无法匹配的孤立 tool 消息
    2. 若 assistant 的 tool_calls 在后续未完整闭合，则移除该 assistant 的 tool_calls 字段
    3. 过滤缺少 id/name 的无效 tool_call
    """
    sanitized: List[Dict[str, Any]] = []
    pending_tool_ids: set[str] = set()
    pending_assistant_index: Optional[int] = None

    dropped_orphan_tools = 0
    dropped_invalid_tool_calls = 0
    stripped_unclosed_tool_calls = 0

    def _strip_pending_assistant_tool_calls() -> None:
        nonlocal pending_tool_ids, pending_assistant_index, stripped_unclosed_tool_calls
        if pending_assistant_index is None or not pending_tool_ids:
            pending_tool_ids = set()
            pending_assistant_index = None
            return
        item = dict(sanitized[pending_assistant_index])
        if "tool_calls" in item:
            item.pop("tool_calls", None)
            stripped_unclosed_tool_calls += 1
        if item.get("content") is None:
            item["content"] = ""
        sanitized[pending_assistant_index] = item
        pending_tool_ids = set()
        pending_assistant_index = None

    for raw in messages:
        if not isinstance(raw, dict):
            continue

        role = raw.get("role")

        if role == "assistant":
            tool_calls = raw.get("tool_calls")
            if isinstance(tool_calls, list) and tool_calls:
                _strip_pending_assistant_tool_calls()

                cleaned_tool_calls: List[Dict[str, Any]] = []
                cleaned_ids: set[str] = set()
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        dropped_invalid_tool_calls += 1
                        continue
                    tc_id = str(tc.get("id", "")).strip()
                    func = tc.get("function")
                    name = ""
                    if isinstance(func, dict):
                        name = str(func.get("name", "")).strip()
                    if not tc_id or not name:
                        dropped_invalid_tool_calls += 1
                        continue
                    arguments = "{}"
                    if isinstance(func, dict):
                        raw_arguments = func.get("arguments")
                        if isinstance(raw_arguments, str) and raw_arguments.strip():
                            arguments = raw_arguments
                        elif raw_arguments is not None:
                            arguments = json.dumps(raw_arguments, ensure_ascii=False)
                    cleaned_tool_calls.append({
                        "id": tc_id,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": arguments,
                        },
                    })
                    cleaned_ids.add(tc_id)

                item = dict(raw)
                if cleaned_tool_calls:
                    item["tool_calls"] = cleaned_tool_calls
                    if item.get("content") is None:
                        item["content"] = ""
                    sanitized.append(item)
                    pending_tool_ids = cleaned_ids
                    pending_assistant_index = len(sanitized) - 1
                else:
                    item.pop("tool_calls", None)
                    if item.get("content") is None:
                        item["content"] = ""
                    sanitized.append(item)
                    pending_tool_ids = set()
                    pending_assistant_index = None
                continue

            _strip_pending_assistant_tool_calls()
            item = dict(raw)
            item.pop("tool_calls", None)
            if item.get("content") is None:
                item["content"] = ""
            sanitized.append(item)
            continue

        if role == "tool":
            if not pending_tool_ids:
                dropped_orphan_tools += 1
                continue

            tool_call_id = str(raw.get("tool_call_id", "")).strip()
            if not tool_call_id:
                if len(pending_tool_ids) == 1:
                    tool_call_id = next(iter(pending_tool_ids))
                else:
                    dropped_orphan_tools += 1
                    continue

            if tool_call_id not in pending_tool_ids:
                dropped_orphan_tools += 1
                continue

            item = {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": raw.get("content") or "",
            }
            sanitized.append(item)
            pending_tool_ids.remove(tool_call_id)
            if not pending_tool_ids:
                pending_assistant_index = None
            continue

        _strip_pending_assistant_tool_calls()

        if role not in {"system", "user"}:
            continue
        item = dict(raw)
        if item.get("content") is None:
            item["content"] = ""
        sanitized.append(item)

    _strip_pending_assistant_tool_calls()

    if dropped_orphan_tools or dropped_invalid_tool_calls or stripped_unclosed_tool_calls:
        logger.warning(
            "Sanitized LLM messages: dropped_orphan_tools=%d dropped_invalid_tool_calls=%d stripped_unclosed_tool_calls=%d input=%d output=%d",
            dropped_orphan_tools,
            dropped_invalid_tool_calls,
            stripped_unclosed_tool_calls,
            len(messages),
            len(sanitized),
        )

    return sanitized


# ============ Retry Logic ============

_RETRYABLE_ERRORS = (
    "rate_limit",
    "timeout",
    "server_error",
    "503",
    "502",
    "429",
    "connection",
    "overloaded",
)


def _is_retryable(error: Exception) -> bool:
    """Check if an error is transient and retryable."""
    err_text = str(error).lower()
    return any(keyword in err_text for keyword in _RETRYABLE_ERRORS)


def _is_model_not_found(error: Exception) -> bool:
    err_text = str(error).lower()
    return (
        "model_not_found" in err_text
        or "no available channel for model" in err_text
        or "model does not exist" in err_text
    )


async def _create_completion_with_retry(kwargs: Dict[str, Any], max_retries: int = MAX_RETRIES):
    """Create completion with retry/max_tokens fallback and model fallback."""
    last_error = None

    requested_model = str(kwargs.get("model") or DEEPSEEK_MODEL).strip() or DEEPSEEK_MODEL
    candidate_models = [requested_model]
    for fallback in DEEPSEEK_FALLBACK_MODELS:
        if fallback not in candidate_models:
            candidate_models.append(fallback)

    for model_index, model_name in enumerate(candidate_models):
        model_kwargs = dict(kwargs)
        model_kwargs["model"] = model_name
        retry_budget = max_retries if model_index == 0 else max(1, min(2, max_retries))

        for attempt in range(retry_budget):
            try:
                return await client.chat.completions.create(**model_kwargs)
            except Exception as e:
                last_error = e
                err_text = str(e).lower()

                # max_tokens fallback (try once per model)
                if (
                    attempt == 0
                    and model_kwargs.get("max_tokens") != DEEPSEEK_MAX_TOKENS_FALLBACK
                    and "max_tokens" in err_text
                ):
                    retry_kwargs = dict(model_kwargs)
                    retry_kwargs["max_tokens"] = DEEPSEEK_MAX_TOKENS_FALLBACK
                    try:
                        return await client.chat.completions.create(**retry_kwargs)
                    except Exception as e2:
                        last_error = e2
                        if _is_model_not_found(e2):
                            logger.warning(
                                "LLM model `%s` unavailable after max_tokens fallback, trying next model.",
                                model_name,
                            )
                            break
                        if not _is_retryable(e2):
                            raise

                if _is_model_not_found(e):
                    logger.warning(
                        "LLM model `%s` unavailable, trying fallback model.",
                        model_name,
                    )
                    break

                # Retry for transient errors
                if _is_retryable(e) and attempt < retry_budget - 1:
                    delay = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                    logger.warning(
                        "LLM request failed (model=%s attempt %d/%d), retrying in %ds: %s",
                        model_name,
                        attempt + 1,
                        retry_budget,
                        delay,
                        str(e)[:200],
                    )
                    await asyncio.sleep(delay)
                    continue

                raise

    raise last_error  # type: ignore


# ============ Token Usage Tracking ============

_total_prompt_tokens = 0
_total_completion_tokens = 0


def _track_usage(response):
    """Track token usage from API response."""
    global _total_prompt_tokens, _total_completion_tokens
    usage = getattr(response, "usage", None)
    if usage:
        _total_prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
        _total_completion_tokens += getattr(usage, "completion_tokens", 0) or 0


def get_token_usage() -> Dict[str, int]:
    """Return cumulative token usage."""
    return {
        "prompt_tokens": _total_prompt_tokens,
        "completion_tokens": _total_completion_tokens,
        "total_tokens": _total_prompt_tokens + _total_completion_tokens,
    }


# ============ Stream Completion ============

async def chat_completion_stream(
    messages: List[Dict[str, Any]],
    use_tools: bool = True,
    allowed_tool_names: Optional[List[str]] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """流式调用 DeepSeek API"""
    if not DEEPSEEK_API_KEY:
        yield {
            "type": "error",
            "data": "LLM API Key 未配置，请在环境变量或 .env 中设置 ANTHROPIC_AUTH_TOKEN、CLAUDE_API_KEY 或 DEEPSEEK_API_KEY 后重启后端。"
        }
        return

    try:
        if _uses_anthropic_messages_api():
            async for chunk in _chat_completion_anthropic_stream(
                messages,
                use_tools=use_tools,
                allowed_tool_names=allowed_tool_names,
            ):
                if isinstance(chunk, dict):
                    yield chunk
            return

        safe_messages = _sanitize_messages_for_api(messages)
        kwargs = {
            "model": DEEPSEEK_MODEL,
            "messages": [{"role": "system", "content": _system_prompt()}] + safe_messages,
            "stream": True,
            "temperature": 0.7,
            "max_tokens": DEEPSEEK_MAX_TOKENS,
        }
        selected_tools = _select_tools(allowed_tool_names)
        if use_tools and selected_tools:
            kwargs["tools"] = selected_tools
            kwargs["tool_choice"] = "auto"

        response = await _create_completion_with_retry(kwargs)

        current_content = ""
        current_tool_calls = {}

        async for chunk in response:
            delta = chunk.choices[0].delta if chunk.choices else None
            if not delta:
                continue

            finish_reason = chunk.choices[0].finish_reason

            if delta.content:
                current_content += delta.content
                yield {"type": "content", "data": delta.content}

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in current_tool_calls:
                        current_tool_calls[idx] = {"id": tc.id or "", "name": "", "arguments": ""}
                    if tc.id:
                        current_tool_calls[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            current_tool_calls[idx]["name"] = tc.function.name
                        if tc.function.arguments:
                            current_tool_calls[idx]["arguments"] += tc.function.arguments

            if finish_reason == "tool_calls":
                for idx, tc in current_tool_calls.items():
                    args, parse_error, preview = _parse_tool_arguments(tc["arguments"])
                    payload = {
                        "id": tc["id"],
                        "name": tc["name"],
                        "arguments": args,
                    }
                    if parse_error:
                        payload["parse_error"] = parse_error
                        if preview:
                            payload["raw_arguments_preview"] = preview
                    yield {"type": "tool_call", "data": payload}
            elif finish_reason == "stop":
                tool_calls_payload: List[Dict[str, Any]] = []
                for tc in current_tool_calls.values():
                    args, parse_error, preview = _parse_tool_arguments(tc["arguments"])
                    item: Dict[str, Any] = {
                        "id": tc["id"],
                        "name": tc["name"],
                        "arguments": args,
                    }
                    if parse_error:
                        item["parse_error"] = parse_error
                        if preview:
                            item["raw_arguments_preview"] = preview
                    tool_calls_payload.append(item)
                yield {"type": "done", "data": {"content": current_content, "tool_calls": tool_calls_payload}}

    except Exception as e:
        yield {"type": "error", "data": str(e)}


# ============ Non-Stream Completion ============

async def chat_completion(
    messages: List[Dict[str, Any]],
    use_tools: bool = True,
    allowed_tool_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """非流式调用 DeepSeek API（带重试）"""
    if not DEEPSEEK_API_KEY:
        return {
            "content": "调用 LLM 时出错: 未配置 API Key。请在环境变量或 .env 中设置 ANTHROPIC_AUTH_TOKEN、CLAUDE_API_KEY 或 DEEPSEEK_API_KEY 后重启后端。",
            "tool_calls": []
        }

    try:
        if _uses_anthropic_messages_api():
            return await _chat_completion_anthropic(
                messages,
                use_tools=use_tools,
                allowed_tool_names=allowed_tool_names,
            )

        safe_messages = _sanitize_messages_for_api(messages)
        kwargs = {
            "model": DEEPSEEK_MODEL,
            "messages": [{"role": "system", "content": _system_prompt()}] + safe_messages,
            "temperature": 0.7,
            "max_tokens": DEEPSEEK_MAX_TOKENS,
        }
        selected_tools = _select_tools(allowed_tool_names)
        if use_tools and selected_tools:
            kwargs["tools"] = selected_tools
            kwargs["tool_choice"] = "auto"

        response = await _create_completion_with_retry(kwargs)
        _track_usage(response)

        choice = response.choices[0]

        result = {
            "content": choice.message.content or "",
            "tool_calls": _normalize_tool_calls(choice.message.tool_calls),
        }
        if choice.finish_reason:
            result["finish_reason"] = choice.finish_reason

        return result

    except Exception as e:
        logger.error("LLM completion failed: %s", str(e)[:500])
        return {"content": f"调用 LLM 时出错: {str(e)}", "tool_calls": []}
