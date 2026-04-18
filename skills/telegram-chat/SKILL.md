---
name: "telegram-chat"
description: "Use Telegram as a chat interface to Agent Zero's LLM. Set up a persistent bot that listens in designated chats and routes messages through the agent."
version: "1.0.0"
author: "AgentZero Telegram Plugin"
license: "MIT"
tags: ["telegram", "chat", "bridge", "llm"]
triggers:
  - "telegram chat bridge"
  - "chat through telegram"
  - "telegram llm chat"
  - "talk to agent on telegram"
  - "bridge a supergroup topic"
  - "map topic to project"
  - "add a topic to the bridge"
allowed_tools:
  - telegram_chat
  - telegram_read
metadata:
  complexity: "intermediate"
  category: "communication"
---

# Telegram Chat Bridge Skill

Set up Telegram as a chat frontend to Agent Zero's LLM.

## Setup Workflow

### Step 1: Find the Chat
List available chats to identify where to set up the bridge:
```json
{"tool": "telegram_read", "args": {"action": "chats"}}
```

### Step 2: Add the Chat
Designate a chat for LLM bridging:
```json
{"tool": "telegram_chat", "args": {"action": "add_chat", "chat_id": "-1001234567890", "label": "llm-chat"}}
```

### Step 3: Start the Bot
Launch the chat bridge:
```json
{"tool": "telegram_chat", "args": {"action": "start"}}
```

### Step 4: Verify
Check that the bot is connected:
```json
{"tool": "telegram_chat", "args": {"action": "status"}}
```

### Optional: Bridge a Supergroup Topic
To route only a specific forum topic thread to Agent Zero:
```json
{"tool": "telegram_chat", "args": {"action": "add_chat", "chat_id": "-1001234567890", "thread_id": "42", "label": "Sprint Planning"}}
```

### Optional: Map Topic to Project
To associate a forum topic with a project identifier:
```json
{"tool": "telegram_manage", "args": {"action": "map_topic", "chat_id": "-1001234567890", "thread_id": "42", "project_id": "proj_sprint42", "name": "Sprint 42"}}
```

## How It Works
- The bot uses long polling for real-time message delivery (no public URL needed)
- Each designated chat gets its own conversation context
- Messages are prefixed with the sender's Telegram display name
- The bot shows a typing indicator while the LLM processes
- Long responses are automatically split into 4096-char chunks
- Photo attachments are forwarded to the LLM for visual analysis in elevated mode

## Security
- Default mode is **restricted** (conversation only, no tools)
- Elevated mode requires `!auth <key>` and must be enabled in config
- Use `allowed_users` to restrict who can interact with the bot
- Sessions expire after configurable timeout (default: 5 minutes)

## Tips
- Create a dedicated group or chat with the bot
- The bot only responds in chats you explicitly add
- Use `stop` and `start` to restart the bot if issues arise
- Enable `auto_start` in config to launch the bot automatically on agent startup
- For supergroups with forum topics, use `thread_id` to bridge individual topics rather than the whole group
- Topic keys are stored as `{chat_id}:topic:{thread_id}` — visible in the `list` output
- Use `telegram_manage` with `list_topics` to see all topic-to-project mappings
