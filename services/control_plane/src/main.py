"""
Control Plane API - Main Application Entry Point

This is the central management API for the AI Agent Platform.
It handles:
- Task definitions (workflow templates)
- Run management (task executions)  
- Metrics and monitoring
- User authentication

Architecture:
    Client → API Gateway → Control Plane → [RDS, Step Functions, SQS]
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app
from sqlalchemy.ext.asyncio import AsyncSession
import structlog
import sys

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
from .database import engine, Base, get_db
from .routers import health
from .middleware import request_id_middleware, logging_middleware

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager
    
    Startup:
    - Initialize database tables
    - Log configuration
    
    Shutdown:
    - Close database connections
    - Cleanup resources
    """
    logger.info(
        "starting_control_plane_api",
        version=settings.VERSION,
        environment=settings.ENVIRONMENT
    )
    
    # Create database tables if they don't exist
    # Note: In production, use Alembic migrations instead
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    logger.info("database_initialized")
    
    yield
    
    logger.info("shutting_down_control_plane_api")
    await engine.dispose()


# Initialize FastAPI application
app = FastAPI(
    title="AI Agent Platform - Control Plane API",
    description="Central management API for AI agent orchestration",
    version=settings.VERSION,
    lifespan=lifespan,
    docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
    redoc_url="/redoc" if settings.ENVIRONMENT != "production" else None,
)


# ============================================================================
# Middleware Configuration
# ============================================================================

# CORS - Allow web clients to access API
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Custom middleware for request tracking and logging
app.middleware("http")(request_id_middleware)
app.middleware("http")(logging_middleware)


# ============================================================================
# Exception Handlers
# ============================================================================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Global exception handler - catch all unhandled errors
    
    This ensures we always return a proper JSON response even when
    something unexpected happens, and we log it for debugging.
    """
    logger.error(
        "unhandled_exception",
        path=request.url.path,
        method=request.method,
        error=str(exc),
        error_type=type(exc).__name__,
        exc_info=True
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "Internal server error",
            "message": str(exc) if settings.DEBUG else "An unexpected error occurred",
            "request_id": request.state.request_id if hasattr(request.state, "request_id") else None
        }
    )


# ============================================================================
# Metrics Endpoint
# ============================================================================

# Mount Prometheus metrics at /metrics
# This is scraped by Prometheus for monitoring
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)


# ============================================================================
# Route Imports (lazy loading to avoid circular imports)
# ============================================================================

# Import routers here to avoid circular import issues
from .routers import tasks, runs, metrics

# Include all routers
app.include_router(health.router, prefix="/health", tags=["Health"])
app.include_router(tasks.router, prefix="/api/v1/tasks", tags=["Tasks"])
app.include_router(runs.router, prefix="/api/v1/runs", tags=["Runs"])
app.include_router(metrics.router, prefix="/api/v1/metrics", tags=["Metrics"])


# ============================================================================
# Root Endpoint
# ============================================================================

@app.get("/")
async def root():
    """
    Root endpoint - basic service info
    
    Returns service name, version, and status.
    Useful for:
    - Health checks (load balancer)
    - Version verification
    - Quick service identification
    """
    return {
        "service": "control-plane-api",
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT,
        "status": "operational",
        "docs_url": "/docs" if settings.ENVIRONMENT != "production" else None
    }


@app.get("/api/v1/info")
async def api_info():
    """
    API information endpoint
    
    Returns detailed API configuration and available endpoints.
    """
    return {
        "version": "v1",
        "endpoints": {
            "tasks": "/api/v1/tasks",
            "runs": "/api/v1/runs",
            "metrics": "/api/v1/metrics",
            "health": "/health"
        },
        "features": {
            "authentication": "JWT",
            "rate_limiting": settings.RATE_LIMIT_ENABLED,
            "metrics": settings.ENABLE_METRICS,
            "tracing": settings.ENABLE_TRACING
        }
    }


# ============================================================================
# Development Server
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    
    # Run development server
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.ENVIRONMENT == "development",
        log_level=settings.LOG_LEVEL.lower()
    )