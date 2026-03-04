"""V1 API sub-routers."""

from fastapi import APIRouter

from app.api.v1.health import router as health_router
from app.api.v1.predict import router as predict_router

v1_router = APIRouter(prefix="/v1")
v1_router.include_router(predict_router, tags=["prediction"])
v1_router.include_router(health_router, tags=["health"])
