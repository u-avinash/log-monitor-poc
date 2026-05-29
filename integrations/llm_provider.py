"""Multi-provider LLM wrapper supporting OpenAI, Anthropic, Google, Groq, etc."""
from __future__ import annotations

from typing import Optional, Dict, Any

import logging
import time

from langchain_core.caches import InMemoryCache
from langchain_core.globals import set_llm_cache
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from config.settings import get_settings
from storage.auth_store import get_project_config, list_projects
from utils.retry_handler import retry_with_backoff, RateLimitError

logger = logging.getLogger(__name__)
settings = get_settings()


class LLMProvider:
    """
    Multi-provider LLM wrapper with caching and retry logic.
    Supports: OpenAI, Anthropic, Google, Groq, Together, Ollama, and
    OpenAI-compatible endpoints configured per project.
    """

    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        project_id: Optional[str] = None,
        app_name: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        """
        Initialize LLM provider.

        Args:
            provider: LLM provider name (defaults to settings or project config)
            model: Model name (defaults to settings or project config)
            project_id: Optional project identifier for per-project config
            app_name: Optional application name used to resolve a project
            api_key: Optional runtime API key override
            base_url: Optional runtime endpoint override
            temperature: Optional runtime temperature override
            max_tokens: Optional runtime max token override
        """
        self.project_id = project_id or self._resolve_project_id(app_name)
        self.project_llm_config = self._load_project_llm_config(self.project_id)

        # Normalize provider to avoid case/alias issues
        resolved_provider = (
            provider
            or self.project_llm_config.get("provider")
            or settings.llm_provider
            or ""
        )
        self.provider = resolved_provider.strip().lower()
        self.model = model or self.project_llm_config.get("model") or settings.llm_model
        self.temperature = self._coerce_float(
            temperature if temperature is not None else self.project_llm_config.get("temperature"),
            settings.llm_temperature,
        )
        self.max_tokens = self._coerce_int(
            max_tokens if max_tokens is not None else self.project_llm_config.get("max_tokens"),
            settings.llm_max_tokens,
        )
        self.base_url = (
            base_url
            or self.project_llm_config.get("base_url")
            or (
                settings.ollama_base_url
                if self.provider == "ollama"
                else None
            )
        )
        self.api_key = api_key or self.project_llm_config.get("api_key") or self._get_default_api_key(self.provider)
        self.api_type = (self.project_llm_config.get("api_type") or "").strip().lower()
        self.api_version = self.project_llm_config.get("api_version") or None
        self.deployment_name = self.project_llm_config.get("deployment_name") or None

        if settings.llm_cache_enabled:
            set_llm_cache(InMemoryCache())
            logger.info("LLM caching enabled")

        self.llm = self._initialize_llm()
        logger.info(
            "Initialized LLM: provider=%s model=%s project_id=%s",
            self.provider,
            self.model,
            self.project_id,
        )

    def _resolve_project_id(self, app_name: Optional[str]) -> Optional[str]:
        if not app_name:
            return None

        normalized_app = app_name.strip().lower()
        for project in list_projects():
            project_name = (project.get("name") or "").strip().lower()
            repo_url = (project.get("repo_url") or "").strip().lower()
            if normalized_app == project_name or normalized_app in repo_url:
                return project.get("id")
        return None

    def _load_project_llm_config(self, project_id: Optional[str]) -> dict:
        if not project_id:
            return {}
        try:
            config = get_project_config(project_id) or {}
            return config.get("llm", {}) or {}
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

    def _get_default_api_key(self, provider: str) -> Optional[str]:
        provider_key = (provider or "").strip().lower()
        api_key_map = {
            "openai": settings.openai_api_key,
            "anthropic": settings.anthropic_api_key,
            "google": settings.google_api_key,
            "google_gemini": settings.google_api_key,
            "groq": settings.groq_api_key,
            "grok": settings.grok_api_key,
            "xai": settings.grok_api_key,
            "x-ai": settings.grok_api_key,
            "x_ai": settings.grok_api_key,
            "together": getattr(settings, "together_api_key", None),
            "together_ai": getattr(settings, "together_api_key", None),
        }
        return api_key_map.get(provider_key)

    def _initialize_llm(self) -> BaseChatModel:
        """Initialize the appropriate LLM client based on provider."""
        try:
            if self.provider in {"openai", "custom", "perplexity", "deepseek", "mistral", "cohere", "hugging_face"}:
                from langchain_openai import ChatOpenAI
                kwargs = {
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
                    base_url=self.base_url or settings.ollama_base_url,
                )

            else:
                raise ValueError(f"Unsupported LLM provider: {self.provider}")

        except ImportError as e:
            logger.error("Failed to import %s package: %s", self.provider, e)
            raise
        except Exception as e:
            logger.error("Failed to initialize %s/%s: %s", self.provider, self.model, e)
            raise

    @retry_with_backoff(max_retries=3, base_delay=2.0, exceptions=(RateLimitError, ConnectionError))
    def invoke(
        self,
        prompt: str,
        system_message: Optional[str] = None,
        temperature: Optional[float] = None,
        json_mode: bool = False,
        **kwargs
    ) -> str:
        """Invoke LLM with prompt and optional system message."""
        try:
            messages = []
            if system_message:
                messages.append(SystemMessage(content=system_message))
            messages.append(HumanMessage(content=prompt))

            invoke_kwargs = {}
            if temperature is not None:
                invoke_kwargs["temperature"] = temperature

            if json_mode:
                if self.provider in {"openai", "custom", "perplexity", "deepseek", "mistral", "cohere", "hugging_face"}:
                    invoke_kwargs["response_format"] = {"type": "json_object"}
                elif self.provider == "anthropic":
                    logger.debug("Using prompt-based JSON for Anthropic")

            logger.debug("LLM invoke: provider=%s model=%s json_mode=%s", self.provider, self.model, json_mode)
            start_time = time.time()

            response = self.llm.invoke(messages, **invoke_kwargs)

            elapsed = time.time() - start_time
            logger.debug("LLM response received in %.2fs, length=%s chars", elapsed, len(response.content))

            if not response.content:
                logger.warning("LLM returned empty response")
                raise ValueError("Empty response from LLM")

            return response.content

        except ConnectionResetError as e:
            logger.error("Connection reset by %s: %s", self.provider, str(e))
            raise ConnectionError(f"Connection reset by {self.provider}: {e}")

        except OSError as e:
            if "WinError 10054" in str(e) or "connection" in str(e).lower():
                logger.error("Connection error with %s: %s", self.provider, str(e))
                raise ConnectionError(f"Connection forcibly closed by {self.provider}: {e}")
            raise

        except Exception as e:
            error_msg = str(e)
            logger.error("LLM invocation failed: provider=%s model=%s error=%s", self.provider, self.model, error_msg)

            lower_msg = error_msg.lower()
            is_rate_limited = (
                "rate" in lower_msg
                or "429" in lower_msg
                or "resource_exhausted" in lower_msg
                or "quota" in lower_msg
            )

            if is_rate_limited:
                logger.warning("Rate limit / quota exhaustion detected")
                fallback_provider = getattr(settings, "llm_fallback_provider", "openai")
                fallback_model = getattr(settings, "llm_fallback_model", "gpt-4o-mini")

                if fallback_provider and fallback_provider != self.provider:
                    logger.warning(
                        "Falling back LLM provider from %s/%s to %s/%s",
                        self.provider,
                        self.model,
                        fallback_provider,
                        fallback_model,
                    )
                    try:
                        fallback_llm = LLMProvider(
                            provider=fallback_provider,
                            model=fallback_model,
                            project_id=self.project_id,
                        )
                        return fallback_llm.invoke(
                            prompt=prompt,
                            system_message=system_message,
                            temperature=temperature,
                            json_mode=json_mode,
                            **kwargs,
                        )
                    except Exception as fallback_e:
                        logger.error("Fallback LLM invocation failed: %s", fallback_e)

                raise RateLimitError(f"Rate limit / quota exceeded: {e}")

            if "connection" in lower_msg or "timeout" in lower_msg:
                logger.error("Connection error with %s: %s", self.provider, error_msg)
                raise ConnectionError(f"Failed to connect to {self.provider}: {e}")

            if "auth" in lower_msg or "api key" in lower_msg or "401" in error_msg:
                logger.error("Authentication error with %s: Check your API key", self.provider)
                raise ValueError(f"Authentication failed for {self.provider}: {e}")

            raise

    async def ainvoke(
        self,
        prompt: str,
        system_message: Optional[str] = None,
        **kwargs
    ) -> str:
        """Async invoke LLM with prompt and optional system message."""
        try:
            messages = []
            if system_message:
                messages.append(SystemMessage(content=system_message))
            messages.append(HumanMessage(content=prompt))

            response = await self.llm.ainvoke(messages, **kwargs)
            return response.content

        except Exception as e:
            if "rate" in str(e).lower() or "429" in str(e):
                raise RateLimitError(f"Rate limit exceeded: {e}")
            raise

    def stream(self, prompt: str, system_message: Optional[str] = None):
        """Stream LLM response."""
        messages = []
        if system_message:
            messages.append(SystemMessage(content=system_message))
        messages.append(HumanMessage(content=prompt))

        for chunk in self.llm.stream(messages):
            yield chunk.content

    def test_connection(self) -> tuple[bool, str]:
        """Test LLM connection with a simple query."""
        try:
            logger.info("Testing connection to %s/%s...", self.provider, self.model)
            response = self.invoke(
                prompt="Respond with only the word 'OK'",
                system_message="You are a test assistant. Respond with exactly 'OK'.",
                temperature=0.0,
            )

            if response and len(response.strip()) > 0:
                logger.info("✓ Connection test passed for %s/%s", self.provider, self.model)
                return True, f"Successfully connected to {self.provider}/{self.model}"
            return False, f"Empty response from {self.provider}"

        except ConnectionError as e:
            msg = f"Connection failed: {str(e)}"
            logger.error(msg)
            return False, msg
        except ValueError as e:
            msg = f"Authentication failed: {str(e)}"
            logger.error(msg)
            return False, msg
        except Exception as e:
            msg = f"Connection test failed: {str(e)}"
            logger.error(msg)
            return False, msg

    def test_connection_fast(self, timeout_seconds: float = 10.0) -> tuple[bool, str]:
        """Fast connectivity test without retry/backoff."""
        try:
            logger.info("Fast-testing connection to %s/%s...", self.provider, self.model)

            messages = [
                SystemMessage(content="You are a test assistant. Respond with exactly 'OK'."),
                HumanMessage(content="Respond with only the word 'OK'"),
            ]

            response = self.llm.invoke(messages, timeout=timeout_seconds)

            text = getattr(response, "content", "") or ""
            if text.strip():
                return True, f"Successfully connected to {self.provider}/{self.model}"
            return False, f"Empty response from {self.provider}/{self.model}"
        except Exception as e:
            return False, f"Fast connection test failed for {self.provider}/{self.model}: {e}"

    def get_model_info(self) -> Dict[str, Any]:
        """Get information about the current model."""
        return {
            "provider": self.provider,
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "base_url": self.base_url,
            "project_id": self.project_id,
            "cache_enabled": settings.llm_cache_enabled,
        }
