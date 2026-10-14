# chatup/backend/app/clean_dues.py
import asyncio
from sqlalchemy import text
from app.core.database import AsyncSessionLocal

async def clean_dues_tables():
    async with AsyncSessionLocal() as session:
        print("Cleaning dues_invoices and club_transactions tables...")
        # Clean dues invoices and transaction ledgers safely
        await session.execute(text("TRUNCATE TABLE dues_invoices, club_transactions CASCADE;"))
        await session.commit()
        print("✓ Dues and financial ledger tables have been cleaned successfully!")

if __name__ == "__main__":
    asyncio.run(clean_dues_tables())