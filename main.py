import asyncio
import logging

from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

import database as db
from config import ADMIN_IDS, DUMMY_PORT

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

router = Router()

# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------

MAIN_MENU_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="💬 Join Anonymous Chat", callback_data="join_chat")]
])

CHAT_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🔕 Leave Chat Room")]],
    resize_keyboard=True,
)

ADMIN_MENU_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📊 System Stats", callback_data="adm_stats")],
    [InlineKeyboardButton(text="📢 Global Broadcast", callback_data="adm_broadcast")],
    [InlineKeyboardButton(text="🔨 Ban User", callback_data="adm_ban")],
    [InlineKeyboardButton(text="🔓 Unban User", callback_data="adm_unban")],
])

BACK_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="⬅️ Back to Admin Panel", callback_data="adm_back")]
])

LEAVE_TEXT = "🔕 Leave Chat Room"


# ---------------------------------------------------------------------------
# FSM States
# ---------------------------------------------------------------------------

class AdminStates(StatesGroup):
    waiting_broadcast = State()
    waiting_ban_id = State()
    waiting_unban_id = State()


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

def is_admin_pm(message: Message) -> bool:
    return (
        message.chat.type == "private"
        and message.from_user is not None
        and message.from_user.id in ADMIN_IDS
    )


def is_admin_pm_cb(cb: CallbackQuery) -> bool:
    return (
        cb.message is not None
        and cb.message.chat.type == "private"
        and cb.from_user.id in ADMIN_IDS
    )


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

async def exit_room(bot: Bot, user_id: int, user_doc: dict) -> None:
    room_id = user_doc.get("r_id")
    if room_id:
        await db.remove_user_from_room(user_id, room_id)
        await db.set_user_room(user_id, None)
        members = await db.get_room_members(room_id)
        for mid in members:
            if mid != user_id:
                try:
                    await bot.send_message(
                        mid,
                        f"🔔 *{user_doc['n']}* has left the chat.",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = await db.get_or_create_user(
        message.from_user.id,
        message.from_user.username,
    )
    if user["s"] == 0:
        await message.answer("🚫 You have been banned from this service.")
        return

    if user.get("r_id"):
        await message.answer(
            "You are currently in a chat room. Use *🔕 Leave Chat Room* to exit first.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=CHAT_KB,
        )
        return

    await message.answer(
        "👋 Welcome to *Anonymous Chat*!\n\nYour alias: *{}*\n\nJoin a room to start chatting anonymously.".format(user["n"]),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=MAIN_MENU_KB,
    )


# ---------------------------------------------------------------------------
# Join chat callback
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "join_chat")
async def cb_join_chat(cb: CallbackQuery) -> None:
    await cb.answer()
    user = await db.get_or_create_user(
        cb.from_user.id,
        cb.from_user.username,
    )
    if user["s"] == 0:
        await cb.message.answer("🚫 You have been banned from this service.")
        return

    if user.get("r_id"):
        await cb.message.answer(
            "You are already in a chat room.",
            reply_markup=CHAT_KB,
        )
        return

    room_id = await db.get_or_assign_room(cb.from_user.id)
    await db.set_user_room(cb.from_user.id, room_id)

    members = await db.get_room_members(room_id)
    member_count = len(members)

    await cb.message.answer(
        f"✅ You joined *{room_id}* as *{user['n']}*.\n👥 {member_count} user(s) in this room.\n\nSay hi — your messages are anonymous!",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=CHAT_KB,
    )

    for mid in members:
        if mid != cb.from_user.id:
            try:
                await cb.bot.send_message(
                    mid,
                    f"🔔 *{user['n']}* has joined the chat.",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Message routing
# ---------------------------------------------------------------------------

@router.message(F.text == LEAVE_TEXT)
async def leave_room(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    if not user or not user.get("r_id"):
        await message.answer(
            "You are not in any chat room.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await message.answer(
            "Return to the main menu whenever you're ready.",
            reply_markup=MAIN_MENU_KB,
        )
        return

    alias = user["n"]
    room_id = user["r_id"]
    members = await db.get_room_members(room_id)

    await db.remove_user_from_room(message.from_user.id, room_id)
    await db.set_user_room(message.from_user.id, None)

    await message.answer(
        "👋 You have left the chat room.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer(
        "Want to join again?",
        reply_markup=MAIN_MENU_KB,
    )

    for mid in members:
        if mid != message.from_user.id:
            try:
                await message.bot.send_message(
                    mid,
                    f"🔔 *{alias}* has left the chat.",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass


async def _broadcast_to_room(bot: Bot, sender_id: int, room_id: str, alias: str, message: Message) -> None:
    members = await db.get_room_members(room_id)
    for mid in members:
        if mid == sender_id:
            continue
        try:
            if message.text:
                await bot.send_message(
                    mid,
                    f"*[{alias}]:* {message.text}",
                    parse_mode=ParseMode.MARKDOWN,
                )
            elif message.photo:
                caption = f"*[{alias}]:* {message.caption}" if message.caption else f"*[{alias}]*"
                await bot.send_photo(mid, message.photo[-1].file_id, caption=caption, parse_mode=ParseMode.MARKDOWN)
            elif message.animation:
                caption = f"*[{alias}]:* {message.caption}" if message.caption else f"*[{alias}]*"
                await bot.send_animation(mid, message.animation.file_id, caption=caption, parse_mode=ParseMode.MARKDOWN)
            elif message.voice:
                await bot.send_voice(mid, message.voice.file_id, caption=f"*[{alias}]* 🎙️", parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            log.warning("Failed to relay to %s: %s", mid, e)


@router.message(F.text & ~F.text.startswith("/"))
async def route_text(message: Message) -> None:
    if message.text == LEAVE_TEXT:
        return
    user = await db.get_user(message.from_user.id)
    if not user or not user.get("r_id"):
        return
    if user["s"] == 0:
        return
    await _broadcast_to_room(message.bot, message.from_user.id, user["r_id"], user["n"], message)


@router.message(F.photo | F.animation | F.voice)
async def route_media(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    if not user or not user.get("r_id"):
        return
    if user["s"] == 0:
        return
    await _broadcast_to_room(message.bot, message.from_user.id, user["r_id"], user["n"], message)


# ---------------------------------------------------------------------------
# /admin command
# ---------------------------------------------------------------------------

@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    if not is_admin_pm(message):
        return
    await state.clear()
    await message.answer("🛡️ *Admin Panel*", parse_mode=ParseMode.MARKDOWN, reply_markup=ADMIN_MENU_KB)


# ---------------------------------------------------------------------------
# Admin callbacks
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "adm_back")
async def adm_back(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    await state.clear()
    await cb.answer()
    await cb.message.edit_text("🛡️ *Admin Panel*", parse_mode=ParseMode.MARKDOWN, reply_markup=ADMIN_MENU_KB)


@router.callback_query(F.data == "adm_stats")
async def adm_stats(cb: CallbackQuery) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    await cb.answer()

    total = await db.count_users()
    banned = await db.count_banned()
    active_chat = await db.count_active_chatters()
    rooms = await db.count_active_rooms()

    text = (
        "📊 *System Statistics*\n\n"
        f"👤 Total registered users: *{total}*\n"
        f"🚫 Banned users: *{banned}*\n"
        f"💬 Active chatters: *{active_chat}*\n"
        f"🏠 Active rooms: *{rooms}*"
    )
    await cb.message.edit_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=BACK_KB)


@router.callback_query(F.data == "adm_broadcast")
async def adm_broadcast_prompt(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    await cb.answer()
    await state.set_state(AdminStates.waiting_broadcast)
    await cb.message.edit_text(
        "📢 *Global Broadcast*\n\nSend the message (text or photo) you want to broadcast to all active users.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=BACK_KB,
    )


@router.message(AdminStates.waiting_broadcast)
async def adm_do_broadcast(message: Message, state: FSMContext) -> None:
    if not is_admin_pm(message):
        return
    await state.clear()

    users = await db.get_all_active_users()
    sent = 0
    failed = 0

    for user in users:
        uid = user["_id"]
        try:
            if message.photo:
                caption = message.caption or ""
                await message.bot.send_photo(uid, message.photo[-1].file_id, caption=f"📢 {caption}")
            elif message.text:
                await message.bot.send_message(uid, f"📢 *Broadcast:*\n\n{message.text}", parse_mode=ParseMode.MARKDOWN)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    await message.answer(
        f"✅ Broadcast complete.\n✔️ Sent: {sent} | ❌ Failed: {failed}",
        reply_markup=ADMIN_MENU_KB,
    )


@router.callback_query(F.data == "adm_ban")
async def adm_ban_prompt(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    await cb.answer()
    await state.set_state(AdminStates.waiting_ban_id)
    await cb.message.edit_text(
        "🔨 *Ban User*\n\nReply with the Telegram *User ID* to ban.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=BACK_KB,
    )


@router.message(AdminStates.waiting_ban_id)
async def adm_do_ban(message: Message, state: FSMContext) -> None:
    if not is_admin_pm(message):
        return

    if not message.text or not message.text.strip().lstrip("-").isdigit():
        await message.answer("⚠️ Please send a valid numeric User ID.")
        return

    target_id = int(message.text.strip())
    await state.clear()

    target = await db.get_user(target_id)
    if not target:
        await message.answer("❌ User not found.", reply_markup=ADMIN_MENU_KB)
        return

    await db.ban_user(target_id)

    if target.get("r_id"):
        await exit_room(message.bot, target_id, target)
        try:
            await message.bot.send_message(
                target_id,
                "🚫 You have been banned and removed from the chat room.",
                reply_markup=ReplyKeyboardRemove(),
            )
        except Exception:
            pass

    await message.answer(
        f"✅ User *{target_id}* ({target.get('n', 'unknown')}) has been banned.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ADMIN_MENU_KB,
    )


@router.callback_query(F.data == "adm_unban")
async def adm_unban_prompt(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    await cb.answer()
    await state.set_state(AdminStates.waiting_unban_id)
    await cb.message.edit_text(
        "🔓 *Unban User*\n\nReply with the Telegram *User ID* to unban.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=BACK_KB,
    )


@router.message(AdminStates.waiting_unban_id)
async def adm_do_unban(message: Message, state: FSMContext) -> None:
    if not is_admin_pm(message):
        return

    if not message.text or not message.text.strip().lstrip("-").isdigit():
        await message.answer("⚠️ Please send a valid numeric User ID.")
        return

    target_id = int(message.text.strip())
    await state.clear()

    target = await db.get_user(target_id)
    if not target:
        await message.answer("❌ User not found.", reply_markup=ADMIN_MENU_KB)
        return

    await db.unban_user(target_id)
    await message.answer(
        f"✅ User *{target_id}* ({target.get('n', 'unknown')}) has been unbanned.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ADMIN_MENU_KB,
    )


# ---------------------------------------------------------------------------
# Dummy web server (port binding for Render / web service platforms)
# ---------------------------------------------------------------------------

async def dummy_web_server() -> None:
    async def handle(_request: web.Request) -> web.Response:
        return web.Response(text="OK")

    app = web.Application()
    app.router.add_get("/", handle)
    app.router.add_get("/health", handle)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", DUMMY_PORT)
    await site.start()
    log.info("Dummy web server listening on port %s", DUMMY_PORT)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    from config import BOT_TOKEN

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    await dummy_web_server()

    log.info("Bot is starting…")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())
