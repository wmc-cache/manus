"""
用于精确上下文预算管理的令牌计数实用程序。
使用tiktoken进行准确的令牌计数，并退回到基于字符的tiktoken不可用时的估计。
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_encoder = None
_FALLBACK_CHARS_PER_TOKEN = 3.5  # Conservative estimate for Chinese + English mixed text


def _get_encoder():
    """Lazy-load tiktoken encoder."""
    global _encoder
    if _encoder is not None:
        return _encoder
    try:
        import tiktoken
        # cl100k_base is used by GPT-4, and is a reasonable approximation
        # for DeepSeek and other modern LLMs
        _encoder = tiktoken.get_encoding("cl100k_base")
        logger.info("Tiktoken encoder loaded successfully (cl100k_base)")
        return _encoder
    except ImportError:
        logger.warning("tiktoken not installed, using character-based estimation")
        return None
    except Exception as exc:
        logger.warning("Failed to load tiktoken encoder: %s", exc)
        return None


def count_tokens(text: str) -> int:
    """Count tokens in a text string.
    
    Uses tiktoken if available, otherwise falls back to character-based estimation.
    """
    if not text:
        return 0
    encoder = _get_encoder()
    if encoder is not None:
        try:
            return len(encoder.encode(text))
        except Exception:
            pass
    # Fallback: character-based estimation
    return int(len(text) / _FALLBACK_CHARS_PER_TOKEN)


def count_message_tokens(message: Dict[str, Any]) -> int:
    """Count tokens in a single LLM message dict.
    
    Accounts for role, content, and tool_calls overhead.
    """
    tokens = 4  # Every message has overhead: <|start|>role\ncontent<|end|>
    
    role = message.get("role", "")
    tokens += count_tokens(role)
    
    content = message.get("content", "")
    if content:
        tokens += count_tokens(str(content))
    
    # Tool calls add extra tokens
    tool_calls = message.get("tool_calls", [])
    if tool_calls:
        for tc in tool_calls:
            tokens += 3  # function call overhead
            func = tc.get("function", {})
            tokens += count_tokens(func.get("name", ""))
            tokens += count_tokens(func.get("arguments", ""))
    
    # Tool call ID for tool messages
    tool_call_id = message.get("tool_call_id", "")
    if tool_call_id:
        tokens += count_tokens(tool_call_id)
    
    return tokens


def count_messages_tokens(messages: List[Dict[str, Any]]) -> int:
    """Count total tokens across all messages."""
    total = 3  # Every conversation has priming tokens
    for msg in messages:
        total += count_message_tokens(msg)
    return total


def estimate_remaining_budget(
    messages: List[Dict[str, Any]],
    max_context_tokens: int = 60000,
    reserved_for_response: int = 4000,
) -> int:
    """Estimate remaining token budget for additional context.
    
    Args:
        messages: Current message list
        max_context_tokens: Maximum context window size
        reserved_for_response: Tokens reserved for model response
        
    Returns:
        Number of tokens available for additional context
    """
    used = count_messages_tokens(messages)
    remaining = max_context_tokens - used - reserved_for_response
    return max(0, remaining)


def truncate_to_token_budget(
    text: str,
    max_tokens: int,
    suffix: str = "\n... [内容已截断]",
) -> str:
    """Truncate text to fit within a token budget.
    
    Uses binary search for efficient truncation with tiktoken,
    or character-based estimation as fallback.
    """
    if count_tokens(text) <= max_tokens:
        return text
    
    encoder = _get_encoder()
    if encoder is not None:
        try:
            tokens = encoder.encode(text)
            suffix_tokens = encoder.encode(suffix)
            budget = max_tokens - len(suffix_tokens)
            if budget <= 0:
                return suffix
            truncated_tokens = tokens[:budget]
            return encoder.decode(truncated_tokens) + suffix
        except Exception:
            pass
    
    # Fallback: character-based truncation
    char_budget = int(max_tokens * _FALLBACK_CHARS_PER_TOKEN)
    if len(text) <= char_budget:
        return text
    return text[:char_budget] + suffix
