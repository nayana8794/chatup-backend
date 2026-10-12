# chatup/backend/app/seed.py
import asyncio
from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.core.security import get_password_hash
from app.models.tenant import Tenant
from app.models.user import User, UserRole, LanguagePreference

async def seed_data():
    async with AsyncSessionLocal() as session:
        # Check if already seeded
        result = await session.execute(select(User).where(User.email == "admin@chatup.com"))
        if result.scalar_one_or_none():
            print("Database already seeded!")
            return

        print("Seeding initial data...")

        # 1. Create Super Admin
        super_admin = User(
            full_name="ChatUp Super Admin",
            email="admin@chatup.com",
            hashed_password=get_password_hash("Admin123!"),
            role=UserRole.SUPER_ADMIN,
            language=LanguagePreference.EN,
        )
        session.add(super_admin)

        # 2. Create Demo Lions Club (Helsinki Lions)
        club = Tenant(
            name="Lions Club Helsinki",
            slug="lions-helsinki",
            district="District 107-N",
            features={
                "video_calls": True,
                "mom_editor": True,
                "social_feed": True,
                "dues_module": True,
                "max_members": 50
            },
            theme_config={
                "primary_color": "#002F6C",
                "secondary_color": "#F2A900",
                "dark_mode": False
            }
        )
        session.add(club)
        await session.flush() # Tenant ID එක ලබා ගැනීමට

        # 3. Create Club President
        president = User(
            tenant_id=club.id,
            full_name="Matti Virtanen (President)",
            email="president@lionshelsinki.fi",
            hashed_password=get_password_hash("President123!"),
            role=UserRole.PRESIDENT,
            language=LanguagePreference.FI, # Finnish
        )
        session.add(president)

        # 4. Create Club Member
        member = User(
            tenant_id=club.id,
            full_name="Johan Lindqvist (Member)",
            email="member@lionshelsinki.fi",
            hashed_password=get_password_hash("Member123!"),
            role=UserRole.MEMBER,
            language=LanguagePreference.SV, # Swedish
        )
        session.add(member)

        await session.commit()
        print("Seeding completed successfully!")
        print("Created Accounts:")
        print("  - Super Admin: admin@chatup.com / Admin123!")
        print("  - President:   president@lionshelsinki.fi / President123!")
        print("  - Member:      member@lionshelsinki.fi / Member123!")

if __name__ == "__main__":
    asyncio.run(seed_data())