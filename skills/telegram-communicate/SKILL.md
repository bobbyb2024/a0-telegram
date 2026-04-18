---
name: "telegram-communicate"
description: "Send messages and interact in Telegram chats via bot account. Reply to conversations, react to messages, forward content, and manage chat settings."
version: "1.0.0"
author: "AgentZero Telegram Plugin"
license: "MIT"
tags: ["telegram", "communication", "messaging"]
triggers:
  - "telegram send"
  - "send telegram message"
  - "reply on telegram"
  - "post to telegram"
  - "send message to a topic thread"
  - "create a forum topic"
  - "send a poll"
  - "send a sticker"
allowed_tools:
  - telegram_send
  - telegram_read
  - telegram_manage
  - telegram_members
metadata:
  complexity: "beginner"
  category: "communication"
---

# Telegram Communication Skill

Send messages and interact in Telegram chats via the bot account.

## Important
- Messages are sent via the **bot account** only
- Always read recent conversation before responding

## Workflow

1. **Read context first**:
   `telegram_read` with `action: messages`, `chat_id: CHAT_ID`, `limit: 20`

2. **Send a message**:
   `telegram_send` with `action: send`, `chat_id: CHAT_ID`, `content: text`

3. **Reply to someone**:
   `telegram_send` with `action: reply`, `chat_id: CHAT_ID`, `content: text`, `reply_to: MSG_ID`

4. **React to a message**:
   `telegram_send` with `action: react`, `chat_id: CHAT_ID`, `message_id: MSG_ID`, `emoji: 👍`

5. **Forward a message**:
   `telegram_send` with `action: forward`, `chat_id: DEST_ID`, `from_chat_id: SRC_ID`, `message_id: MSG_ID`

6. **Pin a message**:
   `telegram_manage` with `chat_id: CHAT_ID`, `action: pin`, `message_id: MSG_ID`

7. **Send message to a forum topic thread**:
   `telegram_send` with `action: send`, `chat_id: CHAT_ID`, `content: TEXT`, `message_thread_id: THREAD_ID`

8. **Create a forum topic**:
   `telegram_manage` with `action: create_topic`, `chat_id: CHAT_ID`, `name: TOPIC_NAME`

9. **Send a poll**:
   `telegram_send` with `action: poll`, `chat_id: CHAT_ID`, `content: QUESTION`, `options: ["A", "B", "C"]`

10. **Send a sticker**:
    `telegram_send` with `action: sticker`, `chat_id: CHAT_ID`, `sticker: FILE_ID`
