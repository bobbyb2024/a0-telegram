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
            elif action == "diagnose":
                return self._diagnose()
            elif action == "set_debug":
                return self._set_debug(input)
            elif action == "get_debug":
                return self._get_debug()
            elif action == "project_sync_status":
                return self._project_sync_status()
            elif action == "project_sync_trigger":
                return self._project_sync_trigger()
            elif action == "project_sync_reset_baseline":
                return self._project_sync_reset_baseline()
            else:
                return {"ok": False, "error": f"Unknown action: {action}"}
        except Exception as e:
            logger.error(
                "Bridge API error on '%s': %s: %s",
                action, type(e).__name__, e, exc_info=True,
            )
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def _status(self) -> dict:
        from usr.plugins.telegram.helpers.telegram_bridge import (
            get_bot_status, get_chat_list, get_debug_mode,
        )
        status = get_bot_status()
        status["chat_count"] = len(get_chat_list())
        status["debug_mode"] = bool(get_debug_mode())
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

    def _diagnose(self) -> dict:
        """Dump the bridge's view of the world for 'messages don't arrive' triage.

        Returns:
          - bridge_code_version (confirms new code is loaded)
          - bridge status (running, bot identity, fatal error if any)
          - chat_list keys + size (empty = respond everywhere)
          - allowed_users + size (empty = allow everyone)
          - full_agent_mode setting
          - topics (project mappings)
          - privacy mode hint for groups
          - ready-to-paste curl command for getUpdates to test the token directly
        """
        try:
            from usr.plugins.telegram.helpers.telegram_bridge import (
                BRIDGE_CODE_VERSION, get_bot_status, get_chat_list, get_topic_map,
                get_debug_mode,
            )
            from usr.plugins.telegram.helpers.telegram_client import get_telegram_config

            cfg = get_telegram_config()
            bridge_cfg = cfg.get("chat_bridge", {}) or {}
            chat_list = get_chat_list()
            topics = get_topic_map()
            token = (cfg.get("bot", {}).get("token", "") or "").strip()
            token_masked = f"{token[:6]}…{token[-4:]}" if token and len(token) > 12 else "(unset)"

            status = get_bot_status()

            return {
                "ok": True,
                "bridge_code_version": BRIDGE_CODE_VERSION,
                "status": status,
                "token_present": bool(token),
                "token_masked": token_masked,
                "full_agent_mode": bridge_cfg.get("full_agent_mode", True),
                "allow_elevated": bridge_cfg.get("allow_elevated", False),
                "debug_mode": bool(get_debug_mode()),
                "chat_list": {
                    "size": len(chat_list),
                    "keys": list(chat_list.keys()),
                    "note": "EMPTY → respond in every chat" if not chat_list else None,
                },
                "allowed_users": {
                    "size": len(bridge_cfg.get("allowed_users", []) or []),
                    "ids": [str(u) for u in (bridge_cfg.get("allowed_users", []) or [])],
                    "note": "EMPTY → allow every user" if not bridge_cfg.get("allowed_users") else None,
                },
                "topic_mappings": len(topics),
                "hints": [
                    "Look for 'UPDATE #' lines in the log to confirm messages arrive.",
                    "No UPDATE lines → bot privacy mode in groups, or polling not running.",
                    "UPDATE but no MSG recv → chat_list or allowed_users filter dropped it.",
                ],
            }
        except Exception as e:
            logger.error("diagnose error: %s: %s", type(e).__name__, e, exc_info=True)
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def _set_debug(self, input: dict) -> dict:
        """Flip the runtime debug-trace flag on the live bridge.

        Accepts ``{"enabled": true|false}``. Changes apply immediately — no
        restart needed. The flag is also seeded from chat_bridge.debug_mode
        in config at bridge startup, so setting it here is in-memory only
        (survives until bridge restart). To make it persistent, update the
        config file as well.
        """
        from usr.plugins.telegram.helpers.telegram_bridge import (
            set_debug_mode, get_debug_mode,
        )
        enabled = bool(input.get("enabled", False))
        new_val = set_debug_mode(enabled)
        logger.info("Debug-trace flag %s via API", "ENABLED" if new_val else "DISABLED")
        return {
            "ok": True,
            "enabled": new_val,
            "message": (
                f"Debug trace {'enabled — every message step will emit a [DBG] log line.' if new_val else 'disabled — silent operation resumed.'}"
            ),
        }

    def _get_debug(self) -> dict:
        """Return the current state of the runtime debug-trace flag."""
        from usr.plugins.telegram.helpers.telegram_bridge import get_debug_mode
        return {"ok": True, "enabled": bool(get_debug_mode())}

    def _project_sync_status(self) -> dict:
        """Return a snapshot of the A0 ↔ Telegram project-sync watcher state.

        Shows whether the loop is running, the last successful tick, the
        last error (if any), and the resolved config (enabled, supergroup,
        archive_action, poll interval, baseline size). The WebUI polls this
        to render the sync panel.
        """
        from usr.plugins.telegram.helpers.telegram_bridge import get_project_sync_status
        return {"ok": True, **get_project_sync_status()}

    def _project_sync_trigger(self) -> dict:
        """Run one project-sync pass on demand and return its summary.

        Useful for "I just created a context in A0, mirror it NOW" without
        waiting for the poll interval. Blocks up to 30 s while the bridge
        thread executes the tick. Returns the same dict shape the
        background loop logs internally (``created_topics``,
        ``closed_topics``, ``deleted_topics``, ``error``).
        """
        from usr.plugins.telegram.helpers.telegram_bridge import trigger_project_sync_tick
        summary = trigger_project_sync_tick()
        # trigger_project_sync_tick already returns {"ok": bool, ...}
        if isinstance(summary, dict) and "ok" in summary:
            return summary
        return {"ok": True, "summary": summary}

    def _project_sync_reset_baseline(self) -> dict:
        """Forget the baseline snapshot so the next tick re-initialises it.

        Handy after an operator imports contexts in bulk and wants the
        mirror to start fresh from "now". The default behaviour will
        record all currently-existing contexts as baseline (so none get
        topics created). If the config has ``sync_existing: true`` this
        instead triggers a one-shot back-fill.
        """
        from usr.plugins.telegram.helpers.telegram_bridge import (
            set_project_baseline, load_chat_state, save_chat_state,
        )
        # Remove the baseline key entirely so is_project_baseline_initialized()
        # reads False and the next tick runs Phase 0 bootstrap.
        state = load_chat_state()
        had_baseline = "project_baseline" in state
        state.pop("project_baseline", None)
        save_chat_state(state)
        return {
            "ok": True,
            "previous_baseline_existed": had_baseline,
            "message": (
                "Baseline cleared. The next project-sync tick will "
                "re-initialise it from the current set of A0 contexts."
            ),
        }

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
