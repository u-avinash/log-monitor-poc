"""Multi-provider LLM wrapper supporting OpenAI, Anthropic, Google, Groq, etc."""
from typing import Optional, Dict, Any
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.caches import InMemoryCache
from langchain_core.globals import set_llm_cache
from config.settings import get_settings
from utils.retry_handler import retry_with_backoff, RateLimitError
import logging
import time

logger = logging.getLogger(__name__)
settings = get_settings()


class LLMProvider:
    """
    Multi-provider LLM wrapper with caching and retry logic.
    Supports: OpenAI, Anthropic, Google, Groq, Together, Ollama.
    """
    
    def __init__(self, provider: Optional[str] = None, model: Optional[str] = None):
        """
        Initialize LLM provider.
        
        Args:
            provider: LLM provider name (defaults to settings)
            model: Model name (defaults to settings)
        """
        # Normalize provider to avoid case/alias issues (e.g. "xAI" vs "xai")
        self.provider = (provider or settings.llm_provider or "").strip().lower()
        self.model = model or settings.llm_model
        self.temperature = settings.llm_temperature
        self.max_tokens = settings.llm_max_tokens
        
        # Initialize cache if enabled
        if settings.llm_cache_enabled:
            set_llm_cache(InMemoryCache())
            logger.info("LLM caching enabled")
        
        self.llm = self._initialize_llm()
        logger.info(f"Initialized LLM: {self.provider}/{self.model}")
    
    def _initialize_llm(self) -> BaseChatModel:
        """Initialize the appropriate LLM client based on provider."""
        try:
            if self.provider == "openai":
                from langchain_openai import ChatOpenAI
                return ChatOpenAI(
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    api_key=settings.openai_api_key,
                    timeout=60.0,  # 60 second timeout
                    max_retries=2
                )
            
            elif self.provider == "anthropic":
                from langchain_anthropic import ChatAnthropic
                return ChatAnthropic(
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    api_key=settings.anthropic_api_key,
                    timeout=60.0,
                    max_retries=2
                )
            
            elif self.provider == "google":
                from langchain_google_genai import ChatGoogleGenerativeAI
                # Increased timeout to 60s for large prompts (e.g., quality reflection with full code)
                # Keep retries at 1 to balance responsiveness with reliability
                return ChatGoogleGenerativeAI(
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    google_api_key=settings.google_api_key,
                    max_retries=1,
                    timeout=60,
                )
            
            elif self.provider in {"groq"}:
                from langchain_groq import ChatGroq
                return ChatGroq(
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    api_key=settings.groq_api_key
                )

            elif self.provider in {"xai", "grok", "x-ai", "x_ai"}:
                # xAI exposes an OpenAI-compatible API.
                from langchain_openai import ChatOpenAI
                return ChatOpenAI(
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    api_key=settings.grok_api_key,
                    base_url="https://api.x.ai/v1",
                    timeout=60.0,
                    max_retries=2,
                )
            
            elif self.provider == "together":
                from langchain_together import ChatTogether
                return ChatTogether(
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    api_key=settings.together_api_key
                )
            
            elif self.provider == "ollama":
                from langchain_ollama import ChatOllama
                return ChatOllama(
                    model=self.model,
                    temperature=self.temperature,
                    base_url=settings.ollama_base_url
                )
            
            else:
                raise ValueError(f"Unsupported LLM provider: {self.provider}")
        
        except ImportError as e:
            logger.error(f"Failed to import {self.provider} package: {e}")
            logger.info(f"Install with: pip install langchain-{self.provider}")
            raise
        except Exception as e:
            logger.error(f"Failed to initialize {self.provider}/{self.model}: {e}")
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
        """
        Invoke LLM with prompt and optional system message.
        
        Args:
            prompt: User prompt
            system_message: Optional system message
            temperature: Optional temperature override
            json_mode: If True, request JSON output (provider-specific)
            **kwargs: Additional parameters to pass to LLM
            
        Returns:
            LLM response text
        """
        try:
            messages = []
            if system_message:
                messages.append(SystemMessage(content=system_message))
            messages.append(HumanMessage(content=prompt))
            
            # Build invoke kwargs
            invoke_kwargs = {}
            if temperature is not None:
                invoke_kwargs['temperature'] = temperature
            
            # Enable JSON mode for supported providers
            if json_mode:
                if self.provider == "openai":
                    invoke_kwargs['response_format'] = {"type": "json_object"}
                    logger.debug("Enabled OpenAI JSON mode")
                elif self.provider == "anthropic":
                    # Anthropic doesn't have a JSON mode flag, rely on prompt engineering
                    logger.debug("Using prompt-based JSON for Anthropic")
            
            # Log the invocation
            logger.debug(f"LLM invoke: provider={self.provider}, model={self.model}, json_mode={json_mode}")
            start_time = time.time()
            
            response = self.llm.invoke(messages, **invoke_kwargs)
            
            elapsed = time.time() - start_time
            logger.debug(f"LLM response received in {elapsed:.2f}s, length={len(response.content)} chars")
            
            # Validate response is not empty
            if not response.content:
                logger.warning("LLM returned empty response")
                raise ValueError("Empty response from LLM")
            
            return response.content
        
        except ConnectionResetError as e:
            logger.error(f"Connection reset by {self.provider}: {str(e)}")
            raise ConnectionError(f"Connection reset by {self.provider}: {e}")
        
        except OSError as e:
            # Catch Windows socket errors (WinError 10054, etc.)
            if "WinError 10054" in str(e) or "connection" in str(e).lower():
                logger.error(f"Connection error with {self.provider}: {str(e)}")
                raise ConnectionError(f"Connection forcibly closed by {self.provider}: {e}")
            raise
        
        except Exception as e:
            error_msg = str(e)
            logger.error(f"LLM invocation failed: provider={self.provider}, model={self.model}, error={error_msg}")

            lower_msg = error_msg.lower()

            # Treat Google "RESOURCE_EXHAUSTED" as rate limit / quota exhaustion too
            is_rate_limited = (
                "rate" in lower_msg
                or "429" in lower_msg
                or "resource_exhausted" in lower_msg
                or "quota" in lower_msg
            )

            if is_rate_limited:
                logger.warning("Rate limit / quota exhaustion detected")

                # If we have a fallback provider configured, try it immediately to avoid workflow failures.
                fallback_provider = getattr(settings, "llm_fallback_provider", "openai")
                fallback_model = getattr(settings, "llm_fallback_model", "gpt-4o-mini")

                if fallback_provider and fallback_provider != self.provider:
                    logger.warning(
                        f"Falling back LLM provider from {self.provider}/{self.model} "
                        f"to {fallback_provider}/{fallback_model}"
                    )
                    try:
                        fallback_llm = LLMProvider(provider=fallback_provider, model=fallback_model)
                        # Note: preserve json_mode intent (for OpenAI it enables response_format)
                        return fallback_llm.invoke(
                            prompt=prompt,
                            system_message=system_message,
                            temperature=temperature,
                            json_mode=json_mode,
                            **kwargs,
                        )
                    except Exception as fallback_e:
                        logger.error(f"Fallback LLM invocation failed: {fallback_e}")

                # No fallback available or fallback failed: propagate as RateLimitError (triggers retry/backoff)
                raise RateLimitError(f"Rate limit / quota exceeded: {e}")

            # Check for connection errors
            if "connection" in lower_msg or "timeout" in lower_msg:
                logger.error(f"Connection error with {self.provider}: {error_msg}")
                raise ConnectionError(f"Failed to connect to {self.provider}: {e}")

            # Check for authentication errors
            if "auth" in lower_msg or "api key" in lower_msg or "401" in error_msg:
                logger.error(f"Authentication error with {self.provider}: Check your API key")
                raise ValueError(f"Authentication failed for {self.provider}: {e}")

            raise
    
    async def ainvoke(
        self,
        prompt: str,
        system_message: Optional[str] = None,
        **kwargs
    ) -> str:
        """
        Async invoke LLM with prompt and optional system message.
        
        Args:
            prompt: User prompt
            system_message: Optional system message
            **kwargs: Additional parameters to pass to LLM
            
        Returns:
            LLM response text
        """
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
        """
        Stream LLM response.
        
        Args:
            prompt: User prompt
            system_message: Optional system message
            
        Yields:
            Response chunks
        """
        messages = []
        if system_message:
            messages.append(SystemMessage(content=system_message))
        messages.append(HumanMessage(content=prompt))
        
        for chunk in self.llm.stream(messages):
            yield chunk.content
    
    def test_connection(self) -> tuple[bool, str]:
        """
        Test LLM connection with a simple query.

        Note: This uses `self.invoke()` and therefore includes retry/backoff behavior.
        For a fast non-retrying test (useful for rate-limited providers), use
        `test_connection_fast()`.
        
        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            logger.info(f"Testing connection to {self.provider}/{self.model}...")
            response = self.invoke(
                prompt="Respond with only the word 'OK'",
                system_message="You are a test assistant. Respond with exactly 'OK'.",
                temperature=0.0
            )
            
            if response and len(response.strip()) > 0:
                logger.info(f"✓ Connection test passed for {self.provider}/{self.model}")
                return True, f"Successfully connected to {self.provider}/{self.model}"
            else:
                logger.warning(f"Connection test returned empty response from {self.provider}")
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
        """
        Fast connectivity test without retry/backoff.

        This bypasses `self.invoke()` (which is wrapped with retry logic) and calls the
        underlying LangChain model directly. Useful when a provider returns long
        retry-after delays (e.g., Google RESOURCE_EXHAUSTED).
        """
        try:
            logger.info(f"Fast-testing connection to {self.provider}/{self.model}...")

            messages = [
                SystemMessage(content="You are a test assistant. Respond with exactly 'OK'."),
                HumanMessage(content="Respond with only the word 'OK'"),
            ]

            # Some providers accept a `timeout` kwarg at invoke-time; if not, it will be ignored.
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
            "cache_enabled": settings.llm_cache_enabled
        }
