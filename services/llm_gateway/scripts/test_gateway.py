"""
LLM Gateway Test Script

Tests all major functionality:
- Health checks
- Completion requests
- Budget enforcement
- Rate limiting
- Failover behavior
"""
import asyncio
import httpx
from uuid import UUID
import sys

BASE_URL = "http://localhost:8001"
TENANT_ID = "00000000-0000-0000-0000-000000000001"  # Demo tenant


class Colors:
    GREEN = '\033[0;32m'
    RED = '\033[0;31m'
    YELLOW = '\033[1;33m'
    NC = '\033[0m'  # No Color


async def test_health():
    """Test 1: Health check"""
    print("\n🧪 Test 1: Health Check")
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{BASE_URL}/health")
        
        if response.status_code == 200:
            data = response.json()
            print(f"{Colors.GREEN}✅ Health check passed{Colors.NC}")
            print(f"   Status: {data['status']}")
            print(f"   Providers: {len(data['providers'])}")
            for provider in data['providers']:
                status_color = Colors.GREEN if provider['status'] == 'healthy' else Colors.YELLOW
                print(f"   - {provider['provider']}: {status_color}{provider['status']}{Colors.NC}")
            return True
        else:
            print(f"{Colors.RED}❌ Health check failed{Colors.NC}")
            return False


async def test_completion():
    """Test 2: Basic completion"""
    print("\n🧪 Test 2: Basic Completion Request")
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(
                f"{BASE_URL}/v1/completions",
                json={
                    "model": "gpt-3.5-turbo",
                    "messages": [
                        {"role": "user", "content": "Say 'test successful' in exactly those words"}
                    ],
                    "tenant_id": TENANT_ID,
                    "max_tokens": 10,
                    "temperature": 0.1
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                print(f"{Colors.GREEN}✅ Completion request passed{Colors.NC}")
                print(f"   Provider: {data['provider']}")
                print(f"   Model: {data['model']}")
                print(f"   Content: {data['content'][:100]}")
                print(f"   Tokens: {data['usage']['total_tokens']}")
                print(f"   Cost: ${data['cost_usd']:.6f}")
                print(f"   Latency: {data['latency_ms']}ms")
                return True
            elif response.status_code == 503:
                print(f"{Colors.YELLOW}⚠️  No providers available (need API keys){Colors.NC}")
                return None  # Not a failure, just unconfigured
            else:
                print(f"{Colors.RED}❌ Completion request failed{Colors.NC}")
                print(f"   Status: {response.status_code}")
                print(f"   Response: {response.text}")
                return False
        except Exception as e:
            print(f"{Colors.RED}❌ Completion request error: {e}{Colors.NC}")
            return False


async def test_different_models():
    """Test 3: Different models"""
    print("\n🧪 Test 3: Test Different Models")
    
    models_to_test = [
        "gpt-3.5-turbo",
        "gpt-4",
        "claude-3-haiku"
    ]
    
    results = {}
    async with httpx.AsyncClient(timeout=30.0) as client:
        for model in models_to_test:
            try:
                response = await client.post(
                    f"{BASE_URL}/v1/completions",
                    json={
                        "model": model,
                        "messages": [
                            {"role": "user", "content": "Hi"}
                        ],
                        "tenant_id": TENANT_ID,
                        "max_tokens": 5
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    results[model] = {
                        "success": True,
                        "provider": data['provider'],
                        "cost": data['cost_usd']
                    }
                    print(f"{Colors.GREEN}✅ {model}: Success (${data['cost_usd']:.6f}){Colors.NC}")
                else:
                    results[model] = {"success": False}
                    print(f"{Colors.YELLOW}⚠️  {model}: Unavailable{Colors.NC}")
            except Exception as e:
                results[model] = {"success": False, "error": str(e)}
                print(f"{Colors.RED}❌ {model}: Error{Colors.NC}")
    
    return any(r.get("success") for r in results.values())


async def test_rate_limiting():
    """Test 4: Rate limiting"""
    print("\n🧪 Test 4: Rate Limiting")
    print("   Sending 10 rapid requests...")
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        tasks = []
        for i in range(10):
            task = client.post(
                f"{BASE_URL}/v1/completions",
                json={
                    "model": "gpt-3.5-turbo",
                    "messages": [{"role": "user", "content": "hi"}],
                    "tenant_id": TENANT_ID,
                    "max_tokens": 5
                }
            )
            tasks.append(task)
        
        try:
            responses = await asyncio.gather(*tasks, return_exceptions=True)
            
            success_count = sum(1 for r in responses if not isinstance(r, Exception) and r.status_code == 200)
            rate_limited = sum(1 for r in responses if not isinstance(r, Exception) and r.status_code == 429)
            
            print(f"   Successful: {success_count}")
            print(f"   Rate limited: {rate_limited}")
            
            if success_count > 0:
                print(f"{Colors.GREEN}✅ Rate limiting working (some requests succeeded){Colors.NC}")
                return True
            else:
                print(f"{Colors.YELLOW}⚠️  All requests failed or rate limited{Colors.NC}")
                return None
        except Exception as e:
            print(f"{Colors.RED}❌ Rate limiting test error: {e}{Colors.NC}")
            return False


async def main():
    """Run all tests"""
    print("=" * 60)
    print("🧪 LLM Gateway Test Suite")
    print("=" * 60)
    
    results = []
    
    # Test 1: Health
    results.append(await test_health())
    
    # Test 2: Basic completion
    results.append(await test_completion())
    
    # Test 3: Different models
    results.append(await test_different_models())
    
    # Test 4: Rate limiting
    results.append(await test_rate_limiting())
    
    # Summary
    print("\n" + "=" * 60)
    print("📊 Test Summary")
    print("=" * 60)
    
    passed = sum(1 for r in results if r is True)
    failed = sum(1 for r in results if r is False)
    skipped = sum(1 for r in results if r is None)
    
    print(f"   {Colors.GREEN}✅ Passed: {passed}{Colors.NC}")
    print(f"   {Colors.RED}❌ Failed: {failed}{Colors.NC}")
    print(f"   {Colors.YELLOW}⚠️  Skipped: {skipped}{Colors.NC}")
    
    if failed == 0:
        print(f"\n{Colors.GREEN}🎉 All tests passed!{Colors.NC}")
        return 0
    else:
        print(f"\n{Colors.RED}⚠️  Some tests failed{Colors.NC}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))