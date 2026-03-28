from fastapi import APIRouter
from pydantic import BaseModel

from marcel_core import __version__

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    version: str


@router.get('/health', response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status='ok', version=__version__)
