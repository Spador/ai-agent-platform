"""
Base Provider Interface

All LLM providers must implement this interface.
This allows easy addition of new providers without changing gateway logic.
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import structlog

from ..schemas import LLMRequest, LLMResponse

logger = structlog.get_logger()


class BaseLLMProvider(ABC):
    """
    Abstract base class for LLM providers
    
    Providers must implement:
    - Model name mapping (e.g., "gpt-4" -> OpenAI's format)
    - Request/response format conversion
    - Error handling and retry logic
    - Cost calculation
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize provider
        
        Args:
            config: Provider-specific configuration
        """
        self.config = config
        self.name = self.__class__.__name__.replace("Provider", "").lower()
        logger.info(f"initialized_{self.name}_provider")
    
    @abstractmethod
    async def completion(self, request: LLMRequest) -> LLMResponse:
        """
        Execute completion request
        
        Args:
            request: Unified LLM request
            
        Returns:
            Unified LLM response
            
        Raises:
            ProviderError: On provider-specific errors
            BudgetExceededError: If cost exceeds budget
            RateLimitError: If rate limited
        """
        pass
    
    @abstractmethod
    def supports_model(self, model: str) -> bool:
        """
        Check if provider supports a model
        
        Args:
            model: Model identifier
            
        Returns:
            True if supported, False otherwise
        """
        pass
    
    @abstractmethod
    def map_model_name(self, model: str) -> str:
        """
        Map generic model name to provider-specific name
        
        Args:
            model: Generic model name (e.g., "gpt-4")
            
        Returns:
            Provider-specific model name
        """
        pass
    
    @abstractmethod
    def calculate_cost(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int
    ) -> float:
        """
        Calculate request cost in USD
        
        Args:
            model: Model identifier
            prompt_tokens: Number of prompt tokens
            completion_tokens: Number of completion tokens
            
        Returns:
            Cost in USD
        """
        pass
    
    def get_name(self) -> str:
        """Get provider name"""
        return self.name
    
    def is_available(self) -> bool:
        """
        Check if provider is configured and available
        
        Override this to add custom availability checks
        (e.g., API key present, service reachable)
        """
        return True


class ProviderError(Exception):
    """Base exception for provider errors"""
    def __init__(self, provider: str, message: str, original_error: Optional[Exception] = None):
        self.provider = provider
        self.message = message
        self.original_error = original_error
        super().__init__(f"{provider}: {message}")


class BudgetExceededError(Exception):
    """Raised when tenant budget is exceeded"""
    def __init__(self, tenant_id: str, current_usage: int, budget: int):
        self.tenant_id = tenant_id
        self.current_usage = current_usage
        self.budget = budget
        super().__init__(f"Budget exceeded: {current_usage}/{budget} tokens")


class RateLimitError(Exception):
    """Raised when rate limit is hit"""
    def __init__(self, tenant_id: str, retry_after: int):
        self.tenant_id = tenant_id
        self.retry_after = retry_after
        super().__init__(f"Rate limit exceeded. Retry after {retry_after}s")


class ModelNotSupportedError(Exception):
    """Raised when model is not supported by provider"""
    def __init__(self, provider: str, model: str):
        self.provider = provider
        self.model = model
        super().__init__(f"{provider} does not support model: {model}")