"""
Model Router - Dynamic model selection based on task type and complexity.

Key features:
1. Route different tasks to appropriate models (planning vs execution vs summarization)
2. Fallback chain for reliability
3. Cost-aware routing (use cheaper models for simple tasks)
4. Configurable via environment variables
"""
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


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

    return default

logger = logging.getLogger(__name__)


@dataclass
class ModelConfig:
    """Configuration for a single model."""
    name: str
    provider: str  # "deepseek", "openai", etc.
    base_url: str
    api_key: str
    max_tokens: int = 8192
    max_tokens_fallback: int = 4096
    temperature: float = 0.0
    supports_tools: bool = True
    cost_tier: str = "medium"  # "low", "medium", "high"


@dataclass
class RoutingDecision:
    """Result of model routing decision."""
    model: ModelConfig
    reason: str
    fallback: Optional[ModelConfig] = None


class ModelRouter:
    """Routes tasks to appropriate models based on task type and complexity."""

    # Task types that can be routed
    TASK_PLANNING = "planning"
    TASK_EXECUTION = "execution"
    TASK_SUMMARIZATION = "summarization"
    TASK_SUB_AGENT = "sub_agent"
    TASK_CODE_GENERATION = "code_generation"

    def __init__(self):
        self._models: Dict[str, ModelConfig] = {}
        self._default_model: Optional[str] = None
        self._routing_rules: Dict[str, str] = {}
        self._load_config()

    def _load_config(self):
        """Load model configurations from environment."""
        # Primary model (DeepSeek) - also read from .env file
        deepseek_key = _resolve_multi_key(
            ("ANTHROPIC_AUTH_TOKEN", "CLAUDE_API_KEY", "DEEPSEEK_API_KEY"),
            default="",
        )
        if deepseek_key:
            base_url = _resolve_multi_key(
                ("ANTHROPIC_BASE_URL", "CLAUDE_BASE_URL", "DEEPSEEK_BASE_URL"),
                default="https://api.deepseek.com",
            )
            model_name = _resolve_multi_key(
                ("ANTHROPIC_DEFAULT_SONNET_MODEL", "CLAUDE_MODEL", "DEEPSEEK_MODEL"),
                default="deepseek-chat",
            )
            self._models["deepseek-chat"] = ModelConfig(
                name=model_name,
                provider="deepseek",
                base_url=base_url,
                api_key=deepseek_key,
                max_tokens=int(os.environ.get("DEEPSEEK_MAX_TOKENS", "8192")),
                max_tokens_fallback=int(os.environ.get("DEEPSEEK_MAX_TOKENS_FALLBACK", "4096")),
                supports_tools=True,
                cost_tier="low",
            )
            self._default_model = "deepseek-chat"

        # Optional: DeepSeek Reasoner for complex planning
        deepseek_reasoner = os.environ.get("DEEPSEEK_REASONER_MODEL", "").strip()
        if deepseek_reasoner and deepseek_key:
            self._models["deepseek-reasoner"] = ModelConfig(
                name=deepseek_reasoner,
                provider="deepseek",
                base_url=_resolve_multi_key(
                    ("ANTHROPIC_BASE_URL", "CLAUDE_BASE_URL", "DEEPSEEK_BASE_URL"),
                    default="https://api.deepseek.com",
                ),
                api_key=deepseek_key,
                max_tokens=int(os.environ.get("DEEPSEEK_REASONER_MAX_TOKENS", "4096")),
                supports_tools=False,  # Reasoner models typically don't support tools
                cost_tier="medium",
            )

        # Optional: OpenAI models
        openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
        openai_base = os.environ.get("OPENAI_BASE_URL", "").strip()
        openai_model = os.environ.get("OPENAI_MODEL", "").strip()
        if openai_key and openai_model:
            self._models["openai"] = ModelConfig(
                name=openai_model,
                provider="openai",
                base_url=openai_base or "https://api.openai.com/v1",
                api_key=openai_key,
                max_tokens=int(os.environ.get("OPENAI_MAX_TOKENS", "4096")),
                supports_tools=True,
                cost_tier="high",
            )

        # Load routing rules from environment
        self._routing_rules = {
            self.TASK_PLANNING: os.environ.get("MANUS_PLANNING_MODEL", "").strip(),
            self.TASK_EXECUTION: os.environ.get("MANUS_EXECUTION_MODEL", "").strip(),
            self.TASK_SUMMARIZATION: os.environ.get("MANUS_SUMMARIZATION_MODEL", "").strip(),
            self.TASK_SUB_AGENT: os.environ.get("MANUS_SUBAGENT_MODEL", "").strip(),
            self.TASK_CODE_GENERATION: os.environ.get("MANUS_CODE_MODEL", "").strip(),
        }

    def get_model(self, task_type: str = TASK_EXECUTION) -> RoutingDecision:
        """Route to the appropriate model based on task type."""
        # Check if there's a specific model configured for this task type
        preferred = self._routing_rules.get(task_type, "").strip()
        if preferred and preferred in self._models:
            model = self._models[preferred]
            fallback = self._get_fallback(preferred)
            return RoutingDecision(
                model=model,
                reason=f"Configured model for {task_type}",
                fallback=fallback,
            )

        # Use default model
        if self._default_model and self._default_model in self._models:
            model = self._models[self._default_model]
            return RoutingDecision(
                model=model,
                reason=f"Default model for {task_type}",
                fallback=None,
            )

        # Emergency fallback - should not happen in normal operation
        raise RuntimeError("No models configured. Please set ANTHROPIC_AUTH_TOKEN, CLAUDE_API_KEY, or DEEPSEEK_API_KEY.")

    def _get_fallback(self, primary_model: str) -> Optional[ModelConfig]:
        """Get fallback model for a given primary model."""
        if self._default_model and self._default_model != primary_model:
            return self._models.get(self._default_model)
        # Find any other available model
        for name, config in self._models.items():
            if name != primary_model:
                return config
        return None

    def get_all_models(self) -> Dict[str, ModelConfig]:
        """Return all configured models."""
        return dict(self._models)

    def has_model(self, name: str) -> bool:
        """Check if a model is configured."""
        return name in self._models


# Global router instance
model_router = ModelRouter()
