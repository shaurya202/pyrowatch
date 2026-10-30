"""Vercel Python serverless entrypoint exposing the FastAPI ASGI app."""
from pyrowatch.server import app

__all__ = ["app"]
