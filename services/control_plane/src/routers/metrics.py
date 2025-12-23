"""
Metrics Router - Tenant-level analytics and reporting

Provides aggregated metrics for:
- Cost analysis
- Token usage
- Run statistics
- Performance metrics
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime, timedelta
from typing import Optional

from ..database import get_db
from ..models import Run, LLMEvent, ToolEvent, User
from ..schemas import TenantMetrics
from ..utils.auth import get_current_user

router = APIRouter()


@router.get("/tenant", response_model=TenantMetrics)
async def get_tenant_metrics(
    period_days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get aggregated metrics for the current tenant
    
    Args:
        period_days: Number of days to look back (default: 30)
    
    Returns:
        Aggregated metrics including:
        - Total runs and completion rate
        - Token usage and costs
        - Average execution time
    """
    period_start = datetime.utcnow() - timedelta(days=period_days)
    
    # Get run statistics
    run_stats = await db.execute(
        select(
            func.count(Run.id).label('total_runs'),
            func.count(Run.id).filter(Run.status == 'completed').label('completed_runs'),
            func.count(Run.id).filter(Run.status == 'failed').label('failed_runs'),
            func.sum(Run.tokens_used).label('total_tokens'),
            func.sum(Run.estimated_cost_usd).label('total_cost'),
            func.avg(Run.duration_seconds).label('avg_duration')
        )
        .filter(
            Run.tenant_id == current_user.tenant_id,
            Run.created_at >= period_start
        )
    )
    stats = run_stats.one()
    
    return TenantMetrics(
        tenant_id=current_user.tenant_id,
        period_start=period_start,
        period_end=datetime.utcnow(),
        total_runs=stats.total_runs or 0,
        completed_runs=stats.completed_runs or 0,
        failed_runs=stats.failed_runs or 0,
        total_tokens=stats.total_tokens or 0,
        total_cost_usd=float(stats.total_cost or 0),
        avg_run_duration_seconds=float(stats.avg_duration or 0)
    )


@router.get("/providers")
async def get_provider_metrics(
    period_days: int = Query(default=7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get LLM provider usage breakdown
    
    Shows which providers are being used and their:
    - Request counts
    - Success/failure rates
    - Average latency
    - Total costs
    """
    period_start = datetime.utcnow() - timedelta(days=period_days)
    
    provider_stats = await db.execute(
        select(
            LLMEvent.provider,
            LLMEvent.model,
            func.count(LLMEvent.id).label('request_count'),
            func.count(LLMEvent.id).filter(LLMEvent.status == 'success').label('success_count'),
            func.count(LLMEvent.id).filter(LLMEvent.status == 'failed').label('failure_count'),
            func.avg(LLMEvent.latency_ms).label('avg_latency_ms'),
            func.sum(LLMEvent.total_cost_usd).label('total_cost')
        )
        .filter(
            LLMEvent.tenant_id == current_user.tenant_id,
            LLMEvent.created_at >= period_start
        )
        .group_by(LLMEvent.provider, LLMEvent.model)
    )
    
    results = []
    for row in provider_stats:
        success_rate = (row.success_count / row.request_count * 100) if row.request_count > 0 else 0
        results.append({
            "provider": row.provider,
            "model": row.model,
            "request_count": row.request_count,
            "success_rate_percent": round(success_rate, 2),
            "avg_latency_ms": round(row.avg_latency_ms or 0, 2),
            "total_cost_usd": float(row.total_cost or 0)
        })
    
    return {
        "period_days": period_days,
        "providers": results
    }


@router.get("/tools")
async def get_tool_metrics(
    period_days: int = Query(default=7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get tool usage statistics
    
    Shows which tools are being used most and their:
    - Execution counts
    - Success rates
    - Average duration
    """
    period_start = datetime.utcnow() - timedelta(days=period_days)
    
    tool_stats = await db.execute(
        select(
            ToolEvent.tool_name,
            func.count(ToolEvent.id).label('execution_count'),
            func.count(ToolEvent.id).filter(ToolEvent.status == 'success').label('success_count'),
            func.avg(ToolEvent.duration_seconds).label('avg_duration')
        )
        .filter(
            ToolEvent.tenant_id == current_user.tenant_id,
            ToolEvent.created_at >= period_start
        )
        .group_by(ToolEvent.tool_name)
    )
    
    results = []
    for row in tool_stats:
        success_rate = (row.success_count / row.execution_count * 100) if row.execution_count > 0 else 0
        results.append({
            "tool_name": row.tool_name,
            "execution_count": row.execution_count,
            "success_rate_percent": round(success_rate, 2),
            "avg_duration_seconds": round(row.avg_duration or 0, 2)
        })
    
    return {
        "period_days": period_days,
        "tools": results
    }


@router.get("/daily")
async def get_daily_metrics(
    days: int = Query(default=7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get daily metrics for charting/trending
    
    Returns time-series data for:
    - Daily run counts
    - Daily token usage
    - Daily costs
    """
    from sqlalchemy import cast, Date
    
    period_start = datetime.utcnow() - timedelta(days=days)
    
    daily_stats = await db.execute(
        select(
            cast(Run.created_at, Date).label('date'),
            func.count(Run.id).label('run_count'),
            func.sum(Run.tokens_used).label('tokens'),
            func.sum(Run.estimated_cost_usd).label('cost')
        )
        .filter(
            Run.tenant_id == current_user.tenant_id,
            Run.created_at >= period_start
        )
        .group_by(cast(Run.created_at, Date))
        .order_by(cast(Run.created_at, Date))
    )
    
    results = []
    for row in daily_stats:
        results.append({
            "date": row.date.isoformat(),
            "run_count": row.run_count,
            "tokens_used": row.tokens or 0,
            "cost_usd": float(row.cost or 0)
        })
    
    return {
        "period_days": days,
        "daily_metrics": results
    }