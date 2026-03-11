"""API endpoint: Get/set Telegram plugin configuration.
URL: POST /api/plugins/telegram/telegram_config_api
"""
import json
import yaml
from pathlib import Path
from helpers.api import ApiHandler, Request, Response


def _get_config_path() -> Path:
    """Find the writable config path for the telegram plugin."""
    candidates = [
        Path(__file__).parent.parent / "config.json",
        Path("/a0/usr/plugins/telegram/config.json"),
        Path("/a0/plugins/telegram/config.json"),
        Path("/git/agent-zero/usr/plugins/telegram/config.json"),
    ]
    for p in candidates:
        if p.parent.exists():
            return p
    return candidates[-1]


class TelegramConfigApi(ApiHandler):

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["GET", "POST"]

    @classmethod
    def requires_csrf(cls) -> bool:
        return True

    async def process(self, input: dict, request: Request) -> dict | Response:
        action = input.get("action", "get")
        if request.method == "GET" or action == "get":
            return self._get_config()
        elif action == "generate_auth_key":
            return self._generate_auth_key()
        else:
            return self._set_config(input)

    def _generate_auth_key(self) -> dict:
        """Generate a new auth key (does not save — user must click Save)."""
        try:
            from plugins.telegram.helpers.sanitize import generate_auth_key
            return {"auth_key": generate_auth_key()}
        except Exception:
            return {"error": "Failed to generate auth key."}

    def _get_config(self) -> dict:
        try:
            config_path = _get_config_path()
            if config_path.exists():
                with open(config_path, "r") as f:
                    config = json.load(f)
            else:
                default_path = config_path.parent / "default_config.yaml"
                if default_path.exists():
                    with open(default_path, "r") as f:
                        config = yaml.safe_load(f) or {}
                else:
                    config = {}

            # Mask bot token for security — show only first 2 and last 2 chars
            masked = json.loads(json.dumps(config))
            bot = masked.get("bot", {})
            if bot.get("token"):
                token = bot["token"]
                if len(token) > 6:
                    bot["token"] = token[:2] + "*" * 8 + token[-2:]
                else:
                    bot["token"] = "********"

            # Mask auth key — show only last 4 chars
            bridge = masked.get("chat_bridge", {})
            if bridge.get("auth_key"):
                key = bridge["auth_key"]
                if len(key) > 6:
                    bridge["auth_key"] = "****" + key[-4:]
                else:
                    bridge["auth_key"] = "********"

            return masked
        except Exception:
            return {"error": "Failed to read configuration."}

    def _set_config(self, input: dict) -> dict:
        try:
            config = input.get("config", input)
            if not config or config == {"action": "set"}:
                return {"error": "No config provided"}

            # Remove the action key if present
            config.pop("action", None)

            config_path = _get_config_path()
            config_path.parent.mkdir(parents=True, exist_ok=True)

            # Merge with existing config (preserve tokens if masked)
            existing = {}
            if config_path.exists():
                with open(config_path, "r") as f:
                    existing = json.load(f)

            # Preserve masked bot token
            new_token = config.get("bot", {}).get("token", "")
            if new_token and "*" * 4 in new_token:
                config.setdefault("bot", {})["token"] = existing.get("bot", {}).get("token", "")

            # Preserve existing auth_key if not provided, empty, or masked
            new_auth_key = config.get("chat_bridge", {}).get("auth_key", "")
            existing_auth_key = existing.get("chat_bridge", {}).get("auth_key", "")
            if (not new_auth_key or "****" in new_auth_key) and existing_auth_key:
                config.setdefault("chat_bridge", {})["auth_key"] = existing_auth_key

            # Merge allowed_users lists instead of replacing
            new_bridge = config.get("chat_bridge", {})
            existing_bridge = existing.get("chat_bridge", {})
            if "allowed_users" in new_bridge and "allowed_users" in existing_bridge:
                merged_users = list(dict.fromkeys(
                    existing_bridge["allowed_users"] + new_bridge["allowed_users"]
                ))
                new_bridge["allowed_users"] = merged_users

            from plugins.telegram.helpers.sanitize import secure_write_json
            secure_write_json(config_path, config)

            return {"ok": True}
        except Exception:
            return {"error": "Failed to save configuration."}
