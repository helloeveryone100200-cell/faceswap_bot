import asyncio
import logging
import os

from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Animation,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    PhotoSize,
    Sticker,
    Voice,
)

import database as db
from config import ADMIN_IDS, BOT_TOKEN

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

router = Router()


# ─── FSM States ──────────────────────────────────────────────────────────────

class AdminFSM(StatesGroup):
    broadcast = State()
    ban = State()
    unban = State()


# ─── Keyboards ───────────────────────────────────────────────────────────────

def kb_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 အမည်ဝှက် စကားဝိုင်းသို့ ဝင်ရန်", callback_data="join_room")],
    ])


def kb_in_room(room_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="❤️ အကောင့်ချင်းချိတ်ရန်", callback_data=f"reveal:{room_id}"),
            InlineKeyboardButton(text="🔕 ထွက်ရန်", callback_data=f"leave:{room_id}"),
        ],
    ])


def kb_admin_panel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 စာရင်းဇယား ကြည့်ရန်", callback_data="admin:stats")],
        [InlineKeyboardButton(text="📢 အားလုံးထံ စာပို့ရန် (Broadcast)", callback_data="admin:broadcast")],
        [InlineKeyboardButton(text="🔨 User ကို Ban မည်", callback_data="admin:ban")],
        [InlineKeyboardButton(text="🔓 User ကို Unban မည်", callback_data="admin:unban")],
    ])


def kb_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ နောက်သို့ ပြန်သွားရန်", callback_data="admin:back")],
    ])


# ─── Guards ──────────────────────────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def is_private(message: Message) -> bool:
    return message.chat.type == "private"


# ─── Broadcast helper ────────────────────────────────────────────────────────

async def do_broadcast(bot: Bot, user_ids: list[int], source: Message) -> tuple[int, int]:
    ok = fail = 0
    for uid in user_ids:
        try:
            if source.photo:
                caption = source.caption or ""
                await bot.send_photo(uid, source.photo[-1].file_id, caption=caption, parse_mode=ParseMode.MARKDOWN)
            elif source.text:
                await bot.send_message(uid, source.text, parse_mode=ParseMode.MARKDOWN)
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    return ok, fail


# ─── Room broadcast ──────────────────────────────────────────────────────────

async def broadcast_to_room(bot: Bot, room_id: str, sender_id: int, alias: str, message: Message) -> None:
    members = await db.get_room_members(room_id, exclude_user_id=sender_id)
    kb = kb_in_room(room_id)
    header = f"*\\[{alias}\\] 💬*\n"

    for uid in members:
        try:
            if message.text:
                await bot.send_message(uid, header + message.text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
            elif message.photo:
                cap = header + (message.caption or "")
                await bot.send_photo(uid, message.photo[-1].file_id, caption=cap, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
            elif message.voice:
                await bot.send_voice(uid, message.voice.file_id, caption=f"*\\[{alias}\\]* 🎤", parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
            elif message.animation:
                await bot.send_animation(uid, message.animation.file_id, caption=f"*\\[{alias}\\]* 🎞️", parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
            elif message.sticker:
                await bot.send_sticker(uid, message.sticker.file_id)
                await bot.send_message(uid, f"*\\[{alias}\\]* 🃏", parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
        except Exception as e:
            logger.warning("Room broadcast failed → %d: %s", uid, e)


# ─── /start ──────────────────────────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    if not is_private(message):
        return
    user = message.from_user
    if not user:
        return
    doc = await db.get_or_create_user(user.id, user.username)

    if doc.get("s", 1) == 0:
        await message.answer("🚫 **သင်သည် ဤ Bot မှ Ban ကျထားပါသည်။**", parse_mode=ParseMode.MARKDOWN)
        return

    if doc.get("r_id"):
        room_id = doc["r_id"]
        count = await db.get_room_member_count(room_id)
        await message.answer(
            f"💬 **သင် စကားဝိုင်းထဲ ရှိနေပါပြီ!**\n\n"
            f"🏷️ သင့်အမည်: **{doc['n']}**\n"
            f"🚪 Room: `{room_id}` · 👥 {count} ဦး\n\n"
            f"_မက်ဆေ့ပေးပို့ရန် ရိုက်ထည့်ပါ_",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_in_room(room_id),
        )
        return

    await message.answer(
        f"👋 မင်္ဂလာပါ **{user.first_name}**!\n\n"
        "🌐 **Anonymous Group Chat Bot** မှ ကြိုဆိုပါသည်!\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "✨ **Bot Features:**\n\n"
        "🎭 အမည်ဖျောက်ကာ ကမ္ဘာတစ်ဝှမ်းရှိ လူများနှင့် စကားပြောနိုင်သည်\n"
        "💬 Text, Photo, Voice, GIF, Sticker ပို့နိုင်သည်\n"
        "❤️ Mutual Reveal — နှစ်ဦးစလုံး ချိတ်ဆက်လိုပါက Username ထုတ်ပေးသည်\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📜 **စည်းကမ်းများ:**\n\n"
        "🚫 Spam မပို့ရ · 🚫 ရိုင်းသောစကားများ မသုံးရ\n"
        "✅ ပျော်ရွှင်ပြီး ရိုးသားစွာ ဆက်ဆံပါ 🙏\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "👇 **ဝင်ရောက်ရန် ခလုတ်နှိပ်ပါ**",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_main_menu(),
    )


# ─── Join room ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "join_room")
async def cb_join_room(cb: CallbackQuery, bot: Bot) -> None:
    user = cb.from_user
    if not cb.message:
        return
    doc = await db.get_or_create_user(user.id, user.username)

    if doc.get("s", 1) == 0:
        await cb.answer("🚫 Ban ကျထားသောကြောင့် ဝင်ခွင့်မရပါ", show_alert=True)
        return
    if doc.get("r_id"):
        await cb.answer("သင် ဝင်ရောက်နေပြီဖြစ်သည်!", show_alert=True)
        return

    room_id = await db.join_room(user.id)
    alias = doc["n"]
    count = await db.get_room_member_count(room_id)

    await cb.message.edit_text(
        f"🎉 **စကားဝိုင်းသို့ ဝင်ရောက်ပြီးပါပြီ!**\n\n"
        f"🏷️ သင့်အမည်: **{alias}**\n"
        f"🚪 Room: `{room_id}` · 👥 {count} ဦး\n\n"
        f"💬 _မက်ဆေ့ရိုက်ပြီး Enter နှိပ်ပါ_\n"
        f"❤️ ချိတ်ဆက်ရန် **'အကောင့်ချင်းချိတ်ရန်'** နှိပ်ပါ",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_in_room(room_id),
    )

    members = await db.get_room_members(room_id, exclude_user_id=user.id)
    for uid in members:
        try:
            await bot.send_message(
                uid,
                f"🔔 **{alias}** — Room သို့ ဝင်ရောက်လာပါပြီ! 👋\n_(ယခု {count} ဦး ရှိသည်)_",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb_in_room(room_id),
            )
        except Exception:
            pass
    await cb.answer()


# ─── Leave room ──────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("leave:"))
async def cb_leave_room(cb: CallbackQuery, bot: Bot) -> None:
    user = cb.from_user
    if not cb.message:
        return
    doc = await db.get_user(user.id)
    if not doc or not doc.get("r_id"):
        await cb.answer("Room ထဲ မရှိပါ", show_alert=True)
        return

    room_id = doc["r_id"]
    alias = doc["n"]
    await db.leave_room(user.id)

    await cb.message.edit_text(
        "👋 **စကားဝိုင်းမှ ထွက်ပြီးပါပြီ**\n\nပြန်ဝင်ရန် `/start` ရိုက်ပါ",
        parse_mode=ParseMode.MARKDOWN,
    )

    members = await db.get_room_members(room_id, exclude_user_id=user.id)
    count = await db.get_room_member_count(room_id)
    for uid in members:
        try:
            await bot.send_message(
                uid,
                f"🚪 **{alias}** — Room မှ ထွက်သွားပါပြီ _(ယခု {count} ဦး ကျန်သည်)_",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb_in_room(room_id),
            )
        except Exception:
            pass
    await cb.answer()


# ─── Mutual Reveal ───────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("reveal:"))
async def cb_reveal(cb: CallbackQuery, bot: Bot) -> None:
    user = cb.from_user
    room_id = cb.data.split(":", 1)[1]

    doc = await db.get_user(user.id)
    if not doc or doc.get("r_id") != room_id:
        await cb.answer("သင် ဤ Room ထဲ မရှိတော့ပါ", show_alert=True)
        return

    alias = doc["n"]
    my_username = f"@{user.username}" if user.username else f"ID: `{user.id}`"

    matched_uid = await db.request_reveal(user.id, room_id)

    if matched_uid:
        matched_doc = await db.get_user(matched_uid)
        matched_alias = matched_doc["n"] if matched_doc else "အမည်မသိ"
        try:
            matched_chat = await bot.get_chat(matched_uid)
            matched_username = f"@{matched_chat.username}" if matched_chat.username else f"ID: `{matched_uid}`"
        except Exception:
            matched_username = f"ID: `{matched_uid}`"

        celebrate = (
            "🎊 **MATCH ဖြစ်သွားပါပြီ!** 🎉\n\n"
            "💫 သင်တို့နှစ်ဦးစလုံး ချိတ်ဆက်လိုကြောင်း ဆန္ဒပြပြီးပါပြီ!\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
        )
        await bot.send_message(
            user.id,
            celebrate + f"🏷️ **{matched_alias}**\n📲 {matched_username}\n\n💌 Private တွင် ဆက်သွယ်နိုင်ပါပြီ! ✨",
            parse_mode=ParseMode.MARKDOWN,
        )
        await bot.send_message(
            matched_uid,
            celebrate + f"🏷️ **{alias}**\n📲 {my_username}\n\n💌 Private တွင် ဆက်သွယ်နိုင်ပါပြီ! ✨",
            parse_mode=ParseMode.MARKDOWN,
        )
        await cb.answer("🎉 Match ဖြစ်သွားပါပြီ!", show_alert=True)
        return

    members_count = len(await db.get_room_members(room_id, exclude_user_id=user.id))
    if members_count == 0:
        await cb.answer("Room ထဲ တစ်ဦးတည်းသာ ရှိသည်", show_alert=True)
        return

    await cb.answer("❤️ Request ပို့ပြီး! တစ်ဦးဦးမှ ပြန်ဆက်ကြည့်မည်...", show_alert=True)

    members = await db.get_room_members(room_id, exclude_user_id=user.id)
    for uid in members:
        try:
            await bot.send_message(
                uid,
                "💌 **Room ထဲမှ တစ်ဦးက သင်နှင့် ချိတ်ဆက်လိုပါသည်!**\n\n"
                "**'❤️ အကောင့်ချင်းချိတ်ရန်'** နှိပ်ပါ — Match ဖြစ်ပါက\n"
                "နှစ်ဦးစလုံး Telegram Username ထုတ်ပြပေးမည် 🎊",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb_in_room(room_id),
            )
        except Exception:
            pass


# ─── Message forwarding ──────────────────────────────────────────────────────

async def _get_active_user(user_id: int, username: str | None) -> dict | None:
    doc = await db.get_user(user_id)
    if not doc:
        return None
    if doc.get("s", 1) == 0 or not doc.get("r_id"):
        return None
    return doc


@router.message(F.text & ~F.text.startswith("/") & F.chat.type == "private")
async def handle_text(message: Message, bot: Bot) -> None:
    user = message.from_user
    if not user:
        return
    doc = await _get_active_user(user.id, user.username)
    if not doc:
        if not (await db.get_user(user.id) or {}).get("r_id"):
            await message.answer("💬 `/start` ရိုက်ပြီး Room ဝင်ပါ", parse_mode=ParseMode.MARKDOWN)
        return
    await broadcast_to_room(bot, doc["r_id"], user.id, doc["n"], message)


@router.message(F.photo & F.chat.type == "private")
async def handle_photo(message: Message, bot: Bot) -> None:
    user = message.from_user
    if not user:
        return
    doc = await _get_active_user(user.id, user.username)
    if doc:
        await broadcast_to_room(bot, doc["r_id"], user.id, doc["n"], message)


@router.message(F.voice & F.chat.type == "private")
async def handle_voice(message: Message, bot: Bot) -> None:
    user = message.from_user
    if not user:
        return
    doc = await _get_active_user(user.id, user.username)
    if doc:
        await broadcast_to_room(bot, doc["r_id"], user.id, doc["n"], message)


@router.message(F.animation & F.chat.type == "private")
async def handle_animation(message: Message, bot: Bot) -> None:
    user = message.from_user
    if not user:
        return
    doc = await _get_active_user(user.id, user.username)
    if doc:
        await broadcast_to_room(bot, doc["r_id"], user.id, doc["n"], message)


@router.message(F.sticker & F.chat.type == "private")
async def handle_sticker(message: Message, bot: Bot) -> None:
    user = message.from_user
    if not user:
        return
    doc = await _get_active_user(user.id, user.username)
    if doc:
        await broadcast_to_room(bot, doc["r_id"], user.id, doc["n"], message)


# ─── Admin: /admin entry ─────────────────────────────────────────────────────

@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    if not is_private(message) or not message.from_user:
        return
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer(
        "🛡️ **Admin Control Panel**\n━━━━━━━━━━━━━━━━━━━━\nလုပ်ဆောင်ချက် ရွေးချယ်ပါ ↓",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_admin_panel(),
    )


# ─── Admin: stats ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin:stats")
async def cb_admin_stats(cb: CallbackQuery) -> None:
    if not cb.from_user or not is_admin(cb.from_user.id) or not cb.message:
        await cb.answer()
        return
    if cb.message.chat.type != "private":
        await cb.answer()
        return

    stats = await db.get_stats()
    await cb.message.edit_text(
        "📊 **Bot Statistics**\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 စုစုပေါင်း Registered: `{stats['total']}`\n"
        f"✅ Active Users: `{stats['active']}`\n"
        f"🚫 Banned Users: `{stats['banned']}`\n"
        f"💬 In Chat Now: `{stats['in_chat']}`\n"
        f"🚪 Active Rooms: `{stats['active_rooms']}`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_back(),
    )
    await cb.answer()


# ─── Admin: broadcast ────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin:broadcast")
async def cb_admin_broadcast(cb: CallbackQuery, state: FSMContext) -> None:
    if not cb.from_user or not is_admin(cb.from_user.id) or not cb.message:
        await cb.answer()
        return
    if cb.message.chat.type != "private":
        await cb.answer()
        return

    await state.set_state(AdminFSM.broadcast)
    await cb.message.edit_text(
        "📢 **Broadcast Message**\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "ပို့လိုသော **Text** သို့မဟုတ် **Photo** ပေးပို့ပါ\n"
        "_(Active Users အားလုံးထံ ရောက်သွားမည်)_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_back(),
    )
    await cb.answer()


@router.message(AdminFSM.broadcast, F.chat.type == "private")
async def handle_broadcast_input(message: Message, bot: Bot, state: FSMContext) -> None:
    if not message.from_user or not is_admin(message.from_user.id):
        return
    if not message.text and not message.photo:
        await message.answer("⚠️ Text သို့မဟုတ် Photo သာ လက်ခံသည်", reply_markup=kb_back())
        return

    user_ids = await db.get_active_user_ids()
    status = await message.answer(
        f"📡 **{len(user_ids)} ဦးထံ ကြော်ငြာနေသည်...**",
        parse_mode=ParseMode.MARKDOWN,
    )
    ok, fail = await do_broadcast(bot, user_ids, message)
    await state.clear()
    await status.edit_text(
        f"✅ **Broadcast ပြီးပါပြီ!**\n\n📤 ရောက်သည်: `{ok}` · ❌ မရောက်: `{fail}`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_admin_panel(),
    )


# ─── Admin: ban ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin:ban")
async def cb_admin_ban(cb: CallbackQuery, state: FSMContext) -> None:
    if not cb.from_user or not is_admin(cb.from_user.id) or not cb.message:
        await cb.answer()
        return
    if cb.message.chat.type != "private":
        await cb.answer()
        return

    await state.set_state(AdminFSM.ban)
    await cb.message.edit_text(
        "🔨 **User ကို Ban မည်**\n━━━━━━━━━━━━━━━━━━━━\n\nBan မည့် **User ID** ရိုက်ထည့်ပါ",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_back(),
    )
    await cb.answer()


@router.message(AdminFSM.ban, F.chat.type == "private")
async def handle_ban_input(message: Message, bot: Bot, state: FSMContext) -> None:
    if not message.from_user or not is_admin(message.from_user.id) or not message.text:
        return
    if not message.text.strip().isdigit():
        await message.answer("❌ User ID (နံပါတ်) ရိုက်ထည့်ပါ", reply_markup=kb_back())
        return

    uid = int(message.text.strip())
    room_id = await db.ban_user(uid)
    await state.clear()

    if room_id:
        members = await db.get_room_members(room_id, exclude_user_id=uid)
        for mid in members:
            try:
                await bot.send_message(mid, "🚪 Room ထဲမှ တစ်ဦး ထွက်သွားပါပြီ", parse_mode=ParseMode.MARKDOWN)
            except Exception:
                pass

    try:
        await bot.send_message(uid, "🚫 **သင်သည် Bot မှ Ban ကျပါပြီ**", parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass

    await message.answer(
        f"🚫 User `{uid}` ကို **Ban ပြီးပါပြီ**",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_admin_panel(),
    )


# ─── Admin: unban ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin:unban")
async def cb_admin_unban(cb: CallbackQuery, state: FSMContext) -> None:
    if not cb.from_user or not is_admin(cb.from_user.id) or not cb.message:
        await cb.answer()
        return
    if cb.message.chat.type != "private":
        await cb.answer()
        return

    await state.set_state(AdminFSM.unban)
    await cb.message.edit_text(
        "🔓 **User ကို Unban မည်**\n━━━━━━━━━━━━━━━━━━━━\n\nUnban မည့် **User ID** ရိုက်ထည့်ပါ",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_back(),
    )
    await cb.answer()


@router.message(AdminFSM.unban, F.chat.type == "private")
async def handle_unban_input(message: Message, bot: Bot, state: FSMContext) -> None:
    if not message.from_user or not is_admin(message.from_user.id) or not message.text:
        return
    if not message.text.strip().isdigit():
        await message.answer("❌ User ID (နံပါတ်) ရိုက်ထည့်ပါ", reply_markup=kb_back())
        return

    uid = int(message.text.strip())
    await db.unban_user(uid)
    await state.clear()

    try:
        await bot.send_message(uid, "✅ **သင်သည် Unban ဖြစ်ပြီးပါပြီ!** `/start` ရိုက်ပါ", parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass

    await message.answer(
        f"✅ User `{uid}` ကို **Unban ပြီးပါပြီ**",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_admin_panel(),
    )


# ─── Admin: back ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin:back")
async def cb_admin_back(cb: CallbackQuery, state: FSMContext) -> None:
    if not cb.from_user or not is_admin(cb.from_user.id) or not cb.message:
        await cb.answer()
        return
    if cb.message.chat.type != "private":
        await cb.answer()
        return

    await state.clear()
    await cb.message.edit_text(
        "🛡️ **Admin Control Panel**\n━━━━━━━━━━━━━━━━━━━━\nလုပ်ဆောင်ချက် ရွေးချယ်ပါ ↓",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_admin_panel(),
    )
    await cb.answer()


# ─── Health server ────────────────────────────────────────────────────────────

async def health_handler(request: web.Request) -> web.Response:
    return web.Response(text="Anon Chat Bot is running! ✅")


async def run_web_server() -> None:
    port = int(os.environ.get("PORT", 10000))
    app = web.Application()
    app.router.add_get("/", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Health server on port %d", port)
    await asyncio.Event().wait()


# ─── Entry point ─────────────────────────────────────────────────────────────

async def run_bot() -> None:
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is not set!")
    if not any(x > 0 for x in ADMIN_IDS):
        raise ValueError("ADMIN_IDS is not set!")

    await db.setup_indexes()
    logger.info("Indexes ready.")

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    logger.info("Anonymous Chat Bot starting...")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


async def main() -> None:
    await asyncio.gather(run_web_server(), run_bot())


if __name__ == "__main__":
    asyncio.run(main())
