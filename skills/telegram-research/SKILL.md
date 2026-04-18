---
name: "telegram-research"
description: "Research and analyze Telegram chat conversations. Summarize chats, read message history, and track community discussions for knowledge gathering."
version: "1.0.0"
author: "AgentZero Telegram Plugin"
license: "MIT"
tags: ["telegram", "research", "summarization", "knowledge"]
triggers:
  - "telegram research"
  - "summarize telegram"
  - "analyze telegram"
  - "telegram chat summary"
  - "read messages from a supergroup topic"
  - "list forum topics"
  - "summarize a topic thread"
allowed_tools:
  - telegram_read
  - telegram_summarize
  - telegram_members
metadata:
  complexity: "intermediate"
  category: "research"
---

# Telegram Research Skill

Use Telegram tools to gather knowledge from Telegram chats.

## Workflow

1. **List chats** to discover available conversations:
   `telegram_read` with `action: chats`

2. **Get chat info** for details about a specific chat:
   `telegram_read` with `action: chat_info`, `chat_id: CHAT_ID`

3. **Read messages** from a chat:
   `telegram_read` with `action: messages`, `chat_id: CHAT_ID`, `limit: 50`

4. **Read messages from a forum topic**:
   `telegram_read` with `action: messages`, `chat_id: CHAT_ID`, `thread_id: THREAD_ID`, `limit: 50`

5. **List forum topics** in a supergroup:
   `telegram_read` with `action: topics`, `chat_id: CHAT_ID`

6. **Summarize** for a structured overview:
   `telegram_summarize` with `chat_id: CHAT_ID`

7. **Summarize a topic thread**:
   `telegram_summarize` with `chat_id: CHAT_ID`, `thread_id: THREAD_ID`

8. **Check members** — list administrators:
   `telegram_members` with `chat_id: CHAT_ID`

## Tips
- Start by listing chats to discover available conversations
- Chat IDs for groups are negative numbers
- Use `telegram_summarize` with `focus` to narrow analysis
- Summaries auto-save to memory by default
- Use `topics` action to discover forum topic thread IDs before reading topic-specific messages
