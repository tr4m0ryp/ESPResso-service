"""Shared FastAPI dependencies."""

from fastapi import Request

from app.config import Settings
from app.models.loader import ModelLoader
from app.normalization.cache import NormalizationCache
from app.normalization.nim_client import NIMClient


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_model_loader(request: Request) -> ModelLoader:
    return request.app.state.model_loader


def get_nim_client(request: Request) -> NIMClient:
    return request.app.state.nim_client


def get_cache(request: Request) -> NormalizationCache:
    return request.app.state.cache
