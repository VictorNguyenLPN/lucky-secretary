"""Telegram Application factory wiring handlers, services, and database."""

from __future__ import annotations

import logging

from telegram import LinkPreviewOptions, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
    MessageHandler,
    filters,
)

from lucky_secretary.bot.handlers.callback import confirmation_callback_handler
from lucky_secretary.bot.handlers.command import cancel_command, help_command, start_command
from lucky_secretary.bot.handlers.message import text_message_handler
from lucky_secretary.core.agent import CalendarAgent
from lucky_secretary.core.config import Settings
from lucky_secretary.core.llm.openai_provider import OpenAIProvider
from lucky_secretary.database.connection import DatabaseManager
from lucky_secretary.database.repository import Repository
from lucky_secretary.integrations.google_calendar.client import GoogleCalendarClient
from lucky_secretary.services.calendar_service import CalendarService
from lucky_secretary.services.conversation_service import ConversationService

logger = logging.getLogger(__name__)


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled Telegram update error: %s", type(context.error).__name__)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "An unexpected error occurred while processing your request. Please try again."
            )
        except Exception:
            pass


def build_application(settings: Settings, db: DatabaseManager) -> Application:
    """Create and configure the python-telegram-bot Application."""
    repo = Repository(db)
    llm_provider = OpenAIProvider(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        base_url=settings.openai_base_url,
    )
    agent = CalendarAgent(
        llm_provider=llm_provider,
        default_timezone=settings.default_timezone,
        default_duration_minutes=settings.default_event_duration,
    )
    calendar_client = GoogleCalendarClient(token_path=settings.google_token_path)
    calendar_service = CalendarService(calendar_client)
    conversation_service = ConversationService(
        repository=repo,
        agent=agent,
        calendar_service=calendar_service,
        default_timezone=settings.default_timezone,
        default_duration_minutes=settings.default_event_duration,
        confirmation_timeout_minutes=settings.confirmation_timeout_minutes,
    )

    async def _post_init(app: Application) -> None:
        await db.init_db()
        logger.info("Database initialized at %s", settings.sqlite_path)

    async def _post_shutdown(app: Application) -> None:
        await db.close()
        logger.info("Lucky Secretary shutdown complete.")

    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .defaults(
            Defaults(
                parse_mode=ParseMode.HTML,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
        )
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    application.bot_data["settings"] = settings
    application.bot_data["repository"] = repo
    application.bot_data["conversation_service"] = conversation_service

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CallbackQueryHandler(confirmation_callback_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))
    application.add_error_handler(_error_handler)

    return application

