# 🌐 Anonymous Group Chat Bot

Burmese-language anonymous group chatting bot built with **aiogram v3**, **MongoDB (motor)**, and **aiohttp**.

## 📁 File Structure

```
├── main.py          # Bot entry point — all handlers, FSM admin panel, health server
├── database.py      # MongoDB async operations (users + rooms)
├── config.py        # Env vars + ALIASES list
├── requirements.txt # Python dependencies
├── .python-version  # Pins Python 3.11.9 for Render
├── .env.example     # Secrets template
└── anon_bot/        # (same files — backup copy)
```

## ⚙️ Setup

### 1. Install
```bash
pip install -r requirements.txt
```

### 2. Configure
```bash
cp .env.example .env
```

| Variable    | Description                                     |
|-------------|--------------------------------------------------|
| `BOT_TOKEN` | Telegram Bot token from @BotFather               |
| `ADMIN_IDS` | Comma-separated admin user IDs e.g. `123,456`    |
| `MONGO_URI` | MongoDB Atlas connection string                  |
| `DB_NAME`   | Database name (default: `anon_chat_bot`)         |

### 3. Run
```bash
python main.py
```

## 🤖 User Commands

| Command   | Description               |
|-----------|---------------------------|
| `/start`  | Welcome + join room button |

## 🛡️ Admin Commands (PM only)

| Command  | Description                          |
|----------|--------------------------------------|
| `/admin` | Opens FSM admin panel with buttons   |

**Admin Panel Buttons:**
- 📊 Stats — Total, Active, Banned users + Active rooms
- 📢 Broadcast — Send text or photo to all active users (0.05s delay)
- 🔨 Ban — Prompts for User ID, force-evicts from room
- 🔓 Unban — Prompts for User ID, notifies user
- ⬅️ Back — Returns to main admin menu

## ❤️ Mutual Identity Reveal

1. User A clicks **"❤️ အကောင့်ချင်းချိတ်ရန်"**
2. All room members are notified anonymously
3. If User B also clicks it → **MATCH!** Both get each other's `@username`

## 🗄️ MongoDB Schema

### `u` (users)
```json
{"_id": 123, "u": "username", "n": "မြေခွေးလေး 🦊", "r_id": "room_1", "s": 1, "j": "...", "req": []}
```

### `r` (rooms)
```json
{"_id": "room_1", "u_ids": [123, 456], "c": 2}
```
