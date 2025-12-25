"""
Orchestrator Worker Configuration

Manages settings for:
- SQS polling
- Database connections
- LLM Gateway integration
- Retry policies
- Tool execution
"""
from pydantic_settings import BaseSettings
from typing import List
from functools import lru_cache


class Settings(BaseSettings):
    """Orchestrator Worker Settings"""
    
    # Application
    VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    
    # Worker Configuration
    WORKER_ID: str = "orchestrator-worker-1"
    WORKER_CONCURRENCY: int = 5  # Max concurrent step executions
    WORKER_POLL_INTERVAL: int = 5  # Seconds between SQS polls
    
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_agent_platform"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 5
    
    # AWS SQS
    AWS_REGION: str = "us-east-1"
    SQS_QUEUE_URL: str = ""  # Will be set by Terraform
    SQS_DLQ_URL: str = ""  # Dead letter queue
    SQS_VISIBILITY_TIMEOUT: int = 300  # 5 minutes
    SQS_WAIT_TIME_SECONDS: int = 20  # Long polling
    SQS_MAX_MESSAGES: int = 10  # Batch size
    
    # LLM Gateway
    LLM_GATEWAY_URL: str = "http://localhost:8001"
    LLM_GATEWAY_TIMEOUT: int = 120  # 2 minutes for LLM calls
    
    # Step Execution
    STEP_DEFAULT_TIMEOUT: int = 300  # 5 minutes
    STEP_MAX_RETRIES: int = 3
    STEP_RETRY_DELAY_BASE: int = 2  # Base for exponential backoff (seconds)
    STEP_RETRY_DELAY_MAX: int = 60  # Max backoff delay
    
    # Tool Execution
    TOOL_EXECUTION_TIMEOUT: int = 120  # 2 minutes
    TOOL_MAX_RETRIES: int = 2
    
    # S3 (for artifacts)
    S3_ARTIFACTS_BUCKET: str = "ai-agent-platform-artifacts-dev"
    S3_PRESIGNED_URL_EXPIRATION: int = 3600  # 1 hour
    
    # Rate Limiting (prevent overwhelming LLM Gateway)
    MAX_CONCURRENT_LLM_CALLS: int = 10
    
    # Health Check
    HEALTH_CHECK_INTERVAL: int = 60  # Seconds
    
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