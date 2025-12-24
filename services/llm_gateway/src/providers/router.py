"""
Provider Router with Circuit Breaker

Handles:
- Provider selection based on model support
- Automatic failover on provider failure
- Circuit breaker pattern to avoid cascading failures
- Retry logic with exponential backoff
"""
from typing import List, Optional
import structlog
from pybreaker import CircuitBreaker, CircuitBreakerError
import asyncio

from .base import BaseLLMProvider, ProviderError, ModelNotSupportedError
from .openai_provider import OpenAIProvider
from .anthropic_provider import AnthropicProvider
from ..schemas import LLMRequest, LLMResponse
from ..config import settings

logger = structlog.get_logger()


class ProviderRouter:
    """
    Intelligent routing between LLM providers
    
    Features:
    - Provider selection based on model
    - Circuit breakers per provider
    - Automatic failover
    - Health tracking
    """
    
    def __init__(self):
        """Initialize router with all available providers"""
        self.providers: List[BaseLLMProvider] = []
        self.circuit_breakers = {}
        
        # Initialize providers
        self._init_providers()
        
        # Initialize circuit breakers
        if settings.CIRCUIT_BREAKER_ENABLED:
            self._init_circuit_breakers()
        
        logger.info(
            "provider_router_initialized",
            provider_count=len(self.providers),
            circuit_breaker_enabled=settings.CIRCUIT_BREAKER_ENABLED
        )
    
    def _init_providers(self):
        """Initialize all configured providers"""
        # OpenAI
        if settings.OPENAI_API_KEY:
            openai = OpenAIProvider({"api_key": settings.OPENAI_API_KEY})
            if openai.is_available():
                self.providers.append(openai)
                logger.info("openai_provider_enabled")
            else:
                logger.warning("openai_provider_unavailable")
        
        # Anthropic
        if settings.ANTHROPIC_API_KEY:
            anthropic = AnthropicProvider({"api_key": settings.ANTHROPIC_API_KEY})
            if anthropic.is_available():
                self.providers.append(anthropic)
                logger.info("anthropic_provider_enabled")
            else:
                logger.warning("anthropic_provider_unavailable")
        
        # Local model (if enabled)
        if settings.LOCAL_MODEL_ENABLED:
            # TODO: Implement local provider
            logger.info("local_provider_not_implemented")
        
        if not self.providers:
            logger.error("no_providers_available")
            raise RuntimeError("No LLM providers configured")
    
    def _init_circuit_breakers(self):
        """Initialize circuit breakers for each provider"""
        for provider in self.providers:
            breaker = CircuitBreaker(
                fail_max=settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD,
                timeout_duration=settings.CIRCUIT_BREAKER_TIMEOUT,
                expected_exception=ProviderError,
                name=f"{provider.get_name()}_breaker"
            )
            self.circuit_breakers[provider.get_name()] = breaker
            
            logger.info(
                "circuit_breaker_created",
                provider=provider.get_name(),
                failure_threshold=settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD,
                timeout_seconds=settings.CIRCUIT_BREAKER_TIMEOUT
            )
    
    async def route(self, request: LLMRequest) -> LLMResponse:
        """
        Route request to appropriate provider with failover
        
        Flow:
        1. Check if specific provider requested
        2. Find providers that support the model
        3. Try each provider in priority order
        4. Handle circuit breaker states
        5. Return response or raise error
        
        Args:
            request: Unified LLM request
            
        Returns:
            LLM response from successful provider
            
        Raises:
            ModelNotSupportedError: No provider supports model
            ProviderError: All providers failed
        """
        # Check for provider preference
        if request.preferred_provider:
            provider = self._get_provider_by_name(request.preferred_provider)
            if provider and provider.supports_model(request.model):
                return await self._execute_with_circuit_breaker(provider, request)
            else:
                logger.warning(
                    "preferred_provider_unavailable",
                    provider=request.preferred_provider,
                    falling_back=True
                )
        
        # Find capable providers
        capable_providers = self._get_providers_for_model(request.model)
        
        if not capable_providers:
            raise ModelNotSupportedError(
                "any",
                request.model
            )
        
        # Try each provider
        attempted_providers = []
        last_error = None
        
        for provider in capable_providers:
            attempted_providers.append(provider.get_name())
            
            try:
                logger.info(
                    "attempting_provider",
                    provider=provider.get_name(),
                    model=request.model,
                    is_fallback=len(attempted_providers) > 1
                )
                
                response = await self._execute_with_circuit_breaker(provider, request)
                
                # Mark as fallback if not first provider
                if len(attempted_providers) > 1:
                    response.is_fallback = True
                
                response.attempted_providers = attempted_providers
                
                logger.info(
                    "provider_succeeded",
                    provider=provider.get_name(),
                    attempts=len(attempted_providers)
                )
                
                return response
                
            except CircuitBreakerError as e:
                logger.warning(
                    "circuit_breaker_open",
                    provider=provider.get_name(),
                    trying_next=True
                )
                last_error = e
                continue
                
            except ProviderError as e:
                logger.warning(
                    "provider_failed",
                    provider=provider.get_name(),
                    error=str(e),
                    trying_next=len(attempted_providers) < len(capable_providers)
                )
                last_error = e
                
                # If this was the last provider, raise
                if len(attempted_providers) >= len(capable_providers):
                    break
                
                # Otherwise, continue to next provider
                continue
        
        # All providers failed
        logger.error(
            "all_providers_failed",
            model=request.model,
            attempted=attempted_providers
        )
        
        raise ProviderError(
            "all",
            f"All providers failed for model {request.model}. "
            f"Attempted: {', '.join(attempted_providers)}",
            last_error
        )
    
    async def _execute_with_circuit_breaker(
        self,
        provider: BaseLLMProvider,
        request: LLMRequest
    ) -> LLMResponse:
        """
        Execute request with circuit breaker protection
        
        Circuit breaker states:
        - CLOSED: Normal operation
        - OPEN: Too many failures, reject immediately
        - HALF_OPEN: Test with single request
        """
        provider_name = provider.get_name()
        
        if settings.CIRCUIT_BREAKER_ENABLED:
            breaker = self.circuit_breakers.get(provider_name)
            if breaker:
                # Call through circuit breaker
                return await breaker.call_async(provider.completion, request)
        
        # No circuit breaker, call directly
        return await provider.completion(request)
    
    def _get_providers_for_model(self, model: str) -> List[BaseLLMProvider]:
        """
        Get list of providers that support a model
        
        Returns providers in priority order from settings
        """
        capable = []
        
        for provider_name in settings.PROVIDER_PRIORITY:
            provider = self._get_provider_by_name(provider_name)
            if provider and provider.supports_model(model):
                capable.append(provider)
        
        # Add any remaining capable providers
        for provider in self.providers:
            if provider not in capable and provider.supports_model(model):
                capable.append(provider)
        
        return capable
    
    def _get_provider_by_name(self, name: str) -> Optional[BaseLLMProvider]:
        """Get provider instance by name"""
        for provider in self.providers:
            if provider.get_name() == name.lower():
                return provider
        return None
    
    def get_provider_health(self) -> dict:
        """
        Get health status of all providers
        
        Returns circuit breaker states and recent error rates
        """
        health = {}
        
        for provider in self.providers:
            provider_name = provider.get_name()
            
            status = {
                "available": provider.is_available(),
                "circuit_breaker_state": "disabled"
            }
            
            if settings.CIRCUIT_BREAKER_ENABLED:
                breaker = self.circuit_breakers.get(provider_name)
                if breaker:
                    status["circuit_breaker_state"] = breaker.current_state
                    status["failure_count"] = breaker.fail_counter
            
            health[provider_name] = status
        
        return health