from pydantic_settings import BaseSettings
from pathlib import Path

class Settings(BaseSettings):
    APP_TITLE: str = "Schedule API RTU MIREA"
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = False

    CACHE_DIR: Path = Path("schedule_cache")
    CACHE_TTL: int = 86400  # 24 hours

    PLAYWRIGHT_TIMEOUT: int = 60000
    BROWSER_HEADLESS: bool = True

    class Config:
        env_file = ".env"

settings = Settings()
