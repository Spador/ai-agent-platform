# Architecture Deep Dive

## System Overview

The AI Agent Platform is built as a microservices architecture running on AWS ECS Fargate, orchestrated through Step Functions, with comprehensive observability and cost controls.

## Design Principles

1. **Reliability First**: Circuit breakers, retries, timeouts at every layer
2. **Cost Governance**: Token budgets enforced before execution
3. **Multi-Tenancy**: Complete isolation between tenants
4. **Observability**: Every operation emits metrics and structured logs
5. **Scalability**: Auto-scaling based on queue depth and resource utilization

## Service Architecture

### 1. Control Plane API

**Purpose**: Central management API for tasks, runs, and metadata

**Technology**: 
- FastAPI (Python 3.11+)
- Pydantic for validation
- SQLAlchemy ORM
- PostgreSQL connection pooling

**Responsibilities**:
- User/tenant management
- Task definition CRUD
- Run lifecycle management (create, start, stop, query)
- Metrics aggregation and reporting
- Authentication/authorization

**Key Endpoints**:
```
POST   /api/v1/tasks                    # Create task definition
GET    /api/v1/tasks/{id}               # Get task details
POST   /api/v1/runs                     # Start new run
GET    /api/v1/runs/{id}                # Get run status
GET    /api/v1/runs/{id}/steps          # Get step timeline
GET    /api/v1/runs/{id}/metrics        # Get cost/token summary
DELETE /api/v1/runs/{id}                # Cancel running task
```

**Scaling**: 
- Horizontal auto-scaling (2-10 tasks)
- Target CPU: 70%
- Health check: `/health`

---

### 2. LLM Gateway

**Purpose**: Intelligent routing layer for LLM requests with governance

**Technology**:
- FastAPI
- Redis for rate limiting
- Circuit breaker pattern (pybreaker)
- Provider SDKs (OpenAI, Anthropic)

**Responsibilities**:
- Token budget tracking and enforcement
- Rate limiting per tenant/user
- Provider health monitoring
- Automatic failover (OpenAI → Anthropic → Local)
- Request/response normalization
- Cost event emission

**Request Flow**:
```
1. Receive LLM request from Orchestrator
2. Check tenant token budget (Redis/RDS)
3. Apply rate limits (sliding window in Redis)
4. Check circuit breaker state for providers
5. Route to primary provider (OpenAI)
6. On failure → retry with backoff
7. On persistent failure → failover to secondary (Anthropic)
8. On all failures → return error + emit alert
9. Log cost event to database
10. Update token usage counter
```

**Provider Priority**:
1. OpenAI GPT-4 (default)
2. Anthropic Claude 3
3. Local LLaMA (fallback for non-critical)

**Rate Limiting**:
- Per-tenant: 100 req/min
- Per-user: 20 req/min
- Sliding window algorithm

---

### 3. Orchestrator Worker

**Purpose**: Execute agent workflows by processing steps from SQS

**Technology**:
- LangGraph for agent orchestration
- CrewAI for multi-agent coordination
- SQS long polling
- Async processing

**Responsibilities**:
- Poll SQS for step messages
- Load run state from RDS
- Execute LangGraph agent step
- Call LLM Gateway for completions
- Call Tool Runtime for tool execution
- Update step status in RDS
- Emit progress events
- Handle retries on transient failures

**Message Processing Loop**:
```python
while True:
    messages = sqs.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=10,
        WaitTimeSeconds=20  # Long polling
    )
    
    for msg in messages:
        step_data = json.loads(msg['Body'])
        
        try:
            result = execute_step(
                run_id=step_data['run_id'],
                step_name=step_data['step_name'],
                attempt=step_data['attempt']
            )
            
            update_step_status(step_id, 'SUCCESS', result)
            sqs.delete_message(msg)
            
        except RetryableError as e:
            if step_data['attempt'] < MAX_RETRIES:
                # Re-enqueue with backoff
                sqs.send_message(
                    MessageBody=json.dumps({
                        **step_data,
                        'attempt': step_data['attempt'] + 1
                    }),
                    DelaySeconds=exponential_backoff(step_data['attempt'])
                )
            else:
                update_step_status(step_id, 'FAILED', str(e))
                
            sqs.delete_message(msg)
```

**Scaling**:
- Auto-scale based on SQS `ApproximateNumberOfMessagesVisible`
- Scale out: queue depth > 100
- Scale in: queue depth < 10
- Min tasks: 1, Max tasks: 20

---

### 4. Tool Runtime

**Purpose**: Isolated execution environment for tools (browser, code)

**Technology**:
- ECS Fargate (one-off tasks)
- Playwright for browser automation
- Sandboxed Python/Node.js execution
- S3 for artifact storage

**Responsibilities**:
- Execute browser automation scripts
- Run untrusted code in sandbox
- Enforce strict timeouts (60s default)
- Network egress allowlist
- Capture screenshots/artifacts
- Upload results to S3

**Security Measures**:
- No internet access except allowlist
- Read-only filesystem
- Resource limits (1 vCPU, 2GB RAM)
- No privileged containers
- Separate security group

**Tool Types**:
1. **Browser Tool**: Playwright-based web automation
2. **Code Executor**: Python/Node sandbox
3. **API Caller**: HTTP client with retry logic

---

## Data Flow

### Creating and Running a Task

```
┌──────┐      ┌────────────────┐      ┌──────────────┐
│Client│      │Control Plane   │      │Step Functions│
└───┬──┘      └───────┬────────┘      └──────┬───────┘
    │                 │                      │
    │ POST /tasks     │                      │
    ├────────────────►│                      │
    │                 │                      │
    │ Task Created    │                      │
    │◄────────────────┤                      │
    │                 │                      │
    │ POST /runs      │                      │
    ├────────────────►│                      │
    │                 │                      │
    │                 │ StartExecution       │
    │                 ├─────────────────────►│
    │                 │                      │
    │ Run Started     │                      │
    │◄────────────────┤                      │
    │                 │                      │
    │                 │         ┌────────────┤ InitRun
    │                 │         │            │
    │                 │         └───────────►│ EnqueueStep
    │                 │                      │
    │                 │                      │ Send to SQS
    │                 │                      ├─────────┐
    │                 │                      │         │
    │                 │                      │◄────────┘
    │                 │                      │
    │                 │         ┌────────────┤ WaitForCompletion
    │                 │         │            │
    │ GET /runs/{id}  │         │            │
    ├────────────────►│         │            │
    │                 │         │            │
    │ Status: RUNNING │         │            │
    │◄────────────────┤         │            │
    │                 │         │            │
    │                 │         └───────────►│ Branch
    │                 │                      │
    │                 │         ┌────────────┤ NextStep/Finalize
    │                 │         │            │
    │                 │         └───────────►│
    │                 │                      │
    │ GET /runs/{id}  │                      │
    ├────────────────►│                      │
    │                 │                      │
    │ Status:COMPLETED│                      │
    │◄────────────────┤                      │
```

### Step Execution Flow

```
┌──────────────┐    ┌─────────────┐    ┌──────────┐    ┌────────────┐
│Step Functions│    │SQS Queue    │    │Orchestr. │    │LLM Gateway │
└──────┬───────┘    └──────┬──────┘    └────┬─────┘    └─────┬──────┘
       │                   │                 │                │
       │ EnqueueStep       │                 │                │
       ├──────────────────►│                 │                │
       │                   │                 │                │
       │                   │ Poll (long)     │                │
       │                   │◄────────────────┤                │
       │                   │                 │                │
       │                   │ Message         │                │
       │                   ├────────────────►│                │
       │                   │                 │                │
       │                   │                 │ Check Budget   │
       │                   │                 ├───────────────►│
       │                   │                 │                │
       │                   │                 │ Budget OK      │
       │                   │                 │◄───────────────┤
       │                   │                 │                │
       │                   │                 │ LLM Call       │
       │                   │                 ├───────────────►│
       │                   │                 │                │
       │                   │                 │ Response       │
       │                   │                 │◄───────────────┤
       │                   │                 │                │
       │                   │                 │ Update State   │
       │                   │                 ├────────┐       │
       │                   │                 │        │       │
       │                   │                 │◄───────┘       │
       │                   │                 │                │
       │                   │ Delete Message  │                │
       │                   │◄────────────────┤                │
       │                   │                 │                │
       │ Poll Step Status  │                 │                │
       │◄──────────────────┼─────────────────┤                │
       │                   │                 │                │
       │ Step SUCCESS      │                 │                │
       ├───────────────────┼────────────────►│                │
```

---

## State Management

### Run States

```
PENDING → RUNNING → COMPLETED
                 ↓
              FAILED
                 ↓
         BUDGET_EXCEEDED
```

### Step States

```
QUEUED → RUNNING → SUCCESS
              ↓
           RETRYING (attempt < max)
              ↓
           FAILED (attempt >= max)
```

---

## Database Schema

See [schema.sql](schema.sql) for complete DDL.

**Key Tables**:
- `tenants`: Multi-tenant isolation
- `users`: User accounts linked to tenants
- `tasks`: Task definitions (reusable templates)
- `runs`: Task execution instances
- `steps`: Individual step executions within runs
- `llm_events`: Every LLM call logged for cost tracking
- `tool_events`: Tool execution audit trail

---

## Observability Stack

### Metrics (Prometheus)

**Agent Metrics**:
```
agent_run_duration_seconds{tenant_id, status}
agent_step_duration_seconds{step_name, status}
agent_token_usage_total{tenant_id, provider, model}
agent_cost_usd_total{tenant_id, provider}
```

**System Metrics**:
```
http_requests_total{service, method, status}
http_request_duration_seconds{service, endpoint}
sqs_messages_visible{queue_name}
ecs_task_count{service}
```

**LLM Gateway Metrics**:
```
llm_requests_total{provider, model, status}
llm_latency_seconds{provider, model}
llm_rate_limit_hits_total{tenant_id}
llm_circuit_breaker_state{provider}
```

### Logs (CloudWatch)

Structured JSON logs from all services:
```json
{
  "timestamp": "2024-12-17T10:30:00Z",
  "level": "INFO",
  "service": "orchestrator",
  "run_id": "run_abc123",
  "step_id": "step_xyz789",
  "message": "Step completed successfully",
  "duration_ms": 1523,
  "tokens_used": 487
}
```

### Tracing (OpenTelemetry)

Distributed traces across services:
- Trace ID propagated via headers
- Spans for each service call
- Automatic instrumentation for FastAPI
- Manual spans for critical operations

---

## Cost Controls

### Token Budget Enforcement

1. **Pre-flight check**: Before any LLM call
2. **Hard limits**: Reject request if budget exceeded
3. **Soft limits**: Warn at 80% usage
4. **Tracking**: Redis counter + periodic sync to RDS

### Rate Limiting

**Implementation**: Token bucket algorithm in Redis

```python
def check_rate_limit(tenant_id: str) -> bool:
    key = f"ratelimit:{tenant_id}"
    current = redis.get(key) or 0
    
    if current >= LIMIT:
        return False
    
    redis.incr(key)
    redis.expire(key, WINDOW_SECONDS)
    return True
```

### Circuit Breaker

Prevents cascading failures when providers are down:

```
States: CLOSED → OPEN → HALF_OPEN → CLOSED

CLOSED: Normal operation
OPEN: All requests fail fast (after threshold failures)
HALF_OPEN: Test with single request
```

---

## Failure Handling

### Retry Strategy

**Exponential backoff**:
```
Attempt 1: 0s
Attempt 2: 2s
Attempt 3: 4s
Attempt 4: 8s
Attempt 5: 16s
Max: 5 attempts
```

**Retryable errors**:
- Network timeouts
- 5xx status codes
- Rate limit errors (429)
- Transient database errors

**Non-retryable errors**:
- 4xx client errors (except 429)
- Budget exceeded
- Invalid task configuration
- Authentication failures

### Dead Letter Queue

Failed messages after max retries → DLQ for manual inspection

---

## Security

### Network Architecture

```
Internet
   │
   ├─► API Gateway (public)
   │      │
   │      └─► Application Load Balancer (private)
   │             │
   │             ├─► Control Plane (private subnet)
   │             └─► LLM Gateway (private subnet)
   │
   └─► NAT Gateway
          │
          └─► Orchestrator Worker (private subnet)
                 │
                 ├─► Tool Runtime (isolated subnet)
                 └─► RDS (private subnet)
```

### Authentication

- JWT tokens issued by Cognito
- Token validation at API Gateway
- Service-to-service: IAM roles

### Secrets Management

- API keys stored in AWS Secrets Manager
- Automatic rotation enabled
- IAM policies for least-privilege access

---

## Deployment Strategy

### Blue-Green Deployment

1. Deploy new version (green)
2. Run smoke tests
3. Shift 10% traffic → monitor
4. Shift 50% traffic → monitor
5. Shift 100% traffic
6. Terminate old version (blue)

### Rollback

- Automatic rollback on CloudWatch alarms
- Manual rollback via Terraform
- Zero-downtime switchover

---

## Disaster Recovery

**RTO (Recovery Time Objective)**: 1 hour
**RPO (Recovery Point Objective)**: 5 minutes

**Backup Strategy**:
- RDS automated backups (daily)
- Point-in-time recovery enabled
- S3 versioning for artifacts
- Cross-region replication (optional)

**Failover**:
- Multi-AZ RDS for automatic failover
- ECS tasks distributed across AZs
- Route53 health checks for DNS failover

---

## Performance Targets

| Metric | Target | Measurement |
|--------|--------|-------------|
| API Latency (p95) | < 200ms | CloudWatch |
| Step Execution (p95) | < 30s | Prometheus |
| LLM Call Latency (p95) | < 5s | Prometheus |
| System Availability | 99.5% | Uptime monitoring |
| Queue Processing | < 10s lag | SQS metrics |

---

## Monitoring & Alerts

### Critical Alerts

1. **Budget Exceeded**: Tenant exceeds token budget
2. **Circuit Breaker Open**: LLM provider unhealthy
3. **High Failure Rate**: >10% step failures
4. **Queue Backlog**: >1000 messages pending
5. **Service Down**: Health check failures

### Alert Channels

- PagerDuty for critical (24/7)
- Slack for warnings
- Email for informational

---

## Future Enhancements

See [future-scope.md](future-scope.md) for roadmap.