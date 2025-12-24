"""
Pydantic Schemas for LLM Gateway

Defines request/response formats that are compatible with:
- OpenAI API format (industry standard)
- Anthropic API format
- Custom extensions for cost tracking
"""
from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any, Literal
from uuid import UUID
from enum import Enum


# ============================================================================
# Request Schemas
# ============================================================================

class Message(BaseModel):
    """Chat message in OpenAI format"""
    role: Literal["system", "user", "assistant", "function"]
    content: str
    name: Optional[str] = None
    function_call: Optional[Dict[str, Any]] = None


class LLMRequest(BaseModel):
    """
    Unified LLM request format
    
    Compatible with OpenAI Chat Completions API
    Extended with custom fields for tracking
    """
    # Required fields
    model: str = Field(..., description="Model identifier (e.g., gpt-4, claude-3-opus)")
    messages: List[Message] = Field(..., min_items=1)
    
    # Optional OpenAI-compatible fields
    temperature: float = Field(default=1.0, ge=0, le=2)
    max_tokens: Optional[int] = Field(default=None, ge=1)
    top_p: float = Field(default=1.0, ge=0, le=1)
    frequency_penalty: float = Field(default=0.0, ge=-2, le=2)
    presence_penalty: float = Field(default=0.0, ge=-2, le=2)
    stop: Optional[List[str]] = None
    
    # Function calling (OpenAI)
    functions: Optional[List[Dict[str, Any]]] = None
    function_call: Optional[str] = None
    
    # Custom fields for tracking
    tenant_id: UUID = Field(..., description="Tenant identifier for cost attribution")
    run_id: Optional[UUID] = Field(None, description="Associated run ID")
    step_id: Optional[UUID] = Field(None, description="Associated step ID")
    user_id: Optional[UUID] = Field(None, description="User who initiated request")
    
    # Override provider preference
    preferred_provider: Optional[str] = Field(None, description="Force specific provider")
    
    @validator('model')
    def validate_model(cls, v):
        """Ensure model is supported"""
        supported_models = [
            'gpt-4', 'gpt-4-turbo-preview', 'gpt-3.5-turbo',
            'claude-3-opus', 'claude-3-sonnet', 'claude-3-haiku'
        ]
        if v not in supported_models:
            # Allow anyway but log warning
            pass
        return v


class TokenUsage(BaseModel):
    """Token usage statistics"""
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class LLMResponse(BaseModel):
    """
    Unified LLM response format
    
    Returns OpenAI-compatible format with extensions
    """
    id: str = Field(..., description="Unique response ID")
    model: str
    provider: str = Field(..., description="Provider that fulfilled request")
    
    # Response content
    content: str = Field(..., description="Generated text")
    finish_reason: str = Field(..., description="Why generation stopped")
    
    # Token usage and costs
    usage: TokenUsage
    cost_usd: float = Field(..., description="Cost in USD")
    
    # Performance metrics
    latency_ms: int = Field(..., description="Total request time")
    
    # Failover tracking
    is_fallback: bool = Field(default=False, description="Was fallback provider used?")
    attempted_providers: List[str] = Field(default_factory=list)
    
    # Function calling result (if applicable)
    function_call: Optional[Dict[str, Any]] = None


# ============================================================================
# Health & Status
# ============================================================================

class ProviderHealth(BaseModel):
    """Health status of a provider"""
    provider: str
    status: Literal["healthy", "degraded", "unavailable"]
    circuit_breaker_state: str
    recent_error_rate: float
    avg_latency_ms: float
    last_success: Optional[str] = None
    last_failure: Optional[str] = None


class GatewayHealth(BaseModel):
    """Overall gateway health"""
    status: str
    version: str
    providers: List[ProviderHealth]
    cache_hit_rate: float
    requests_last_minute: int


# ============================================================================
# Budget & Rate Limiting
# ============================================================================

class BudgetStatus(BaseModel):
    """Tenant budget status"""
    tenant_id: UUID
    budget_monthly: int
    used_current_month: int
    remaining: int
    percentage_used: float
    is_exceeded: bool
    soft_limit_reached: bool


class RateLimitInfo(BaseModel):
    """Rate limit information"""
    tenant_id: UUID
    limit_per_minute: int
    current_usage: int
    remaining: int
    reset_at: str
    is_limited: bool


# ============================================================================
# Cost Estimation
# ============================================================================

class CostEstimate(BaseModel):
    """Estimated cost for a request"""
    model: str
    estimated_prompt_tokens: int
    estimated_completion_tokens: int
    estimated_cost_usd: float
    provider: str