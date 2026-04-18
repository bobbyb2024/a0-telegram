"""API endpoint: Telegram webhook receiver.

URL: POST /api/plugins/telegram/webhook

Telegram posts Update objects here when webhook mode is enabled.
The endpoint validates the X-Telegram-Bot-Api-Secret-Token header
(when a secret_token is configured) and feeds the update to the
running ChatBridgeBot instance via its application dispatcher.

NOTE: This handler is only functional when the bridge is running in
webhook mode (config.webhook.enabled = true).  In long-polling mode
Telegram never calls this endpoint, so it is a no-op.
"""
import hashlib
import hmac
import json
import logging

from helpers.api import ApiHandler, Request, Response

logger = logging.getLogger("telegram_webhook_api")


class TelegramWebhookApi(ApiHandler):

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["POST"]

    @classmethod
    def requires_csrf(cls) -> bool:
        # Telegram servers cannot send CSRF tokens — use secret_token instead.
        return False

    async def process(self, input: dict, request: Request) -> dict | Response:
        try:
            from usr.plugins.telegram.helpers.telegram_client import get_telegram_config
            from usr.plugins.telegram.helpers.telegram_bridge import get_bot_status, get_bridge_application

            config = get_telegram_config()
            webhook_cfg = config.get("webhook", {})

            if not webhook_cfg.get("enabled", False):
                logger.debug("Webhook update received but webhook mode is disabled — ignoring.")
                return Response(status=200, body=b'{"ok":true}',
                                headers={"Content-Type": "application/json"})

            # ----------------------------------------------------------------
            # Validate secret token (HMAC-safe comparison)
            # ----------------------------------------------------------------
            secret_token = (webhook_cfg.get("secret_token", "") or "").strip()
            if not secret_token:
                # No secret configured — any client can POST fake updates.
                # Log once at WARNING so operators notice this in production.
                logger.warning(
                    "Webhook mode is enabled but webhook.secret_token is not set. "
                    "Any HTTP client can POST fake Telegram updates to this endpoint. "
                    "Set a secret_token in config and register it with setWebhook."
                )
            if secret_token:
                provided = ""
                if hasattr(request, "headers"):
                    provided = (request.headers.get("X-Telegram-Bot-Api-Secret-Token", "") or "").strip()
                if not hmac.compare_digest(
                    secret_token.encode(), provided.encode()
                ):
                    logger.warning("Webhook: invalid secret token from %s",
                                   getattr(request, "remote", "unknown"))
                    return Response(status=403, body=b'{"ok":false,"error":"forbidden"}',
                                    headers={"Content-Type": "application/json"})

            # ----------------------------------------------------------------
            # Feed update to the running application
            # ----------------------------------------------------------------
            status = get_bot_status()
            if not status.get("running"):
                logger.warning("Webhook update received but bridge is not running.")
                # Still return 200 so Telegram doesn't retry with exponential backoff.
                return Response(status=200, body=b'{"ok":true}',
                                headers={"Content-Type": "application/json"})

            app = get_bridge_application()
            if app is None:
                logger.warning("Webhook update received but no application instance available.")
                return Response(status=200, body=b'{"ok":true}',
                                headers={"Content-Type": "application/json"})

            try:
                # input is already parsed JSON (dict) by the framework.
                # python-telegram-bot expects an Update object.
                from telegram import Update as PTBUpdate
                update = PTBUpdate.de_json(input, app.bot)
                await app.process_update(update)
            except Exception as e:
                logger.error("Webhook: error processing update %s: %s",
                             input.get("update_id", "?"), type(e).__name__, exc_info=True)
                # Return 200 regardless — a non-200 causes Telegram to retry.

            return Response(status=200, body=b'{"ok":true}',
                            headers={"Content-Type": "application/json"})

        except Exception as e:
            logger.error("Webhook handler error: %s", type(e).__name__, exc_info=True)
            return Response(status=200, body=b'{"ok":true}',
                            headers={"Content-Type": "application/json"})
