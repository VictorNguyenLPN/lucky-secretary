"""Telegram inline keyboard builders."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def build_confirmation_keyboard(request_id: str) -> InlineKeyboardMarkup:
    """Build Confirm / Cancel inline keyboard tied to a calendar_requests UUID."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Confirm", callback_data=f"confirm:{request_id}"),
                InlineKeyboardButton("Cancel", callback_data=f"cancel:{request_id}"),
            ]
        ]
    )

