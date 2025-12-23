"""
AWS Step Functions Integration

Handles starting and managing Step Functions state machine executions.
For local development, this can be mocked or use localstack.
"""
import boto3
import json
import structlog
from typing import Dict, Any, Optional

from ..config import settings

logger = structlog.get_logger()

# Initialize Step Functions client
try:
    stepfunctions_client = boto3.client(
        'stepfunctions',
        region_name=settings.AWS_REGION
    )
except Exception as e:
    logger.warning("stepfunctions_client_init_failed", error=str(e))
    stepfunctions_client = None


async def start_workflow(run_id: str, task_config: Dict[str, Any]) -> Optional[str]:
    """
    Start a Step Functions state machine execution
    
    Args:
        run_id: Unique run identifier
        task_config: Task configuration including steps and tools
    
    Returns:
        Execution ARN if successful, None otherwise
    
    State Machine Flow:
        1. InitRun - Validate and mark as running
        2. EnqueueStep - Send first step to SQS
        3. WaitForStepCompletion - Poll for completion
        4. Branch - Decide next step or finalize
        5. FinalizeRun - Mark as completed/failed
    """
    if not stepfunctions_client:
        logger.warning(
            "stepfunctions_disabled",
            run_id=run_id,
            reason="Client not initialized (ok for local dev)"
        )
        return None
    
    if not settings.STEP_FUNCTIONS_STATE_MACHINE_ARN:
        logger.warning(
            "stepfunctions_disabled",
            run_id=run_id,
            reason="State machine ARN not configured"
        )
        return None
    
    try:
        # Prepare execution input
        execution_input = {
            "run_id": run_id,
            "task_config": task_config,
            "steps": task_config.get("steps", [])
        }
        
        # Start execution
        response = stepfunctions_client.start_execution(
            stateMachineArn=settings.STEP_FUNCTIONS_STATE_MACHINE_ARN,
            name=f"run-{run_id}",
            input=json.dumps(execution_input)
        )
        
        execution_arn = response['executionArn']
        
        logger.info(
            "stepfunctions_started",
            run_id=run_id,
            execution_arn=execution_arn
        )
        
        return execution_arn
        
    except Exception as e:
        logger.error(
            "stepfunctions_start_failed",
            run_id=run_id,
            error=str(e),
            exc_info=True
        )
        return None


async def stop_workflow(execution_arn: str) -> bool:
    """
    Stop a running Step Functions execution
    
    Args:
        execution_arn: ARN of the execution to stop
    
    Returns:
        True if stopped successfully, False otherwise
    """
    if not stepfunctions_client:
        return False
    
    try:
        stepfunctions_client.stop_execution(
            executionArn=execution_arn,
            error="UserCancelled",
            cause="User requested cancellation"
        )
        
        logger.info(
            "stepfunctions_stopped",
            execution_arn=execution_arn
        )
        
        return True
        
    except Exception as e:
        logger.error(
            "stepfunctions_stop_failed",
            execution_arn=execution_arn,
            error=str(e)
        )
        return False


async def get_workflow_status(execution_arn: str) -> Optional[Dict[str, Any]]:
    """
    Get current status of a Step Functions execution
    
    Args:
        execution_arn: ARN of the execution
    
    Returns:
        Execution details including status, start/stop times, output
    """
    if not stepfunctions_client:
        return None
    
    try:
        response = stepfunctions_client.describe_execution(
            executionArn=execution_arn
        )
        
        return {
            "status": response.get("status"),
            "started_at": response.get("startDate"),
            "stopped_at": response.get("stopDate"),
            "output": response.get("output")
        }
        
    except Exception as e:
        logger.error(
            "stepfunctions_status_failed",
            execution_arn=execution_arn,
            error=str(e)
        )
        return None