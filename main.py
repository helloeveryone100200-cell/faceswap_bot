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
    [InlineKeyboardButton(text="💬 Join Anonymous Chat", callback_data="join_chat")],
    [InlineKeyboardButton(text="🎲 Find Stranger (1-on-1)", callback_data="find_stranger")],
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
NEXT_TEXT  = "⏭️ Next Stranger"
STOP_TEXT  = "🔚 Stop Chat"

STRANGER_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=NEXT_TEXT), KeyboardButton(text=STOP_TEXT)]],
    resize_keyboard=True,
)


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

    if user.get("p_id"):
        await message.answer(
            "You are in a 1-on-1 stranger chat. Use *🔚 Stop Chat* to exit first.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=STRANGER_KB,
        )
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


# ---------------------------------------------------------------------------
# Stranger chat helpers
# ---------------------------------------------------------------------------

async def _disconnect_stranger(bot: Bot, user_id: int, partner_id: int, notify_partner: bool = True) -> None:
    await db.set_partner(user_id, None)
    await db.set_partner(partner_id, None)
    if notify_partner:
        try:
            await bot.send_message(
                partner_id,
                "🔌 Your chat partner has disconnected.",
                reply_markup=ReplyKeyboardRemove(),
            )
            await bot.send_message(partner_id, "Find someone new?", reply_markup=MAIN_MENU_KB)
        except Exception:
            pass


async def _send_to_stranger(bot: Bot, partner_id: int, alias: str, message: Message) -> None:
    try:
        if message.text:
            await bot.send_message(partner_id, f"*[{alias}]:* {message.text}", parse_mode=ParseMode.MARKDOWN)
        elif message.photo:
            caption = f"*[{alias}]:* {message.caption}" if message.caption else f"*[{alias}]*"
            await bot.send_photo(partner_id, message.photo[-1].file_id, caption=caption, parse_mode=ParseMode.MARKDOWN)
        elif message.animation:
            caption = f"*[{alias}]:* {message.caption}" if message.caption else f"*[{alias}]*"
            await bot.send_animation(partner_id, message.animation.file_id, caption=caption, parse_mode=ParseMode.MARKDOWN)
        elif message.voice:
            await bot.send_voice(partner_id, message.voice.file_id, caption=f"*[{alias}]* 🎙️", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        log.warning("Failed to relay to stranger %s: %s", partner_id, e)


async def _do_find(bot: Bot, user_id: int, user: dict) -> None:
    await db.enter_queue(user_id)
    partner_id = await db.find_and_match(user_id)

    if partner_id is None:
        waiting = await db.count_waiting()
        await bot.send_message(
            user_id,
            f"🔍 Searching for a stranger… ({waiting} in queue)\n\nUse *🔚 Stop Chat* to cancel.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=STRANGER_KB,
        )
        return

    partner = await db.get_user(partner_id)
    if not partner:
        await db.leave_queue(user_id)
        await bot.send_message(user_id, "⚠️ Match error. Please try again.", reply_markup=MAIN_MENU_KB)
        return

    await db.set_partner(user_id, partner_id)
    await db.set_partner(partner_id, user_id)
    await db.leave_queue(user_id)

    await bot.send_message(
        user_id,
        f"🎲 Connected with *{partner['n']}*!\n\nSay hi — they don't know who you are. 😊",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=STRANGER_KB,
    )
    await bot.send_message(
        partner_id,
        f"🎲 Connected with *{user['n']}*!\n\nSay hi — they don't know who you are. 😊",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=STRANGER_KB,
    )


# /find command & callback

@router.message(Command("find"))
async def cmd_find(message: Message) -> None:
    user = await db.get_or_create_user(message.from_user.id, message.from_user.username)
    if user["s"] == 0:
        await message.answer("🚫 You are banned.")
        return
    if user.get("r_id"):
        await message.answer("⚠️ Leave the group chat room first.", reply_markup=CHAT_KB)
        return
    if user.get("p_id"):
        await message.answer("⚠️ You are already in a 1-on-1 chat.", reply_markup=STRANGER_KB)
        return
    await _do_find(message.bot, message.from_user.id, user)


@router.callback_query(F.data == "find_stranger")
async def cb_find_stranger(cb: CallbackQuery) -> None:
    await cb.answer()
    user = await db.get_or_create_user(cb.from_user.id, cb.from_user.username)
    if user["s"] == 0:
        await cb.message.answer("🚫 You are banned.")
        return
    if user.get("r_id"):
        await cb.message.answer("⚠️ Leave the group chat room first.", reply_markup=CHAT_KB)
        return
    if user.get("p_id"):
        await cb.message.answer("⚠️ Already in a 1-on-1 chat.", reply_markup=STRANGER_KB)
        return
    await _do_find(cb.bot, cb.from_user.id, user)


# ⏭️ Next Stranger

@router.message(F.text == NEXT_TEXT)
async def next_stranger(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    if not user:
        return
    if user.get("p_id"):
        await _disconnect_stranger(message.bot, message.from_user.id, user["p_id"])
    await db.leave_queue(message.from_user.id)
    await message.answer("🔄 Looking for a new stranger…", reply_markup=STRANGER_KB)
    await _do_find(message.bot, message.from_user.id, user)


# 🔚 Stop Chat

@router.message(F.text == STOP_TEXT)
async def stop_stranger(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    if not user:
        return
    if user.get("p_id"):
        await _disconnect_stranger(message.bot, message.from_user.id, user["p_id"])
    await db.leave_queue(message.from_user.id)
    await message.answer("👋 You have left the stranger chat.", reply_markup=ReplyKeyboardRemove())
    await message.answer("Back to the main menu:", reply_markup=MAIN_MENU_KB)


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
    if message.text in (LEAVE_TEXT, NEXT_TEXT, STOP_TEXT):
        return
    user = await db.get_user(message.from_user.id)
    if not user or user["s"] == 0:
        return
    if user.get("p_id"):
        await _send_to_stranger(message.bot, user["p_id"], user["n"], message)
    elif user.get("r_id"):
        await _broadcast_to_room(message.bot, message.from_user.id, user["r_id"], user["n"], message)


@router.message(F.photo | F.animation | F.voice)
async def route_media(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    if not user or user["s"] == 0:
        return
    if user.get("p_id"):
        await _send_to_stranger(message.bot, user["p_id"], user["n"], message)
    elif user.get("r_id"):
        await _broadcast_to_room(message.bot, message.from_user.id, user["r_id"], user["n"], message)


# ---------------------------------------------------------------------------
# /admin command
# ---------------------------------------------------------------------------

PER_PAGE = 15


def build_userlist_text(users: list[dict], page: int, total: int) -> str:
    total_pages = max(1, -(-total // PER_PAGE))
    lines = [f"👥 *User List* — Page {page + 1}/{total_pages} (Total: {total})\n"]
    for u in users:
        status = "🚫" if u["s"] == 0 else ("💬" if u.get("r_id") else "✅")
        uname = f"@{u['u']}" if u.get("u") else "no username"
        lines.append(f"{status} *{u['n']}* — {uname} (`{u['_id']}`)")
    return "\n".join(lines)


def build_userlist_kb(page: int, total: int) -> InlineKeyboardMarkup:
    total_pages = max(1, -(-total // PER_PAGE))
    buttons = []
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️ Prev", callback_data=f"ul_page:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="ul_noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Next ▶️", callback_data=f"ul_page:{page + 1}"))
    buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="⬅️ Back to Admin Panel", callback_data="adm_back")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("userlist"))
async def cmd_userlist(message: Message) -> None:
    if not is_admin_pm(message):
        return
    users, total = await db.get_users_paginated(0, PER_PAGE)
    await message.answer(
        build_userlist_text(users, 0, total),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=build_userlist_kb(0, total),
    )


@router.callback_query(F.data.startswith("ul_page:"))
async def cb_userlist_page(cb: CallbackQuery) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    await cb.answer()
    page = int(cb.data.split(":")[1])
    users, total = await db.get_users_paginated(page, PER_PAGE)
    await cb.message.edit_text(
        build_userlist_text(users, page, total),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=build_userlist_kb(page, total),
    )


@router.callback_query(F.data == "ul_noop")
async def cb_userlist_noop(cb: CallbackQuery) -> None:
    await cb.answer()


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
