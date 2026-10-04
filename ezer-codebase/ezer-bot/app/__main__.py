"""Local development entry point: ``python -m app`` (honours EZER_HOST / EZER_PORT)."""

import uvicorn

from app.config import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
