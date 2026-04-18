"""API endpoint: Chat bridge start/stop/status.
URL: POST /api/plugins/telegram/telegram_bridge_api
"""
import logging
from helpers.api import ApiHandler, Request, Response

logger = logging.getLogger("telegram_bridge_api")


class TelegramBridgeApi(ApiHandler):

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["POST"]

    @classmethod
    def requires_csrf(cls) -> bool:
        return True

    async def process(self, input: dict, request: Request) -> dict | Response:
        action = input.get("action", "status")

        try:
            if action == "status":
                return self._status()
            elif action == "start":
                return await self._start()
            elif action == "stop":
                return await self._stop()
            elif action == "restart":
                return await self._restart()
            elif action == "list_topics":
                return self._list_topics(input)
            elif action == "map_topic":
                return self._map_topic(input)
            elif action == "unmap_topic":
                return self._unmap_topic(input)
            else:
                return {"ok": False, "error": f"Unknown action: {action}"}
        except Exception as e:
            logger.error(
                "Bridge API error on '%s': %s: %s",
                action, type(e).__name__, e, exc_info=True,
            )
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def _status(self) -> dict:
        from usr.plugins.telegram.helpers.telegram_bridge import get_bot_status, get_chat_list
        status = get_bot_status()
        status["chat_count"] = len(get_chat_list())
        return {"ok": True, **status}

    async def _start(self) -> dict:
        from usr.plugins.telegram.helpers.telegram_bridge import get_bot_status, start_chat_bridge
        from usr.plugins.telegram.helpers.telegram_client import get_telegram_config

        status = get_bot_status()
        if status.get("running"):
            return {"ok": True, "message": "Bridge is already running", **status}

        config = get_telegram_config()
        token = (config.get("bot", {}).get("token", "") or "").strip()
        if not token:
            return {"ok": False, "error": "No bot token configured"}

        logger.info(f"Starting bridge with token present={bool(token)}")
        await start_chat_bridge(token)
        final_status = get_bot_status()
        logger.info(f"Bridge start result: {final_status}")
        return {"ok": True, "message": "Bridge started", **final_status}

    async def _stop(self) -> dict:
        from usr.plugins.telegram.helpers.telegram_bridge import get_bot_status, stop_chat_bridge

        await stop_chat_bridge()
        return {"ok": True, "message": "Bridge stopped", **get_bot_status()}

    def _list_topics(self, input: dict) -> dict:
        from usr.plugins.telegram.helpers.telegram_bridge import get_topic_map
        all_topics = get_topic_map()
        chat_id = str(input.get("chat_id", "")).strip()
        if chat_id:
            topics = {k: v for k, v in all_topics.items() if k.startswith(f"{chat_id}:topic:")}
        else:
            topics = dict(all_topics)
        return {"ok": True, "topics": topics, "count": len(topics)}

    def _map_topic(self, input: dict) -> dict:
        chat_id = str(input.get("chat_id", "")).strip()
        thread_id = str(input.get("thread_id", "")).strip()
        project_id = str(input.get("project_id", "")).strip()
        name = str(input.get("name", "")).strip()
        if not chat_id or not thread_id:
            return {"ok": False, "error": "chat_id and thread_id are required"}
        try:
            tid = int(thread_id)
        except ValueError:
            return {"ok": False, "error": "thread_id must be an integer"}
        topic_key = f"{chat_id}:topic:{tid}"
        from usr.plugins.telegram.helpers.telegram_bridge import set_topic_project
        set_topic_project(topic_key, project_id or topic_key, name or f"Topic {tid}")
        return {"ok": True, "topic_key": topic_key, "project_id": project_id or topic_key}

    def _unmap_topic(self, input: dict) -> dict:
        chat_id = str(input.get("chat_id", "")).strip()
        thread_id = str(input.get("thread_id", "")).strip()
        if not chat_id or not thread_id:
            return {"ok": False, "error": "chat_id and thread_id are required"}
        try:
            tid = int(thread_id)
        except ValueError:
            return {"ok": False, "error": "thread_id must be an integer"}
        topic_key = f"{chat_id}:topic:{tid}"
        from usr.plugins.telegram.helpers.telegram_bridge import load_chat_state, save_chat_state
        state = load_chat_state()
        removed = state.get("topics", {}).pop(topic_key, None)
        if removed is not None:
            save_chat_state(state)
        return {"ok": True, "removed": removed is not None, "topic_key": topic_key}

    async def _restart(self) -> dict:
        from usr.plugins.telegram.helpers.telegram_bridge import get_bot_status, start_chat_bridge, stop_chat_bridge
        from usr.plugins.telegram.helpers.telegram_client import get_telegram_config

        await stop_chat_bridge()

        config = get_telegram_config()
        token = (config.get("bot", {}).get("token", "") or "").strip()
        if not token:
            return {"ok": False, "error": "No bot token configured"}

        await start_chat_bridge(token)
        return {"ok": True, "message": "Bridge restarted", **get_bot_status()}
