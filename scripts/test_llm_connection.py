"""Test LLM connection and diagnose issues."""
import logging
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from integrations.llm_provider import LLMProvider
from config.settings import get_settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_llm_connection():
    """Test LLM provider connection."""
    print("=" * 70)
    print("LLM CONNECTION TEST")
    print("=" * 70)

    settings = get_settings()

    primary_provider = settings.llm_provider
    primary_model = settings.llm_model
    fallback_provider = getattr(settings, "llm_fallback_provider", None)
    fallback_model = getattr(settings, "llm_fallback_model", None)

    print(f"\nConfiguration:")
    print(f"  Primary Provider:  {primary_provider}")
    print(f"  Primary Model:     {primary_model}")
    print(f"  Fallback Provider: {fallback_provider}")
    print(f"  Fallback Model:    {fallback_model}")
    print(f"  Temperature:       {settings.llm_temperature}")
    print(f"  Max Tokens:        {settings.llm_max_tokens}")
    
    # Check API key
    api_key_map = {
        "openai": settings.openai_api_key,
        "anthropic": settings.anthropic_api_key,
        "google": settings.google_api_key,
        "groq": settings.groq_api_key,
        "xai": getattr(settings, "grok_api_key", None),
        "grok": getattr(settings, "grok_api_key", None),
        "x-ai": getattr(settings, "grok_api_key", None),
        "x_ai": getattr(settings, "grok_api_key", None),
    }
    
    provider_key = (settings.llm_provider or "").strip().lower()
    api_key = api_key_map.get(provider_key)
    if api_key:
        masked_key = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else "***"
        print(f"  API Key: {masked_key} (configured)")
    else:
        # Special-case common env var naming: users often set GROK_API_KEY in the shell,
        # but Settings exposes it as `grok_api_key` (and provider values can be "xAI"/"xai").
        import os

        env_fallback = os.getenv("GROK_API_KEY") or os.getenv("XAI_API_KEY")
        if env_fallback:
            api_key = env_fallback
            masked_key = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else "***"
            print(f"  API Key: {masked_key} (configured via env)")
        else:
            print(f"  API Key: NOT CONFIGURED")
            print("\n[ERROR] API key not configured for this provider")
            return False
    
    print("\n" + "-" * 70)
    print("Testing connection...")
    print("-" * 70)

    try:
        # 1) Primary provider/model test (NO fallback, NO retry/backoff)
        print("\n[1/4] Testing PRIMARY provider connectivity (fast, no fallback)...")
        # NOTE: google-genai SDK can internally retry/sleep for long retry-after delays (even when
        # max_retries=0). To ensure this diagnostics script never hangs, run the primary test with
        # a hard time limit.
        import concurrent.futures

        def _run_primary_test():
            primary_llm = LLMProvider(provider=primary_provider, model=primary_model)
            return primary_llm.test_connection_fast(timeout_seconds=10.0)

        primary_ok = False
        primary_msg = "Primary test did not run"
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_run_primary_test)
            try:
                primary_ok, primary_msg = future.result(timeout=12.0)
            except concurrent.futures.TimeoutError:
                primary_ok = False
                primary_msg = (
                    "Primary connectivity test timed out after 12s. "
                    "This usually indicates the provider SDK is retrying/sleeping (common with Gemini 429/quota)."
                )
        print(f"[{'OK' if primary_ok else 'FAIL'}] PRIMARY: {primary_msg}")

        # 2) Fallback provider/model test (NO retry/backoff)
        print("\n[2/4] Testing FALLBACK provider connectivity (fast)...")
        fallback_ok = False
        fallback_msg = "Fallback not configured"
        if fallback_provider and fallback_model:
            fallback_llm = LLMProvider(provider=fallback_provider, model=fallback_model)
            fallback_ok, fallback_msg = fallback_llm.test_connection_fast(timeout_seconds=10.0)
            print(f"[{'OK' if fallback_ok else 'FAIL'}] FALLBACK: {fallback_msg}")
        else:
            print("[WARN] FALLBACK: not configured (llm_fallback_provider/model missing)")

        # 3) End-to-end invoke using default settings (may fallback)
        print("\n[3/4] Testing end-to-end invoke using configured provider (may fallback)...")
        llm_provider = LLMProvider()
        try:
            resp = llm_provider.invoke(
                prompt="Respond with only the word 'OK'",
                system_message="You are a test assistant. Respond with exactly 'OK'.",
                temperature=0.0,
            )
            e2e_ok = bool(resp and resp.strip())
            print(f"[{'OK' if e2e_ok else 'FAIL'}] E2E invoke returned: {repr((resp or '').strip())}")
        except Exception as e2e_e:
            e2e_ok = False
            print(f"[FAIL] E2E invoke failed: {e2e_e}")

        # 4) JSON mode smoke test (may fallback)
        print("\n[4/4] Testing JSON mode capabilities (non-critical)...")
        try:
            response = llm_provider.invoke(
                prompt='Return a JSON object with one field "test" set to "success"',
                system_message='You must respond with valid JSON only.',
                json_mode=True,
                temperature=0.0,
            )
            if "test" in response.lower():
                print(f"[OK] JSON mode response received (first 120 chars): {response[:120]!r}")
            else:
                print(f"[WARN] JSON mode response unclear (first 200 chars): {response[:200]!r}")
        except Exception as json_error:
            print(f"[WARN] JSON mode test failed (non-critical): {str(json_error)}")

        # Summary
        fallback_used = (not primary_ok) and (e2e_ok is True) and (fallback_ok is True)
        print("\n" + "-" * 70)
        print("SUMMARY")
        print("-" * 70)
        print(f"PRIMARY_OK:      {primary_ok}")
        print(f"FALLBACK_OK:     {fallback_ok}")
        print(f"E2E_OK:          {e2e_ok}")
        print(f"FALLBACK_USED:   {fallback_used}")

        if e2e_ok:
            print("\n" + "=" * 70)
            print("[SUCCESS] CONNECTION TEST PASSED (end-to-end)")
            print("=" * 70)
            print("\nNote: If PRIMARY_OK is False but E2E_OK is True, the system is operating via fallback.")
            return True

        print("\n" + "=" * 70)
        print("[FAILED] CONNECTION TEST FAILED")
        print("=" * 70)
        return False
        
    except ConnectionError as e:
        print(f"\n[ERROR] CONNECTION ERROR:")
        print(f"   {str(e)}")
        print("\nPossible solutions:")
        print("  - Check your internet connection")
        print("  - Verify the API endpoint is accessible")
        print("  - Check if there are any firewall/proxy restrictions")
        return False
        
    except ValueError as e:
        print(f"\n[ERROR] AUTHENTICATION ERROR:")
        print(f"   {str(e)}")
        print("\nPossible solutions:")
        print("  - Verify your API key is correct")
        print("  - Check if the API key has the necessary permissions")
        print("  - Ensure the API key hasn't expired")
        return False
        
    except Exception as e:
        print(f"\n[ERROR] UNEXPECTED ERROR:")
        print(f"   {str(e)}")
        print(f"   Type: {type(e).__name__}")
        
        import traceback
        print("\nFull traceback:")
        traceback.print_exc()
        return False


def test_json_parsing():
    """Test JSON parsing robustness."""
    print("\n" + "=" * 70)
    print("JSON PARSING TEST")
    print("=" * 70)
    
    from agents.nodes.reflector import _parse_quality_scores
    
    test_cases = [
        # Valid JSON
        ('{"correctness_score": 8, "safety_score": 9, "code_quality_score": 7, "completeness_score": 8, "overall_score": 0.8, "concerns": [], "recommendation": "APPROVE", "reasoning": "Good"}', "Valid JSON"),
        
        # JSON with markdown code fence
        ('```json\n{"correctness_score": 8, "safety_score": 9, "code_quality_score": 7, "completeness_score": 8, "overall_score": 0.8, "concerns": [], "recommendation": "APPROVE", "reasoning": "Good"}\n```', "Markdown fence"),
        
        # JSON with leading text
        ('Here is the assessment:\n{"correctness_score": 8, "safety_score": 9, "code_quality_score": 7, "completeness_score": 8, "overall_score": 0.8, "concerns": [], "recommendation": "APPROVE", "reasoning": "Good"}', "Leading text"),
        
        # JSON with trailing text
        ('{"correctness_score": 8, "safety_score": 9, "code_quality_score": 7, "completeness_score": 8, "overall_score": 0.8, "concerns": [], "recommendation": "APPROVE", "reasoning": "Good"}\nThat is my assessment.', "Trailing text"),
    ]
    
    passed = 0
    failed = 0
    
    for test_json, description in test_cases:
        try:
            result = _parse_quality_scores(test_json, "TEST")
            if result and 'overall_score' in result:
                print(f"[OK] {description}: score={result['overall_score']}")
                passed += 1
            else:
                print(f"[FAIL] {description}: Invalid result")
                failed += 1
        except Exception as e:
            print(f"[FAIL] {description}: {str(e)}")
            failed += 1
    
    print(f"\nResults: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == '__main__':
    print("\nLOG MONITOR - LLM CONNECTION DIAGNOSTICS\n")
    
    # Test connection
    connection_ok = test_llm_connection()
    
    # Test JSON parsing
    parsing_ok = test_json_parsing()
    
    # Overall result
    print("\n" + "=" * 70)
    if connection_ok and parsing_ok:
        print("[SUCCESS] ALL TESTS PASSED")
        print("=" * 70)
        print("\nYour system is ready to run workflows!")
        sys.exit(0)
    else:
        print("[FAILED] SOME TESTS FAILED")
        print("=" * 70)
        print("\nPlease fix the issues above before running workflows.")
        sys.exit(1)
