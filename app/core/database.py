# chatup/backend/app/core/database.py
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from app.core.config import settings

raw_url = settings.DATABASE_URL
if "?" in raw_url:
    base_url, query = raw_url.split("?", 1)
    params = [p for p in query.split("&") if not p.startswith("sslmode=") and not p.startswith("channel_binding=")]
    clean_query = "&".join(params)
    raw_url = base_url + (f"?{clean_query}" if clean_query else "")

if raw_url.startswith("postgres://"):
    raw_url = raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif raw_url.startswith("postgresql://") and not raw_url.startswith("postgresql+asyncpg://"):
    raw_url = raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(
    raw_url,
    echo=settings.DEBUG,
    pool_size=10,
    max_overflow=20,
    connect_args={"ssl": "require"} if "neon.tech" in raw_url else {}
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()