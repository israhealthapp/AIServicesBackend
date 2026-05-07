from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.logging import logger
from app.api.chat import router as chat_router
from app.api.intent import router as intent_router
from app.api.voice_command import router as voice_command_router
from app.api.voice_ws import router as voice_ws_router
from app.api.caregiver_summary import router as caregiver_summary_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("IsraHealthcare Chatbot service started")
    yield
    logger.info("IsraHealthcare Chatbot service stopped")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.APP_NAME,
        lifespan=lifespan,
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url=None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(chat_router)
    app.include_router(intent_router)
    app.include_router(caregiver_summary_router)
    app.include_router(voice_command_router)
    app.include_router(voice_ws_router)

    return app


app = create_app()
