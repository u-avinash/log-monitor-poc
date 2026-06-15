"""Multi-provider LLM wrapper — credentials loaded exclusively from project DB config."""
from __future__ import annotations

import json
import re
import logging
import time
from typing import Optional, Dict, Any

from langchain_core.caches import InMemoryCache
from langchain_core.globals import set_llm_cache
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from config.settings import get_settings
from utils.retry_handler import retry_with_backoff, RateLimitError

logger = logging.getLogger(__name__)
settings = get_settings()


def _normalize_response_content(content: Any) -> str:
    """Normalize provider-specific response content to a plain string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text_value = item.get("text")
                if isinstance(text_value, str):
                    parts.append(text_value)
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        return "".join(parts)
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)
    return str(content)


class LLMProvider:
    """
    Multi-provider LLM wrapper with caching and retry logic.

    All credentials and model settings are loaded from the per-project DB config
    (stored encrypted).  No fallback to environment variables or settings.py.

    Supported providers: openai, azure_openai, anthropic, google_gemini,
    groq, grok/xai, together, ollama, mistral, cohere, deepseek, perplexity,
    hugging_face, bedrock, custom.
    """

    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        project_id: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        """
        Initialise LLM provider.

        At least one of `project_id` (to load config from DB) or explicit
        `provider` + `api_key` + `model` must be supplied.  If `project_id`
        is given, DB values are used as the primary source; explicit kwargs
        override them.
        """
        self.project_id = project_id
        project_cfg = self._load_project_llm_config(project_id)

        resolved_provider = (
            provider
            or project_cfg.get("provider")
            or ""
        ).strip().lower()

        if not resolved_provider:
            raise ValueError(
                f"LLM provider is not configured"
                + (f" for project '{project_id}'" if project_id else "")
                + ". Configure it via Team Admin → Project Configuration → LLM."
            )

        self.provider = resolved_provider
        self.model = model or project_cfg.get("model") or ""
        if not self.model:
            raise ValueError(
                f"LLM model is not configured for provider '{self.provider}'"
                + (f" in project '{project_id}'" if project_id else "")
                + ". Configure it via Team Admin → Project Configuration → LLM."
            )

        self.temperature = self._coerce_float(
            temperature if temperature is not None else project_cfg.get("temperature"),
            0.2,
        )
        self.max_tokens = self._coerce_int(
            max_tokens if max_tokens is not None else project_cfg.get("max_tokens"),
            4096,
        )
        self.base_url = (
            base_url
            or project_cfg.get("base_url")
            or (None)
        )
        self.api_key = api_key or project_cfg.get("api_key") or None
        self.api_version = project_cfg.get("api_version") or None
        self.deployment_name = project_cfg.get("deployment_name") or None

        # Validate that credential is present for providers that require it
        self._validate_configuration()

        if settings.llm_cache_enabled:
            set_llm_cache(InMemoryCache())

        self.llm = self._initialize_llm()
        logger.info(
            "Initialized LLM: provider=%s model=%s project_id=%s",
            self.provider,
            self.model,
            self.project_id,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _load_project_llm_config(self, project_id: Optional[str]) -> dict:
        if not project_id:
            return {}
        try:
            from storage.auth_store import get_project_config
            config = get_project_config(project_id) or {}
            return config.get("llm") or {}
        except Exception as exc:
            logger.warning("Failed to load project LLM config for %s: %s", project_id, exc)
            return {}

    def _coerce_float(self, value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    def _coerce_int(self, value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    def _provider_requires_api_key(self, provider: str) -> bool:
        return (provider or "").strip().lower() not in {"ollama"}

    def _validate_configuration(self) -> None:
        if self._provider_requires_api_key(self.provider) and not self.api_key:
            raise ValueError(
                f"API key is not configured for LLM provider '{self.provider}'"
                + (f" in project '{self.project_id}'" if self.project_id else "")
                + ". Configure it via Team Admin → Project Configuration → LLM."
            )
        if self.provider == "azure_openai" and not self.base_url:
            raise ValueError(
                "Azure OpenAI requires a base_url (azure_endpoint). "
                "Configure it via Team Admin → Project Configuration → LLM."
            )

    def _initialize_llm(self) -> BaseChatModel:
        """Initialise the appropriate LLM client based on provider."""
        try:
            if self.provider in {
                "openai", "custom", "perplexity", "deepseek",
                "mistral", "cohere", "hugging_face",
            }:
                from langchain_openai import ChatOpenAI
                kwargs: dict = {
                    "model": self.model,
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens,
                    "api_key": self.api_key,
                    "timeout": 60.0,
                    "max_retries": 2,
                }
                if self.base_url:
                    kwargs["base_url"] = self.base_url
                return ChatOpenAI(**kwargs)

            elif self.provider == "azure_openai":
                from langchain_openai import AzureChatOpenAI
                return AzureChatOpenAI(
                    model=self.model,
                    azure_deployment=self.deployment_name or self.model,
                    api_version=self.api_version or "2024-10-21",
                    azure_endpoint=self.base_url,
                    api_key=self.api_key,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    timeout=60.0,
                    max_retries=2,
                )

            elif self.provider == "anthropic":
                from langchain_anthropic import ChatAnthropic
                return ChatAnthropic(
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    api_key=self.api_key,
                    timeout=60.0,
                    max_retries=2,
                )

            elif self.provider in {"google", "google_gemini"}:
                from langchain_google_genai import ChatGoogleGenerativeAI
                return ChatGoogleGenerativeAI(
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    google_api_key=self.api_key,
                    max_retries=1,
                    timeout=60,
                )

            elif self.provider == "groq":
                from langchain_groq import ChatGroq
                return ChatGroq(
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    api_key=self.api_key,
                )

            elif self.provider in {"xai", "grok", "x-ai", "x_ai"}:
                from langchain_openai import ChatOpenAI
                return ChatOpenAI(
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    api_key=self.api_key,
                    base_url=self.base_url or "https://api.x.ai/v1",
                    timeout=60.0,
                    max_retries=2,
                )

            elif self.provider in {"together", "together_ai"}:
                from langchain_together import ChatTogether
                return ChatTogether(
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    api_key=self.api_key,
                )

            elif self.provider == "ollama":
                from langchain_ollama import ChatOllama
                return ChatOllama(
                    model=self.model,
                    temperature=self.temperature,
                    base_url=self.base_url or "http://localhost:11434",
                )

            else:
                raise ValueError(f"Unsupported LLM provider: '{self.provider}'")

        except ImportError as exc:
            logger.error("Missing package for provider '%s': %s", self.provider, exc)
            raise
        except Exception as exc:
            logger.error("Failed to initialise %s/%s: %s", self.provider, self.model, exc)
            raise

    # ── Public API ────────────────────────────────────────────────────────────

    @retry_with_backoff(max_retries=3, base_delay=2.0, exceptions=(RateLimitError, ConnectionError))
    def invoke(
        self,
        prompt: str,
        system_message: Optional[str] = None,
        temperature: Optional[float] = None,
        json_mode: bool = False,
        **kwargs,
    ) -> str:
        """Invoke the LLM synchronously and return the response string."""
        try:
            messages = []
            if system_message:
                messages.append(SystemMessage(content=system_message))
            messages.append(HumanMessage(content=prompt))

            invoke_kwargs: dict = {}
            if temperature is not None:
                invoke_kwargs["temperature"] = temperature

            if json_mode and self.provider in {
                "openai", "custom", "perplexity", "deepseek",
                "mistral", "cohere", "hugging_face",
            }:
                invoke_kwargs["response_format"] = {"type": "json_object"}

            logger.debug(
                "LLM invoke: provider=%s model=%s json_mode=%s",
                self.provider, self.model, json_mode,
            )
            start = time.time()
            response = self.llm.invoke(messages, **invoke_kwargs)
            response_text = _normalize_response_content(getattr(response, "content", response))
            elapsed = time.time() - start
            logger.debug("LLM response in %.2fs, %d chars", elapsed, len(response_text))

            if not response_text:
                raise ValueError("Empty response from LLM")

            return response_text

        except ConnectionResetError as exc:
            raise ConnectionError(f"Connection reset by {self.provider}: {exc}")

        except OSError as exc:
            if "WinError 10054" in str(exc) or "connection" in str(exc).lower():
                raise ConnectionError(f"Connection forcibly closed by {self.provider}: {exc}")
            raise

        except Exception as exc:
            error_msg = str(exc)
            lower_msg = error_msg.lower()

            is_rate_limited = any(
                token in lower_msg
                for token in (
                    "rate", "429", "503", "resource_exhausted",
                    "quota", "unavailable", "high demand", "overloaded",
                )
            )
            if is_rate_limited:
                logger.warning("Rate limit / quota exhaustion on %s/%s", self.provider, self.model)
                raise RateLimitError(f"Rate limit / transient provider overload: {exc}")

            if "connection" in lower_msg or "timeout" in lower_msg:
                raise ConnectionError(f"Failed to connect to {self.provider}: {exc}")

            if "auth" in lower_msg or "api key" in lower_msg or "401" in error_msg:
                raise ValueError(f"Authentication failed for {self.provider}: {exc}")

            raise

    async def ainvoke(
        self,
        prompt: str,
        system_message: Optional[str] = None,
        **kwargs,
    ) -> str:
        """Async invoke."""
        messages = []
        if system_message:
            messages.append(SystemMessage(content=system_message))
        messages.append(HumanMessage(content=prompt))
        response = await self.llm.ainvoke(messages, **kwargs)
        return _normalize_response_content(getattr(response, "content", response))

    def stream(self, prompt: str, system_message: Optional[str] = None):
        """Stream LLM response chunks."""
        messages = []
        if system_message:
            messages.append(SystemMessage(content=system_message))
        messages.append(HumanMessage(content=prompt))
        for chunk in self.llm.stream(messages):
            yield chunk.content

    def test_connection(self) -> tuple[bool, str]:
        """Test LLM connection with a simple query."""
        try:
            response = self.invoke(
                prompt="Respond with only the word 'OK'",
                system_message="You are a test assistant. Respond with exactly 'OK'.",
                temperature=0.0,
            )
            if response and response.strip():
                return True, f"Successfully connected to {self.provider}/{self.model}"
            return False, f"Empty response from {self.provider}"
        except Exception as exc:
            return False, f"Connection test failed: {exc}"

    def test_connection_fast(self, timeout_seconds: float = 10.0) -> tuple[bool, str]:
        """Fast connectivity test without retry/backoff."""
        try:
            messages = [
                SystemMessage(content="You are a test assistant. Respond with exactly 'OK'."),
                HumanMessage(content="Respond with only the word 'OK'"),
            ]
            response = self.llm.invoke(messages, timeout=timeout_seconds)
            text = _normalize_response_content(getattr(response, "content", response))
            if text.strip():
                return True, f"Successfully connected to {self.provider}/{self.model}"
            return False, f"Empty response from {self.provider}/{self.model}"
        except Exception as exc:
            return False, f"Fast connection test failed for {self.provider}/{self.model}: {exc}"

    def get_model_info(self) -> Dict[str, Any]:
        """Return metadata about the current model configuration."""
        return {
            "provider": self.provider,
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "base_url": self.base_url,
            "project_id": self.project_id,
            "cache_enabled": settings.llm_cache_enabled,
        }
