# 🤖 Telegram Face Swap Bot

AI-powered Face Swap Bot built with **aiogram v3**, **MongoDB**, and **Hugging Face Gradio API**.

---

## 📁 File Structure

```
bot/
├── main.py          # Bot entry point, all handlers
├── config.py        # Environment variable config
├── database.py      # MongoDB async operations (motor)
├── face_swap.py     # Hugging Face / Gradio integration
├── requirements.txt # Python dependencies
├── .env.example     # Environment variable template
└── README.md        # This file
```

---

## ⚙️ Setup Instructions

### 1. Prerequisites

- Python 3.11+
- A Telegram Bot token from [@BotFather](https://t.me/BotFather)
- A MongoDB connection string (free tier at [MongoDB Atlas](https://www.mongodb.com/cloud/atlas))
- Your Telegram User ID (get it from [@userinfobot](https://t.me/userinfobot))

### 2. Install Dependencies

```bash
cd bot
pip install -r requirements.txt
```

### 3. Configure Environment Variables

```bash
cp .env.example .env
```

Edit `.env` and fill in your values:

| Variable   | Description                                         |
|------------|-----------------------------------------------------|
| `BOT_TOKEN`| Your Telegram Bot token from BotFather              |
| `ADMIN_ID` | Your Telegram numeric user ID                       |
| `MONGO_URI`| MongoDB connection string                           |
| `DB_NAME`  | Database name (default: `faceswap_bot`)             |
| `HF_SPACE` | Hugging Face Space slug (default: `felixrosberg/face-swap`) |

### 4. Run the Bot

```bash
python main.py
```

---

## 🤖 Bot Commands

### User Commands
| Command   | Description                        |
|-----------|------------------------------------|
| `/start`  | Welcome message + bot features     |
| `/swap`   | Start a face swap session          |
| `/cancel` | Cancel current session             |
| `/help`   | Help and usage guide               |

### Admin-Only Commands
| Command                                          | Description                          |
|--------------------------------------------------|--------------------------------------|
| `/admin`                                         | Admin panel with bot stats           |
| `/userlist`                                      | List all registered users            |
| `/broadcast all <msg>`                           | Broadcast to all users               |
| `/broadcast <user_id> <msg>`                     | Send message to specific user        |
| `/ban <user_id>`                                 | Ban a user                           |
| `/unban <user_id>`                               | Unban a user                         |
| `/setwelcome <text> \| <btn_text> \| <btn_url>` | Set custom welcome message + button  |

---

## 🔄 Face Swap Flow

1. User sends `/swap`
2. Bot asks for **Source Photo** (face to copy)
3. User sends source photo
4. Bot asks for **Target Photo or Video** (face to paste onto)
5. User sends target media
6. Bot sends result to Hugging Face Space via `gradio_client`
7. Result is returned to the user

---

## 🗄️ MongoDB Schema

### `users` collection
```json
{
  "_id": 123456789,
  "u": "username",
  "j": "2024-01-01T00:00:00Z",
  "s": 1,
  "p_c": 42,
  "v_c": 7
}
```

### `settings` collection
```json
{
  "_id": "welcome",
  "text": "Custom welcome message",
  "btn_text": "Button Label",
  "btn_url": "https://example.com"
}
```

---

## 🌐 Changing the Hugging Face Space

The default space is `felixrosberg/face-swap`. If it's unavailable, set `HF_SPACE` in your `.env` to another public Gradio-based face swap space.

The `face_swap.py` calls two API endpoints:
- `/run_swap` — for photos
- `/run_swap_video` — for videos

You may need to adjust the `api_name` parameters in `face_swap.py` to match the chosen space's API schema.

---

## 🚀 Deployment Options

- **VPS/Server**: Run with `screen`, `tmux`, or `systemd`
- **Railway**: Add environment variables in dashboard, deploy from GitHub
- **Render**: Free tier available, connect GitHub repo
