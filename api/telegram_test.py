"""API endpoint: Test Telegram bot connection.
URL: POST /api/plugins/telegram/telegram_test

Returns bot identity info and a privacy-mode advisory when the bot
cannot read all group messages (can_read_all_group_messages = false).
Privacy mode is the Telegram default for bots added to groups; it means
the bot only receives messages that directly mention it or start with '/'.
The advisory helps operators understand why the bridge may miss messages.
"""
import logging

from helpers.api import ApiHandler, Request, Response

logger = logging.getLogger("telegram_test")


class TelegramTest(ApiHandler):

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["GET", "POST"]

    @classmethod
    def requires_csrf(cls) -> bool:
        return True

    async def process(self, input: dict, request: Request) -> dict | Response:
        try:
            from usr.plugins.telegram.helpers.telegram_client import TelegramClient, get_telegram_config

            config = get_telegram_config()
            token = (config.get("bot", {}).get("token", "") or "").strip()
            if not token:
                return {"ok": False, "error": "No bot token configured"}

            client = TelegramClient(token=token)
            me = await client.get_me()
            await client.close()

            username = me.get("username", "")
            first_name = me.get("first_name", "")
            user_label = f"@{username}" if username else first_name

            # Privacy mode check — when False the bot only receives messages
            # that mention it or commands.  This is the Telegram default.
            can_read_all = me.get("can_read_all_group_messages", False)
            privacy_advisory = None
            if not can_read_all:
                privacy_advisory = (
                    "Privacy mode is ON: the bot only receives messages that "
                    "mention it (@bot) or start with '/'. To read all group "
                    "messages, disable privacy mode via @BotFather → "
                    "Bot Settings → Group Privacy → Turn off."
                )
                logger.info(
                    "Bot @%s has privacy mode ON (can_read_all_group_messages=false). "
                    "Bridge will miss non-mention group messages.",
                    username or first_name,
                )

            result = {
                "ok": True,
                "user": user_label,
                "user_id": me.get("id"),
                "username": username,
                "first_name": first_name,
                "can_read_all_group_messages": can_read_all,
                "privacy_mode": not can_read_all,
            }
            if privacy_advisory:
                result["privacy_advisory"] = privacy_advisory
            return result

        except Exception as e:
            return {"ok": False, "error": f"Connection failed: {type(e).__name__}: {e}"}
