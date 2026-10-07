"""Pre-flight validation of LLM capabilities before deal workflows start."""
import asyncio
import time
import structlog
from app.core.llm.llm_gateway import get_llm_gateway
from app.core.llm.model_registry import get_capabilities

logger = structlog.get_logger()

async def probe_llm_health(
    provider: str,
    model: str,
    timeout_sec: int = 10,
) -> dict:
    """
    Test if an LLM endpoint is reachable and can handle a basic request.
    Returns: {"healthy": bool, "latency_ms": float, "error": str | None, "capabilities": dict}
    """
    gateway = get_llm_gateway()

    try:
        start = time.time()

        # Simple probe prompt
        response = await asyncio.wait_for(
            gateway.call(
                provider=provider,
                model=model,
                prompt="Respond with exactly one word: OK",
                temperature=0.0,
                max_tokens=5,
            ),
            timeout=timeout_sec,
        )

        latency_ms = (time.time() - start) * 1000
        caps = get_capabilities(model, provider)

        return {
            "healthy": True,
            "latency_ms": round(latency_ms, 2),
            "error": None,
            "capabilities": caps.__dict__ if hasattr(caps, "__dict__") else str(caps),
        }
    except asyncio.TimeoutError:
        return {"healthy": False, "latency_ms": None, "error": f"Timeout after {timeout_sec}s"}
    except Exception as e:
        logger.warning("llm_probe_failed", provider=provider, model=model, error=str(e))
        return {"healthy": False, "latency_ms": None, "error": str(e)}

async def probe_fallback_chain() -> dict:
    """Test the primary and common fallback LLM providers."""
    # Define probes based on common configuration
    probes = [
        ("gemini", "gemini-3.8-flash"),
        ("openai", "gpt-4o-mini"),
        ("mistral", "mistral-small"),
        ("ollama", "llama3.1:8b"),
    ]

    results = {}
    for provider, model in probes:
        results[f"{provider}:{model}"] = await probe_llm_health(provider, model)

    # Find first healthy provider
    healthy = [k for k, v in results.items() if v["healthy"]]
    if healthy:
        logger.info("llm_fallback_chain_health", healthy_count=len(healthy), first_healthy=healthy[0])
    else:
        logger.error("llm_fallback_chain_all_down", results=results)

    return results
