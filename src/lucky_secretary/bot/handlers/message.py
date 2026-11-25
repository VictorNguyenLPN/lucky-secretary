"""Telegram text message handler."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from lucky_secretary.bot.handlers.command import is_authorized_user
from lucky_secretary.bot.keyboards import build_confirmation_keyboard
from lucky_secretary.core.config import Settings
from lucky_secretary.database.repository import Repository
from lucky_secretary.services.conversation_service import ConversationService

logger = logging.getLogger(__name__)


async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming natural language text messages."""
    settings: Settings = context.bot_data["settings"]
    if not is_authorized_user(update, settings):
        uid = update.effective_user.id if update.effective_user else "unknown"
        logger.warning("Unauthorized message attempt from user_id=%s", uid)
        if update.message:
            await update.message.reply_text("Unauthorized access.")
        return

    if not update.message or not update.message.text or not update.effective_user:
        return

    telegram_user_id = str(update.effective_user.id)
    logger.info("Telegram message received from user_id=%s", telegram_user_id)

    service: ConversationService = context.bot_data["conversation_service"]
    response = await service.handle_user_message(telegram_user_id, update.message.text)

    reply_markup = (
        build_confirmation_keyboard(response.reply_markup_request_id)
        if response.reply_markup_request_id
        else None
    )
    sent = await update.message.reply_text(response.text, reply_markup=reply_markup)

    if response.reply_markup_request_id:
        repo: Repository = context.bot_data["repository"]
        await repo.update_calendar_request(
            response.reply_markup_request_id,
            telegram_message_id=str(sent.message_id),
        )

