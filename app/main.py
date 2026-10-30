# chatup/backend/app/main.py
import os
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.core.database import get_db
from app.core.config import settings
from app.api.v1.auth import router as auth_router
from app.api.v1.members import router as members_router
from app.api.v1.admin import router as admin_router
from app.api.v1.meeting_minutes import router as mom_router
from app.api.v1.chat import router as chat_router
from app.api.v1.events import router as events_router
from app.api.v1.financial import router as financial_router
from app.api.v1.calling import router as calling_router
from app.api.v1.social import router as social_router
from app.api.v1.push import router as push_router
from app.api.v1.polls import router as polls_router


app = FastAPI(
    title=settings.PROJECT_NAME,
    version="1.0.0",
    description="SaaS Backend for Community Clubs"
)

# CORS configuration
origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files mounting to serve uploaded photos publicly
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(os.path.join(STATIC_DIR, "uploads"), exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Include All Routers
app.include_router(auth_router, prefix="/api/v1")
app.include_router(members_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")
app.include_router(mom_router, prefix="/api/v1")
app.include_router(chat_router, prefix="/api/v1")
app.include_router(events_router, prefix="/api/v1")
app.include_router(financial_router, prefix="/api/v1")
app.include_router(calling_router, prefix="/api/v1")
app.include_router(social_router, prefix="/api/v1")
app.include_router(push_router, prefix="/api/v1/push")
app.include_router(polls_router, prefix="/api/v1")

@app.get("/")
def read_root():
    return {"message": "Welcome to ChatUp API Platform (Live on Cloud)"}

@app.get("/health/db")
async def check_db_health(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "connected successfully"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}