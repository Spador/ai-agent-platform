# Production-Grade AI Agent Platform

## What It Does
A multi-tenant AI agent orchestration platform that executes complex tasks through coordinated AI agents with enterprise-grade reliability, cost controls, and observability.

## Why It Exists
AI agents in production need governance: token budget enforcement, automatic failover across LLM providers, circuit breakers for failing operations, and comprehensive cost tracking. This platform provides the infrastructure layer that production AI systems require but most implementations lack.

## Tech Stack (subject to change)

**Core Services:**
- **Control Plane API**: FastAPI-based REST API for task & run management
- **LLM Gateway**: Intelligent routing with budget enforcement and provider fallback
- **Orchestrator Worker**: Agent execution engine powered by LangGraph
- **Tool Runtime**: Isolated execution environment for browser automation and code execution

**Infrastructure:**
- **AWS ECS Fargate**: Container orchestration
- **AWS Step Functions**: Workflow state management
- **Amazon RDS (PostgreSQL)**: Relational state store
- **Amazon SQS**: Message queuing for async processing
- **Amazon S3**: Artifact storage
- **API Gateway + Cognito**: Authentication & edge routing

**Observability:**
- **Prometheus**: Metrics collection
- **Grafana**: Visualization dashboards
- **OpenTelemetry**: Distributed tracing
- **CloudWatch**: Centralized logging

**DevOps:**
- **Terraform**: Infrastructure as Code
- **GitHub Actions**: CI/CD pipelines
- **Docker**: Containerization

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         API Gateway                             │
│                    (Authentication Layer)                       │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Control Plane API                           │
│  • Task Management  • Run Lifecycle  • Metrics Aggregation      │
└─────────────────┬───────────────────────────┬───────────────────┘
                  │                           │
                  ▼                           ▼
        ┌─────────────────┐         ┌─────────────────┐
        │ Step Functions  │         │   PostgreSQL    │
        │  State Machine  │◄────────┤   (Run State)   │
        └────────┬────────┘         └─────────────────┘
                 │
                 │ Enqueue Step
                 ▼
        ┌─────────────────┐
        │  SQS Queue      │
        └────────┬────────┘
                 │
                 │ Poll Messages
                 ▼
        ┌─────────────────────────────────┐
        │    Orchestrator Worker (ECS)    │
        │  • LangGraph Execution          │
        │  • Agent Coordination           │
        └──────┬──────────────────┬───────┘
               │                  │
               ▼                  ▼
    ┌──────────────────┐  ┌──────────────────┐
    │  LLM Gateway     │  │  Tool Runtime    │
    │  • Rate Limits   │  │  • Playwright    │
    │  • Cost Tracking │  │  • Code Exec     │
    │  • Failover      │  │  • Isolation     │
    └──────────────────┘  └──────────────────┘
               │
               ▼
    ┌──────────────────────────┐
    │  LLM Providers           │
    │  OpenAI → Anthropic →    │
    │  Local Models            │
    └──────────────────────────┘

    Observability Layer (All Services)
    ══════════════════════════════════
    Prometheus ← OpenTelemetry → Grafana
    CloudWatch Logs ← Structured JSON
```

## Key Features

### 🧠 AI Capabilities
- Multi-agent orchestration using LangGraph
- Directed Acyclic Graph (DAG) task execution
- Dynamic tool calling (web browsing, code execution, API integration)
- Automatic LLM provider fallback

### ⚙️ Platform Reliability
- Per-tenant token budget enforcement
- Rate limiting and throttling
- Circuit breakers for cascading failure prevention
- Exponential backoff retry logic
- Configurable timeouts and kill-switches

### 📊 Observability
- Real-time token usage metrics
- Task execution latency tracking
- Provider-level failure rates
- Cost attribution per tenant/run
- Distributed tracing across services

### 🔒 Enterprise Features
- Multi-tenant isolation
- JWT-based authentication
- Audit logging for compliance
- Encrypted artifact storage
- Network security groups

## Project Structure

```
ai-agent-platform/
├── services/
│   ├── control_plane/       # REST API for task/run management
│   ├── llm_gateway/          # LLM routing with cost controls
│   └── tools_runtime/        # Isolated tool execution
├── workers/
│   └── orchestrator/         # Agent execution worker
├── infra/
│   └── terraform/            # Complete infrastructure definitions
│       ├── network/          # VPC, subnets, security groups
│       ├── data/             # RDS, S3, DynamoDB
│       ├── compute/          # ECS services and task definitions
│       ├── messaging/        # SQS queues
│       ├── workflow/         # Step Functions state machines
│       ├── edge/             # API Gateway, Cognito
│       └── observability/    # CloudWatch, Prometheus
├── docs/
│   ├── architecture.md
│   ├── schema.sql
│   ├── cost-analysis.md
│   ├── scaling.md
│   ├── migration-ecs-to-eks.md
│   └── future-scope.md
└── .github/
    └── workflows/            # CI/CD pipelines

```

## Quick Start

### Prerequisites
- AWS Account with appropriate permissions
- Terraform >= 1.5.0
- Docker >= 24.0
- Python >= 3.11
- AWS CLI configured

### Local Development

```bash
# Clone the repository
git clone https://github.com/yourusername/ai-agent-platform.git
cd ai-agent-platform

# Set up Python virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r services/control_plane/requirements.txt

# Run local services
docker-compose up -d
```

### Infrastructure Deployment

```bash
cd infra/terraform

# Initialize Terraform
terraform init

# Plan infrastructure changes
terraform plan -var-file=environments/dev.tfvars

# Deploy infrastructure
terraform apply -var-file=environments/dev.tfvars
```

### Run a Demo Task
(place holder)

```bash
# Create a task
curl -X POST https://api.example.com/tasks \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "name": "Research AI Safety Papers",
    "description": "Find top 5 papers on AI safety from 2024",
    "steps": ["search_papers", "analyze_content", "summarize"]
  }'

# Start a run
curl -X POST https://api.example.com/runs \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"task_id": "task_123", "token_budget": 50000}'

# Monitor progress
curl https://api.example.com/runs/run_456/steps
```

## Documentation
(place holder)
- [Architecture Deep Dive](docs/architecture.md)
- [Database Schema](docs/schema.sql)
- [Cost Analysis](docs/cost-analysis.md)
- [Scaling Strategy](docs/scaling.md)
- [ECS to EKS Migration Path](docs/migration-ecs-to-eks.md)
- [Future Enhancements](docs/future-scope.md)

## Monitoring
(place holder)
Access Grafana dashboards at: `https://grafana.example.com`

**Key Dashboards:**
- Agent Performance Overview
- Cost Attribution by Tenant
- LLM Provider Health
- System Resource Utilization

## Contributing

This is a portfolio project demonstrating production-grade AI infrastructure. Contributions, suggestions, and feedback are welcome!

## License

MIT License - See LICENSE file for details

## Author

Built by Shivam Parashar - Demonstrating expertise in AI infrastructure, DevOps automation, and enterprise-scale system design.

**Contact:** sp3466 | [LinkedIn](https://www.linkedin.com/in/shivam-parashar1) | [Portfolio](https://spador.github.io/Shivam/)
