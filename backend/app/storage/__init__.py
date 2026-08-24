"""Persistence infrastructure for local and future service deployments."""

from app.config import settings
from app.storage.database import Database

database = Database(settings.database_path)

__all__ = ["database"]
