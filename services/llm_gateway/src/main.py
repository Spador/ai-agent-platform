"""
LLM Gateway - Main Application

Central routing layer for all LLM requests with:
- Multi-provider support (OpenAI, Anthropic)
- Automatic failover and retries
- Token budget enforcement
- Rate limiting
- Cost tracking
- Circuit breakers
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
import structlog
import redis.asyncio as aioredis
from datetime import datetime

# Configure structured logging
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ]
)

from .config import settings
from .database import get_db, close_db
from .schemas import LLMRequest, LLMResponse, GatewayHealth, ProviderHealth
from .providers.router import ProviderRouter
from .providers.base import ProviderError, BudgetExceededError, RateLimitError
from .utils.budget import BudgetEnforcer, RateLimiter

logger = structlog.get_logger()

# Global instances
router: ProviderRouter = None
redis_client: aioredis.Redis = None
budget_enforcer: BudgetEnforcer = None
rate_limiter: RateLimiter = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    global router, redis_client, budget_enforcer, rate_limiter
    
    logger.info(
        "starting_llm_gateway",
        version=settings.VERSION,
        environment=settings.ENVIRONMENT
    )
    
    # Initialize Redis
    redis_client = aioredis.from_url(
        settings.REDIS_URL,
        max_connections=settings.REDIS_MAX_CONNECTIONS,
        decode_responses=False
    )
    await redis_client.ping()
    logger.info("redis_connected")
    
    # Initialize provider router
    router = ProviderRouter()
    
    # Initialize budget enforcer and rate limiter
    budget_enforcer = BudgetEnforcer(redis_client)
    rate_limiter = RateLimiter(redis_client)
    
    logger.info("llm_gateway_initialized")
    
    yield
    
    # Cleanup
    logger.info("shutting_down_llm_gateway")
    await redis_client.close()
    await close_db()


# Initialize FastAPI
app = FastAPI(
    title="AI Agent Platform - LLM Gateway",
    description="Intelligent routing layer for LLM requests with cost controls",
    version=settings.VERSION,
    lifespan=lifespan,
    docs_url="/docs" if settings.ENVIRONMENT != "production" else None
)


# ============================================================================
# Exception Handlers
# ============================================================================

@app.exception_handler(BudgetExceededError)
async def budget_exceeded_handler(request: Request, exc: BudgetExceededError):
    """Handle budget exceeded errors"""
    logger.warning(
        "request_blocked_budget_exceeded",
        tenant_id=exc.tenant_id,
        current_usage=exc.current_usage,
        budget=exc.budget
    )
    return JSONResponse(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        content={
            "error": "budget_exceeded",
            "message": str(exc),
            "tenant_id": exc.tenant_id,
            "current_usage": exc.current_usage,
            "budget": exc.budget
        }
    )


@app.exception_handler(RateLimitError)
async def rate_limit_handler(request: Request, exc: RateLimitError):
    """Handle rate limit errors"""
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={
            "error": "rate_limit_exceeded",
            "message": str(exc),
            "retry_after": exc.retry_after
        },
        headers={"Retry-After": str(exc.retry_after)}
    )


@app.exception_handler(ProviderError)
async def provider_error_handler(request: Request, exc: ProviderError):
    """Handle provider errors"""
    logger.error(
        "provider_error",
        provider=exc.provider,
        error=exc.message
    )
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "error": "provider_error",
            "message": exc.message,
            "provider": exc.provider
        }
    )


# ============================================================================
# Main Endpoints
# ============================================================================

@app.post("/v1/completions", response_model=LLMResponse)
async def create_completion(
    request: LLMRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Create LLM completion with cost controls
    
    Flow:
    1. Check rate limit
    2. Estimate tokens (rough)
    3. Check budget
    4. Route to provider (with failover)
    5. Log cost event
    6. Update budget counter
    7. Return response
    """
    logger.info(
        "completion_request_received",
        tenant_id=str(request.tenant_id),
        model=request.model,
        message_count=len(request.messages)
    )
    
    # Check rate limit
    rate_check = await rate_limiter.check_rate_limit(request.tenant_id)
    if not rate_check["allowed"]:
        raise RateLimitError(str(request.tenant_id), rate_check["reset_seconds"])
    
    # Estimate tokens (rough calculation)
    # Real counting happens after provider response
    estimated_tokens = sum(len(msg.content.split()) * 1.3 for msg in request.messages)
    estimated_tokens = int(estimated_tokens)
    
    # Check budget
    budget_check = await budget_enforcer.check_budget(
        request.tenant_id,
        estimated_tokens,
        db
    )
    
    if not budget_check["allowed"]:
        raise BudgetExceededError(
            str(request.tenant_id),
            budget_check["used_current_month"],
            budget_check["budget_monthly"]
        )
    
    # Warn if soft limit reached
    if budget_check.get("soft_limit_reached"):
        logger.warning(
            "budget_soft_limit_reached",
            tenant_id=str(request.tenant_id),
            percentage=budget_check["percentage_used"]
        )
    
    # Route request to provider
    response = await router.route(request)
    
    # Log cost event to database
    await _log_llm_event(request, response, db)
    
    # Update budget counter
    await budget_enforcer.increment_usage(
        request.tenant_id,
        response.usage.total_tokens
    )
    
    logger.info(
        "completion_request_completed",
        tenant_id=str(request.tenant_id),
        provider=response.provider,
        tokens=response.usage.total_tokens,
        cost_usd=response.cost_usd
    )
    
    return response


async def _log_llm_event(
    request: LLMRequest,
    response: LLMResponse,
    db: AsyncSession
):
    """Log LLM event to database for cost tracking"""
    from ..control_plane.src.models import LLMEvent  # Import model
    
    event = LLMEvent(
        run_id=request.run_id,
        step_id=request.step_id,
        tenant_id=request.tenant_id,
        provider=response.provider,
        model=response.model,
        prompt_tokens=response.usage.prompt_tokens,
        completion_tokens=response.usage.completion_tokens,
        total_tokens=response.usage.total_tokens,
        total_cost_usd=response.cost_usd,
        latency_ms=response.latency_ms,
        status="success",
        is_fallback=response.is_fallback
    )
    
    db.add(event)
    await db.flush()


# ============================================================================
# Health & Monitoring
# ============================================================================

@app.get("/health", response_model=GatewayHealth)
async def health_check():
    """
    Comprehensive health check
    
    Returns status of:
    - Overall gateway
    - Each provider
    - Circuit breaker states
    """
    provider_health = []
    
    health_status = router.get_provider_health()
    for provider_name, status in health_status.items():
        provider_health.append(ProviderHealth(
            provider=provider_name,
            status="healthy" if status["available"] else "unavailable",
            circuit_breaker_state=status["circuit_breaker_state"],
            recent_error_rate=0.0,  # TODO: Track from metrics
            avg_latency_ms=0.0,  # TODO: Track from metrics
            last_success=None,
            last_failure=None
        ))
    
    overall_status = "healthy" if all(p.status == "healthy" for p in provider_health) else "degraded"
    
    return GatewayHealth(
        status=overall_status,
        version=settings.VERSION,
        providers=provider_health,
        cache_hit_rate=0.0,  # TODO: Implement caching
        requests_last_minute=0  # TODO: Track from Redis
    )


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "llm-gateway",
        "version": settings.VERSION,
        "status": "operational",
        "providers": [p.get_name() for p in router.providers]
    }


# ============================================================================
# Development Server
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=8001,  # Different port from control plane
        reload=settings.ENVIRONMENT == "development",
        log_level=settings.LOG_LEVEL.lower()
    )