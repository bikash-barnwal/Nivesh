"""Compatibility entry point: `uvicorn app:app`. Prefer `uv run nivesh server`."""

from nivesh.api.main import app

__all__ = ["app"]
