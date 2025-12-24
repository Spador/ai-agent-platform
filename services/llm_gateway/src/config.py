"""
LLM Gateway Configuration

Manages settings for:
- LLM provider credentials
- Circuit breaker thresholds
- Rate limiting rules
- Cost tracking
"""
from pydantic_settings import BaseSettings
from typing import Dict
from functools import lru_cache


class Settings(BaseSettings):
    """LLM Gateway Settings"""
    
    # Application
    VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    
    # Database (for cost event logging)
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_agent_platform"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 0
    
    # Redis (for rate limiting and caching)
    REDIS_URL: str = "redis://localhost:6379/1"  # Use DB 1 to separate from control plane
    REDIS_MAX_CONNECTIONS: int = 50
    
    # OpenAI Configuration
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_TIMEOUT: int = 60
    OPENAI_MAX_RETRIES: int = 3
    
    # Anthropic Configuration
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_BASE_URL: str = "https://api.anthropic.com"
    ANTHROPIC_TIMEOUT: int = 60
    ANTHROPIC_MAX_RETRIES: int = 3
    
    # Local Model Configuration (optional)
    LOCAL_MODEL_URL: str = "http://localhost:8080"
    LOCAL_MODEL_ENABLED: bool = False
    
    # Provider Priority (order matters)
    PROVIDER_PRIORITY: list = ["openai", "anthropic", "local"]
    
    # Circuit Breaker Settings
    CIRCUIT_BREAKER_ENABLED: bool = True
    CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = 5  # failures before opening
    CIRCUIT_BREAKER_TIMEOUT: int = 60  # seconds to wait before half-open
    CIRCUIT_BREAKER_EXPECTED_EXCEPTION: tuple = (Exception,)
    
    # Rate Limiting (per tenant)
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_REQUESTS_PER_MINUTE: int = 100
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    
    # Token Budget Enforcement
    BUDGET_CHECK_ENABLED: bool = True
    BUDGET_SOFT_LIMIT_PERCENT: int = 80  # Warn at 80%
    
    # Cost Tracking (USD per 1K tokens)
    # Prices as of 2024 - update regularly!
    TOKEN_COSTS: Dict[str, Dict[str, float]] = {
        "gpt-4-turbo-preview": {
            "prompt": 0.01,
            "completion": 0.03
        },
        "gpt-4": {
            "prompt": 0.03,
            "completion": 0.06
        },
        "gpt-3.5-turbo": {
            "prompt": 0.0005,
            "completion": 0.0015
        },
        "claude-3-opus": {
            "prompt": 0.015,
            "completion": 0.075
        },
        "claude-3-sonnet": {
            "prompt": 0.003,
            "completion": 0.015
        },
        "claude-3-haiku": {
            "prompt": 0.00025,
            "completion": 0.00125
        }
    }
    
    # Caching
    CACHE_ENABLED: bool = True
    CACHE_TTL_SECONDS: int = 3600  # 1 hour
    CACHE_IDENTICAL_PROMPTS: bool = True  # Cache exact matches
    
    # Monitoring
    ENABLE_METRICS: bool = True
    ENABLE_TRACING: bool = True
    
    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()


settings = get_settings()