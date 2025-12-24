"""
Database Connection for LLM Gateway

Used for:
- Logging LLM events (cost tracking)
- Checking tenant token budgets
- Reading tenant rate limits
"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from typing import AsyncGenerator
import structlog

from .config import settings

logger = structlog.get_logger()

# Create async engine
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_pre_ping=True,
    pool_recycle=3600,
    future=True
)

# Create session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

# Base for models (we'll import from control plane)
Base = declarative_base()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency to get database session"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error("database_error", error=str(e), exc_info=True)
            raise
        finally:
            await session.close()


async def close_db():
    """Close database connections"""
    await engine.dispose()
    logger.info("database_closed")