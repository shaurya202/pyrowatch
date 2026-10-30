"""Vercel FastAPI entrypoint exposing the PyroWatch ASGI application."""
from pyrowatch.server import app

__all__ = ["app"]
