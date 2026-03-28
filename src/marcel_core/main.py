from contextlib import asynccontextmanager
from typing import AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()  # loads ANTHROPIC_API_KEY and other vars from .env into the process env

from marcel_core.api.chat import router as chat_router
from marcel_core.api.health import router as health_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup: initialise storage, load skill registry, etc. (filled in later issues)
    yield
    # Shutdown: flush any pending writes, close connections


app = FastAPI(title='Marcel', lifespan=lifespan)

app.include_router(health_router)
app.include_router(chat_router)
