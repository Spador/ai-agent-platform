"""
OpenAI Provider Implementation

Handles all OpenAI models:
- GPT-4 Turbo
- GPT-4
- GPT-3.5 Turbo
"""
import time
from typing import Dict, Any
import structlog
from openai import AsyncOpenAI, OpenAIError, RateLimitError as OpenAIRateLimitError

from .base import BaseLLMProvider, ProviderError, RateLimitError
from ..schemas import LLMRequest, LLMResponse, TokenUsage
from ..config import settings

logger = structlog.get_logger()


class OpenAIProvider(BaseLLMProvider):
    """
    OpenAI API Provider
    
    Uses official OpenAI Python SDK for:
    - Automatic retries
    - Proper error handling
    - Streaming support (future)
    """
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        
        # Initialize OpenAI client
        self.client = AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY or config.get("api_key"),
            base_url=settings.OPENAI_BASE_URL,
            timeout=settings.OPENAI_TIMEOUT,
            max_retries=settings.OPENAI_MAX_RETRIES
        )
        
        # Model mappings
        self.supported_models = {
            "gpt-4": "gpt-4",
            "gpt-4-turbo": "gpt-4-turbo-preview",
            "gpt-4-turbo-preview": "gpt-4-turbo-preview",
            "gpt-3.5-turbo": "gpt-3.5-turbo",
            "gpt-3.5": "gpt-3.5-turbo"
        }
    
    async def completion(self, request: LLMRequest) -> LLMResponse:
        """
        Execute OpenAI completion request
        
        Flow:
        1. Map model name
        2. Convert request to OpenAI format
        3. Call API with retries
        4. Parse response
        5. Calculate cost
        6. Return unified response
        """
        start_time = time.time()
        
        # Map model name
        openai_model = self.map_model_name(request.model)
        
        try:
            # Prepare messages
            messages = [
                {"role": msg.role, "content": msg.content}
                for msg in request.messages
            ]
            
            # Make API call
            logger.info(
                "openai_request_started",
                model=openai_model,
                tenant_id=str(request.tenant_id),
                message_count=len(messages)
            )
            
            response = await self.client.chat.completions.create(
                model=openai_model,
                messages=messages,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                top_p=request.top_p,
                frequency_penalty=request.frequency_penalty,
                presence_penalty=request.presence_penalty,
                stop=request.stop,
                functions=request.functions,
                function_call=request.function_call
            )
            
            # Calculate latency
            latency_ms = int((time.time() - start_time) * 1000)
            
            # Extract content
            choice = response.choices[0]
            content = choice.message.content or ""
            finish_reason = choice.finish_reason
            
            # Token usage
            usage = TokenUsage(
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                total_tokens=response.usage.total_tokens
            )
            
            # Calculate cost
            cost = self.calculate_cost(
                openai_model,
                usage.prompt_tokens,
                usage.completion_tokens
            )
            
            logger.info(
                "openai_request_completed",
                model=openai_model,
                tokens=usage.total_tokens,
                cost_usd=cost,
                latency_ms=latency_ms
            )
            
            # Function call result (if present)
            function_call = None
            if choice.message.function_call:
                function_call = {
                    "name": choice.message.function_call.name,
                    "arguments": choice.message.function_call.arguments
                }
            
            return LLMResponse(
                id=response.id,
                model=openai_model,
                provider="openai",
                content=content,
                finish_reason=finish_reason,
                usage=usage,
                cost_usd=cost,
                latency_ms=latency_ms,
                function_call=function_call,
                attempted_providers=["openai"]
            )
            
        except OpenAIRateLimitError as e:
            logger.warning(
                "openai_rate_limit",
                model=openai_model,
                error=str(e)
            )
            raise RateLimitError(str(request.tenant_id), retry_after=60)
        
        except OpenAIError as e:
            logger.error(
                "openai_error",
                model=openai_model,
                error=str(e),
                error_type=type(e).__name__
            )
            raise ProviderError("openai", str(e), e)
        
        except Exception as e:
            logger.error(
                "openai_unexpected_error",
                model=openai_model,
                error=str(e),
                exc_info=True
            )
            raise ProviderError("openai", f"Unexpected error: {str(e)}", e)
    
    def supports_model(self, model: str) -> bool:
        """Check if model is supported"""
        return model in self.supported_models
    
    def map_model_name(self, model: str) -> str:
        """Map generic name to OpenAI-specific name"""
        mapped = self.supported_models.get(model, model)
        if mapped != model:
            logger.debug(
                "model_name_mapped",
                original=model,
                mapped=mapped
            )
        return mapped
    
    def calculate_cost(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int
    ) -> float:
        """
        Calculate cost based on OpenAI pricing
        
        Prices are stored in settings.TOKEN_COSTS and updated regularly.
        """
        # Get pricing from config
        pricing = settings.TOKEN_COSTS.get(model)
        
        if not pricing:
            logger.warning(
                "model_pricing_not_found",
                model=model,
                fallback="gpt-3.5-turbo"
            )
            pricing = settings.TOKEN_COSTS["gpt-3.5-turbo"]
        
        # Calculate cost per 1K tokens
        prompt_cost = (prompt_tokens / 1000) * pricing["prompt"]
        completion_cost = (completion_tokens / 1000) * pricing["completion"]
        total_cost = prompt_cost + completion_cost
        
        return round(total_cost, 6)
    
    def is_available(self) -> bool:
        """Check if OpenAI is configured"""
        has_key = bool(settings.OPENAI_API_KEY)
        if not has_key:
            logger.warning("openai_api_key_missing")
        return has_key