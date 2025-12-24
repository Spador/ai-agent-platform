"""
Anthropic Provider Implementation

Handles Claude models:
- Claude 3 Opus (most capable)
- Claude 3 Sonnet (balanced)
- Claude 3 Haiku (fastest)
"""
import time
from typing import Dict, Any
import structlog
from anthropic import AsyncAnthropic, APIError, RateLimitError as AnthropicRateLimitError

from .base import BaseLLMProvider, ProviderError, RateLimitError
from ..schemas import LLMRequest, LLMResponse, TokenUsage
from ..config import settings

logger = structlog.get_logger()


class AnthropicProvider(BaseLLMProvider):
    """
    Anthropic Claude Provider
    
    Key differences from OpenAI:
    - Different message format (system message separate)
    - Different token counting
    - Different response structure
    """
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        
        # Initialize Anthropic client
        self.client = AsyncAnthropic(
            api_key=settings.ANTHROPIC_API_KEY or config.get("api_key"),
            timeout=settings.ANTHROPIC_TIMEOUT,
            max_retries=settings.ANTHROPIC_MAX_RETRIES
        )
        
        # Model mappings
        self.supported_models = {
            "claude-3-opus": "claude-3-opus-20240229",
            "claude-3-sonnet": "claude-3-sonnet-20240229",
            "claude-3-haiku": "claude-3-haiku-20240307",
            "claude-opus": "claude-3-opus-20240229",
            "claude-sonnet": "claude-3-sonnet-20240229",
            "claude-haiku": "claude-3-haiku-20240307"
        }
    
    async def completion(self, request: LLMRequest) -> LLMResponse:
        """
        Execute Anthropic completion request
        
        Anthropic differences:
        - System message goes in separate 'system' parameter
        - No function calling (yet)
        - Different max_tokens handling
        """
        start_time = time.time()
        
        # Map model name
        anthropic_model = self.map_model_name(request.model)
        
        try:
            # Extract system message
            system_message = None
            messages = []
            
            for msg in request.messages:
                if msg.role == "system":
                    system_message = msg.content
                else:
                    messages.append({
                        "role": msg.role,
                        "content": msg.content
                    })
            
            # Anthropic requires max_tokens
            max_tokens = request.max_tokens or 4096
            
            logger.info(
                "anthropic_request_started",
                model=anthropic_model,
                tenant_id=str(request.tenant_id),
                message_count=len(messages)
            )
            
            # Make API call
            response = await self.client.messages.create(
                model=anthropic_model,
                messages=messages,
                system=system_message,
                max_tokens=max_tokens,
                temperature=request.temperature,
                top_p=request.top_p,
                stop_sequences=request.stop
            )
            
            # Calculate latency
            latency_ms = int((time.time() - start_time) * 1000)
            
            # Extract content
            content = ""
            if response.content:
                # Anthropic returns list of content blocks
                content = "".join([
                    block.text for block in response.content
                    if hasattr(block, 'text')
                ])
            
            finish_reason = response.stop_reason or "stop"
            
            # Token usage
            usage = TokenUsage(
                prompt_tokens=response.usage.input_tokens,
                completion_tokens=response.usage.output_tokens,
                total_tokens=response.usage.input_tokens + response.usage.output_tokens
            )
            
            # Calculate cost
            cost = self.calculate_cost(
                anthropic_model,
                usage.prompt_tokens,
                usage.completion_tokens
            )
            
            logger.info(
                "anthropic_request_completed",
                model=anthropic_model,
                tokens=usage.total_tokens,
                cost_usd=cost,
                latency_ms=latency_ms
            )
            
            return LLMResponse(
                id=response.id,
                model=anthropic_model,
                provider="anthropic",
                content=content,
                finish_reason=finish_reason,
                usage=usage,
                cost_usd=cost,
                latency_ms=latency_ms,
                attempted_providers=["anthropic"]
            )
            
        except AnthropicRateLimitError as e:
            logger.warning(
                "anthropic_rate_limit",
                model=anthropic_model,
                error=str(e)
            )
            raise RateLimitError(str(request.tenant_id), retry_after=60)
        
        except APIError as e:
            logger.error(
                "anthropic_error",
                model=anthropic_model,
                error=str(e),
                status_code=e.status_code if hasattr(e, 'status_code') else None
            )
            raise ProviderError("anthropic", str(e), e)
        
        except Exception as e:
            logger.error(
                "anthropic_unexpected_error",
                model=anthropic_model,
                error=str(e),
                exc_info=True
            )
            raise ProviderError("anthropic", f"Unexpected error: {str(e)}", e)
    
    def supports_model(self, model: str) -> bool:
        """Check if model is supported"""
        return model in self.supported_models
    
    def map_model_name(self, model: str) -> str:
        """Map generic name to Anthropic-specific name"""
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
        """Calculate cost based on Anthropic pricing"""
        # Try to find exact model match first
        pricing = settings.TOKEN_COSTS.get(model)
        
        # Fallback to generic model names
        if not pricing:
            if "opus" in model:
                pricing = settings.TOKEN_COSTS.get("claude-3-opus")
            elif "sonnet" in model:
                pricing = settings.TOKEN_COSTS.get("claude-3-sonnet")
            elif "haiku" in model:
                pricing = settings.TOKEN_COSTS.get("claude-3-haiku")
        
        if not pricing:
            logger.warning(
                "model_pricing_not_found",
                model=model,
                fallback="claude-3-haiku"
            )
            pricing = settings.TOKEN_COSTS["claude-3-haiku"]
        
        # Calculate cost
        prompt_cost = (prompt_tokens / 1000) * pricing["prompt"]
        completion_cost = (completion_tokens / 1000) * pricing["completion"]
        total_cost = prompt_cost + completion_cost
        
        return round(total_cost, 6)
    
    def is_available(self) -> bool:
        """Check if Anthropic is configured"""
        has_key = bool(settings.ANTHROPIC_API_KEY)
        if not has_key:
            logger.warning("anthropic_api_key_missing")
        return has_key