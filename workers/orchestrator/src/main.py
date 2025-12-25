"""
Orchestrator Worker - Main Application

Long-running worker that:
1. Polls SQS for step execution messages
2. Executes steps via StepExecutor
3. Updates database with results
4. Handles retries and failures

This runs as a background service, not a web server.
"""
import asyncio
import signal
import sys
import structlog

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
from .sqs_handler import SQSHandler
from .step_executor import StepExecutor
from .database import close_db

logger = structlog.get_logger()


class OrchestratorWorker:
    """
    Main worker process
    
    Manages:
    - SQS polling lifecycle
    - Graceful shutdown
    - Health monitoring
    """
    
    def __init__(self):
        """Initialize worker"""
        self.step_executor = StepExecutor()
        self.sqs_handler = SQSHandler(self.step_executor)
        self.shutdown_event = asyncio.Event()
        
        logger.info(
            "orchestrator_worker_initialized",
            worker_id=settings.WORKER_ID,
            version=settings.VERSION,
            environment=settings.ENVIRONMENT
        )
    
    async def start(self):
        """
        Start worker
        
        Begins SQS polling and waits for shutdown signal
        """
        logger.info("starting_orchestrator_worker", worker_id=settings.WORKER_ID)
        
        # Register signal handlers for graceful shutdown
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig,
                lambda: asyncio.create_task(self.shutdown())
            )
        
        # Start SQS polling in background
        polling_task = asyncio.create_task(self.sqs_handler.start())
        
        # Start health check in background
        health_task = asyncio.create_task(self._health_check_loop())
        
        # Wait for shutdown signal
        await self.shutdown_event.wait()
        
        # Stop polling
        await self.sqs_handler.stop()
        
        # Wait for tasks to complete
        polling_task.cancel()
        health_task.cancel()
        
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
        
        try:
            await health_task
        except asyncio.CancelledError:
            pass
        
        # Cleanup
        await close_db()
        
        logger.info("orchestrator_worker_stopped", worker_id=settings.WORKER_ID)
    
    async def shutdown(self):
        """Graceful shutdown"""
        logger.info("shutdown_signal_received", worker_id=settings.WORKER_ID)
        self.shutdown_event.set()
    
    async def _health_check_loop(self):
        """
        Periodic health check
        
        Logs worker status every minute
        """
        while True:
            try:
                await asyncio.sleep(settings.HEALTH_CHECK_INTERVAL)
                
                logger.info(
                    "worker_health_check",
                    worker_id=settings.WORKER_ID,
                    status="healthy"
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    "health_check_error",
                    error=str(e),
                    exc_info=True
                )


async def main():
    """Main entry point"""
    worker = OrchestratorWorker()
    
    try:
        await worker.start()
    except KeyboardInterrupt:
        logger.info("keyboard_interrupt_received")
    except Exception as e:
        logger.error(
            "worker_fatal_error",
            error=str(e),
            exc_info=True
        )
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())