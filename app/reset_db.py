# chatup/backend/app/reset_db.py
import asyncio
from datetime import datetime
import uuid
from sqlalchemy import text
from app.core.database import AsyncSessionLocal
from app.core.security import get_password_hash
from app.models.user import User, UserRole, LanguagePreference


async def reset_and_clean_database():
    print("Connecting to Neon PostgreSQL to flush all test data...")
    
    async with AsyncSessionLocal() as session:
        # 1. Truncate all application tables safely with CASCADE (preserving alembic_version)
        truncate_query = text("""
            TRUNCATE TABLE 
                push_subscriptions,
                post_comments,
                post_likes,
                social_posts,
                club_documents,
                financial_sheets,
                poll_voters,
                poll_options,
                polls,
                event_rsvps,
                events,
                club_transactions,
                dues_invoices,
                chat_read_receipts,
                chat_messages,
                chat_participants,
                chat_rooms,
                meeting_minutes,
                users,
                tenants
            RESTART IDENTITY CASCADE;
        """)
        await session.execute(truncate_query)
        await session.commit()
        print("✓ All test tables cleanly truncated!")

        # 2. Seed only the Master Super Admin (Platform Owner)
        now = datetime.utcnow().replace(tzinfo=None)
        super_admin = User(
            id=uuid.uuid4(),
            full_name="ChatUp Super Admin",
            email="admin@chatup.com",
            phone_number="+358 50 1112233",
            hashed_password=get_password_hash("Admin123!"),
            role=UserRole.SUPER_ADMIN,
            language=LanguagePreference.EN,
            is_active=True,
            must_change_password=False,
            created_at=now,
            updated_at=now,
        )
        session.add(super_admin)
        await session.commit()

        print("\n========================================================")
        print("🎉 DATABASE SUCCESSFULLY RESET & FRESH!")
        print("========================================================")
        print("Single Master Account:")
        print("  Email:    admin@chatup.com")
        print("  Password: Admin123!")
        print("  Role:     SUPER_ADMIN")
        print("========================================================\n")


if __name__ == "__main__":
    asyncio.run(reset_and_clean_database())