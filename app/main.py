# chatup/backend/app/main.py
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.core.database import get_db
from app.core.config import settings
from app.api.v1.auth import router as auth_router
from app.api.v1.members import router as members_router
from app.api.v1.admin import router as admin_router
from app.api.v1.meeting_minutes import router as mom_router
from app.api.v1.chat import router as chat_router

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="1.0.0",
    description="SaaS Backend for Community Clubs"
)

origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include All Routers
app.include_router(auth_router, prefix="/api/v1")
app.include_router(members_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")
app.include_router(mom_router, prefix="/api/v1")
app.include_router(chat_router, prefix="/api/v1")

@app.get("/")
def read_root():
    return {"message": "Welcome to ChatUp API Platform"}

@app.get("/health/db")
async def check_db_health(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "connected successfully"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}