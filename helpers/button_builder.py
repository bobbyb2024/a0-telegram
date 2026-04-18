"""Inline keyboard button construction helpers.

Telegram inline keyboards use nested lists:
  [[{text, callback_data}, ...], ...]  — each inner list is one row

Usage:
    from usr.plugins.telegram.helpers.button_builder import approval_buttons
    buttons = approval_buttons()
    # -> [[{"text": "✅ Approve", "callback_data": "approve"},
    #      {"text": "❌ Reject",  "callback_data": "reject"}]]
"""
from typing import Optional


def approval_buttons(
    approve_label: str = "✅ Approve",
    reject_label: str = "❌ Reject",
    approve_data: str = "approve",
    reject_data: str = "reject",
) -> list[list[dict]]:
    """Standard two-button approval/rejection row."""
    return [[
        {"text": approve_label, "callback_data": approve_data},
        {"text": reject_label,  "callback_data": reject_data},
    ]]


def choice_buttons(
    choices: list[str],
    prefix: str = "choice",
    per_row: int = 1,
) -> list[list[dict]]:
    """One button per choice. per_row controls how many fit on each row.

    Uses the choice index (not the raw text) in callback_data to avoid the
    Telegram 64-byte callback_data limit and special-character issues.
    The display label still shows the full choice text (truncated to 40 chars).
    """
    _MAX_LABEL = 40
    # Telegram callback_data hard limit is 64 bytes; prefix + ":" + index is safe
    buttons = [
        {
            "text": c[:_MAX_LABEL],
            "callback_data": f"{prefix}:{i}",
        }
        for i, c in enumerate(choices)
    ]
    rows = []
    for i in range(0, len(buttons), per_row):
        rows.append(buttons[i:i + per_row])
    return rows


def confirm_button(
    label: str = "✅ Confirm",
    data: str = "confirm",
) -> list[list[dict]]:
    """Single confirm button."""
    return [[{"text": label, "callback_data": data}]]


def yes_no_buttons(
    yes_label: str = "Yes",
    no_label: str = "No",
    yes_data: str = "yes",
    no_data: str = "no",
) -> list[list[dict]]:
    """Simple yes/no row."""
    return [[
        {"text": yes_label, "callback_data": yes_data},
        {"text": no_label,  "callback_data": no_data},
    ]]


def url_button(label: str, url: str) -> list[list[dict]]:
    """Single button that opens a URL (uses url field, not callback_data)."""
    return [[{"text": label, "url": url}]]


def remove_keyboard() -> dict:
    """Reply markup to remove an inline keyboard."""
    return {"inline_keyboard": []}


def build_keyboard(buttons: list[list[dict]]) -> dict:
    """Wrap button rows in reply_markup format."""
    return {"inline_keyboard": buttons}
