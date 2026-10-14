# chatup/backend/app/fix_enum.py
import asyncio
from sqlalchemy import text
from app.core.database import AsyncSessionLocal

async def fix_postgres_enums():
    async with AsyncSessionLocal() as session:
        print("Registering PostgreSQL Enum types...")
        try:
            # 1. Create billingcycle enum type in postgres
            await session.execute(
                text("CREATE TYPE billingcycle AS ENUM ('YEARLY', 'MONTHLY', 'QUARTERLY', 'ONE_TIME');")
            )
            print("✓ billingcycle enum created successfully.")
        except Exception as e:
            print(f"! billingcycle notice: {e}")

        try:
            # 2. Add AWAITING_VERIFICATION to existing duesstatus enum
            await session.execute(
                text("ALTER TYPE duesstatus ADD VALUE IF NOT EXISTS 'AWAITING_VERIFICATION';")
            )
            print("✓ duesstatus enum updated successfully.")
        except Exception as e:
            print(f"! duesstatus notice: {e}")

        await session.commit()
        print("All PostgreSQL Enums are ready!")

if __name__ == "__main__":
    asyncio.run(fix_postgres_enums())