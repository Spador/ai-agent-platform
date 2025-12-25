"""
Step Executor

Executes individual steps within a run:
- LLM calls (via LLM Gateway)
- Tool executions
- Decision points
- Parallel operations

This is where the actual AI agent logic runs!
"""
import asyncio
from typing import Dict, Any, Optional
from datetime import datetime
from uuid import UUID
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import settings
from .database import AsyncSessionLocal
from .agents.llm_client import LLMClient
from .tools.tool_executor import ToolExecutor

logger = structlog.get_logger()


class StepExecutor:
    """
    Executes steps with proper error handling and state management
    
    Step Types:
    - llm: Call LLM via gateway
    - tool: Execute tool (browser, code, etc)
    - decision: Branching logic
    - parallel: Execute multiple steps concurrently
    """
    
    def __init__(self):
        """Initialize step executor"""
        self.llm_client = LLMClient()
        self.tool_executor = ToolExecutor()
        
        logger.info("step_executor_initialized")
    
    async def execute(
        self,
        run_id: str,
        step_id: str,
        step_name: str,
        step_type: str,
        step_config: Dict[str, Any],
        attempt: int = 1
    ) -> Dict[str, Any]:
        """
        Execute a step
        
        Args:
            run_id: Run identifier
            step_id: Step identifier
            step_name: Human-readable step name
            step_type: Type of step (llm, tool, decision, parallel)
            step_config: Step configuration
            attempt: Current attempt number
        
        Returns:
            {
                "success": bool,
                "output": Any,
                "error": Optional[str],
                "retryable": bool,
                "tokens_used": int,
                "cost_usd": float
            }
        """
        start_time = datetime.utcnow()
        
        logger.info(
            "step_execution_started",
            run_id=run_id,
            step_id=step_id,
            step_name=step_name,
            step_type=step_type,
            attempt=attempt
        )
        
        async with AsyncSessionLocal() as db:
            try:
                # Update step status to running
                await self._update_step_status(
                    db, step_id, "running", started_at=start_time
                )
                
                # Execute based on type
                if step_type == "llm":
                    result = await self._execute_llm_step(
                        db, run_id, step_id, step_config
                    )
                elif step_type == "tool":
                    result = await self._execute_tool_step(
                        db, run_id, step_id, step_config
                    )
                elif step_type == "decision":
                    result = await self._execute_decision_step(
                        step_config
                    )
                elif step_type == "parallel":
                    result = await self._execute_parallel_step(
                        db, run_id, step_config
                    )
                else:
                    raise ValueError(f"Unknown step type: {step_type}")
                
                # Calculate duration
                completed_at = datetime.utcnow()
                duration = int((completed_at - start_time).total_seconds())
                
                # Update step status to success
                await self._update_step_status(
                    db,
                    step_id,
                    "success",
                    completed_at=completed_at,
                    duration_seconds=duration,
                    output_data=result.get("output"),
                    tokens_used=result.get("tokens_used", 0),
                    cost_usd=result.get("cost_usd", 0.0)
                )
                
                # Update run tokens and cost
                await self._update_run_usage(
                    db,
                    run_id,
                    result.get("tokens_used", 0),
                    result.get("cost_usd", 0.0)
                )
                
                await db.commit()
                
                logger.info(
                    "step_execution_completed",
                    run_id=run_id,
                    step_id=step_id,
                    duration_seconds=duration,
                    tokens=result.get("tokens_used", 0)
                )
                
                return {
                    "success": True,
                    "output": result.get("output"),
                    "tokens_used": result.get("tokens_used", 0),
                    "cost_usd": result.get("cost_usd", 0.0)
                }
                
            except Exception as e:
                # Handle failure
                completed_at = datetime.utcnow()
                duration = int((completed_at - start_time).total_seconds())
                
                logger.error(
                    "step_execution_failed",
                    run_id=run_id,
                    step_id=step_id,
                    error=str(e),
                    attempt=attempt,
                    exc_info=True
                )
                
                # Determine if retryable
                retryable = self._is_retryable_error(e)
                
                # Update step status
                await self._update_step_status(
                    db,
                    step_id,
                    "retrying" if retryable and attempt < settings.STEP_MAX_RETRIES else "failed",
                    completed_at=completed_at,
                    duration_seconds=duration,
                    error_message=str(e)
                )
                
                await db.commit()
                
                return {
                    "success": False,
                    "error": str(e),
                    "retryable": retryable
                }
    
    async def _execute_llm_step(
        self,
        db: AsyncSession,
        run_id: str,
        step_id: str,
        config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute LLM step
        
        Config format:
        {
            "model": "gpt-4",
            "prompt": "Analyze this...",
            "context": {...},
            "max_tokens": 1000,
            "temperature": 0.7
        }
        """
        # Get run context
        run = await self._get_run(db, run_id)
        
        # Build messages from config
        messages = []
        
        # Add system message if provided
        if config.get("system_prompt"):
            messages.append({
                "role": "system",
                "content": config["system_prompt"]
            })
        
        # Add user prompt
        prompt = config.get("prompt", "")
        
        # Inject context if provided
        if config.get("context"):
            prompt = f"Context: {config['context']}\n\n{prompt}"
        
        messages.append({
            "role": "user",
            "content": prompt
        })
        
        # Call LLM via gateway
        response = await self.llm_client.completion(
            model=config.get("model", "gpt-3.5-turbo"),
            messages=messages,
            tenant_id=str(run.tenant_id),
            run_id=run_id,
            step_id=step_id,
            max_tokens=config.get("max_tokens"),
            temperature=config.get("temperature", 0.7)
        )
        
        return {
            "output": response["content"],
            "tokens_used": response["usage"]["total_tokens"],
            "cost_usd": response["cost_usd"]
        }
    
    async def _execute_tool_step(
        self,
        db: AsyncSession,
        run_id: str,
        step_id: str,
        config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute tool step
        
        Config format:
        {
            "tool": "browser",
            "action": "search",
            "params": {...}
        }
        """
        tool_name = config.get("tool")
        action = config.get("action")
        params = config.get("params", {})
        
        result = await self.tool_executor.execute(
            tool_name=tool_name,
            action=action,
            params=params,
            run_id=run_id,
            step_id=step_id
        )
        
        return {
            "output": result["output"],
            "tokens_used": 0,  # Tools don't use tokens
            "cost_usd": 0.0
        }
    
    async def _execute_decision_step(
        self,
        config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute decision/branching logic
        
        Config format:
        {
            "condition": "result.contains('success')",
            "if_true": "next_step_name",
            "if_false": "error_step_name"
        }
        """
        # TODO: Implement decision logic
        # For now, just return success
        return {
            "output": {"decision": "placeholder"},
            "tokens_used": 0,
            "cost_usd": 0.0
        }
    
    async def _execute_parallel_step(
        self,
        db: AsyncSession,
        run_id: str,
        config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute multiple steps in parallel
        
        Config format:
        {
            "steps": [
                {"name": "step1", "type": "llm", ...},
                {"name": "step2", "type": "tool", ...}
            ]
        }
        """
        # TODO: Implement parallel execution
        # For now, just return success
        return {
            "output": {"parallel_results": []},
            "tokens_used": 0,
            "cost_usd": 0.0
        }
    
    def _is_retryable_error(self, error: Exception) -> bool:
        """
        Determine if error is retryable
        
        Retryable:
        - Network timeouts
        - Rate limits
        - 5xx server errors
        
        Non-retryable:
        - Invalid config
        - Budget exceeded
        - 4xx client errors
        """
        error_str = str(error).lower()
        
        # Check for retryable patterns
        retryable_patterns = [
            "timeout",
            "connection",
            "rate limit",
            "503",
            "502",
            "500"
        ]
        
        for pattern in retryable_patterns:
            if pattern in error_str:
                return True
        
        return False
    
    async def _get_run(self, db: AsyncSession, run_id: str):
        """Get run from database"""
        # Import here to avoid circular imports
        from ...control_plane.src.models import Run
        
        result = await db.execute(
            select(Run).filter(Run.id == UUID(run_id))
        )
        return result.scalar_one()
    
    async def _update_step_status(
        self,
        db: AsyncSession,
        step_id: str,
        status: str,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
        duration_seconds: Optional[int] = None,
        output_data: Optional[Dict] = None,
        error_message: Optional[str] = None,
        tokens_used: int = 0,
        cost_usd: float = 0.0
    ):
        """Update step status in database"""
        from ...control_plane.src.models import Step
        
        result = await db.execute(
            select(Step).filter(Step.id == UUID(step_id))
        )
        step = result.scalar_one()
        
        step.status = status
        if started_at:
            step.started_at = started_at
        if completed_at:
            step.completed_at = completed_at
        if duration_seconds is not None:
            step.duration_seconds = duration_seconds
        if output_data:
            step.output_data = output_data
        if error_message:
            step.error_message = error_message
        if tokens_used:
            step.tokens_used = tokens_used
        if cost_usd:
            step.cost_usd = cost_usd
        
        await db.flush()
    
    async def _update_run_usage(
        self,
        db: AsyncSession,
        run_id: str,
        tokens: int,
        cost: float
    ):
        """Update run token usage and cost"""
        from ...control_plane.src.models import Run
        
        result = await db.execute(
            select(Run).filter(Run.id == UUID(run_id))
        )
        run = result.scalar_one()
        
        run.tokens_used += tokens
        run.estimated_cost_usd += cost
        
        await db.flush()