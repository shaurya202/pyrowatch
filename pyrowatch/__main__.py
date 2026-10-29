"""Run PyroWatch with `python -m pyrowatch` or `pyrowatch`."""
from __future__ import annotations

from .config import HOST, PORT


def main() -> None:
    import uvicorn

    uvicorn.run("pyrowatch.server:app", host=HOST, port=PORT)


if __name__ == "__main__":
    main()
