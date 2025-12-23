"""
SQLAlchemy Database Models

These models map directly to the PostgreSQL tables defined in docs/schema.sql
Each model represents a table and its relationships.
"""
from sqlalchemy import Column, String, Integer, Boolean, DateTime, ForeignKey, Text, DECIMAL, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from .database import Base


class Tenant(Base):
    """
    Tenant model for multi-tenancy support
    
    Each tenant is an isolated organization with their own:
    - Users
    - Tasks
    - Runs
    - Token budget
    - Rate limits
    """
    __tablename__ = "tenants"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    token_budget_monthly = Column(Integer, nullable=False, default=1000000)
    token_used_current_month = Column(Integer, nullable=False, default=0)
    rate_limit_per_minute = Column(Integer, nullable=False, default=100)
    status = Column(String(50), nullable=False, default='active')
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    users = relationship("User", back_populates="tenant", cascade="all, delete-orphan")
    tasks = relationship("Task", back_populates="tenant", cascade="all, delete-orphan")
    runs = relationship("Run", back_populates="tenant", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Tenant(id={self.id}, name={self.name})>"


class User(Base):
    """
    User model - belongs to a tenant
    
    Users can:
    - Create tasks
    - Start runs
    - Have individual token budgets (optional)
    - Have different roles (admin, member, viewer)
    """
    __tablename__ = "users"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    name = Column(String(255))
    role = Column(String(50), nullable=False, default='member')
    token_budget_monthly = Column(Integer)  # Optional: override tenant budget
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    tenant = relationship("Tenant", back_populates="users")
    created_tasks = relationship("Task", foreign_keys="Task.created_by", back_populates="creator")
    created_runs = relationship("Run", foreign_keys="Run.created_by", back_populates="creator")
    
    def __repr__(self):
        return f"<User(id={self.id}, email={self.email})>"


class Task(Base):
    """
    Task model - reusable workflow template
    
    A task defines:
    - The agent workflow (LangGraph config)
    - Default token budget
    - Timeout and retry settings
    - Tools and models to use
    
    Tasks are created once and can be run multiple times.
    """
    __tablename__ = "tasks"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    task_config = Column(JSON, nullable=False)  # LangGraph workflow definition
    default_token_budget = Column(Integer, default=10000)
    timeout_seconds = Column(Integer, default=3600)
    max_retries = Column(Integer, default=3)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    tenant = relationship("Tenant", back_populates="tasks")
    creator = relationship("User", foreign_keys=[created_by], back_populates="created_tasks")
    runs = relationship("Run", back_populates="task", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Task(id={self.id}, name={self.name})>"


class Run(Base):
    """
    Run model - a single execution of a task
    
    Tracks:
    - Execution status and timing
    - Token usage and costs
    - Step-by-step progress
    - Results and artifacts
    """
    __tablename__ = "runs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=False)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    
    # Status tracking
    status = Column(String(50), nullable=False, default='pending')
    error_message = Column(Text)
    
    # Timing
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    duration_seconds = Column(Integer)
    
    # Cost tracking
    token_budget = Column(Integer, nullable=False)
    tokens_used = Column(Integer, default=0)
    estimated_cost_usd = Column(DECIMAL(10, 6), default=0)
    
    # AWS Step Functions integration
    state_machine_execution_arn = Column(String(500))
    current_step = Column(String(255))
    
    # Results
    artifacts_s3_key = Column(String(500))
    result_summary = Column(JSON)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    tenant = relationship("Tenant", back_populates="runs")
    task = relationship("Task", back_populates="runs")
    creator = relationship("User", foreign_keys=[created_by], back_populates="created_runs")
    steps = relationship("Step", back_populates="run", cascade="all, delete-orphan", order_by="Step.step_order")
    
    def __repr__(self):
        return f"<Run(id={self.id}, status={self.status})>"


class Step(Base):
    """
    Step model - individual steps within a run
    
    Each step represents:
    - An LLM call
    - A tool execution
    - A decision point
    - A parallel operation
    """
    __tablename__ = "steps"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    
    # Step identification
    step_name = Column(String(255), nullable=False)
    step_type = Column(String(100), nullable=False)
    step_order = Column(Integer, nullable=False)
    
    # Status and retries
    status = Column(String(50), nullable=False, default='queued')
    attempt_number = Column(Integer, default=1)
    max_attempts = Column(Integer, default=3)
    
    # Timing
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    duration_seconds = Column(Integer)
    
    # Input/Output
    input_data = Column(JSON)
    output_data = Column(JSON)
    error_message = Column(Text)
    
    # Cost tracking
    tokens_used = Column(Integer, default=0)
    cost_usd = Column(DECIMAL(10, 6), default=0)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    run = relationship("Run", back_populates="steps")
    
    def __repr__(self):
        return f"<Step(id={self.id}, name={self.step_name}, status={self.status})>"


class LLMEvent(Base):
    """
    LLM Event model - detailed tracking of every LLM API call
    
    Critical for:
    - Cost attribution
    - Performance monitoring
    - Debugging
    - Audit trails
    """
    __tablename__ = "llm_events"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"))
    step_id = Column(UUID(as_uuid=True), ForeignKey("steps.id", ondelete="CASCADE"))
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    
    # Provider details
    provider = Column(String(100), nullable=False)  # openai, anthropic, local
    model = Column(String(100), nullable=False)
    
    # Token usage
    prompt_tokens = Column(Integer, nullable=False)
    completion_tokens = Column(Integer, nullable=False)
    total_tokens = Column(Integer, nullable=False)
    
    # Cost calculation
    cost_per_1k_prompt_tokens = Column(DECIMAL(10, 6))
    cost_per_1k_completion_tokens = Column(DECIMAL(10, 6))
    total_cost_usd = Column(DECIMAL(10, 6))
    
    # Performance
    latency_ms = Column(Integer)
    
    # Status
    status = Column(String(50), nullable=False)
    error_message = Column(Text)
    
    # Failover tracking
    is_fallback = Column(Boolean, default=False)
    previous_provider = Column(String(100))
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f"<LLMEvent(provider={self.provider}, tokens={self.total_tokens})>"


class ToolEvent(Base):
    """
    Tool Event model - tracking of tool executions
    
    Tools include:
    - Browser automation (Playwright)
    - Code execution
    - API calls
    - File operations
    """
    __tablename__ = "tool_events"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"))
    step_id = Column(UUID(as_uuid=True), ForeignKey("steps.id", ondelete="CASCADE"))
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    
    # Tool details
    tool_name = Column(String(100), nullable=False)
    tool_action = Column(String(255), nullable=False)
    
    # Execution data
    input_params = Column(JSON)
    output_data = Column(JSON)
    artifacts_s3_key = Column(String(500))
    
    # Performance
    duration_seconds = Column(Integer)
    status = Column(String(50), nullable=False)
    error_message = Column(Text)
    
    # Resource usage
    ecs_task_arn = Column(String(500))
    cpu_utilization_percent = Column(DECIMAL(5, 2))
    memory_utilization_mb = Column(Integer)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f"<ToolEvent(tool={self.tool_name}, status={self.status})>"