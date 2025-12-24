"""
Token Budget Enforcement

Prevents runaway costs by:
- Checking tenant budgets before requests
- Warning at 80% usage (soft limit)
- Blocking at 100% usage (hard limit)
- Real-time budget tracking in Redis + periodic sync to database
"""
from typing import Optional
from uuid import UUID
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import redis.asyncio as aioredis

from ..config import settings

logger = structlog.get_logger()


class BudgetEnforcer:
    """
    Enforces token budgets per tenant
    
    Uses Redis for fast checks + PostgreSQL for persistence
    """
    
    def __init__(self, redis_client: aioredis.Redis):
        self.redis = redis_client
    
    async def check_budget(
        self,
        tenant_id: UUID,
        estimated_tokens: int,
        db: AsyncSession
    ) -> dict:
        """
        Check if tenant has sufficient budget
        
        Returns:
            {
                "allowed": bool,
                "budget_monthly": int,
                "used_current_month": int,
                "remaining": int,
                "soft_limit_reached": bool,
                "hard_limit_reached": bool
            }
        """
        if not settings.BUDGET_CHECK_ENABLED:
            return {"allowed": True}
        
        tenant_id_str = str(tenant_id)
        
        # Try Redis first (fast path)
        redis_key = f"budget:{tenant_id_str}"
        cached = await self.redis.get(redis_key)
        
        if cached:
            # Parse cached budget data
            try:
                budget_data = eval(cached.decode())  # Safe since we control the data
                return self._check_limits(
                    budget_data["budget_monthly"],
                    budget_data["used_current_month"],
                    estimated_tokens
                )
            except Exception as e:
                logger.warning("budget_cache_parse_error", error=str(e))
        
        # Cache miss - fetch from database
        from ...control_plane.src.models import Tenant  # Import from control plane
        
        result = await db.execute(
            select(Tenant).filter(Tenant.id == tenant_id)
        )
        tenant = result.scalar_one_or_none()
        
        if not tenant:
            logger.error("tenant_not_found", tenant_id=tenant_id_str)
            return {"allowed": False, "error": "Tenant not found"}
        
        # Cache for 60 seconds
        await self.redis.setex(
            redis_key,
            60,
            str({
                "budget_monthly": tenant.token_budget_monthly,
                "used_current_month": tenant.token_used_current_month
            })
        )
        
        return self._check_limits(
            tenant.token_budget_monthly,
            tenant.token_used_current_month,
            estimated_tokens
        )
    
    def _check_limits(
        self,
        budget_monthly: int,
        used_current_month: int,
        estimated_tokens: int
    ) -> dict:
        """Check budget limits and calculate remaining"""
        remaining = budget_monthly - used_current_month
        percentage_used = (used_current_month / budget_monthly * 100) if budget_monthly > 0 else 100
        
        # Hard limit (100%)
        hard_limit_reached = (used_current_month + estimated_tokens) >= budget_monthly
        
        # Soft limit (80%)
        soft_limit_threshold = budget_monthly * (settings.BUDGET_SOFT_LIMIT_PERCENT / 100)
        soft_limit_reached = used_current_month >= soft_limit_threshold and not hard_limit_reached
        
        result = {
            "allowed": not hard_limit_reached,
            "budget_monthly": budget_monthly,
            "used_current_month": used_current_month,
            "remaining": max(0, remaining),
            "percentage_used": round(percentage_used, 2),
            "soft_limit_reached": soft_limit_reached,
            "hard_limit_reached": hard_limit_reached
        }
        
        if hard_limit_reached:
            logger.warning(
                "budget_hard_limit_reached",
                **result
            )
        elif soft_limit_reached:
            logger.info(
                "budget_soft_limit_reached",
                **result
            )
        
        return result
    
    async def increment_usage(
        self,
        tenant_id: UUID,
        tokens_used: int
    ):
        """
        Increment token usage counter
        
        Updates Redis immediately for fast checks.
        Database sync happens periodically via background job.
        """
        tenant_id_str = str(tenant_id)
        redis_key = f"budget:{tenant_id_str}"
        
        # Increment in Redis
        await self.redis.incrby(f"{redis_key}:counter", tokens_used)
        
        logger.debug(
            "budget_usage_incremented",
            tenant_id=tenant_id_str,
            tokens=tokens_used
        )


class RateLimiter:
    """
    Token bucket rate limiter
    
    Limits requests per tenant per minute
    """
    
    def __init__(self, redis_client: aioredis.Redis):
        self.redis = redis_client
    
    async def check_rate_limit(
        self,
        tenant_id: UUID,
        limit: Optional[int] = None
    ) -> dict:
        """
        Check if request is within rate limit
        
        Uses sliding window algorithm:
        - Key expires after window duration
        - Increment counter
        - Check if over limit
        """
        if not settings.RATE_LIMIT_ENABLED:
            return {"allowed": True}
        
        tenant_id_str = str(tenant_id)
        rate_key = f"ratelimit:{tenant_id_str}"
        
        # Get current limit (use tenant-specific or default)
        limit = limit or settings.RATE_LIMIT_REQUESTS_PER_MINUTE
        window = settings.RATE_LIMIT_WINDOW_SECONDS
        
        # Atomic increment and check
        pipe = self.redis.pipeline()
        pipe.incr(rate_key)
        pipe.expire(rate_key, window)
        results = await pipe.execute()
        
        current_count = results[0]
        
        allowed = current_count <= limit
        remaining = max(0, limit - current_count)
        
        result = {
            "allowed": allowed,
            "limit": limit,
            "current": current_count,
            "remaining": remaining,
            "reset_seconds": window
        }
        
        if not allowed:
            logger.warning(
                "rate_limit_exceeded",
                tenant_id=tenant_id_str,
                **result
            )
        
        return result