# Scaling Strategy

## Overview

This document outlines the scaling architecture and strategies for the AI Agent Platform, covering both horizontal and vertical scaling approaches.

## Current Architecture Limits

### Single-Region Setup (Initial)

| Component | Min | Default | Max |
|-----------|-----|---------|-----|
| Control Plane Tasks | 2 | 2 | 10 |
| LLM Gateway Tasks | 2 | 2 | 10 |
| Orchestrator Workers | 1 | 3 | 20 |
| Tool Runtime (concurrent) | 0 | 5 | 50 |
| Database Connections | 20 | 20 | 100 |
| Concurrent Runs | 10 | 50 | 500 |

**Expected Load**: 10,000 runs/month = ~14 runs/hour = ~1 run/minute

---

## Horizontal Scaling

### ECS Auto-Scaling Configuration

#### Control Plane & LLM Gateway

**Scaling Policy: Target Tracking**
```hcl
resource "aws_appautoscaling_policy" "control_plane_cpu" {
  name               = "control-plane-cpu-scaling"
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.control_plane.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  policy_type        = "TargetTrackingScaling"

  target_tracking_scaling_policy_configuration {
    target_value       = 70.0
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}
```

**Trigger Points**:
- Scale out: CPU > 70% for 2 minutes
- Scale in: CPU < 50% for 5 minutes
- Memory: Same thresholds

#### Orchestrator Worker

**Scaling Policy: SQS Queue-Based**
```hcl
resource "aws_appautoscaling_policy" "orchestrator_queue" {
  name               = "orchestrator-queue-scaling"
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.orchestrator.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  policy_type        = "TargetTrackingScaling"

  target_tracking_scaling_policy_configuration {
    target_value = 100.0  # Messages per task
    
    customized_metric_specification {
      metric_name = "ApproximateNumberOfMessagesVisible"
      namespace   = "AWS/SQS"
      statistic   = "Average"
      
      dimensions {
        name  = "QueueName"
        value = aws_sqs_queue.orchestrator.name
      }
    }
    
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}
```

**Scaling Logic**:
- Messages per worker: 100
- If queue depth = 1000 messages → 10 workers
- If queue depth = 100 messages → 1 worker
- Max queue lag: 10 seconds at peak

---

## Vertical Scaling

### When to Scale Up (Fargate Task Size)

#### Control Plane
**Current**: 0.5 vCPU, 1 GB RAM

**Scale to 1 vCPU, 2 GB when**:
- Request latency p95 > 500ms
- Memory usage > 80%
- Database connection pool saturation

#### Orchestrator Worker
**Current**: 1 vCPU, 2 GB RAM

**Scale to 2 vCPU, 4 GB when**:
- LangGraph execution time > 30s
- Memory usage > 85% (large agent graphs)
- OOM errors in logs

#### LLM Gateway
**Current**: 0.5 vCPU, 1 GB RAM

**Scale to 1 vCPU, 2 GB when**:
- Redis connection errors
- Request queue buildup
- Circuit breaker activation frequency increases

---

## Database Scaling

### RDS Scaling Path

#### Phase 1: Development (Current)
- **Instance**: db.t4g.small (2 vCPU, 2 GB)
- **Connections**: 87 max
- **IOPS**: 3000 (GP3)
- **Throughput**: 125 MB/s
- **Cost**: ~$74/month

**Good for**: <100 concurrent runs

#### Phase 2: Production Launch
- **Instance**: db.r6g.large (2 vCPU, 16 GB)
- **Connections**: 697 max
- **IOPS**: 12,000 (GP3)
- **Throughput**: 500 MB/s
- **Cost**: ~$385/month

**Good for**: 500-1000 concurrent runs

#### Phase 3: Scale-Up
- **Instance**: db.r6g.xlarge (4 vCPU, 32 GB)
- **Connections**: 1397 max
- **IOPS**: 16,000 (GP3)
- **Throughput**: 1000 MB/s
- **Cost**: ~$770/month

**Good for**: 1000-5000 concurrent runs

#### Phase 4: Read Replicas
- **Primary**: db.r6g.xlarge (writes)
- **Replica 1**: db.r6g.large (analytics queries)
- **Replica 2**: db.r6g.large (dashboard reads)

**Query Routing**:
```python
# Write operations → Primary
async def create_run(run_data):
    async with get_db_session(primary=True) as db:
        # ...

# Read operations → Replica
async def get_run_metrics(tenant_id):
    async with get_db_session(replica=True) as db:
        # ...
```

#### Phase 5: Aurora Serverless v2 (Optional)
For variable workloads:
- **Min**: 0.5 ACUs (1 GB RAM)
- **Max**: 128 ACUs (256 GB RAM)
- **Cost**: $0.12/ACU-hour

**Best for**: Bursty workloads with 10x variance

---

## Redis Scaling

### Phase 1: Single Node (Current)
- **Instance**: cache.t4g.micro (2 vCPU, 0.5 GB)
- **Cost**: ~$12/month
- **Good for**: 10,000 ops/second

### Phase 2: Cluster Mode
- **Instance**: cache.r6g.large (2 vCPU, 13 GB)
- **Shards**: 3
- **Replicas per shard**: 1
- **Cost**: ~$450/month
- **Good for**: 100,000 ops/second

**Data Sharding**:
```python
# Rate limiting keys: shard by tenant_id
rate_limit_key = f"ratelimit:{tenant_id}"  # Redis hashes this

# Token budget keys: shard by tenant_id
budget_key = f"budget:{tenant_id}"

# Circuit breaker: single key (small dataset)
cb_key = f"circuit_breaker:{provider}"
```

---

## SQS Scaling

SQS auto-scales, but queue configuration matters:

### Throughput Optimization

**Standard Queue Settings**:
```hcl
resource "aws_sqs_queue" "orchestrator" {
  name                       = "orchestrator-steps-queue"
  visibility_timeout_seconds = 300  # 5 minutes
  message_retention_seconds  = 1209600  # 14 days
  receive_wait_time_seconds  = 20  # Long polling
  
  # Prevent message duplication issues
  deduplication_scope        = "messageGroup"
  fifo_throughput_limit      = "perMessageGroupId"
}
```

**Expected Throughput**:
- Standard queue: 3,000 messages/second
- FIFO queue: 300 messages/second (or 3,000 with batching)

**For >1000 runs/minute**: Use standard queue with idempotency keys

---

## Multi-Region Architecture

### Phase 1: Single Region (us-east-1)
Current setup - everything in one region.

### Phase 2: Active-Passive DR (us-west-2)

**Components Replicated**:
- ✅ S3 (Cross-Region Replication)
- ✅ RDS (Cross-Region Read Replica)
- ✅ Terraform state
- ❌ ECS services (deploy on failover)
- ❌ SQS (regional service)

**Failover Process**:
1. Promote RDS replica to primary
2. Deploy ECS services in us-west-2
3. Update Route53 to point to new ALB
4. Recreate SQS queues

**RTO**: 1 hour, **RPO**: 5 minutes

### Phase 3: Active-Active (Global)

**Regions**: us-east-1, us-west-2, eu-west-1

**Architecture Changes**:
- Route53 latency-based routing
- Regional ECS clusters (all active)
- Regional SQS queues
- Aurora Global Database
- S3 with multi-region access points

**Run Distribution**:
- Users create runs in nearest region
- Runs execute in same region
- Cross-region reads from Aurora replicas

**Cost Impact**: 3x infrastructure + cross-region data transfer

---

## Load Testing Results

### Test Scenario 1: Steady State
- **Load**: 100 runs/minute
- **Duration**: 1 hour
- **Workers**: Auto-scaled to 5
- **Results**:
  - p95 latency: 8.2s
  - p99 latency: 12.1s
  - Error rate: 0.02%
  - Cost: $2.50 for test

### Test Scenario 2: Spike
- **Load**: 0 → 1000 runs in 1 minute
- **Duration**: 5 minutes
- **Workers**: Auto-scaled 1 → 15
- **Results**:
  - Queue lag: 45s peak, cleared in 3 minutes
  - p95 latency: 48.2s (includes queue time)
  - Error rate: 0.1%
  - Scale-out time: 90 seconds

### Test Scenario 3: Sustained Peak
- **Load**: 500 runs/minute
- **Duration**: 6 hours
- **Workers**: Stable at 18
- **Results**:
  - p95 latency: 9.8s
  - p99 latency: 15.3s
  - Database connections: 68% utilized
  - Error rate: 0.05%

---

## Performance Optimization

### Database Query Optimization

**Before**:
```python
# N+1 query problem
runs = await db.execute(select(Run).filter(Run.tenant_id == tenant_id))
for run in runs:
    steps = await db.execute(select(Step).filter(Step.run_id == run.id))
```

**After**:
```python
# Eager loading with joinedload
runs = await db.execute(
    select(Run)
    .filter(Run.tenant_id == tenant_id)
    .options(joinedload(Run.steps))
)
```

**Result**: 10x faster for runs with multiple steps

### Redis Pipeline Batching

**Before**:
```python
for tenant_id in tenant_ids:
    usage = await redis.get(f"usage:{tenant_id}")
```

**After**:
```python
pipe = redis.pipeline()
for tenant_id in tenant_ids:
    pipe.get(f"usage:{tenant_id}")
results = await pipe.execute()
```

**Result**: 5x faster for bulk operations

### Connection Pool Tuning

**Before**:
```python
# Default: pool_size=5, max_overflow=10
engine = create_async_engine(DATABASE_URL)
```

**After**:
```python
# Tuned for 10 workers × 2 connections each
engine = create_async_engine(
    DATABASE_URL,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,  # Verify connections
    pool_recycle=3600    # Recycle after 1 hour
)
```

**Result**: Eliminated "connection pool exhausted" errors

---

## Capacity Planning

### Formulas

**Orchestrator Workers Needed**:
```
Workers = (Runs per minute × Avg execution time in minutes) / 60
        + 20% buffer
```

Example: 100 runs/min × 5 min avg = 500 / 60 = 8.3 → **10 workers**

**Database Connections Needed**:
```
Connections = (Control Plane tasks × 5) 
            + (LLM Gateway tasks × 3)
            + (Orchestrator workers × 2)
            + (Reserved for maintenance × 5)
```

Example: (2×5) + (2×3) + (10×2) + 5 = **41 connections**

**S3 Storage Growth**:
```
Monthly Growth = Runs/month × Avg artifact size × 1.2 buffer
```

Example: 10,000 runs × 2 MB = **20 GB/month**

---

## Cost at Scale

| Runs/Month | Infrastructure | LLM Costs | Total |
|------------|---------------|-----------|-------|
| 10,000 | $860 | $900 | $1,760 |
| 50,000 | $1,200 | $4,500 | $5,700 |
| 100,000 | $2,200 | $9,000 | $11,200 |
| 500,000 | $5,500 | $45,000 | $50,500 |
| 1,000,000 | $9,800 | $90,000 | $99,800 |

**Key Insights**:
- LLM costs dominate at scale (80-90%)
- Infrastructure scales sub-linearly
- Optimization focus: reduce token usage

---

## Monitoring Scaling Health

### Key Metrics to Watch

**Queue Lag**:
```promql
# Alert if queue lag > 30s
sqs_approximate_age_of_oldest_message_seconds > 30
```

**Worker Saturation**:
```promql
# Alert if all workers busy
ecs_service_running_count >= ecs_service_desired_count
AND sqs_approximate_number_of_messages_visible > 100
```

**Database Connection Pool**:
```promql
# Alert if connections > 80% utilized
db_connections_in_use / db_connections_max > 0.8
```

**API Latency**:
```promql
# Alert if p95 latency > 1s
http_request_duration_seconds{quantile="0.95"} > 1.0
```

---

## Scaling Decision Tree

```
Start: Are you experiencing performance issues?
│
├─ Yes: What type?
│  ├─ Slow API responses
│  │  └─ Scale: Control Plane tasks (horizontal)
│  │
│  ├─ Queue backlog growing
│  │  └─ Scale: Orchestrator workers (horizontal)
│  │
│  ├─ Database connection errors
│  │  ├─ Pool exhausted: Increase pool_size
│  │  └─ CPU saturated: Scale RDS instance (vertical)
│  │
│  ├─ Out of memory errors
│  │  └─ Scale: Fargate task memory (vertical)
│  │
│  └─ Rate limit errors
│     └─ Scale: Redis cluster (horizontal)
│
└─ No: Are you planning for growth?
   └─ Forecast capacity needs
      └─ Scale proactively before hitting limits
```

---

## Future: Kubernetes Migration

When ECS limits are reached (typically >100 services), migrate to EKS.

**Benefits**:
- Better resource utilization
- More granular scaling
- Multi-cluster federation
- Advanced scheduling

See [migration-ecs-to-eks.md](migration-ecs-to-eks.md) for details.