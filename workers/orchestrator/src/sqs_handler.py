"""
SQS Message Handler

Polls SQS for step execution messages and processes them.

Message Format:
{
    "run_id": "uuid",
    "step_id": "uuid",
    "step_name": "search_web",
    "step_type": "tool",
    "step_config": {...},
    "attempt": 1
}
"""
import json
import asyncio
from typing import List, Dict, Any, Optional
import structlog
import boto3
from botocore.exceptions import ClientError

from .config import settings
from .step_executor import StepExecutor

logger = structlog.get_logger()


class SQSHandler:
    """
    SQS Message Handler
    
    Responsibilities:
    - Long poll SQS for messages
    - Parse and validate messages
    - Execute steps via StepExecutor
    - Handle success/failure
    - Delete or retry messages
    """
    
    def __init__(self, step_executor: StepExecutor):
        """Initialize SQS handler"""
        self.step_executor = step_executor
        self.running = False
        self.sqs_client = None
        
        # Initialize SQS client
        if settings.SQS_QUEUE_URL:
            self.sqs_client = boto3.client('sqs', region_name=settings.AWS_REGION)
            logger.info(
                "sqs_handler_initialized",
                queue_url=settings.SQS_QUEUE_URL,
                worker_id=settings.WORKER_ID
            )
        else:
            logger.warning("sqs_queue_url_not_configured")
    
    async def start(self):
        """
        Start polling loop
        
        Runs continuously until stopped.
        Uses long polling to reduce empty responses.
        """
        if not self.sqs_client:
            logger.error("cannot_start_sqs_handler_not_configured")
            return
        
        self.running = True
        logger.info("sqs_polling_started", worker_id=settings.WORKER_ID)
        
        while self.running:
            try:
                # Poll for messages (long polling)
                messages = await self._receive_messages()
                
                if messages:
                    logger.info(
                        "sqs_messages_received",
                        count=len(messages),
                        worker_id=settings.WORKER_ID
                    )
                    
                    # Process messages concurrently
                    tasks = [
                        self._process_message(msg)
                        for msg in messages
                    ]
                    await asyncio.gather(*tasks, return_exceptions=True)
                else:
                    # No messages, brief pause before next poll
                    await asyncio.sleep(1)
                    
            except Exception as e:
                logger.error(
                    "sqs_polling_error",
                    error=str(e),
                    worker_id=settings.WORKER_ID,
                    exc_info=True
                )
                # Wait before retrying to avoid tight error loop
                await asyncio.sleep(settings.WORKER_POLL_INTERVAL)
    
    async def stop(self):
        """Stop polling loop"""
        logger.info("stopping_sqs_polling", worker_id=settings.WORKER_ID)
        self.running = False
    
    async def _receive_messages(self) -> List[Dict[str, Any]]:
        """
        Receive messages from SQS
        
        Uses long polling to wait up to 20 seconds for messages.
        This reduces empty responses and API costs.
        """
        try:
            response = await asyncio.to_thread(
                self.sqs_client.receive_message,
                QueueUrl=settings.SQS_QUEUE_URL,
                MaxNumberOfMessages=settings.SQS_MAX_MESSAGES,
                WaitTimeSeconds=settings.SQS_WAIT_TIME_SECONDS,
                VisibilityTimeout=settings.SQS_VISIBILITY_TIMEOUT,
                MessageAttributeNames=['All']
            )
            
            return response.get('Messages', [])
            
        except ClientError as e:
            logger.error("sqs_receive_error", error=str(e))
            return []
    
    async def _process_message(self, message: Dict[str, Any]):
        """
        Process a single SQS message
        
        Flow:
        1. Parse message body
        2. Validate required fields
        3. Execute step via StepExecutor
        4. If success → delete message
        5. If retryable failure → leave message (will be retried)
        6. If non-retryable → move to DLQ
        """
        receipt_handle = message['ReceiptHandle']
        message_id = message['MessageId']
        
        try:
            # Parse message body
            body = json.loads(message['Body'])
            
            logger.info(
                "processing_step_message",
                message_id=message_id,
                run_id=body.get('run_id'),
                step_name=body.get('step_name'),
                attempt=body.get('attempt', 1)
            )
            
            # Validate required fields
            required_fields = ['run_id', 'step_id', 'step_name', 'step_type']
            missing_fields = [f for f in required_fields if f not in body]
            
            if missing_fields:
                logger.error(
                    "invalid_message_missing_fields",
                    message_id=message_id,
                    missing=missing_fields
                )
                # Move to DLQ (non-retryable)
                await self._move_to_dlq(message, "Missing required fields")
                return
            
            # Execute step
            result = await self.step_executor.execute(
                run_id=body['run_id'],
                step_id=body['step_id'],
                step_name=body['step_name'],
                step_type=body['step_type'],
                step_config=body.get('step_config', {}),
                attempt=body.get('attempt', 1)
            )
            
            if result['success']:
                # Success - delete message
                logger.info(
                    "step_execution_succeeded",
                    message_id=message_id,
                    run_id=body['run_id'],
                    step_name=body['step_name']
                )
                await self._delete_message(receipt_handle)
            else:
                # Failed - check if should retry
                if result.get('retryable') and body.get('attempt', 1) < settings.STEP_MAX_RETRIES:
                    # Leave message in queue for automatic retry
                    logger.warning(
                        "step_execution_failed_will_retry",
                        message_id=message_id,
                        run_id=body['run_id'],
                        step_name=body['step_name'],
                        attempt=body.get('attempt', 1),
                        error=result.get('error')
                    )
                    # Message will become visible again after VisibilityTimeout
                else:
                    # Non-retryable or max retries reached
                    logger.error(
                        "step_execution_failed_permanently",
                        message_id=message_id,
                        run_id=body['run_id'],
                        step_name=body['step_name'],
                        error=result.get('error')
                    )
                    await self._move_to_dlq(message, result.get('error'))
            
        except json.JSONDecodeError as e:
            logger.error(
                "invalid_message_json",
                message_id=message_id,
                error=str(e)
            )
            await self._move_to_dlq(message, "Invalid JSON")
        
        except Exception as e:
            logger.error(
                "message_processing_error",
                message_id=message_id,
                error=str(e),
                exc_info=True
            )
            # Leave in queue for retry
    
    async def _delete_message(self, receipt_handle: str):
        """Delete message from queue after successful processing"""
        try:
            await asyncio.to_thread(
                self.sqs_client.delete_message,
                QueueUrl=settings.SQS_QUEUE_URL,
                ReceiptHandle=receipt_handle
            )
            logger.debug("message_deleted", receipt_handle=receipt_handle[:50])
        except ClientError as e:
            logger.error("message_delete_error", error=str(e))
    
    async def _move_to_dlq(self, message: Dict[str, Any], reason: str):
        """
        Move message to Dead Letter Queue
        
        For messages that can't be processed and shouldn't be retried.
        """
        if not settings.SQS_DLQ_URL:
            logger.warning("dlq_not_configured_deleting_message")
            await self._delete_message(message['ReceiptHandle'])
            return
        
        try:
            # Send to DLQ with error information
            body = json.loads(message['Body'])
            body['dlq_reason'] = reason
            body['original_message_id'] = message['MessageId']
            
            await asyncio.to_thread(
                self.sqs_client.send_message,
                QueueUrl=settings.SQS_DLQ_URL,
                MessageBody=json.dumps(body)
            )
            
            # Delete from main queue
            await self._delete_message(message['ReceiptHandle'])
            
            logger.info(
                "message_moved_to_dlq",
                message_id=message['MessageId'],
                reason=reason
            )
        except Exception as e:
            logger.error("dlq_move_error", error=str(e))