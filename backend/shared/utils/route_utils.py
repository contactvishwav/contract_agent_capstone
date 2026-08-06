import os
from fastapi import APIRouter

def is_development() -> bool:
    """Check if running in development environment"""
    return os.getenv("ENVIRONMENT", "development") != "production"

def is_production() -> bool:
    """Check if running in production environment"""
    return os.getenv("ENVIRONMENT", "development") == "production"

def conditionally_include_router(app, router: APIRouter, condition: bool = True):
    """Conditionally include router based on environment or condition"""
    if condition:
        app.include_router(router)