"""
Runs Router - Manage task executions

This router handles:
- Creating new runs
- Starting/stopping runs
- Querying run status
- Getting step details
- Retrieving metrics
"""
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import joinedload
from typing import List, Optional
from uuid import UUID
from datetime import datetime
import structlog

from ..database import get_db
from ..models import Run, Task, Step, User
from ..schemas import (
    RunCreate, RunResponse, RunStatusUpdate,
    StepResponse, RunMetrics
)
from ..utils.auth import get_current_user
from ..utils.step_functions import start_workflow

logger = structlog.get_logger()
router = APIRouter()


@router.post("/", response_model=RunResponse, status_code=status.HTTP_201_CREATED)
async def create_run(
    run_data: RunCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Create a new run and optionally start it
    
    Steps:
    1. Validate task exists and user has access
    2. Check tenant token budget
    3. Create run record
    4. Optionally start Step Functions workflow
    
    Returns:
        Run object with status 'pending'
    """
    # Get task
    result = await db.execute(
        select(Task).filter(
            Task.id == run_data.task_id,
            Task.tenant_id == current_user.tenant_id,
            Task.is_active == True
        )
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found or inactive"
        )
    
    # Determine token budget
    token_budget = run_data.token_budget or task.default_token_budget
    
    # Check tenant budget
    tenant_result = await db.execute(
        select(User).filter(User.id == current_user.id)
        .options(joinedload(User.tenant))
    )
    user_with_tenant = tenant_result.scalar_one()
    tenant = user_with_tenant.tenant
    
    if tenant.token_used_current_month + token_budget > tenant.token_budget_monthly:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Insufficient token budget for this run"
        )
    
    # Create run
    run = Run(
        task_id=task.id,
        tenant_id=current_user.tenant_id,
        created_by=current_user.id,
        status='pending',
        token_budget=token_budget
    )
    
    db.add(run)
    await db.flush()
    await db.refresh(run)
    
    logger.info(
        "run_created",
        run_id=str(run.id),
        task_id=str(task.id),
        tenant_id=str(current_user.tenant_id),
        token_budget=token_budget
    )
    
    # Start workflow in background (non-blocking)
    background_tasks.add_task(start_workflow, str(run.id), task.task_config)
    
    return run


@router.get("/", response_model=List[RunResponse])
async def list_runs(
    skip: int = 0,
    limit: int = 20,
    status_filter: Optional[str] = None,
    task_id: Optional[UUID] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    List all runs for current tenant with optional filtering
    
    Query params:
    - skip: Pagination offset
    - limit: Max results (1-100)
    - status_filter: Filter by status (pending, running, completed, etc.)
    - task_id: Filter by specific task
    """
    query = select(Run).filter(Run.tenant_id == current_user.tenant_id)
    
    if status_filter:
        query = query.filter(Run.status == status_filter)
    
    if task_id:
        query = query.filter(Run.task_id == task_id)
    
    query = query.order_by(Run.created_at.desc()).offset(skip).limit(min(limit, 100))
    
    result = await db.execute(query)
    runs = result.scalars().all()
    
    return runs


@router.get("/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get detailed information about a specific run
    """
    result = await db.execute(
        select(Run).filter(
            Run.id == run_id,
            Run.tenant_id == current_user.tenant_id
        )
    )
    run = result.scalar_one_or_none()
    
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found"
        )
    
    return run


@router.get("/{run_id}/steps", response_model=List[StepResponse])
async def get_run_steps(
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get all steps for a run in execution order
    
    This is useful for:
    - Showing progress timeline
    - Debugging failed runs
    - Understanding execution flow
    """
    # Verify run belongs to tenant
    run_result = await db.execute(
        select(Run).filter(
            Run.id == run_id,
            Run.tenant_id == current_user.tenant_id
        )
    )
    run = run_result.scalar_one_or_none()
    
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found"
        )
    
    # Get all steps ordered by step_order
    steps_result = await db.execute(
        select(Step)
        .filter(Step.run_id == run_id)
        .order_by(Step.step_order)
    )
    steps = steps_result.scalars().all()
    
    return steps


@router.get("/{run_id}/metrics", response_model=RunMetrics)
async def get_run_metrics(
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get aggregated metrics for a run
    
    Includes:
    - Total/completed/failed steps
    - Token usage and costs
    - LLM and tool call counts
    """
    # Verify run access
    run_result = await db.execute(
        select(Run).filter(
            Run.id == run_id,
            Run.tenant_id == current_user.tenant_id
        )
    )
    run = run_result.scalar_one_or_none()
    
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found"
        )
    
    # Count steps by status
    steps_stats = await db.execute(
        select(
            func.count(Step.id).label('total'),
            func.count(Step.id).filter(Step.status == 'success').label('completed'),
            func.count(Step.id).filter(Step.status == 'failed').label('failed')
        ).filter(Step.run_id == run_id)
    )
    stats = steps_stats.one()
    
    # Count LLM events
    from ..models import LLMEvent
    llm_count = await db.execute(
        select(func.count(LLMEvent.id)).filter(LLMEvent.run_id == run_id)
    )
    llm_calls = llm_count.scalar() or 0
    
    # Count tool events
    from ..models import ToolEvent
    tool_count = await db.execute(
        select(func.count(ToolEvent.id)).filter(ToolEvent.run_id == run_id)
    )
    tool_calls = tool_count.scalar() or 0
    
    return RunMetrics(
        run_id=run.id,
        total_steps=stats.total or 0,
        completed_steps=stats.completed or 0,
        failed_steps=stats.failed or 0,
        tokens_used=run.tokens_used,
        estimated_cost_usd=float(run.estimated_cost_usd),
        duration_seconds=run.duration_seconds,
        llm_calls=llm_calls,
        tool_calls=tool_calls
    )


@router.put("/{run_id}/status", response_model=RunResponse)
async def update_run_status(
    run_id: UUID,
    status_update: RunStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Update run status (typically called by orchestrator)
    
    This endpoint is used internally to:
    - Mark run as running when execution starts
    - Update current step
    - Mark as completed/failed when done
    - Record error messages
    """
    result = await db.execute(
        select(Run).filter(
            Run.id == run_id,
            Run.tenant_id == current_user.tenant_id
        )
    )
    run = result.scalar_one_or_none()
    
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found"
        )
    
    # Update status
    old_status = run.status
    run.status = status_update.status.value
    
    # Set timestamps
    if status_update.status.value == 'running' and not run.started_at:
        run.started_at = datetime.utcnow()
    
    if status_update.status.value in ['completed', 'failed', 'cancelled', 'budget_exceeded', 'timeout']:
        if not run.completed_at:
            run.completed_at = datetime.utcnow()
            if run.started_at:
                run.duration_seconds = int((run.completed_at - run.started_at).total_seconds())
    
    # Update other fields
    if status_update.error_message:
        run.error_message = status_update.error_message
    
    if status_update.current_step:
        run.current_step = status_update.current_step
    
    await db.flush()
    await db.refresh(run)
    
    logger.info(
        "run_status_updated",
        run_id=str(run_id),
        old_status=old_status,
        new_status=run.status
    )
    
    return run


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_run(
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Cancel a running task
    
    This will:
    - Mark run as cancelled
    - Stop Step Functions execution
    - Clean up any in-progress steps
    """
    result = await db.execute(
        select(Run).filter(
            Run.id == run_id,
            Run.tenant_id == current_user.tenant_id
        )
    )
    run = result.scalar_one_or_none()
    
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found"
        )
    
    if run.status not in ['pending', 'running']:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel run with status: {run.status}"
        )
    
    # Update status
    run.status = 'cancelled'
    run.completed_at = datetime.utcnow()
    if run.started_at:
        run.duration_seconds = int((run.completed_at - run.started_at).total_seconds())
    
    # TODO: Stop Step Functions execution
    # if run.state_machine_execution_arn:
    #     await stop_step_functions_execution(run.state_machine_execution_arn)
    
    await db.flush()
    
    logger.info(
        "run_cancelled",
        run_id=str(run_id),
        tenant_id=str(current_user.tenant_id)
    )