#!/bin/bash
set -e

echo "🚀 Setting up AI Agent Platform local environment..."
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check prerequisites
echo "📋 Checking prerequisites..."

if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ Docker is required but not installed.${NC}"
    echo "Install from: https://docs.docker.com/get-docker/"
    exit 1
fi

if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python 3.11+ is required but not installed.${NC}"
    exit 1
fi

PYTHON_VERSION=$(python3 --version | cut -d' ' -f2 | cut -d'.' -f1,2)
if (( $(echo "$PYTHON_VERSION < 3.11" | bc -l) )); then
    echo -e "${RED}❌ Python 3.11+ required, found $PYTHON_VERSION${NC}"
    exit 1
fi

echo -e "${GREEN}✅ All prerequisites met${NC}"
echo ""

# Start PostgreSQL
echo "📦 Starting PostgreSQL..."
if docker ps -a --format '{{.Names}}' | grep -q "^ai-agent-db$"; then
    echo -e "${YELLOW}PostgreSQL container already exists, removing...${NC}"
    docker stop ai-agent-db 2>/dev/null || true
    docker rm ai-agent-db 2>/dev/null || true
fi

docker run --name ai-agent-db \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_DB=ai_agent_platform \
  -p 5432:5432 \
  -d postgres:15

# Start Redis
echo "📦 Starting Redis..."
if docker ps -a --format '{{.Names}}' | grep -q "^ai-agent-redis$"; then
    echo -e "${YELLOW}Redis container already exists, removing...${NC}"
    docker stop ai-agent-redis 2>/dev/null || true
    docker rm ai-agent-redis 2>/dev/null || true
fi

docker run --name ai-agent-redis \
  -p 6379:6379 \
  -d redis:7-alpine

# Wait for services to be ready
echo "⏳ Waiting for services to start..."
sleep 15

# Test PostgreSQL connection
echo "🔍 Testing PostgreSQL connection..."
for i in {1..30}; do
    if docker exec ai-agent-db pg_isready -U postgres > /dev/null 2>&1; then
        echo -e "${GREEN}✅ PostgreSQL is ready${NC}"
        break
    fi
    if [ $i -eq 30 ]; then
        echo -e "${RED}❌ PostgreSQL failed to start${NC}"
        exit 1
    fi
    sleep 1
done

# Test Redis connection
echo "🔍 Testing Redis connection..."
if docker exec ai-agent-redis redis-cli ping | grep -q "PONG"; then
    echo -e "${GREEN}✅ Redis is ready${NC}"
else
    echo -e "${RED}❌ Redis failed to start${NC}"
    exit 1
fi

# Apply database schema
echo "🗄️  Applying database schema..."
if [ -f "docs/schema.sql" ]; then
    docker exec -i ai-agent-db psql -U postgres -d ai_agent_platform < docs/schema.sql
    echo -e "${GREEN}✅ Database schema applied${NC}"
else
    echo -e "${RED}❌ schema.sql not found in docs/ directory${NC}"
    exit 1
fi

# Verify tables were created
echo "🔍 Verifying database tables..."
TABLE_COUNT=$(docker exec ai-agent-db psql -U postgres -d ai_agent_platform -t -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public';")
if [ "$TABLE_COUNT" -gt 5 ]; then
    echo -e "${GREEN}✅ Found $TABLE_COUNT tables in database${NC}"
else
    echo -e "${RED}❌ Expected more than 5 tables, found $TABLE_COUNT${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}🎉 Local environment setup complete!${NC}"
echo ""
echo "📊 Service Status:"
echo "  PostgreSQL: localhost:5432"
echo "  Redis: localhost:6379"
echo ""
echo "📝 Database Credentials:"
echo "  Host: localhost"
echo "  Port: 5432"
echo "  Database: ai_agent_platform"
echo "  Username: postgres"
echo "  Password: postgres"
echo ""
echo "🔧 Useful commands:"
echo "  Stop services:    docker stop ai-agent-db ai-agent-redis"
echo "  Start services:   docker start ai-agent-db ai-agent-redis"
echo "  Remove services:  docker rm -f ai-agent-db ai-agent-redis"
echo "  View logs:        docker logs ai-agent-db"
echo "  Connect to DB:    docker exec -it ai-agent-db psql -U postgres -d ai_agent_platform"
echo ""
echo "Next steps:"
echo "  1. cd services/control_plane"
echo "  2. python3 -m venv venv"
echo "  3. source venv/bin/activate"
echo "  4. pip install -r requirements.txt"
echo "  5. python src/main.py"