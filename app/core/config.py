from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "ChatUp SaaS"
    DEBUG: bool = True
    DATABASE_URL: str

    class Config:
        env_file = ".env"

settings = Settings()