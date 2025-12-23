#!/bin/bash
# API Testing Script
# Tests all Control Plane endpoints

set -e

BASE_URL="http://localhost:8000"
ADMIN_EMAIL="admin@demo.com"
USER_EMAIL="user@demo.com"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "🧪 Testing AI Agent Platform Control Plane API"
echo "================================================"
echo ""

# Test 1: Health Check
echo "Test 1: Health Check"
response=$(curl -s "$BASE_URL/health")
if echo "$response" | grep -q "healthy"; then
    echo -e "${GREEN}✅ Health check passed${NC}"
else
    echo -e "${RED}❌ Health check failed${NC}"
    echo "Response: $response"
    exit 1
fi
echo ""

# Test 2: Root Endpoint
echo "Test 2: Root Endpoint"
response=$(curl -s "$BASE_URL/")
if echo "$response" | grep -q "control-plane-api"; then
    echo -e "${GREEN}✅ Root endpoint passed${NC}"
else
    echo -e "${RED}❌ Root endpoint failed${NC}"
    exit 1
fi
echo ""

# Test 3: API Info
echo "Test 3: API Info"
response=$(curl -s "$BASE_URL/api/v1/info")
if echo "$response" | grep -q "endpoints"; then
    echo -e "${GREEN}✅ API info passed${NC}"
else
    echo -e "${RED}❌ API info failed${NC}"
    exit 1
fi
echo ""

# Test 4: Get JWT Token (simplified - in production use proper auth)
echo "Test 4: Authentication"
# For MVP, we'll create a simple token manually
# In production, this would be POST /auth/login with email/password

# Generate a simple JWT for testing
# NOTE: This is INSECURE and for development only!
ADMIN_USER_ID="00000000-0000-0000-0000-000000000002"

echo -e "${YELLOW}Note: Using simplified auth for development${NC}"
echo "In production, implement proper login endpoint with password verification"

# For now, we'll use direct API calls without auth
# Once auth router is added, uncomment below:
# TOKEN=$(curl -s -X POST "$BASE_URL/auth/login" \
#   -H "Content-Type: application/json" \
#   -d "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"demo\"}" \
#   | jq -r '.access_token')

echo ""

# Test 5: List Tasks (without auth for now)
echo "Test 5: List Tasks"
echo -e "${YELLOW}Skipping auth-protected endpoints for now${NC}"
echo "Run 'python scripts/seed_db.py' first to create demo data"
echo ""

# Test 6: Metrics Endpoint
echo "Test 6: Prometheus Metrics"
response=$(curl -s "$BASE_URL/metrics")
if echo "$response" | grep -q "http_requests_total"; then
    echo -e "${GREEN}✅ Metrics endpoint passed${NC}"
else
    echo -e "${RED}❌ Metrics endpoint failed${NC}"
    exit 1
fi
echo ""

echo -e "${GREEN}✅ All basic tests passed!${NC}"
echo ""
echo "Next steps:"
echo "1. Run: python scripts/seed_db.py"
echo "2. Implement authentication router"
echo "3. Test protected endpoints"
echo "4. Deploy to AWS"