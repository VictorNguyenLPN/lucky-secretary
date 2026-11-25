"""Application entrypoint for Lucky Secretary."""

from __future__ import annotations

import logging
import sys

from lucky_secretary.bot.application import build_application
from lucky_secretary.core.config import get_settings
from lucky_secretary.database.connection import DatabaseManager


def configure_logging() -> None:
    """Configure structured-style logging without leaking httpx/telegram token URLs."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )
    # Prevent httpx from logging full Telegram bot token URLs at INFO level.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def main() -> None:
    """Validate configuration and start Telegram Long Polling."""
    configure_logging()
    logger = logging.getLogger(__name__)

    settings = get_settings()
    db = DatabaseManager(settings.sqlite_path)
    application = build_application(settings, db)

    logger.info(
        "Starting Lucky Secretary (env=%s, model=%s, timezone=%s)",
        settings.app_env,
        settings.openai_model,
        settings.default_timezone,
    )
    application.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()

