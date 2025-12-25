"""
LLM Client

Wraps calls to LLM Gateway with:
- Retry logic
- Timeout handling
- Response parsing
"""
import httpx
from typing import List, Dict, Any, Optional
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import settings

logger = structlog.get_logger()


class LLMClient:
    """
    Client for LLM Gateway
    
    Handles all LLM completions with proper error handling
    """
    
    def __init__(self):
        """Initialize LLM client"""
        self.base_url = settings.LLM_GATEWAY_URL
        self.timeout = settings.LLM_GATEWAY_TIMEOUT
        
        logger.info("llm_client_initialized", gateway_url=self.base_url)
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True
    )
    async def completion(
        self,
        model: str,
        messages: List[Dict[str, str]],
        tenant_id: str,
        run_id: Optional[str] = None,
        step_id: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.7,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Call LLM Gateway for completion
        
        Args:
            model: Model identifier (e.g., gpt-4, claude-3-opus)
            messages: List of messages [{"role": "user", "content": "..."}]
            tenant_id: Tenant for cost attribution
            run_id: Associated run (optional)
            step_id: Associated step (optional)
            max_tokens: Max tokens to generate
            temperature: Sampling temperature
            **kwargs: Additional model parameters
        
        Returns:
            {
                "content": str,
                "usage": {"prompt_tokens": int, "completion_tokens": int, ...},
                "cost_usd": float,
                "provider": str,
                ...
            }
        """
        logger.info(
            "llm_request_started",
            model=model,
            message_count=len(messages),
            tenant_id=tenant_id
        )
        
        request_data = {
            "model": model,
            "messages": messages,
            "tenant_id": tenant_id,
            "temperature": temperature
        }
        
        if run_id:
            request_data["run_id"] = run_id
        if step_id:
            request_data["step_id"] = step_id
        if max_tokens:
            request_data["max_tokens"] = max_tokens
        
        # Add any additional parameters
        request_data.update(kwargs)
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/v1/completions",
                    json=request_data
                )
                
                if response.status_code == 200:
                    data = response.json()
                    
                    logger.info(
                        "llm_request_completed",
                        model=model,
                        provider=data.get("provider"),
                        tokens=data.get("usage", {}).get("total_tokens"),
                        cost_usd=data.get("cost_usd")
                    )
                    
                    return data
                
                elif response.status_code == 402:
                    # Budget exceeded
                    error_data = response.json()
                    logger.error(
                        "llm_request_budget_exceeded",
                        tenant_id=tenant_id,
                        error=error_data
                    )
                    raise BudgetExceededError(error_data.get("message"))
                
                elif response.status_code == 429:
                    # Rate limited
                    logger.warning("llm_request_rate_limited", tenant_id=tenant_id)
                    raise RateLimitError("Rate limit exceeded")
                
                else:
                    # Other error
                    logger.error(
                        "llm_request_failed",
                        status_code=response.status_code,
                        response=response.text
                    )
                    raise LLMError(f"LLM request failed: {response.status_code}")
        
        except httpx.TimeoutException as e:
            logger.error("llm_request_timeout", model=model, timeout=self.timeout)
            raise LLMError(f"Request timeout after {self.timeout}s")
        
        except httpx.RequestError as e:
            logger.error("llm_request_error", model=model, error=str(e))
            raise LLMError(f"Request error: {str(e)}")


class LLMError(Exception):
    """Base LLM error"""
    pass


class BudgetExceededError(LLMError):
    """Budget exceeded"""
    pass


class RateLimitError(LLMError):
    """Rate limit hit"""
    pass