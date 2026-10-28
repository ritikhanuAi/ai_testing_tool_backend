from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import List, Optional
import os

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Application
    APP_NAME: str = "AI Website Testing Platform"
    APP_ENV: str = "development"
    PROJECT_VERSION: str = "1.0.0"
    DEBUG: bool = True
    SECRET_KEY: str = "change-me-to-a-very-secure-random-secret-key-at-least-32-chars"
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    ALLOWED_HOSTS: str = "*"
    CORS_ALLOWED_ORIGINS: str = "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000,http://127.0.0.1:5173"

    @property
    def cors_origins_list(self) -> List[str]:
        if not self.CORS_ALLOWED_ORIGINS:
            return ["*"]
        return [origin.strip() for origin in self.CORS_ALLOWED_ORIGINS.split(",") if origin.strip()]

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_testing_tool"
    DATABASE_URL_SYNC: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/ai_testing_tool"

    # Redis & Celery
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # AI Engine
    AI_PROVIDER: str = "stpi"
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5:latest"
    OPEN_BASE_URL: str = "https://stpi.roadathena.com/v1"
    OPEN_API_KEY: str = "hanuai@321"
    OPENAI_MODEL: str = "Qwen3.8-27B-GGUF"
    AI_TIMEOUT_SECONDS: int = 60
    OPENAI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None

    # Storage & Evidence
    STORAGE_DIR: str = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../storage"))
    MAX_CONCURRENT_BROWSERS: int = 4
    BROWSER_DEFAULT_TIMEOUT_MS: int = 30000
    DEFAULT_VIEWPORT_WIDTH: int = 1280
    DEFAULT_VIEWPORT_HEIGHT: int = 720

    # Safety Guardrails
    ALLOW_PRIVATE_IPS: bool = False
    DEFAULT_SCAN_RATE_LIMIT: int = 10  # Max requests per second to target site

settings = Settings()

