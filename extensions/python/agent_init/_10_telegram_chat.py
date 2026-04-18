"""Auto-start the Telegram chat bridge on agent initialization.

Only starts if:
  - A bot token is configured
  - chat_bridge.auto_start is true in config
  - At least one bridge chat is registered

After starting, this extension also launches a watchdog coroutine that
monitors the bridge and automatically restarts it if it crashes or goes
silent for longer than watchdog.stale_seconds (default 300 s).

NOTE: agent_init is dispatched via call_extensions_sync(), so execute()
must be synchronous.  start_chat_bridge() is async, so we schedule it on
the running event loop with create_task().

The dedup flag lives on the bridge module (a true singleton) rather than
on this extension module, which A0 may reimport from multiple search paths.
"""

import asyncio
import logging

from helpers.extension import Extension

logger = logging.getLogger("telegram_chat_bridge")

# Watchdog state lives at module level so it survives extension reimports.
_watchdog_task: "asyncio.Task | None" = None


class TelegramChatBridgeInit(Extension):

    def execute(self, **kwargs):
        if not self.agent:
            return

        # Only run for the main agent, not subordinates
        if self.agent.number != 0:
            return

        try:
            import usr.plugins.telegram.helpers.telegram_bridge as bridge

            # Only attempt once per process lifetime (flag lives on the
            # bridge module so it survives reimports of this extension)
            if bridge._auto_start_attempted or bridge.is_bridge_polling():
                return

            bridge._auto_start_attempted = True

            from helpers import plugins

            config = plugins.get_plugin_config("telegram", agent=self.agent)
            bot_token = config.get("bot", {}).get("token", "")

            if not bot_token:
                return  # No token, skip

            bridge_config = config.get("chat_bridge", {})
            if not bridge_config.get("auto_start", False):
                return  # Auto-start disabled

            chats = bridge.get_chat_list()
            if not chats:
                return  # No chats configured

            logger.warning(
                "Auto-starting Telegram chat bridge (%d chat(s))...", len(chats)
            )

            # start_chat_bridge is async — schedule it on the running loop
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(bridge.start_chat_bridge(bot_token))
                # Schedule watchdog on same loop
                watchdog_cfg = config.get("watchdog", {})
                if watchdog_cfg.get("enabled", True):
                    loop.create_task(
                        _watchdog(bot_token, config, watchdog_cfg)
                    )
            except RuntimeError:
                asyncio.run(bridge.start_chat_bridge(bot_token))

            logger.warning("Telegram chat bridge auto-start scheduled.")

        except Exception as e:
            logger.warning(
                "Telegram chat bridge auto-start failed: %s",
                type(e).__name__,
                exc_info=True,
            )


# ---------------------------------------------------------------------------
# Watchdog coroutine
# ---------------------------------------------------------------------------

async def _watchdog(bot_token: str, config: dict, watchdog_cfg: dict) -> None:
    """Monitor the bridge and restart it on crash or silence.

    Uses exponential backoff (10 s → 20 s → 40 s … capped at 300 s).
    Resets the restart counter after a clean run of 3600 seconds.
    """
    import time
    try:
        import usr.plugins.telegram.helpers.telegram_bridge as bridge
    except Exception:
        return

    stale_seconds: int = int(watchdog_cfg.get("stale_seconds", 300))
    max_restarts: int = int(watchdog_cfg.get("max_restarts", 5))
    check_interval: int = 30   # How often to poll bridge health (seconds)
    clean_window: int = 3600   # Restart counter resets after this many clean seconds

    restarts = 0
    clean_since = time.monotonic()
    backoff = 10

    logger.info("Telegram bridge watchdog started (stale=%ds, max_restarts=%d).",
                stale_seconds, max_restarts)

    while True:
        await asyncio.sleep(check_interval)
        now = time.monotonic()

        # Reset restart counter after a sustained clean run
        if (now - clean_since) > clean_window:
            if restarts > 0:
                logger.info("Watchdog: clean window elapsed, resetting restart counter (%d → 0).",
                            restarts)
            restarts = 0
            backoff = 10

        try:
            status = bridge.get_bot_status()
        except Exception:
            status = {}

        running = status.get("running", False)
        last_activity = status.get("last_activity_ts", 0)  # unix timestamp or 0

        # Determine if bridge is stale
        stale = False
        if not running:
            stale = True
            reason = "bridge not running"
        elif last_activity and (time.time() - last_activity) > stale_seconds:
            stale = True
            reason = f"no activity for {stale_seconds}s"
        else:
            clean_since = now  # reset clean timer whenever bridge looks healthy
            continue

        # Stop retrying on fatal errors (e.g. invalid/revoked bot token).
        # The user must update the token in settings and manually start the bridge.
        fatal = getattr(bridge, "_fatal_error", None)
        fatal_type = getattr(bridge, "_fatal_error_type", None)
        if fatal:
            logger.error(
                "Watchdog: bridge has a fatal %s error — automatic restart suppressed. "
                "%s",
                fatal_type or "unknown", fatal,
            )
            return

        if restarts >= max_restarts:
            logger.error(
                "Watchdog: bridge is %s but max_restarts (%d) reached — giving up.",
                reason, max_restarts,
            )
            # Don't keep looping forever once we give up
            return

        logger.warning(
            "Watchdog: bridge is %s — restarting (attempt %d/%d, backoff %ds).",
            reason, restarts + 1, max_restarts, backoff,
        )

        try:
            if running:
                await bridge.stop_chat_bridge()
            await asyncio.sleep(backoff)
            await bridge.start_chat_bridge(bot_token)
            restarts += 1
            clean_since = time.monotonic()
            backoff = min(backoff * 2, 300)
            logger.info("Watchdog: bridge restarted successfully.")
        except Exception as e:
            logger.error("Watchdog: restart attempt failed: %s", type(e).__name__, exc_info=True)
            restarts += 1
            backoff = min(backoff * 2, 300)
