---
status: development
repo: https://github.com/bobbyb2024/a0-telegram
index_pr: https://github.com/agent0ai/a0-plugins/pull/61
published_date: 2026-03-11
version: 1.2.0
---

# Release Status

## Publication
- **GitHub**: https://github.com/spinnakergit/a0-telegram
- **Plugin Index PR**: [#61](https://github.com/agent0ai/a0-plugins/pull/61) (CI passed)
- **Published**: 2026-03-11

## Verification Completed
- **Automated Tests**: 58/58 PASS (regression suite, 2026-03-11)
- **Human Verification**: 76/76 PASS, 0 failures, 0 skipped (2026-03-11)
  - Phase 1-2: WebUI + Config & Connection: 16/16
  - Phase 3-7: Tools (read, send, members, summarize, manage): 19/19
  - Phase 8-12: Bridge (lifecycle, restricted, auth, elevated, session): 23/23
  - Phase 13-14: Security (injection + access control): 9/9
  - Phase 15-16: Edge cases + documentation: 9/9
- **Security Assessment**: PASS (2026-03-11)
  - 1 Critical fixed (VULN-001: auth keys in message_store.json)
  - 1 High fixed (VULN-002: world-readable message_store.json)
  - 2 Medium accepted (framework-level CSRF dependencies)
  - 2 Low accepted (minimal risk)
  - 3 Informational (framework-level issues)

## Issues Found & Fixed During Verification
| # | Issue | Severity | Status |
|---|-------|----------|--------|
| 1 | `!status` command not recognized | Low | Fixed |
| 2 | Elevated mode infection check false positive | High | Fixed |
| 3 | Config API merge behavior for allowlist | Low | Documented (framework limitation) |
| 4 | Auto-start extension non-functional | High | Fixed (complete rewrite) |
| 5 | README security section insufficient | Medium | Fixed |
| 6 | getUpdates conflict with simultaneous polling | High | Fixed (guard function) |

## Changelog

### v1.2.0 — 2026-04-17
Major feature release: emoji reactions, streaming responses, supergroup/forum topic integration, and many reliability improvements.

**New Features**
- **Emoji reaction lifecycle** (Track 1): Bot reacts to incoming messages with 👍→🤔→✅/❌ to signal received/processing/done/error. Configurable emojis. Silently ignored if reactions are disabled in the chat.
- **Streaming responses** (Track 2): Progressive message edits stream the agent's reply in real time. Three modes: word / sentence / paragraph. Debounced at configurable edit_interval_ms. Plain text with `▌` cursor during streaming; full HTML on final edit.
- **Supergroup & forum topic integration** (Track 3): Each forum topic routes to an isolated Agent Zero project context using canonical `{chat_id}:topic:{thread_id}` keys. Auto-creates context on new topic creation. Topic map persisted in chat state.
- **Inline button approvals** (Track 4): Agent can request Yes/No confirmations via inline keyboard buttons. `request_approval()` suspends until user clicks or timeout expires. New `telegram_send` actions: `send_buttons`, `stop_poll`.
- **Edited message handling** (Track 5): When a user edits their message, the bot deletes its prior reply and regenerates the response.
- **Bot slash commands** (Track 6): `/auth`, `/deauth`, `/status`, `/help`, `/newcontext`, `/cancel` registered via `setMyCommands`. Usable from any Telegram client command menu.
- **Webhook mode** (Track 7): New `api/telegram_webhook_api.py` receives Telegram Update POSTs. Validates `X-Telegram-Bot-Api-Secret-Token`. Config toggle in WebUI. Falls back gracefully when bridge not running.
- **Voice transcription** (Track 8): Voice messages and audio files downloaded and transcribed via the utility model before being routed to the agent.
- **Document / file handling** (Track 9): Documents and photos in elevated mode are downloaded and attached to the agent context.
- **Conversation persistence** (Track 10): New `helpers/conversation_store.py` — write-through cache persists conversation history to disk. Survives agent restarts.
- **Bridge watchdog** (Track 11): New watchdog coroutine in the auto-start extension monitors bridge health and restarts with exponential backoff (max 5 restarts, resets after 1 hour clean run).
- **Concurrency control** (Track 12): `asyncio.Semaphore` limits simultaneous agent responses (configurable `max_concurrent`, default 3).
- **Polls** (Track 13): New `telegram_send` action `poll` and `stop_poll`. Full poll creation support.
- **Privacy mode detection** (Track 14): `/api/plugins/telegram/telegram_test` now returns `privacy_mode`, `can_read_all_group_messages`, and a human-readable `privacy_advisory` when privacy mode is ON. WebUI shows the advisory banner.
- **Sticker & delete actions** (Track 15): New `telegram_send` actions `sticker` and `delete`.
- **Per-user context isolation** (Track 16): Optional `per_user_context` config key scopes conversation history to individual Telegram users within a chat.
- **Auto-summary on context expiry** (Track 17): `touch_topic()` tracks last activity per topic; future cron hook can trigger auto-summary.

**New Files**
- `helpers/rate_limiter.py` — token bucket rate limiter (global / per-chat / edit / react)
- `helpers/button_builder.py` — helpers for building inline keyboard markup
- `helpers/conversation_store.py` — persistent conversation history (write-through disk cache)
- `helpers/stream_response.py` — streaming edit loop with debouncing
- `api/telegram_webhook_api.py` — webhook POST receiver

**Modified Files**
- `helpers/telegram_client.py` — 20 new API methods (edit, delete, buttons, webhook, forum topics, polls, stickers, reactions)
- `helpers/telegram_bridge.py` — complete rewrite with all new track implementations
- `helpers/sanitize.py` — added `validate_topic_key()` for composite `{chat_id}:topic:{thread_id}` keys
- `helpers/message_store.py` — dual-key storage for topic messages; `get_messages()` topic param
- `helpers/format_telegram.py` — `format_streaming_chunk()` / `format_streaming_final()` for safe partial-text streaming
- `helpers/poll_state.py` — topic-key support in watch_chats
- `tools/telegram_send.py` — `edit`, `delete`, `send_buttons`, `poll`, `stop_poll`, `sticker` actions; `message_thread_id` threading
- `tools/telegram_read.py` — `thread_id` param, `topics` action
- `tools/telegram_chat.py` — topic-key aware add/remove/list
- `tools/telegram_manage.py` — `map_topic`, `unmap_topic`, `list_topics`, `create_topic`, `rename_topic`, `close_topic`, `reopen_topic`
- `tools/telegram_summarize.py` — topic-key lookup, thread label in memory
- `api/telegram_bridge_api.py` — `list_topics`, `map_topic`, `unmap_topic` actions
- `api/telegram_test.py` — privacy mode detection and advisory
- `extensions/python/agent_init/_10_telegram_chat.py` — watchdog coroutine with exponential backoff
- `webui/config.html` — reactions, streaming, supergroups, approvals, webhook, watchdog config sections
- `webui/main.html` — topic count stat, privacy advisory banner, webhook mode indicator, restart count, topics dashboard panel
- `default_config.yaml` — all new config sections with documented defaults
- `plugin.yaml` — version bump 1.1.0 → 1.2.0
- All 5 prompt files — new parameters and JSON examples
- All 3 skill files — new trigger patterns and capabilities

### v1.1.0 — 2026-03-25
Standards conformance release.
- **config.html**: Migrated to A0's standard Alpine.js `x-model` settings framework (removes custom fetchApi/save logic)
- **telegram_config_api.py**: Simplified to only handle `generate_auth_key` (config CRUD now handled by framework)
- **main.html**: Fixed CSRF timing bug (lazy fetchApi) and stale addEventListener on component reload (inline onclick + window namespace)
- **install.sh**: Skip file copy when installed in-place via Plugin Hub
- **hooks.py**: New install/uninstall lifecycle hooks with proper logging
- **docs/SETUP.md**: New — full credential setup guide (BotFather, 3 install options, credential mapping, rate limits, troubleshooting)
- **docs/QUICKSTART.md**: Added Known Behaviors, Troubleshooting table, Plugin Hub install option, credential mapping
- **docs/DEVELOPMENT.md**: Added Alpine.js config pattern, main.html dashboard pattern, architecture decisions, testing section
- **docs/README.md**: Added SETUP link, verification badge, updated API endpoint descriptions
- **thumbnail.png**: Added (256x256 RGBA, transparent corners)
- **.gitignore**: Added `.toggle-*`, `tests/*.json`
- **README.md**: Added verification badge section
- All `print()` in hooks.py replaced with `logging.getLogger()`

### v1.0.0 — 2026-03-11
Initial release.
- 6 tools: read, send, members, summarize, manage, chat
- 3 API endpoints: test, config, bridge
- Chat bridge with dual-mode security (restricted/elevated)
- 3 skills: research, communicate, chat
- Auto-start extension for chat bridge
- Full verification: 58/58 regression, 76/76 HV, security assessed
