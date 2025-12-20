#!/bin/bash
set -e

echo "🚀 Setting up AI Agent Platform local environment..."

# Check prerequisites
command -v docker >/dev/null 2>&1 || { echo "❌ Docker required but not installed. Aborting." >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "❌ Python 3.11+ required but not installed. Aborting." >&2; exit 1; }

# Start PostgreSQL
echo "📦 Starting PostgreSQL..."
docker run --name ai-agent-db \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=ai_agent_platform \
  -p 5432:5432 \
  -d postgres:15 || echo "PostgreSQL container already exists"

# Start Redis
echo "📦 Starting Redis..."
docker run --name ai-agent-redis \
  -p 6379:6379 \
  -d redis:7-alpine || echo "Redis container already exists"

# Wait for services
echo "⏳ Waiting for services to start..."
sleep 10

# Apply database schema
echo "🗄️  Applying database schema..."
docker exec -i ai-agent-db psql -U postgres -d ai_agent_platform < docs/schema.sql

echo "✅ Local environment ready!"
echo ""
echo "Next steps:"
echo "1. cd services/control_plane"
echo "2. python -m venv venv"
echo "3. source venv/bin/activate"
echo "4. pip install -r requirements.txt"
echo "5. python src/main.py"
