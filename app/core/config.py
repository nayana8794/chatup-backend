from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "ChatUp SaaS"
    DEBUG: bool = True
    DATABASE_URL: str

    # Web Push Notification Settings (VAPID)
    VAPID_PUBLIC_KEY: str = ""
    VAPID_PRIVATE_KEY: str = ""
    VAPID_CLAIM_EMAIL: str = "mailto:admin@chatup.com"

    class Config:
        env_file = ".env"
        extra = "allow"

settings = Settings()