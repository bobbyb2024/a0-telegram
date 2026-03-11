# Telegram Integration Plugin — Quick Start

## Prerequisites

- Agent Zero instance (Docker or local)
- Telegram account

## Step 1: Create a Bot with BotFather

1. Open Telegram and search for [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts
3. Choose a name (e.g., "My Agent Zero Bot")
4. Choose a username (must end in `bot`, e.g., `my_a0_bot`)
5. Copy the bot token (looks like `1234567890:ABCDefGHIJklmnoPQRSTuvwxyz`)

### Optional: Configure Bot Settings

Send these commands to @BotFather:
- `/setprivacy` — Set to "Disable" if the bot should read all group messages
- `/setjoingroups` — Set to "Enable" to allow adding to groups
- `/setcommands` — Not needed (the plugin uses `!` commands, not `/` commands)

## Step 2: Install the Plugin

```bash
# From inside the Agent Zero container:
cd /tmp
# Copy plugin files, then:
./install.sh

# Or manually:
cp -r a0-telegram/ /a0/usr/plugins/telegram/
ln -sf /a0/usr/plugins/telegram /a0/plugins/telegram
touch /a0/usr/plugins/telegram/.toggle-1
```

## Step 3: Configure

1. Open Agent Zero WebUI
2. Go to Settings > External Services > Telegram Integration
3. Paste your bot token
4. Click "Save Telegram Settings"
5. Click "Test Connection" on the dashboard

Or set the environment variable:
```bash
export TELEGRAM_BOT_TOKEN="1234567890:ABCDefGHIJklmnoPQRSTuvwxyz"
```

## Step 4: First Use

Ask the agent:
> "List my Telegram chats"

> "Read the last 20 messages from Telegram chat -1001234567890"

> "Send 'Hello from Agent Zero!' to Telegram chat -1001234567890"

## Step 5: Set Up Chat Bridge (Optional)

1. Add the bot to a Telegram group, or start a private chat with it
2. Tell the agent: "Add Telegram chat -1001234567890 to the chat bridge"
3. Tell the agent: "Start the Telegram chat bridge"
4. Send a message to the bot — it will respond via Agent Zero's LLM

## Getting Chat IDs

- **Private chats**: Send a message to the bot, then use `telegram_read` with `action: chats`
- **Groups**: Add the bot to the group, send a message, then use `telegram_read` with `action: chats`
- **Via @userinfobot**: Forward a message from the chat to [@userinfobot](https://t.me/userinfobot)
- Group/supergroup IDs are negative numbers (e.g., `-1001234567890`)
