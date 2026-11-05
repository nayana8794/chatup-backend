# chatup/backend/app/seed.py
import asyncio
from datetime import datetime
import uuid
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.security import get_password_hash
from app.models.tenant import Tenant
from app.models.user import User, UserRole, LanguagePreference


async def seed_data():
    async with AsyncSessionLocal() as session:
        # Check if platform already seeded
        result = await session.execute(select(User).where(User.email == "admin@chatup.com"))
        if result.scalar_one_or_none():
            # Ensure District Governor exists even if database was partially seeded
            gov_check = await session.execute(
                select(User).where(User.email == "governor@lionsdistrict107.fi")
            )
            if not gov_check.scalar_one_or_none():
                now = datetime.utcnow().replace(tzinfo=None)
                governor_user = User(
                    id=uuid.uuid4(),
                    tenant_id=None,
                    full_name="Tapani Rahko (District Governor)",
                    email="governor@lionsdistrict107.fi",
                    phone_number="+358 40 9876543",
                    hashed_password=get_password_hash("Governor123!"),
                    role=UserRole.DISTRICT_GOVERNOR,
                    language=LanguagePreference.FI,
                    district="District 107-N",
                    is_active=True,
                    must_change_password=False,
                    created_at=now,
                    updated_at=now,
                )
                session.add(governor_user)
                await session.commit()
                print("Added missing District Governor account: governor@lionsdistrict107.fi / Governor123!")
            else:
                print("Database already fully seeded!")
            return

        print("Seeding initial ChatUp SaaS multi-tier hierarchy data...")
        now = datetime.utcnow().replace(tzinfo=None)

        # 1. Platform Owner (Super Admin)
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

        # 2. Regional Leader (District Governor - Piirikuvernööri for District 107-N)
        governor_user = User(
            id=uuid.uuid4(),
            tenant_id=None,
            full_name="Tapani Rahko (District Governor)",
            email="governor@lionsdistrict107.fi",
            phone_number="+358 40 9876543",
            hashed_password=get_password_hash("Governor123!"),
            role=UserRole.DISTRICT_GOVERNOR,
            language=LanguagePreference.FI,
            district="District 107-N",
            is_active=True,
            must_change_password=False,
            created_at=now,
            updated_at=now,
        )
        session.add(governor_user)

        # 3. Demo Lions Club Workspace (under District 107-N)
        club = Tenant(
            id=uuid.uuid4(),
            name="Lions Club Helsinki",
            slug="lions-helsinki",
            district="District 107-N",
            features={
                "video_calls": True,
                "mom_editor": True,
                "social_feed": True,
                "dues_module": True,
                "max_members": 50,
            },
            theme_config={
                "primary_color": "#002F6C",
                "secondary_color": "#F2A900",
                "dark_mode": False,
            },
            created_at=now,
            updated_at=now,
        )
        session.add(club)
        await session.flush()

        # 4. Local Club President (Charter President appointed for Lions Club Helsinki)
        president = User(
            id=uuid.uuid4(),
            tenant_id=club.id,
            full_name="Matti Virtanen (President)",
            email="president@lionshelsinki.fi",
            phone_number="+358 40 1234567",
            hashed_password=get_password_hash("President123!"),
            role=UserRole.PRESIDENT,
            language=LanguagePreference.FI,
            district="District 107-N",
            is_active=True,
            must_change_password=False,
            created_at=now,
            updated_at=now,
        )
        session.add(president)

        # 5. Local Club Member
        member = User(
            id=uuid.uuid4(),
            tenant_id=club.id,
            full_name="Johan Lindqvist (Member)",
            email="member@lionshelsinki.fi",
            phone_number="+358 40 7654321",
            hashed_password=get_password_hash("Member123!"),
            role=UserRole.MEMBER,
            language=LanguagePreference.SV,
            district="District 107-N",
            is_active=True,
            must_change_password=False,
            created_at=now,
            updated_at=now,
        )
        session.add(member)

        await session.commit()
        print("Seeding completed successfully!")
        print("Created Accounts:")
        print("  - Super Admin:       admin@chatup.com / Admin123!")
        print("  - District Governor: governor@lionsdistrict107.fi / Governor123! (District 107-N)")
        print("  - Club President:    president@lionshelsinki.fi / President123!")
        print("  - Club Member:       member@lionshelsinki.fi / Member123!")


if __name__ == "__main__":
    asyncio.run(seed_data())