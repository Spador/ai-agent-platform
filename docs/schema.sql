-- AI Agent Platform Database Schema
-- PostgreSQL 15+

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- TENANTS & USERS
-- ============================================================================

CREATE TABLE tenants (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    token_budget_monthly INTEGER NOT NULL DEFAULT 1000000,
    token_used_current_month INTEGER NOT NULL DEFAULT 0,
    rate_limit_per_minute INTEGER NOT NULL DEFAULT 100,
    status VARCHAR(50) NOT NULL DEFAULT 'active',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_tenant_status CHECK (status IN ('active', 'suspended', 'deleted'))
);

CREATE INDEX idx_tenants_status ON tenants(status);

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email VARCHAR(255) NOT NULL UNIQUE,
    name VARCHAR(255),
    role VARCHAR(50) NOT NULL DEFAULT 'member',
    token_budget_monthly INTEGER,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_user_role CHECK (role IN ('admin', 'member', 'viewer'))
);

CREATE INDEX idx_users_tenant ON users(tenant_id);
CREATE INDEX idx_users_email ON users(email);

-- ============================================================================
-- TASKS & RUNS
-- ============================================================================

CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    created_by UUID NOT NULL REFERENCES users(id),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    task_config JSONB NOT NULL, -- LangGraph configuration, steps, tools
    default_token_budget INTEGER DEFAULT 10000,
    timeout_seconds INTEGER DEFAULT 3600,
    max_retries INTEGER DEFAULT 3,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_task_budget CHECK (default_token_budget > 0),
    CONSTRAINT chk_task_timeout CHECK (timeout_seconds > 0)
);

CREATE INDEX idx_tasks_tenant ON tasks(tenant_id);
CREATE INDEX idx_tasks_active ON tasks(is_active) WHERE is_active = true;

CREATE TABLE runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id UUID NOT NULL REFERENCES tasks(id),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    created_by UUID NOT NULL REFERENCES users(id),
    
    -- Status tracking
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    error_message TEXT,
    
    -- Execution metadata
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    duration_seconds INTEGER,
    
    -- Cost tracking
    token_budget INTEGER NOT NULL,
    tokens_used INTEGER DEFAULT 0,
    estimated_cost_usd DECIMAL(10, 6) DEFAULT 0,
    
    -- State machine
    state_machine_execution_arn VARCHAR(500),
    current_step VARCHAR(255),
    
    -- Artifacts
    artifacts_s3_key VARCHAR(500),
    result_summary JSONB,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_run_status CHECK (status IN (
        'pending', 'running', 'completed', 'failed', 'cancelled', 'budget_exceeded', 'timeout'
    )),
    CONSTRAINT chk_run_tokens CHECK (tokens_used <= token_budget)
);

CREATE INDEX idx_runs_task ON runs(task_id);
CREATE INDEX idx_runs_tenant ON runs(tenant_id);
CREATE INDEX idx_runs_status ON runs(status);
CREATE INDEX idx_runs_created_at ON runs(created_at DESC);
CREATE INDEX idx_runs_tenant_status ON runs(tenant_id, status);

-- ============================================================================
-- STEPS
-- ============================================================================

CREATE TABLE steps (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    
    -- Step identification
    step_name VARCHAR(255) NOT NULL,
    step_type VARCHAR(100) NOT NULL, -- 'llm_call', 'tool_execution', 'decision', 'parallel'
    step_order INTEGER NOT NULL,
    
    -- Status tracking
    status VARCHAR(50) NOT NULL DEFAULT 'queued',
    attempt_number INTEGER DEFAULT 1,
    max_attempts INTEGER DEFAULT 3,
    
    -- Timing
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    duration_seconds INTEGER,
    
    -- Input/Output
    input_data JSONB,
    output_data JSONB,
    error_message TEXT,
    
    -- Cost tracking
    tokens_used INTEGER DEFAULT 0,
    cost_usd DECIMAL(10, 6) DEFAULT 0,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_step_status CHECK (status IN (
        'queued', 'running', 'success', 'failed', 'retrying', 'cancelled'
    )),
    CONSTRAINT chk_step_attempts CHECK (attempt_number <= max_attempts)
);

CREATE INDEX idx_steps_run ON steps(run_id);
CREATE INDEX idx_steps_status ON steps(status);
CREATE INDEX idx_steps_run_order ON steps(run_id, step_order);

-- ============================================================================
-- LLM EVENTS (Detailed cost tracking)
-- ============================================================================

CREATE TABLE llm_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id UUID REFERENCES runs(id) ON DELETE CASCADE,
    step_id UUID REFERENCES steps(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    
    -- Provider details
    provider VARCHAR(100) NOT NULL, -- 'openai', 'anthropic', 'local'
    model VARCHAR(100) NOT NULL,
    
    -- Request/Response
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    total_tokens INTEGER NOT NULL,
    
    -- Cost
    cost_per_1k_prompt_tokens DECIMAL(10, 6),
    cost_per_1k_completion_tokens DECIMAL(10, 6),
    total_cost_usd DECIMAL(10, 6),
    
    -- Performance
    latency_ms INTEGER,
    
    -- Status
    status VARCHAR(50) NOT NULL, -- 'success', 'failed', 'rate_limited', 'budget_exceeded'
    error_message TEXT,
    
    -- Failover tracking
    is_fallback BOOLEAN DEFAULT false,
    previous_provider VARCHAR(100),
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_llm_status CHECK (status IN (
        'success', 'failed', 'rate_limited', 'budget_exceeded', 'timeout'
    ))
);

CREATE INDEX idx_llm_events_run ON llm_events(run_id);
CREATE INDEX idx_llm_events_tenant ON llm_events(tenant_id);
CREATE INDEX idx_llm_events_provider ON llm_events(provider);
CREATE INDEX idx_llm_events_created_at ON llm_events(created_at DESC);
CREATE INDEX idx_llm_events_tenant_date ON llm_events(tenant_id, created_at);

-- ============================================================================
-- TOOL EVENTS (Tool execution audit trail)
-- ============================================================================

CREATE TABLE tool_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id UUID REFERENCES runs(id) ON DELETE CASCADE,
    step_id UUID REFERENCES steps(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    
    -- Tool details
    tool_name VARCHAR(100) NOT NULL, -- 'browser', 'code_executor', 'api_caller'
    tool_action VARCHAR(255) NOT NULL,
    
    -- Execution
    input_params JSONB,
    output_data JSONB,
    artifacts_s3_key VARCHAR(500), -- Screenshots, downloaded files, etc.
    
    -- Performance
    duration_seconds INTEGER,
    status VARCHAR(50) NOT NULL,
    error_message TEXT,
    
    -- Resource usage
    ecs_task_arn VARCHAR(500),
    cpu_utilization_percent DECIMAL(5, 2),
    memory_utilization_mb INTEGER,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_tool_status CHECK (status IN (
        'success', 'failed', 'timeout', 'cancelled'
    ))
);

CREATE INDEX idx_tool_events_run ON tool_events(run_id);
CREATE INDEX idx_tool_events_tenant ON tool_events(tenant_id);
CREATE INDEX idx_tool_events_tool ON tool_events(tool_name);
CREATE INDEX idx_tool_events_created_at ON tool_events(created_at DESC);

-- ============================================================================
-- RATE LIMITING & CIRCUIT BREAKER STATE
-- ============================================================================

CREATE TABLE rate_limit_state (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    user_id UUID REFERENCES users(id),
    
    window_start TIMESTAMP WITH TIME ZONE NOT NULL,
    window_end TIMESTAMP WITH TIME ZONE NOT NULL,
    request_count INTEGER DEFAULT 0,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(tenant_id, window_start)
);

CREATE INDEX idx_rate_limit_tenant_window ON rate_limit_state(tenant_id, window_start);

CREATE TABLE circuit_breaker_state (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    provider VARCHAR(100) NOT NULL UNIQUE,
    state VARCHAR(50) NOT NULL DEFAULT 'closed',
    failure_count INTEGER DEFAULT 0,
    last_failure_at TIMESTAMP WITH TIME ZONE,
    last_success_at TIMESTAMP WITH TIME ZONE,
    opened_at TIMESTAMP WITH TIME ZONE,
    half_open_at TIMESTAMP WITH TIME ZONE,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_cb_state CHECK (state IN ('closed', 'open', 'half_open'))
);

-- ============================================================================
-- AUDIT LOG
-- ============================================================================

CREATE TABLE audit_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES tenants(id),
    user_id UUID REFERENCES users(id),
    
    action VARCHAR(255) NOT NULL,
    resource_type VARCHAR(100) NOT NULL,
    resource_id UUID,
    
    changes JSONB,
    ip_address INET,
    user_agent TEXT,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_audit_tenant ON audit_log(tenant_id);
CREATE INDEX idx_audit_user ON audit_log(user_id);
CREATE INDEX idx_audit_created_at ON audit_log(created_at DESC);

-- ============================================================================
-- VIEWS FOR ANALYTICS
-- ============================================================================

-- Run summary view
CREATE VIEW run_summary AS
SELECT 
    r.id as run_id,
    r.tenant_id,
    t.name as task_name,
    r.status,
    r.tokens_used,
    r.token_budget,
    r.estimated_cost_usd,
    r.duration_seconds,
    COUNT(s.id) as total_steps,
    COUNT(CASE WHEN s.status = 'success' THEN 1 END) as successful_steps,
    COUNT(CASE WHEN s.status = 'failed' THEN 1 END) as failed_steps,
    r.created_at,
    r.completed_at
FROM runs r
JOIN tasks t ON r.task_id = t.id
LEFT JOIN steps s ON s.run_id = r.id
GROUP BY r.id, r.tenant_id, t.name, r.status, r.tokens_used, 
         r.token_budget, r.estimated_cost_usd, r.duration_seconds,
         r.created_at, r.completed_at;

-- Tenant cost summary view
CREATE VIEW tenant_cost_summary AS
SELECT 
    t.id as tenant_id,
    t.name as tenant_name,
    t.token_budget_monthly,
    t.token_used_current_month,
    COUNT(DISTINCT r.id) as total_runs,
    SUM(r.estimated_cost_usd) as total_cost_usd,
    SUM(r.tokens_used) as total_tokens_used,
    AVG(r.duration_seconds) as avg_run_duration_seconds
FROM tenants t
LEFT JOIN runs r ON r.tenant_id = t.id 
    AND r.created_at >= date_trunc('month', CURRENT_TIMESTAMP)
GROUP BY t.id, t.name, t.token_budget_monthly, t.token_used_current_month;

-- Provider performance view
CREATE VIEW provider_performance AS
SELECT 
    provider,
    model,
    DATE_TRUNC('hour', created_at) as hour,
    COUNT(*) as request_count,
    COUNT(CASE WHEN status = 'success' THEN 1 END) as success_count,
    COUNT(CASE WHEN status = 'failed' THEN 1 END) as failure_count,
    AVG(latency_ms) as avg_latency_ms,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms) as p95_latency_ms,
    SUM(total_cost_usd) as total_cost_usd
FROM llm_events
GROUP BY provider, model, DATE_TRUNC('hour', created_at);

-- ============================================================================
-- FUNCTIONS
-- ============================================================================

-- Update tenant token usage
CREATE OR REPLACE FUNCTION update_tenant_token_usage()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.tokens_used > OLD.tokens_used THEN
        UPDATE tenants 
        SET token_used_current_month = token_used_current_month + (NEW.tokens_used - OLD.tokens_used),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = NEW.tenant_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_tenant_tokens
AFTER UPDATE OF tokens_used ON runs
FOR EACH ROW
EXECUTE FUNCTION update_tenant_token_usage();

-- Update run duration on completion
CREATE OR REPLACE FUNCTION update_run_duration()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.completed_at IS NOT NULL AND OLD.completed_at IS NULL THEN
        NEW.duration_seconds := EXTRACT(EPOCH FROM (NEW.completed_at - NEW.started_at));
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_run_duration
BEFORE UPDATE OF completed_at ON runs
FOR EACH ROW
EXECUTE FUNCTION update_run_duration();

-- Update step duration on completion
CREATE OR REPLACE FUNCTION update_step_duration()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.completed_at IS NOT NULL AND OLD.completed_at IS NULL THEN
        NEW.duration_seconds := EXTRACT(EPOCH FROM (NEW.completed_at - NEW.started_at));
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_step_duration
BEFORE UPDATE OF completed_at ON steps
FOR EACH ROW
EXECUTE FUNCTION update_step_duration();

-- Reset monthly token usage (run via cron)
CREATE OR REPLACE FUNCTION reset_monthly_token_usage()
RETURNS void AS $$
BEGIN
    UPDATE tenants 
    SET token_used_current_month = 0,
        updated_at = CURRENT_TIMESTAMP;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- SEED DATA (for development)
-- ============================================================================

-- Insert demo tenant
INSERT INTO tenants (id, name, token_budget_monthly, rate_limit_per_minute)
VALUES 
    ('00000000-0000-0000-0000-000000000001', 'Demo Tenant', 5000000, 200);

-- Insert demo user
INSERT INTO users (id, tenant_id, email, name, role)
VALUES 
    ('00000000-0000-0000-0000-000000000002', 
     '00000000-0000-0000-0000-000000000001',
     'demo@example.com', 
     'Demo User', 
     'admin');

-- Initialize circuit breaker states
INSERT INTO circuit_breaker_state (provider, state)
VALUES 
    ('openai', 'closed'),
    ('anthropic', 'closed'),
    ('local', 'closed');

-- ============================================================================
-- MATERIALIZED VIEWS (for dashboard performance)
-- ============================================================================

CREATE MATERIALIZED VIEW mv_daily_metrics AS
SELECT 
    DATE_TRUNC('day', r.created_at) as date,
    r.tenant_id,
    COUNT(DISTINCT r.id) as total_runs,
    COUNT(DISTINCT CASE WHEN r.status = 'completed' THEN r.id END) as completed_runs,
    COUNT(DISTINCT CASE WHEN r.status = 'failed' THEN r.id END) as failed_runs,
    SUM(r.tokens_used) as total_tokens,
    SUM(r.estimated_cost_usd) as total_cost_usd,
    AVG(r.duration_seconds) as avg_duration_seconds
FROM runs r
GROUP BY DATE_TRUNC('day', r.created_at), r.tenant_id;

CREATE INDEX idx_mv_daily_metrics_date ON mv_daily_metrics(date);
CREATE INDEX idx_mv_daily_metrics_tenant ON mv_daily_metrics(tenant_id);

-- Refresh function (call from cron daily)
CREATE OR REPLACE FUNCTION refresh_daily_metrics()
RETURNS void AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_daily_metrics;
END;
$$ LANGUAGE plpgsql;