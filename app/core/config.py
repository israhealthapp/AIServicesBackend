from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    APP_NAME: str = "IsraHealthcare Chatbot"
    DEBUG: bool = False
    PORT: int = 8000
    ALLOWED_ORIGINS: list[str] = ["*"]

    GEMINI_API_KEY: str
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GEMINI_MAX_TOKENS: int = 1024

    DEEPGRAM_API_KEY: str = ""  # Optional: for real-time voice transcription via WebSocket

    WHISPER_API_KEY: str = ""  # Optional: for faster transcription via whisper-api.com
    USE_WHISPER_TRANSCRIPTION: bool = True  # Toggle between Whisper and Gemini for transcription

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


@lru_cache()
def get_settings() -> Settings:
    return Settings()
