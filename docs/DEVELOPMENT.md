# Telegram Integration Plugin — Development Guide

## Project Structure

```
a0-telegram/
├── plugin.yaml           # Plugin manifest
├── default_config.yaml   # Default settings
├── initialize.py         # Dependency installer
├── install.sh            # Deployment script
├── helpers/              # Shared modules
│   ├── __init__.py
│   ├── telegram_client.py   # REST API client wrapper
│   ├── telegram_bridge.py   # Chat bridge bot (polling-based)
│   ├── sanitize.py          # Prompt injection defense
│   └── poll_state.py        # Background polling state
├── tools/                # Tool implementations (6)
│   ├── telegram_read.py
│   ├── telegram_send.py
│   ├── telegram_members.py
│   ├── telegram_summarize.py
│   ├── telegram_manage.py
│   └── telegram_chat.py
├── prompts/              # Tool prompt definitions (6)
├── api/                  # API handlers (3)
│   ├── telegram_test.py
│   ├── telegram_config_api.py
│   └── telegram_bridge_api.py
├── webui/                # Dashboard and settings UI
│   ├── main.html
│   └── config.html
├── skills/               # Skill definitions (3)
├── extensions/           # Agent init hooks
│   └── python/agent_init/_10_telegram_chat.py
├── tests/                # Regression test suite
│   └── regression_test.sh
└── docs/                 # Documentation
```

## Development Setup

1. Start the dev container:
   ```bash
   docker start agent-zero-dev
   ```

2. Install the plugin:
   ```bash
   docker cp a0-telegram/. agent-zero-dev:/a0/usr/plugins/telegram/
   docker exec agent-zero-dev ln -sf /a0/usr/plugins/telegram /a0/plugins/telegram
   docker exec agent-zero-dev touch /a0/usr/plugins/telegram/.toggle-1
   docker exec agent-zero-dev supervisorctl restart run_ui
   ```

3. Run tests:
   ```bash
   ./tests/regression_test.sh agent-zero-dev 50083
   ```

## Adding a New Tool

1. Create `tools/telegram_<action>.py` with a Tool subclass:
   ```python
   from helpers.tool import Tool, Response

   class TelegramAction(Tool):
       async def execute(self, **kwargs) -> Response:
           # Implementation
           return Response(message="Result", break_loop=False)
   ```

2. Create `prompts/agent.system.tool.telegram_<action>.md` with JSON examples

3. Add import test to `tests/regression_test.sh`

4. Update documentation

## Code Style

- Follow existing patterns from Discord/Signal plugins
- Use `async/await` for all I/O operations
- Always close client connections (`await client.close()`)
- Return `Response(message=..., break_loop=False)` from tools
- Sanitize ALL external content before passing to LLM
- All API handlers must have `requires_csrf() -> True`
- WebUI must use `data-tg=` attributes, not bare IDs
- WebUI must use `globalThis.fetchApi || fetch`

## Key Patterns

### Tool Implementation
```python
from helpers.tool import Tool, Response
from plugins.telegram.helpers.telegram_client import TelegramClient, get_telegram_config
from plugins.telegram.helpers.sanitize import require_auth

class MyTool(Tool):
    async def execute(self, **kwargs) -> Response:
        config = get_telegram_config(self.agent)
        try:
            require_auth(config)
        except ValueError as e:
            return Response(message=f"Auth error: {e}", break_loop=False)

        client = TelegramClient.from_config(agent=self.agent)
        # ... use client ...
        await client.close()
        return Response(message="Done", break_loop=False)
```

### API Handler
```python
from helpers.api import ApiHandler, Request, Response

class MyApi(ApiHandler):
    @classmethod
    def requires_csrf(cls) -> bool:
        return True  # MANDATORY

    async def process(self, input: dict, request: Request) -> dict | Response:
        return {"ok": True}
```
