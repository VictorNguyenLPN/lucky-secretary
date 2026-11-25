"""Telegram command handlers (/start, /help, /cancel)."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from lucky_secretary.core.config import Settings
from lucky_secretary.services.conversation_service import ConversationService

logger = logging.getLogger(__name__)


def is_authorized_user(update: Update, settings: Settings) -> bool:
    """Check whether the Telegram sender is in TELEGRAM_ALLOWED_USER_IDS."""
    user = update.effective_user
    if user is None:
        return False
    return user.id in settings.allowed_user_ids


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    settings: Settings = context.bot_data["settings"]
    if not is_authorized_user(update, settings):
        uid = update.effective_user.id if update.effective_user else "unknown"
        logger.warning("Unauthorized /start attempt from user_id=%s", uid)
        if update.message:
            await update.message.reply_text("Unauthorized access.")
        return

    if update.message:
        await update.message.reply_text(
            "👋 <b>Welcome to Lucky Secretary</b>\n\n"
            "Send an event description in Vietnamese or English, and I will prepare "
            "a Google Calendar preview for your confirmation.\n\n"
            "<b>Examples</b>\n"
            "• Tạo lịch họp với team vào 9 giờ sáng mai.\n"
            "• Ngày mai tôi có những lịch nào?\n"
            "• Đổi lịch họp team ngày mai sang 10h.\n"
            "• Xóa lịch họp team ngày mai.\n"
            "• Schedule a meeting tomorrow at 2 PM for 90 minutes.\n\n"
            "<b>Commands</b>\n"
            "• /help - Show usage instructions\n"
            "• /cancel - Discard an ongoing request\n"
            "• /clear - Clear recent chat messages"
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    settings: Settings = context.bot_data["settings"]
    if not is_authorized_user(update, settings):
        uid = update.effective_user.id if update.effective_user else "unknown"
        logger.warning("Unauthorized /help attempt from user_id=%s", uid)
        if update.message:
            await update.message.reply_text("Unauthorized access.")
        return

    if update.message:
        await update.message.reply_text(
            "📖 <b>Lucky Secretary Help</b>\n\n"
            "<b>Examples</b>\n"
            "• Tạo lịch họp với team vào 9 giờ sáng mai.\n"
            "• Đặt lịch phỏng vấn lúc 10h sáng ngày 28/11, kéo dài 1 tiếng.\n"
            "• Ngày mai tôi có những lịch nào?\n"
            "• Đổi lịch họp team ngày mai sang 10h.\n"
            "• Xóa lịch họp team ngày mai.\n"
            "• Schedule a meeting tomorrow at 2 PM for 90 minutes.\n\n"
            "<b>Commands</b>\n"
            "• /start - Start the assistant\n"
            "• /help - Show usage instructions\n"
            "• /cancel - Cancel the current pending event request\n"
            "• /clear - Clear recent chat messages"
        )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /cancel command."""
    settings: Settings = context.bot_data["settings"]
    if not is_authorized_user(update, settings):
        uid = update.effective_user.id if update.effective_user else "unknown"
        logger.warning("Unauthorized /cancel attempt from user_id=%s", uid)
        if update.message:
            await update.message.reply_text("Unauthorized access.")
        return

    assert update.effective_user is not None
    service: ConversationService = context.bot_data["conversation_service"]
    response = await service.handle_cancel_command(str(update.effective_user.id))
    if update.message:
        await update.message.reply_text(response.text)


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear recent Telegram chat messages and reset state while keeping the bot visible."""
    settings: Settings = context.bot_data["settings"]
    if not is_authorized_user(update, settings):
        uid = update.effective_user.id if update.effective_user else "unknown"
        logger.warning("Unauthorized /clear attempt from user_id=%s", uid)
        if update.message:
            await update.message.reply_text("Unauthorized access.")
        return

    if not update.effective_user or not update.effective_chat or not update.message:
        return

    service: ConversationService = context.bot_data["conversation_service"]
    await service.handle_cancel_command(str(update.effective_user.id))

    chat_id = update.effective_chat.id
    current_msg_id = update.message.message_id
    start_id = max(1, current_msg_id - 500)
    message_ids = list(range(current_msg_id, start_id - 1, -1))

    for idx in range(0, len(message_ids), 100):
        chunk = message_ids[idx : idx + 100]
        try:
            await context.bot.delete_messages(chat_id=chat_id, message_ids=chunk)
        except TelegramError as exc:
            logger.debug("Stopped or skipped batch delete at chunk %s: %s", idx, exc)

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "🧹 <b>Chat history cleared.</b>\n\n"
            "Send an event request whenever you're ready, or use /help for examples."
        ),
    )


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle unsupported or non-existent slash commands."""
    settings: Settings = context.bot_data["settings"]
    if not is_authorized_user(update, settings):
        uid = update.effective_user.id if update.effective_user else "unknown"
        logger.warning("Unauthorized unknown command attempt from user_id=%s", uid)
        if update.message:
            await update.message.reply_text("Unauthorized access.")
        return

    if update.message:
        await update.message.reply_text(
            "⚠️ <b>Unsupported Command</b>\n\n"
            "This command does not exist.\n\n"
            "<b>Available commands</b>\n"
            "• /start - Start the assistant\n"
            "• /help - Show usage instructions\n"
            "• /cancel - Cancel the current pending event request\n"
            "• /clear - Clear recent chat messages"
        )

