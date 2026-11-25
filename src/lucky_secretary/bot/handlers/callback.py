"""Telegram inline keyboard callback handler."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from lucky_secretary.bot.handlers.command import is_authorized_user
from lucky_secretary.core.config import Settings
from lucky_secretary.services.conversation_service import ConversationService

logger = logging.getLogger(__name__)


async def confirmation_callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle Confirm / Cancel button clicks."""
    query = update.callback_query
    if query is None:
        return

    settings: Settings = context.bot_data["settings"]
    if not is_authorized_user(update, settings):
        uid = update.effective_user.id if update.effective_user else "unknown"
        logger.warning("Unauthorized callback attempt from user_id=%s", uid)
        await query.answer("Unauthorized access.", show_alert=True)
        return

    await query.answer()

    data = query.data or ""
    if ":" not in data:
        await query.edit_message_text("Invalid callback action.")
        return

    action, request_id = data.split(":", 1)
    if action not in ("confirm", "cancel") or not request_id:
        await query.edit_message_text("Invalid callback action.")
        return

    assert update.effective_user is not None
    service: ConversationService = context.bot_data["conversation_service"]
    response = await service.handle_confirmation(
        telegram_user_id=str(update.effective_user.id),
        request_id=request_id,
        confirmed=(action == "confirm"),
    )

    if response.clear_keyboard:
        await query.edit_message_text(text=response.text)
    elif query.message:
        await query.message.reply_text(response.text)

