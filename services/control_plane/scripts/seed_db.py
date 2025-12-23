"""
Database Seeding Script

Creates sample data for development and testing:
- Demo tenant
- Demo users (admin, member)
- Sample tasks
- Sample runs

Usage:
    python scripts/seed_db.py
"""
import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import select
from database import AsyncSessionLocal, init_db
from models import Tenant, User, Task
import uuid


async def seed_database():
    """Seed the database with sample data"""
    print("🌱 Seeding database...")
    
    # Initialize database
    await init_db()
    
    async with AsyncSessionLocal() as db:
        try:
            # Check if demo tenant already exists
            result = await db.execute(
                select(Tenant).filter(Tenant.name == "Demo Tenant")
            )
            existing_tenant = result.scalar_one_or_none()
            
            if existing_tenant:
                print("⚠️  Demo tenant already exists, skipping seed")
                return
            
            # Create demo tenant
            tenant = Tenant(
                id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
                name="Demo Tenant",
                token_budget_monthly=5000000,
                rate_limit_per_minute=200
            )
            db.add(tenant)
            await db.flush()
            print(f"✅ Created tenant: {tenant.name}")
            
            # Create admin user
            admin_user = User(
                id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
                tenant_id=tenant.id,
                email="admin@demo.com",
                name="Admin User",
                role="admin"
            )
            db.add(admin_user)
            await db.flush()
            print(f"✅ Created admin user: {admin_user.email}")
            
            # Create member user
            member_user = User(
                id=uuid.UUID("00000000-0000-0000-0000-000000000003"),
                tenant_id=tenant.id,
                email="user@demo.com",
                name="Demo User",
                role="member",
                token_budget_monthly=100000
            )
            db.add(member_user)
            await db.flush()
            print(f"✅ Created member user: {member_user.email}")
            
            # Create sample tasks
            
            # Task 1: Web Research
            task1 = Task(
                tenant_id=tenant.id,
                created_by=admin_user.id,
                name="Web Research Assistant",
                description="Search the web for information and summarize findings",
                task_config={
                    "steps": [
                        {
                            "name": "search_web",
                            "type": "tool",
                            "tool": "browser",
                            "action": "search"
                        },
                        {
                            "name": "analyze_results",
                            "type": "llm",
                            "model": "gpt-4",
                            "prompt": "Analyze the search results and extract key information"
                        },
                        {
                            "name": "summarize",
                            "type": "llm",
                            "model": "gpt-3.5-turbo",
                            "prompt": "Create a concise summary of the findings"
                        }
                    ],
                    "tools": ["browser"],
                    "models": ["gpt-4", "gpt-3.5-turbo"]
                },
                default_token_budget=15000,
                timeout_seconds=600
            )
            db.add(task1)
            print(f"✅ Created task: {task1.name}")
            
            # Task 2: Code Review
            task2 = Task(
                tenant_id=tenant.id,
                created_by=admin_user.id,
                name="Code Review Assistant",
                description="Review code for bugs, security issues, and best practices",
                task_config={
                    "steps": [
                        {
                            "name": "analyze_code",
                            "type": "llm",
                            "model": "gpt-4",
                            "prompt": "Analyze the code for potential issues"
                        },
                        {
                            "name": "security_check",
                            "type": "llm",
                            "model": "gpt-4",
                            "prompt": "Check for security vulnerabilities"
                        },
                        {
                            "name": "generate_report",
                            "type": "llm",
                            "model": "gpt-3.5-turbo",
                            "prompt": "Generate a detailed review report"
                        }
                    ],
                    "tools": [],
                    "models": ["gpt-4", "gpt-3.5-turbo"]
                },
                default_token_budget=20000,
                timeout_seconds=900
            )
            db.add(task2)
            print(f"✅ Created task: {task2.name}")
            
            # Task 3: Data Analysis
            task3 = Task(
                tenant_id=tenant.id,
                created_by=member_user.id,
                name="Data Analysis Pipeline",
                description="Analyze data and generate insights",
                task_config={
                    "steps": [
                        {
                            "name": "load_data",
                            "type": "tool",
                            "tool": "code_executor",
                            "action": "run_python"
                        },
                        {
                            "name": "analyze",
                            "type": "llm",
                            "model": "gpt-4",
                            "prompt": "Analyze the data and identify patterns"
                        },
                        {
                            "name": "visualize",
                            "type": "tool",
                            "tool": "code_executor",
                            "action": "create_chart"
                        },
                        {
                            "name": "summarize",
                            "type": "llm",
                            "model": "gpt-3.5-turbo",
                            "prompt": "Summarize the key insights"
                        }
                    ],
                    "tools": ["code_executor"],
                    "models": ["gpt-4", "gpt-3.5-turbo"]
                },
                default_token_budget=25000,
                timeout_seconds=1200
            )
            db.add(task3)
            print(f"✅ Created task: {task3.name}")
            
            # Commit all changes
            await db.commit()
            
            print("\n🎉 Database seeded successfully!")
            print(f"\n📊 Summary:")
            print(f"  Tenants: 1")
            print(f"  Users: 2")
            print(f"  Tasks: 3")
            print(f"\n🔐 Demo Credentials:")
            print(f"  Admin: admin@demo.com")
            print(f"  User: user@demo.com")
            print(f"  (No password required for dev - JWT tokens will be issued)")
            
        except Exception as e:
            await db.rollback()
            print(f"❌ Error seeding database: {e}")
            raise


if __name__ == "__main__":
    asyncio.run(seed_database())