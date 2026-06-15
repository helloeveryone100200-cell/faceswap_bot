import asyncio
import logging
import secrets as _sec
import time

from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import SkipHandler
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
    MessageReactionUpdated,
    ReactionTypeEmoji,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

import database as db
from config import ADMIN_IDS, DUMMY_PORT

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

router = Router()

# ---------------------------------------------------------------------------
# Rate limiter (anti-spam): 3 seconds between messages per user
# ---------------------------------------------------------------------------

RATE_LIMIT_SECONDS = 3
_last_msg_time: dict[int, float] = {}


def _is_rate_limited(user_id: int) -> bool:
    now = time.monotonic()
    last = _last_msg_time.get(user_id, 0.0)
    if now - last < RATE_LIMIT_SECONDS:
        return True
    _last_msg_time[user_id] = now
    return False


# ---------------------------------------------------------------------------
# Bad words filter
# ---------------------------------------------------------------------------

BAD_WORDS: set[str] = {
    "မင်းမေ", "မောက်မ", "အပျော်သမား", "ညစ်ညမ်း",
    "ဆိုးကောင်", "ပျော်တော်", "ညာကောင်", "ပေါက်ကရ",
    "ကောင်မနက်", "နက်ကောင်", "အလိုးခံ", "တောင်တုပ်",
    "မင်းညောင်း", "ပြောင်နေ", "မပိုင်ကောင်", "သားမိုက်",
    "မိုက်ကောင်", "ဆိပ်ကောင်", "ကြောင်မ", "ကိုင်မ",
    "fuck", "shit", "bitch", "asshole", "bastard",
}


def _has_bad_words(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in BAD_WORDS)


# ---------------------------------------------------------------------------
# Icebreaker questions
# ---------------------------------------------------------------------------

ICEBREAKERS: list[str] = [
    "🍕🍔 What's your all-time favorite food?",
    "🎬 What movie can you watch over and over again?",
    "🎵 What song is stuck in your head right now?",
    "🌏 If you could visit any country tomorrow, where would you go?",
    "🎮 What's your favorite game to play (mobile, PC, or board)?",
    "🐾 Do you prefer cats 🐱 or dogs 🐶?",
    "🌙 Are you a night owl or an early bird?",
    "📚 Last book or manga/manhwa/webtoon you really enjoyed?",
    "☕🧋 Coffee or boba tea?",
    "🦸 If you had one superpower, what would it be?",
]

# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------

MAIN_MENU_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🔍 Find a Stranger", callback_data="find_stranger")],
    [InlineKeyboardButton(text="🏷️ My Interests / Tags", callback_data="my_tags")],
])

GENDER_SELECT_KB = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="Male 👦", callback_data="gender_m"),
        InlineKeyboardButton(text="Female 👧", callback_data="gender_f"),
    ]
])

TARGET_GENDER_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="Anyone 🌐", callback_data="tg_any")],
    [
        InlineKeyboardButton(text="Find Boys 👦", callback_data="tg_m"),
        InlineKeyboardButton(text="Find Girls 👧", callback_data="tg_f"),
    ],
])

STRANGER_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="⏭️ Next Stranger"), KeyboardButton(text="🔚 Stop Chat")],
        [KeyboardButton(text="🚨 Report Stranger")],
    ],
    resize_keyboard=True,
)

ADMIN_MENU_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📈 Advanced Stats", callback_data="adm_stats")],
    [InlineKeyboardButton(text="👥 User Management", callback_data="ul_0")],
    [InlineKeyboardButton(text="🚨 Reports Dashboard", callback_data="rp_0")],
    [InlineKeyboardButton(text="🔎 Search User", callback_data="adm_search")],
    [InlineKeyboardButton(text="📢 Global Broadcast", callback_data="adm_broadcast")],
    [InlineKeyboardButton(text="🔨 Ban User", callback_data="adm_ban")],
    [InlineKeyboardButton(text="🔓 Unban User", callback_data="adm_unban")],
])

BACK_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="⬅️ Back to Admin Panel", callback_data="adm_back")]
])

NEXT_TEXT   = "⏭️ Next Stranger"
STOP_TEXT   = "🔚 Stop Chat"
REPORT_TEXT = "🚨 Report Stranger"


# ---------------------------------------------------------------------------
# FSM States
# ---------------------------------------------------------------------------

class UserStates(StatesGroup):
    entering_tags = State()


class AdminStates(StatesGroup):
    waiting_broadcast  = State()
    waiting_ban_id     = State()
    waiting_unban_id   = State()
    waiting_alias      = State()   # force alias change; target_id stored in FSM data
    waiting_search     = State()   # search user by ID or username


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
# Helpers — formatting
# ---------------------------------------------------------------------------

def _gender_label(user: dict) -> str:
    g = user.get("g")
    return "👦" if g == "M" else ("👧" if g == "F" else "👤")


def _status_label(user: dict) -> str:
    if user.get("s") == 0:
        ban_exp = user.get("ban_exp")
        return "⏳ Temp Ban" if ban_exp else "🚫 Banned"
    if user.get("p_id"):
        return "💬 In Chat"
    return "✅ Active"


def _user_card(user: dict) -> str:
    uid      = user["_id"]
    alias    = user.get("n", "—")
    uname    = f"@{user['u']}" if user.get("u") else "—"
    gender   = _gender_label(user)
    status   = _status_label(user)
    tags     = " ".join(f"#{t}" for t in user.get("tags", [])) or "—"
    reports  = user.get("report_count", 0)
    joined   = user.get("j")
    join_str = joined.strftime("%Y-%m-%d") if joined else "—"
    return (
        f"👤 <b>{alias}</b> {gender}\n"
        f"🆔 <code>{uid}</code>  |  {uname}\n"
        f"📊 Status: {status}\n"
        f"🚨 Reports received: <b>{reports}</b>\n"
        f"🏷️ Tags: {tags}\n"
        f"📅 Joined: {join_str}"
    )


def _user_action_kb(user_id: int) -> InlineKeyboardMarkup:
    s = str(user_id)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔨 Ban",   callback_data=f"ub_{s}"),
            InlineKeyboardButton(text="🔓 Unban", callback_data=f"uu_{s}"),
        ],
        [InlineKeyboardButton(text="✏️ Change Alias", callback_data=f"ua_{s}")],
        [InlineKeyboardButton(text="🗑️ Clear Reports",  callback_data=f"ucr_{s}")],
        [InlineKeyboardButton(text="⬅️ Back to Admin Panel", callback_data="adm_back")],
    ])


def _pagination_kb(current_page: int, total: int, per_page: int, prefix: str) -> InlineKeyboardMarkup:
    total_pages = max(1, (total + per_page - 1) // per_page)
    buttons = []
    row = []
    if current_page > 0:
        row.append(InlineKeyboardButton(text="◀️ Prev", callback_data=f"{prefix}_{current_page - 1}"))
    row.append(InlineKeyboardButton(
        text=f"Page {current_page + 1}/{total_pages}", callback_data="noop"
    ))
    if (current_page + 1) * per_page < total:
        row.append(InlineKeyboardButton(text="Next ▶️", callback_data=f"{prefix}_{current_page + 1}"))
    buttons.append(row)
    buttons.append([InlineKeyboardButton(text="⬅️ Back to Admin Panel", callback_data="adm_back")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ---------------------------------------------------------------------------
# Stranger connection helpers
# ---------------------------------------------------------------------------

async def _connect_strangers(bot: Bot, user_a_id: int, user_b_id: int) -> None:
    import random
    await db.leave_queue(user_a_id)
    await db.leave_queue(user_b_id)
    await db.set_partner(user_a_id, user_b_id)
    await db.set_partner(user_b_id, user_a_id)
    await db.log_match(user_a_id, user_b_id)

    user_a = await db.get_user(user_a_id)
    user_b = await db.get_user(user_b_id)
    if not user_a or not user_b:
        return

    icebreaker = random.choice(ICEBREAKERS)
    tpl = (
        "🎉 <b>You're connected with a stranger!</b>\n"
        "Your partner: <b>{name} {icon}</b>\n\n"
        "🧊 <b>Icebreaker:</b> <i>{ice}</i>\n\n"
        "Send a message to start chatting!"
    )
    await bot.send_message(
        user_a_id,
        tpl.format(name=user_b["n"], icon=_gender_label(user_b), ice=icebreaker),
        parse_mode=ParseMode.HTML, reply_markup=STRANGER_KB,
    )
    await bot.send_message(
        user_b_id,
        tpl.format(name=user_a["n"], icon=_gender_label(user_a), ice=icebreaker),
        parse_mode=ParseMode.HTML, reply_markup=STRANGER_KB,
    )


async def _disconnect_stranger(
    bot: Bot, user_id: int, partner_id: int, notify_partner: bool = True
) -> None:
    await db.set_partner(user_id, None)
    await db.set_partner(partner_id, None)
    if notify_partner:
        try:
            await bot.send_message(partner_id, "🔌 Your chat partner has disconnected.",
                                   reply_markup=ReplyKeyboardRemove())
            await bot.send_message(partner_id, "Find someone new?", reply_markup=MAIN_MENU_KB)
        except Exception:
            pass


async def _do_find(bot: Bot, user_id: int, target_gender: str) -> None:
    user = await db.get_user(user_id)
    if not user:
        return

    user_gender = user.get("g")
    user_tags   = user.get("tags", [])

    await db.enter_queue(user_id, user_gender, target_gender, user_tags)

    partner_id = await db.find_and_match(user_id, user_gender, target_gender, user_tags, strict=True)
    if partner_id:
        await _connect_strangers(bot, user_id, partner_id)
        return

    waiting = await db.count_waiting()
    await bot.send_message(
        user_id,
        f"🔍 Searching for a stranger… ({waiting} in queue)\n\n"
        "Use <b>🔚 Stop Chat</b> to cancel.",
        parse_mode=ParseMode.HTML, reply_markup=STRANGER_KB,
    )
    asyncio.create_task(_fallback_match(bot, user_id))


async def _fallback_match(bot: Bot, user_id: int) -> None:
    await asyncio.sleep(10)
    if not await db.is_in_queue(user_id):
        return
    qe = await db.get_queue_entry(user_id)
    if not qe:
        return
    user = await db.get_user(user_id)
    if not user:
        return
    partner_id = await db.find_and_match(user_id, user.get("g"), qe.get("tg", "any"), [], strict=False)
    if partner_id:
        await _connect_strangers(bot, user_id, partner_id)
        return

    await asyncio.sleep(30)
    if not await db.is_in_queue(user_id):
        return
    partner_id = await db.find_and_match(user_id, None, "any", [], strict=False)
    if partner_id:
        await _connect_strangers(bot, user_id, partner_id)
        return
    try:
        waiting = await db.count_waiting()
        await bot.send_message(
            user_id,
            f"⏳ Still searching… ({waiting} in queue)\n\nUse <b>🔚 Stop Chat</b> to cancel.",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Media relay
# ---------------------------------------------------------------------------

async def _relay_message(
    bot: Bot, to_id: int, alias: str, message: Message,
    reply_to_message_id: int | None = None,
) -> Message | None:
    kwargs: dict = {}
    if reply_to_message_id:
        kwargs["reply_to_message_id"] = reply_to_message_id
    try:
        if message.text:
            return await bot.send_message(to_id, f"<b>{alias}</b>\n{message.text}",
                                          parse_mode=ParseMode.HTML, **kwargs)
        elif message.sticker:
            if reply_to_message_id:
                await bot.send_message(to_id, f"<b>{alias}</b> sent a sticker:",
                                       parse_mode=ParseMode.HTML, **kwargs)
            return await bot.send_sticker(to_id, message.sticker.file_id)
        elif message.photo:
            cap = f"<b>{alias}</b>\n{message.caption}" if message.caption else f"<b>{alias}</b>"
            return await bot.send_photo(to_id, message.photo[-1].file_id,
                                        caption=cap, parse_mode=ParseMode.HTML, **kwargs)
        elif message.video:
            cap = f"<b>{alias}</b>\n{message.caption}" if message.caption else f"<b>{alias}</b> 🎬"
            return await bot.send_video(to_id, message.video.file_id,
                                        caption=cap, parse_mode=ParseMode.HTML, **kwargs)
        elif message.video_note:
            if reply_to_message_id:
                await bot.send_message(to_id, f"<b>{alias}</b> sent a video message:",
                                       parse_mode=ParseMode.HTML, **kwargs)
            return await bot.send_video_note(to_id, message.video_note.file_id)
        elif message.animation:
            cap = f"<b>{alias}</b>\n{message.caption}" if message.caption else f"<b>{alias}</b> 🎞️"
            return await bot.send_animation(to_id, message.animation.file_id,
                                            caption=cap, parse_mode=ParseMode.HTML, **kwargs)
        elif message.voice:
            return await bot.send_voice(to_id, message.voice.file_id,
                                        caption=f"<b>{alias}</b> 🎙️",
                                        parse_mode=ParseMode.HTML, **kwargs)
        elif message.audio:
            title = message.audio.title or "audio"
            return await bot.send_audio(to_id, message.audio.file_id,
                                        caption=f"<b>{alias}</b> 🎵\n<i>{title}</i>",
                                        parse_mode=ParseMode.HTML, **kwargs)
        elif message.document:
            fname = message.document.file_name or "file"
            cap = (f"<b>{alias}</b>\n{message.caption}" if message.caption
                   else f"<b>{alias}</b> 📎\n<i>{fname}</i>")
            return await bot.send_document(to_id, message.document.file_id,
                                           caption=cap, parse_mode=ParseMode.HTML, **kwargs)
        elif message.gift:
            star_count = getattr(message.gift.gift, "star_count", "?")
            return await bot.send_message(
                to_id, f"🎁 <b>{alias}</b> sent a gift worth <b>{star_count} ⭐</b>",
                parse_mode=ParseMode.HTML, **kwargs)
        elif message.paid_media:
            return await bot.send_message(to_id, f"💎 <b>{alias}</b> shared paid media.",
                                          parse_mode=ParseMode.HTML, **kwargs)
    except Exception as e:
        log.warning("Relay to %s failed: %s", to_id, e)
    return None


async def _send_to_stranger(
    bot: Bot, sender_id: int, sender_msg_id: int,
    partner_id: int, alias: str, message: Message,
    reply_to_partner_msg_id: int | None = None,
) -> None:
    msg_key = _sec.token_urlsafe(6)
    sent = await _relay_message(bot, partner_id, alias, message, reply_to_partner_msg_id)
    if sent:
        await db.create_msg(msg_key, [[sender_id, sender_msg_id], [partner_id, sent.message_id]])


# ---------------------------------------------------------------------------
# Native reaction mirror
# ---------------------------------------------------------------------------

@router.message_reaction()
async def on_message_reaction(event: MessageReactionUpdated, bot: Bot) -> None:
    doc = await db.find_msg_by_copy(event.chat.id, event.message_id)
    if not doc:
        return
    reactions: list[ReactionTypeEmoji] = [
        ReactionTypeEmoji(type="emoji", emoji=r.emoji)
        for r in event.new_reaction if r.type == "emoji"
    ]
    for (copy_chat, copy_msg) in doc["c"]:
        if copy_chat == event.chat.id and copy_msg == event.message_id:
            continue
        try:
            await bot.set_message_reaction(chat_id=copy_chat, message_id=copy_msg,
                                           reaction=reactions)
        except Exception as e:
            log.warning("Reaction mirror failed %s/%s: %s", copy_chat, copy_msg, e)


# ---------------------------------------------------------------------------
# Edit sync
# ---------------------------------------------------------------------------

@router.edited_message()
async def on_edited_message(message: Message, bot: Bot) -> None:
    if not message.from_user or not message.text:
        return
    user = await db.get_user(message.from_user.id)
    if not user or not user.get("p_id"):
        return
    doc = await db.find_msg_by_copy(message.chat.id, message.message_id)
    if not doc:
        return
    alias = user["n"]
    for (copy_chat, copy_msg) in doc["c"]:
        if copy_chat == message.chat.id and copy_msg == message.message_id:
            continue
        try:
            await bot.edit_message_text(
                f"<b>{alias}</b>\n{message.text} ✏️",
                chat_id=copy_chat, message_id=copy_msg, parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            log.warning("Edit sync failed %s/%s: %s", copy_chat, copy_msg, e)


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = await db.get_or_create_user(message.from_user.id, message.from_user.username)

    active = await db.lift_expired_temp_ban(message.from_user.id)
    if not active:
        user = await db.get_user(message.from_user.id)
        ban_exp = user.get("ban_exp")
        if ban_exp:
            from datetime import datetime, timezone
            remaining = ban_exp - datetime.now(timezone.utc)
            hrs  = int(remaining.total_seconds() // 3600)
            mins = int((remaining.total_seconds() % 3600) // 60)
            await message.answer(
                f"🚫 You are temporarily banned.\n⏳ Remaining: <b>{hrs}h {mins}m</b>",
                parse_mode=ParseMode.HTML)
        else:
            await message.answer("🚫 You have been permanently banned from this service.")
        return

    if user.get("p_id"):
        await message.answer(
            "You are already in a chat. Use <b>🔚 Stop Chat</b> to exit first.",
            parse_mode=ParseMode.HTML, reply_markup=STRANGER_KB)
        return

    if not user.get("g"):
        await message.answer(
            f"👋 Welcome to <b>Anonymous Chat</b>!\n\n"
            f"Your alias: <b>{user['n']}</b>\n\nFirst, please select your gender:",
            parse_mode=ParseMode.HTML, reply_markup=GENDER_SELECT_KB)
        return

    await message.answer(
        f"👋 Welcome back, <b>{user['n']}</b> {_gender_label(user)}!\n\nReady to chat?",
        parse_mode=ParseMode.HTML, reply_markup=MAIN_MENU_KB)


# ---------------------------------------------------------------------------
# Gender selection
# ---------------------------------------------------------------------------

@router.callback_query(F.data.in_({"gender_m", "gender_f"}))
async def cb_set_gender(cb: CallbackQuery) -> None:
    await cb.answer()
    gender = "M" if cb.data == "gender_m" else "F"
    await db.set_gender(cb.from_user.id, gender)
    user = await db.get_user(cb.from_user.id)
    icon = "👦" if gender == "M" else "👧"
    await cb.message.edit_text(
        f"✅ Gender set to {icon}\n\nYour alias: <b>{user['n']}</b>\n\nReady to chat?",
        parse_mode=ParseMode.HTML, reply_markup=MAIN_MENU_KB)


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "my_tags")
async def cb_my_tags(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    user = await db.get_user(cb.from_user.id)
    current = ", ".join(f"#{t}" for t in user.get("tags", [])) if user.get("tags") else "none"
    await state.set_state(UserStates.entering_tags)
    await cb.message.answer(
        f"🏷️ <b>Your current tags:</b> {current}\n\n"
        "Send up to <b>3 hashtags</b>.\nExample: <code>#gaming #movies #kpop</code>",
        parse_mode=ParseMode.HTML)


@router.message(UserStates.entering_tags)
async def handle_tags_input(message: Message, state: FSMContext) -> None:
    await state.clear()
    if not message.text:
        await message.answer("⚠️ Please send hashtags as text.")
        return
    raw_tags = [t.lstrip("#").lower().strip()
                for t in message.text.split() if t.startswith("#") and len(t) > 1]
    if not raw_tags:
        await message.answer("⚠️ No valid hashtags found. Format: <code>#gaming #movies</code>",
                             parse_mode=ParseMode.HTML)
        return
    tags = list(dict.fromkeys(raw_tags))[:3]
    await db.set_tags(message.from_user.id, tags)
    await message.answer(
        f"✅ Tags saved: <b>{' '.join('#' + t for t in tags)}</b>",
        parse_mode=ParseMode.HTML, reply_markup=MAIN_MENU_KB)


@router.message(Command("tags"))
async def cmd_tags(message: Message, state: FSMContext) -> None:
    user = await db.get_user(message.from_user.id)
    current = ", ".join(f"#{t}" for t in user.get("tags", [])) if user and user.get("tags") else "none"
    await state.set_state(UserStates.entering_tags)
    await message.answer(
        f"🏷️ <b>Your current tags:</b> {current}\n\n"
        "Send up to <b>3 hashtags</b>.\nExample: <code>#gaming #movies #kpop</code>",
        parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# Find stranger
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "find_stranger")
async def cb_find_stranger(cb: CallbackQuery) -> None:
    await cb.answer()
    user = await db.get_or_create_user(cb.from_user.id, cb.from_user.username)
    active = await db.lift_expired_temp_ban(cb.from_user.id)
    if not active:
        await cb.message.answer("🚫 You are banned and cannot use this feature.")
        return
    if user.get("p_id"):
        await cb.message.answer("You are already in a chat. Use <b>🔚 Stop Chat</b> to exit.",
                                parse_mode=ParseMode.HTML, reply_markup=STRANGER_KB)
        return
    if not user.get("g"):
        await cb.message.answer("Please set your gender first:", reply_markup=GENDER_SELECT_KB)
        return
    tags = user.get("tags", [])
    tag_hint = (f"\n🏷️ Tags: {' '.join('#' + t for t in tags)}" if tags
                else "\n💡 Tip: Set /tags to find like-minded strangers!")
    await cb.message.answer(f"Who would you like to talk to?{tag_hint}",
                            parse_mode=ParseMode.HTML, reply_markup=TARGET_GENDER_KB)


@router.callback_query(F.data.in_({"tg_any", "tg_m", "tg_f"}))
async def cb_target_gender(cb: CallbackQuery) -> None:
    await cb.answer()
    target_gender = {"tg_any": "any", "tg_m": "M", "tg_f": "F"}[cb.data]
    user = await db.get_or_create_user(cb.from_user.id, cb.from_user.username)
    if user.get("p_id"):
        await cb.message.answer("You are already in a chat.", reply_markup=STRANGER_KB)
        return
    labels = {"any": "Anyone 🌐", "M": "Boys 👦", "F": "Girls 👧"}
    await cb.message.answer(f"✅ Searching for: <b>{labels[target_gender]}</b>",
                            parse_mode=ParseMode.HTML)
    await _do_find(cb.bot, cb.from_user.id, target_gender)


# ---------------------------------------------------------------------------
# Stranger chat — Stop / Next / Report
# ---------------------------------------------------------------------------

@router.message(F.text == STOP_TEXT)
async def stop_chat(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    if not user:
        return
    if await db.is_in_queue(message.from_user.id):
        await db.leave_queue(message.from_user.id)
        await message.answer("🔚 Search cancelled.", reply_markup=ReplyKeyboardRemove())
        await message.answer("Want to try again?", reply_markup=MAIN_MENU_KB)
        return
    partner_id = user.get("p_id")
    if not partner_id:
        await message.answer("You are not in a chat.", reply_markup=ReplyKeyboardRemove())
        await message.answer("Return to menu:", reply_markup=MAIN_MENU_KB)
        return
    await _disconnect_stranger(message.bot, message.from_user.id, partner_id, notify_partner=True)
    await message.answer("🔚 You left the chat.", reply_markup=ReplyKeyboardRemove())
    await message.answer("Want to find another stranger?", reply_markup=MAIN_MENU_KB)


@router.message(F.text == NEXT_TEXT)
async def next_stranger(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    if not user:
        return
    partner_id = user.get("p_id")
    if partner_id:
        await _disconnect_stranger(message.bot, message.from_user.id, partner_id, notify_partner=True)
    await db.leave_queue(message.from_user.id)
    await message.answer("🔍 Finding next stranger…")
    await _do_find(message.bot, message.from_user.id, "any")


@router.message(F.text == REPORT_TEXT)
async def report_stranger(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    if not user or not user.get("p_id"):
        await message.answer("You are not in a chat.")
        return
    partner_id = user["p_id"]
    await _disconnect_stranger(message.bot, message.from_user.id, partner_id, notify_partner=False)
    new_count = await db.add_report(message.from_user.id, partner_id)
    await message.answer(
        "🚨 <b>Report submitted.</b> You have been disconnected.\nLooking for a new stranger…",
        parse_mode=ParseMode.HTML, reply_markup=ReplyKeyboardRemove())
    if new_count >= db.AUTO_BAN_REPORT_THRESHOLD:
        try:
            await message.bot.send_message(
                partner_id,
                "🚫 Your account has been suspended for 24 hours due to multiple reports.",
                reply_markup=ReplyKeyboardRemove())
        except Exception:
            pass
    else:
        try:
            await message.bot.send_message(partner_id,
                "⚠️ You have been reported and disconnected.", reply_markup=ReplyKeyboardRemove())
            await message.bot.send_message(partner_id, "Find someone new?", reply_markup=MAIN_MENU_KB)
        except Exception:
            pass
    await _do_find(message.bot, message.from_user.id, "any")


# ---------------------------------------------------------------------------
# Main relay handler
# ---------------------------------------------------------------------------

@router.message(F.chat.type == "private")
async def on_private_message(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    # Let dedicated Command() handlers take precedence over this catch-all relay
    if message.text and message.text.startswith("/"):
        raise SkipHandler()
    current_state = await state.get_state()
    if current_state in (UserStates.entering_tags.state, AdminStates.waiting_search.state,
                         AdminStates.waiting_broadcast.state, AdminStates.waiting_ban_id.state,
                         AdminStates.waiting_unban_id.state, AdminStates.waiting_alias.state):
        return
    user = await db.get_user(message.from_user.id)
    if not user:
        return
    partner_id = user.get("p_id")
    if not partner_id:
        if message.text and not message.text.startswith("/"):
            await message.answer("You are not in a chat. Use the menu below.", reply_markup=MAIN_MENU_KB)
        return
    if _is_rate_limited(message.from_user.id):
        await message.answer("⚡ You're sending too fast! Please wait a moment.")
        return
    check_text = message.text or message.caption or ""
    if check_text and _has_bad_words(check_text):
        await message.answer(
            "⚠️ Your message contains inappropriate content and was not sent.\n"
            "Please keep the conversation respectful.")
        return
    reply_to_partner_msg_id: int | None = None
    if message.reply_to_message:
        doc = await db.find_msg_by_copy(message.chat.id, message.reply_to_message.message_id)
        if doc:
            for (copy_chat, copy_msg) in doc["c"]:
                if copy_chat == partner_id:
                    reply_to_partner_msg_id = copy_msg
                    break
    await _send_to_stranger(
        message.bot,
        sender_id=message.from_user.id,
        sender_msg_id=message.message_id,
        partner_id=partner_id,
        alias=user["n"],
        message=message,
        reply_to_partner_msg_id=reply_to_partner_msg_id,
    )


# ===========================================================================
# ADMIN PANEL
# ===========================================================================

@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not is_admin_pm(message):
        return
    await message.answer("🛡️ <b>Admin Panel</b>", parse_mode=ParseMode.HTML,
                         reply_markup=ADMIN_MENU_KB)


@router.callback_query(F.data == "adm_back")
async def adm_back(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    await state.clear()
    await cb.answer()
    await cb.message.edit_text("🛡️ <b>Admin Panel</b>", parse_mode=ParseMode.HTML,
                               reply_markup=ADMIN_MENU_KB)


@router.callback_query(F.data == "noop")
async def cb_noop(cb: CallbackQuery) -> None:
    await cb.answer()


# ---------------------------------------------------------------------------
# 📈 Advanced Stats
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "adm_stats")
async def adm_stats(cb: CallbackQuery) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    await cb.answer("Loading stats…")

    total        = await db.count_users()
    banned       = await db.count_banned()
    temp_banned  = await db.count_temp_banned()
    active_chat  = await db.count_active_chatters()
    waiting      = await db.count_waiting()
    total_match  = await db.count_matches_total()
    total_rep    = await db.count_reports_total()
    new_today    = await db.count_new_users_today()
    match_today  = await db.count_matches_today()
    rep_today    = await db.count_reports_today()

    perm_banned = banned - temp_banned

    text = (
        "📈 <b>Advanced System Statistics</b>\n\n"
        "━━━━━━━ Users ━━━━━━━\n"
        f"👤 Total registered:   <b>{total}</b>\n"
        f"🆕 New today:          <b>{new_today}</b>\n"
        f"🚫 Perm banned:        <b>{perm_banned}</b>\n"
        f"⏳ Temp banned:        <b>{temp_banned}</b>\n\n"
        "━━━━━━━ Activity ━━━━━━━\n"
        f"💬 Active in chat:     <b>{active_chat}</b>\n"
        f"🔍 In queue:           <b>{waiting}</b>\n\n"
        "━━━━━━━ Matches ━━━━━━━\n"
        f"🤝 All-time matches:   <b>{total_match}</b>\n"
        f"🤝 Matches today:      <b>{match_today}</b>\n\n"
        "━━━━━━━ Reports ━━━━━━━\n"
        f"🚨 Total reports:      <b>{total_rep}</b>\n"
        f"🚨 Reports today:      <b>{rep_today}</b>"
    )
    await cb.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=BACK_KB)


# ---------------------------------------------------------------------------
# 👥 User Management
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("ul_"))
async def adm_user_list(cb: CallbackQuery) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    await cb.answer()
    page = int(cb.data.split("_")[1])
    users, total = await db.get_users_paginated(page)

    if not users:
        await cb.message.edit_text("No users found.", reply_markup=BACK_KB)
        return

    lines = [f"👥 <b>User List</b> (Total: {total})\n"]
    for u in users:
        g     = _gender_label(u)
        s     = _status_label(u)
        uname = f"@{u['u']}" if u.get("u") else "—"
        lines.append(
            f"• <b>{u['n']}</b> {g} | <code>{u['_id']}</code> | {uname} | {s}"
        )

    user_buttons = [
        [InlineKeyboardButton(
            text=f"{u['n']} {_gender_label(u)}",
            callback_data=f"uv_{u['_id']}"
        )]
        for u in users
    ]
    pagination = _pagination_kb(page, total, db.USERS_PER_PAGE, "ul")
    all_buttons = user_buttons + pagination.inline_keyboard

    await cb.message.edit_text(
        "\n".join(lines), parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=all_buttons)
    )


@router.callback_query(F.data.startswith("uv_"))
async def adm_view_user(cb: CallbackQuery) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    await cb.answer()
    user_id = int(cb.data.split("_")[1])
    user = await db.get_user(user_id)
    if not user:
        await cb.message.edit_text("❌ User not found.", reply_markup=BACK_KB)
        return
    await cb.message.edit_text(
        f"👤 <b>User Profile</b>\n\n{_user_card(user)}",
        parse_mode=ParseMode.HTML,
        reply_markup=_user_action_kb(user_id)
    )


@router.callback_query(F.data.startswith("ub_"))
async def adm_ban_from_view(cb: CallbackQuery) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    user_id = int(cb.data.split("_")[1])
    user = await db.get_user(user_id)
    if not user:
        await cb.answer("User not found.", show_alert=True)
        return
    await db.ban_user(user_id)
    if user.get("p_id"):
        await _disconnect_stranger(cb.bot, user_id, user["p_id"], notify_partner=True)
    elif await db.is_in_queue(user_id):
        await db.leave_queue(user_id)
    try:
        await cb.bot.send_message(user_id, "🚫 You have been permanently banned.",
                                  reply_markup=ReplyKeyboardRemove())
    except Exception:
        pass
    await cb.answer(f"✅ Banned {user.get('n')}", show_alert=True)
    user = await db.get_user(user_id)
    await cb.message.edit_text(f"👤 <b>User Profile</b>\n\n{_user_card(user)}",
                               parse_mode=ParseMode.HTML,
                               reply_markup=_user_action_kb(user_id))


@router.callback_query(F.data.startswith("uu_"))
async def adm_unban_from_view(cb: CallbackQuery) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    user_id = int(cb.data.split("_")[1])
    user = await db.get_user(user_id)
    if not user:
        await cb.answer("User not found.", show_alert=True)
        return
    await db.unban_user(user_id)
    await cb.answer(f"✅ Unbanned {user.get('n')}", show_alert=True)
    user = await db.get_user(user_id)
    await cb.message.edit_text(f"👤 <b>User Profile</b>\n\n{_user_card(user)}",
                               parse_mode=ParseMode.HTML,
                               reply_markup=_user_action_kb(user_id))


@router.callback_query(F.data.startswith("ua_"))
async def adm_alias_prompt(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    await cb.answer()
    user_id = int(cb.data.split("_")[1])
    user = await db.get_user(user_id)
    if not user:
        await cb.message.edit_text("❌ User not found.", reply_markup=BACK_KB)
        return
    await state.set_state(AdminStates.waiting_alias)
    await state.update_data(target_id=user_id)
    await cb.message.answer(
        f"✏️ <b>Change Alias</b> for <b>{user['n']}</b> (<code>{user_id}</code>)\n\n"
        "Send the new alias (text only):",
        parse_mode=ParseMode.HTML)


@router.message(AdminStates.waiting_alias)
async def adm_do_alias(message: Message, state: FSMContext) -> None:
    if not is_admin_pm(message):
        return
    if not message.text or not message.text.strip():
        await message.answer("⚠️ Please send a non-empty alias text.")
        return
    data = await state.get_data()
    target_id = data.get("target_id")
    await state.clear()
    if not target_id:
        await message.answer("❌ Session expired. Please try again.", reply_markup=ADMIN_MENU_KB)
        return
    new_alias = message.text.strip()
    old_user = await db.get_user(target_id)
    await db.set_alias(target_id, new_alias)
    await message.answer(
        f"✅ Alias changed: <b>{old_user.get('n', '?')}</b> → <b>{new_alias}</b>",
        parse_mode=ParseMode.HTML, reply_markup=ADMIN_MENU_KB)


@router.callback_query(F.data.startswith("ucr_"))
async def adm_clear_reports(cb: CallbackQuery) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    user_id = int(cb.data.split("_")[1])
    user = await db.get_user(user_id)
    if not user:
        await cb.answer("User not found.", show_alert=True)
        return
    await db.clear_user_reports(user_id)
    await cb.answer(f"✅ Reports cleared for {user.get('n')}", show_alert=True)
    user = await db.get_user(user_id)
    await cb.message.edit_text(f"👤 <b>User Profile</b>\n\n{_user_card(user)}",
                               parse_mode=ParseMode.HTML,
                               reply_markup=_user_action_kb(user_id))


# ---------------------------------------------------------------------------
# 🔎 Search User
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "adm_search")
async def adm_search_prompt(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    await cb.answer()
    await state.set_state(AdminStates.waiting_search)
    await cb.message.answer(
        "🔎 <b>Search User</b>\n\n"
        "Send a <b>Telegram User ID</b> (numeric) or <b>@username</b>:",
        parse_mode=ParseMode.HTML)


@router.message(AdminStates.waiting_search)
async def adm_do_search(message: Message, state: FSMContext) -> None:
    if not is_admin_pm(message):
        return
    if not message.text or not message.text.strip():
        await message.answer("⚠️ Please send a user ID or username.")
        return
    await state.clear()
    query = message.text.strip()
    results = await db.search_users(query)
    if not results:
        await message.answer(f"❌ No users found for: <code>{query}</code>",
                             parse_mode=ParseMode.HTML, reply_markup=ADMIN_MENU_KB)
        return
    buttons = [
        [InlineKeyboardButton(
            text=f"{u['n']} {_gender_label(u)} | {u['_id']}",
            callback_data=f"uv_{u['_id']}"
        )]
        for u in results
    ]
    buttons.append([InlineKeyboardButton(text="⬅️ Back to Admin Panel", callback_data="adm_back")])
    await message.answer(
        f"🔎 Found <b>{len(results)}</b> result(s):",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


# ---------------------------------------------------------------------------
# 🚨 Reports Dashboard
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("rp_"))
async def adm_reports_list(cb: CallbackQuery) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    await cb.answer()
    page = int(cb.data.split("_")[1])
    reports, total = await db.get_reports_paginated(page)

    if not reports:
        await cb.message.edit_text(
            "🚨 <b>Reports Dashboard</b>\n\nNo reports found. ✅",
            parse_mode=ParseMode.HTML, reply_markup=BACK_KB)
        return

    lines = [f"🚨 <b>Reports Dashboard</b> (Total: {total})\n"]
    report_buttons = []

    for r in reports:
        ts       = r["t"].strftime("%m-%d %H:%M") if r.get("t") else "—"
        rid_str  = str(r["_id"])
        reported = r.get("reported", 0)
        reporter = r.get("reporter", 0)
        lines.append(f"• Reported: <code>{reported}</code> ← by <code>{reporter}</code>  [{ts}]")
        report_buttons.append([
            InlineKeyboardButton(text=f"🗑️ Dismiss",      callback_data=f"rd_{rid_str}"),
            InlineKeyboardButton(text=f"🔨 Ban {reported}", callback_data=f"rb_{reported}"),
        ])

    pagination = _pagination_kb(page, total, db.REPORTS_PER_PAGE, "rp")
    all_buttons = report_buttons + pagination.inline_keyboard

    await cb.message.edit_text(
        "\n".join(lines), parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=all_buttons)
    )


@router.callback_query(F.data.startswith("rd_"))
async def adm_dismiss_report(cb: CallbackQuery) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    report_id = cb.data[3:]
    try:
        await db.dismiss_report(report_id)
        await cb.answer("🗑️ Report dismissed.", show_alert=False)
    except Exception as e:
        await cb.answer(f"Error: {e}", show_alert=True)
        return
    reports, total = await db.get_reports_paginated(0)
    if not reports:
        await cb.message.edit_text("🚨 <b>Reports Dashboard</b>\n\nNo reports found. ✅",
                                   parse_mode=ParseMode.HTML, reply_markup=BACK_KB)
        return
    lines = [f"🚨 <b>Reports Dashboard</b> (Total: {total})\n"]
    report_buttons = []
    for r in reports:
        ts      = r["t"].strftime("%m-%d %H:%M") if r.get("t") else "—"
        rid_str = str(r["_id"])
        reported = r.get("reported", 0)
        reporter = r.get("reporter", 0)
        lines.append(f"• Reported: <code>{reported}</code> ← by <code>{reporter}</code>  [{ts}]")
        report_buttons.append([
            InlineKeyboardButton(text="🗑️ Dismiss",       callback_data=f"rd_{rid_str}"),
            InlineKeyboardButton(text=f"🔨 Ban {reported}", callback_data=f"rb_{reported}"),
        ])
    pagination = _pagination_kb(0, total, db.REPORTS_PER_PAGE, "rp")
    all_buttons = report_buttons + pagination.inline_keyboard
    await cb.message.edit_text("\n".join(lines), parse_mode=ParseMode.HTML,
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=all_buttons))


@router.callback_query(F.data.startswith("rb_"))
async def adm_ban_from_report(cb: CallbackQuery) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    user_id = int(cb.data.split("_")[1])
    user = await db.get_user(user_id)
    if not user:
        await cb.answer("User not found.", show_alert=True)
        return
    await db.ban_user(user_id)
    if user.get("p_id"):
        await _disconnect_stranger(cb.bot, user_id, user["p_id"], notify_partner=True)
    elif await db.is_in_queue(user_id):
        await db.leave_queue(user_id)
    try:
        await cb.bot.send_message(user_id, "🚫 You have been permanently banned.",
                                  reply_markup=ReplyKeyboardRemove())
    except Exception:
        pass
    await cb.answer(f"✅ {user.get('n')} banned.", show_alert=True)


# ---------------------------------------------------------------------------
# 📢 Broadcast / Ban / Unban (existing, unchanged)
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "adm_broadcast")
async def adm_broadcast_prompt(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    await cb.answer()
    await state.set_state(AdminStates.waiting_broadcast)
    await cb.message.edit_text(
        "📢 <b>Global Broadcast</b>\n\nSend the message (text or photo) to broadcast.",
        parse_mode=ParseMode.HTML, reply_markup=BACK_KB)


@router.message(AdminStates.waiting_broadcast)
async def adm_do_broadcast(message: Message, state: FSMContext) -> None:
    if not is_admin_pm(message):
        return
    await state.clear()
    users = await db.get_all_active_users()
    sent = failed = 0
    for user in users:
        uid = user["_id"]
        try:
            if message.photo:
                cap = message.caption or ""
                await message.bot.send_photo(uid, message.photo[-1].file_id, caption=f"📢 {cap}")
            elif message.text:
                await message.bot.send_message(
                    uid, f"📢 <b>Broadcast:</b>\n\n{message.text}", parse_mode=ParseMode.HTML)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await message.answer(f"✅ Broadcast complete.\n✔️ Sent: {sent} | ❌ Failed: {failed}",
                         reply_markup=ADMIN_MENU_KB)


@router.callback_query(F.data == "adm_ban")
async def adm_ban_prompt(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    await cb.answer()
    await state.set_state(AdminStates.waiting_ban_id)
    await cb.message.edit_text(
        "🔨 <b>Ban User</b>\n\nReply with the Telegram <b>User ID</b> to ban.",
        parse_mode=ParseMode.HTML, reply_markup=BACK_KB)


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
    if target.get("p_id"):
        await _disconnect_stranger(message.bot, target_id, target["p_id"], notify_partner=True)
    elif await db.is_in_queue(target_id):
        await db.leave_queue(target_id)
    try:
        await message.bot.send_message(target_id, "🚫 You have been permanently banned.",
                                       reply_markup=ReplyKeyboardRemove())
    except Exception:
        pass
    await message.answer(
        f"✅ User <code>{target_id}</code> (<b>{target.get('n', 'unknown')}</b>) banned.",
        parse_mode=ParseMode.HTML, reply_markup=ADMIN_MENU_KB)


@router.callback_query(F.data == "adm_unban")
async def adm_unban_prompt(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    await cb.answer()
    await state.set_state(AdminStates.waiting_unban_id)
    await cb.message.edit_text(
        "🔓 <b>Unban User</b>\n\nReply with the Telegram <b>User ID</b> to unban.",
        parse_mode=ParseMode.HTML, reply_markup=BACK_KB)


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
        f"✅ User <code>{target_id}</code> (<b>{target.get('n', 'unknown')}</b>) unbanned.",
        parse_mode=ParseMode.HTML, reply_markup=ADMIN_MENU_KB)


# ---------------------------------------------------------------------------
# Dummy web server
# ---------------------------------------------------------------------------

async def dummy_web_server() -> None:
    async def handle(_request: web.Request) -> web.Response:
        return web.Response(text="OK")
    app = web.Application()
    app.router.add_get("/", handle)
    app.router.add_get("/health", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", DUMMY_PORT).start()
    log.info("Dummy web server listening on port %s", DUMMY_PORT)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    from config import BOT_TOKEN
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp  = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await db.ensure_indexes()
    await dummy_web_server()
    log.info("Bot starting (1-on-1 stranger chat mode)…")
    await dp.start_polling(
        bot,
        allowed_updates=["message", "callback_query", "message_reaction", "edited_message"],
    )


if __name__ == "__main__":
    asyncio.run(main())
