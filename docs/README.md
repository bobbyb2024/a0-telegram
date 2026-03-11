# Telegram Integration Plugin Documentation

## Overview

Send, receive, and manage messages via Telegram Bot API with real-time chat bridge support.

## Contents

- [Quick Start](QUICKSTART.md) — Installation and first-use guide
- [Development](DEVELOPMENT.md) — Contributing and development setup

## Architecture

```
┌─────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Telegram    │────>│  telegram_client │────>│  Telegram Bot   │
│  Bot API     │<────│  (REST wrapper)  │<────│  API Server     │
└─────────────┘     └─────────────────┘     └─────────────────┘
                           │
                    ┌──────┴──────┐
                    │             │
              ┌─────┴─────┐ ┌────┴─────┐
              │  Tools (6) │ │  APIs (3) │
              │ read/send/ │ │ test/     │
              │ members/   │ │ config/   │
              │ summarize/ │ │ bridge    │
              │ manage/    │ └──────────┘
              │ chat       │
              └────────────┘

Chat Bridge:
┌──────────┐     ┌──────────────────┐     ┌───────────────┐
│ Telegram │────>│ telegram_bridge   │────>│ Agent Zero    │
│ Users    │<────│ (polling bot)     │<────│ LLM / Agent   │
└──────────┘     └──────────────────┘     └───────────────┘
                  │ Restricted: call_utility_model (no tools)
                  │ Elevated:   context.communicate (full agent)
```

### Components

- **telegram_client.py**: Lightweight REST wrapper around the Telegram Bot API using aiohttp
- **telegram_bridge.py**: Persistent bot using python-telegram-bot with polling for the chat bridge
- **sanitize.py**: Prompt injection defense (NFKC normalization, zero-width stripping, injection pattern blocking)
- **poll_state.py**: Persistent state for background message watching

### Security Layers

1. **Sanitization**: All external content normalized and checked for injection patterns
2. **CSRF protection**: All API endpoints require CSRF tokens
3. **Token masking**: Bot tokens masked in API responses and WebUI
4. **User allowlist**: Chat bridge only responds to authorized users
5. **Rate limiting**: Per-user message rate limits in chat bridge
6. **Auth key**: HMAC constant-time comparison for elevated mode authentication
7. **Session timeout**: Elevated sessions auto-expire (default: 5 minutes)

## Tools

| Tool | Description |
|------|-------------|
| `telegram_read` | Read messages, list chats, get chat info |
| `telegram_send` | Send messages, photos, reactions, forward |
| `telegram_members` | List group administrators |
| `telegram_summarize` | Summarize chat conversations with LLM |
| `telegram_manage` | Pin/unpin messages, set title/description |
| `telegram_chat` | Chat bridge control (start/stop/status) |

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/plugins/telegram/telegram_test` | GET/POST | Test bot connection |
| `/api/plugins/telegram/telegram_config_api` | GET/POST | Read/write config (token masked) |
| `/api/plugins/telegram/telegram_bridge_api` | POST | Chat bridge start/stop/status |

## Skills

| Skill | Description |
|-------|-------------|
| `telegram-research` | Read and analyze chat history |
| `telegram-communicate` | Send messages and manage chats |
| `telegram-chat` | Interactive chat bridge operation |
