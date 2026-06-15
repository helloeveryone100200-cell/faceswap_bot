import asyncio
import logging
import secrets as _sec
import time

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
# Bad words filter — Myanmar vulgar/toxic terms
# Extend this set freely; matching is substring + case-insensitive.
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
# Icebreaker questions sent to both users on match
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
    [InlineKeyboardButton(text="📊 System Stats", callback_data="adm_stats")],
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
    waiting_broadcast = State()
    waiting_ban_id    = State()
    waiting_unban_id  = State()


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
# Stranger connection helpers
# ---------------------------------------------------------------------------

def _gender_label(user: dict) -> str:
    g = user.get("g")
    return "👦" if g == "M" else ("👧" if g == "F" else "👤")


async def _connect_strangers(bot: Bot, user_a_id: int, user_b_id: int) -> None:
    """Pair two matched users: set partners, remove from queue, send icebreaker."""
    await db.leave_queue(user_a_id)
    await db.leave_queue(user_b_id)
    await db.set_partner(user_a_id, user_b_id)
    await db.set_partner(user_b_id, user_a_id)

    user_a = await db.get_user(user_a_id)
    user_b = await db.get_user(user_b_id)
    if not user_a or not user_b:
        return

    import random
    icebreaker = random.choice(ICEBREAKERS)

    connected_text = (
        "🎉 <b>You're connected with a stranger!</b>\n"
        "Your partner: <b>{name} {icon}</b>\n\n"
        "🧊 <b>Icebreaker:</b> <i>{ice}</i>\n\n"
        "Send a message to start chatting!"
    )

    await bot.send_message(
        user_a_id,
        connected_text.format(name=user_b["n"], icon=_gender_label(user_b), ice=icebreaker),
        parse_mode=ParseMode.HTML,
        reply_markup=STRANGER_KB,
    )
    await bot.send_message(
        user_b_id,
        connected_text.format(name=user_a["n"], icon=_gender_label(user_a), ice=icebreaker),
        parse_mode=ParseMode.HTML,
        reply_markup=STRANGER_KB,
    )


async def _disconnect_stranger(
    bot: Bot, user_id: int, partner_id: int, notify_partner: bool = True
) -> None:
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


async def _do_find(bot: Bot, user_id: int, target_gender: str) -> None:
    """
    Enter the matchmaking queue and attempt to find a partner.
    Matching priority:
      1. Immediate: shared tags + compatible gender
      2. After 10 s: gender-compatible only (tags relaxed)
      3. After 30 s more: anyone in queue
    """
    user = await db.get_user(user_id)
    if not user:
        return

    user_gender = user.get("g")
    user_tags   = user.get("tags", [])

    await db.enter_queue(user_id, user_gender, target_gender, user_tags)

    partner_id = await db.find_and_match(
        user_id, user_gender, target_gender, user_tags, strict=True
    )
    if partner_id:
        await _connect_strangers(bot, user_id, partner_id)
        return

    waiting = await db.count_waiting()
    await bot.send_message(
        user_id,
        f"🔍 Searching for a stranger… ({waiting} in queue)\n\n"
        "Use <b>🔚 Stop Chat</b> to cancel.",
        parse_mode=ParseMode.HTML,
        reply_markup=STRANGER_KB,
    )
    asyncio.create_task(_fallback_match(bot, user_id))


async def _fallback_match(bot: Bot, user_id: int) -> None:
    """Background task: relax match criteria after delays."""
    # Phase 2: after 10 s — gender only (no tag requirement)
    await asyncio.sleep(10)
    if not await db.is_in_queue(user_id):
        return

    qe = await db.get_queue_entry(user_id)
    if not qe:
        return

    user = await db.get_user(user_id)
    if not user:
        return

    partner_id = await db.find_and_match(
        user_id, user.get("g"), qe.get("tg", "any"), [], strict=False
    )
    if partner_id:
        await _connect_strangers(bot, user_id, partner_id)
        return

    # Phase 3: after 30 s more — match anyone
    await asyncio.sleep(30)
    if not await db.is_in_queue(user_id):
        return

    partner_id = await db.find_and_match(user_id, None, "any", [], strict=False)
    if partner_id:
        await _connect_strangers(bot, user_id, partner_id)
        return

    # Still nothing — update waiting count in the existing searching message
    try:
        waiting = await db.count_waiting()
        await bot.send_message(
            user_id,
            f"⏳ Still searching… ({waiting} in queue)\n\n"
            "Use <b>🔚 Stop Chat</b> to cancel.",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Media relay — relays any Telegram message type to a recipient
# ---------------------------------------------------------------------------

async def _relay_message(
    bot: Bot,
    to_id: int,
    alias: str,
    message: Message,
    reply_to_message_id: int | None = None,
) -> Message | None:
    """Relay any supported message type. Returns the sent Message or None."""
    kwargs: dict = {}
    if reply_to_message_id:
        kwargs["reply_to_message_id"] = reply_to_message_id

    try:
        if message.text:
            return await bot.send_message(
                to_id,
                f"<b>{alias}</b>\n{message.text}",
                parse_mode=ParseMode.HTML,
                **kwargs,
            )
        elif message.sticker:
            if reply_to_message_id:
                await bot.send_message(
                    to_id, f"<b>{alias}</b> sent a sticker:",
                    parse_mode=ParseMode.HTML, **kwargs
                )
            return await bot.send_sticker(to_id, message.sticker.file_id)
        elif message.photo:
            cap = f"<b>{alias}</b>\n{message.caption}" if message.caption else f"<b>{alias}</b>"
            return await bot.send_photo(
                to_id, message.photo[-1].file_id,
                caption=cap, parse_mode=ParseMode.HTML, **kwargs,
            )
        elif message.video:
            cap = f"<b>{alias}</b>\n{message.caption}" if message.caption else f"<b>{alias}</b> 🎬"
            return await bot.send_video(
                to_id, message.video.file_id,
                caption=cap, parse_mode=ParseMode.HTML, **kwargs,
            )
        elif message.video_note:
            if reply_to_message_id:
                await bot.send_message(
                    to_id, f"<b>{alias}</b> sent a video message:",
                    parse_mode=ParseMode.HTML, **kwargs,
                )
            return await bot.send_video_note(to_id, message.video_note.file_id)
        elif message.animation:
            cap = f"<b>{alias}</b>\n{message.caption}" if message.caption else f"<b>{alias}</b> 🎞️"
            return await bot.send_animation(
                to_id, message.animation.file_id,
                caption=cap, parse_mode=ParseMode.HTML, **kwargs,
            )
        elif message.voice:
            return await bot.send_voice(
                to_id, message.voice.file_id,
                caption=f"<b>{alias}</b> 🎙️", parse_mode=ParseMode.HTML, **kwargs,
            )
        elif message.audio:
            title = message.audio.title or "audio"
            return await bot.send_audio(
                to_id, message.audio.file_id,
                caption=f"<b>{alias}</b> 🎵\n<i>{title}</i>",
                parse_mode=ParseMode.HTML, **kwargs,
            )
        elif message.document:
            fname = message.document.file_name or "file"
            cap = (
                f"<b>{alias}</b>\n{message.caption}"
                if message.caption
                else f"<b>{alias}</b> 📎\n<i>{fname}</i>"
            )
            return await bot.send_document(
                to_id, message.document.file_id,
                caption=cap, parse_mode=ParseMode.HTML, **kwargs,
            )
        elif message.gift:
            star_count = getattr(message.gift.gift, "star_count", "?")
            return await bot.send_message(
                to_id,
                f"🎁 <b>{alias}</b> sent a gift worth <b>{star_count} ⭐</b>",
                parse_mode=ParseMode.HTML, **kwargs,
            )
        elif message.paid_media:
            return await bot.send_message(
                to_id,
                f"💎 <b>{alias}</b> shared paid media.",
                parse_mode=ParseMode.HTML, **kwargs,
            )
    except Exception as e:
        log.warning("Relay to %s failed: %s", to_id, e)

    return None


async def _send_to_stranger(
    bot: Bot,
    sender_id: int,
    sender_msg_id: int,
    partner_id: int,
    alias: str,
    message: Message,
    reply_to_partner_msg_id: int | None = None,
) -> None:
    """Relay a message to the stranger and record both message IDs for sync."""
    msg_key = _sec.token_urlsafe(6)
    sent = await _relay_message(bot, partner_id, alias, message, reply_to_partner_msg_id)
    if sent:
        await db.create_msg(
            msg_key,
            [[sender_id, sender_msg_id], [partner_id, sent.message_id]],
        )


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
        for r in event.new_reaction
        if r.type == "emoji"
    ]

    for (copy_chat, copy_msg) in doc["c"]:
        if copy_chat == event.chat.id and copy_msg == event.message_id:
            continue
        try:
            await bot.set_message_reaction(
                chat_id=copy_chat, message_id=copy_msg, reaction=reactions
            )
        except Exception as e:
            log.warning("Reaction mirror failed %s/%s: %s", copy_chat, copy_msg, e)


# ---------------------------------------------------------------------------
# Edit sync — when a user edits their sent message, mirror to partner's copy
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
                chat_id=copy_chat,
                message_id=copy_msg,
                parse_mode=ParseMode.HTML,
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
            remaining = ban_exp - __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            hrs = int(remaining.total_seconds() // 3600)
            mins = int((remaining.total_seconds() % 3600) // 60)
            await message.answer(
                f"🚫 You are temporarily banned.\n"
                f"⏳ Remaining: <b>{hrs}h {mins}m</b>",
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.answer("🚫 You have been permanently banned from this service.")
        return

    if user.get("p_id"):
        await message.answer(
            "You are already in a chat. Use <b>🔚 Stop Chat</b> to exit first.",
            parse_mode=ParseMode.HTML,
            reply_markup=STRANGER_KB,
        )
        return

    # First time: ask gender
    if not user.get("g"):
        await message.answer(
            f"👋 Welcome to <b>Anonymous Chat</b>!\n\n"
            f"Your alias: <b>{user['n']}</b>\n\n"
            "First, please select your gender:",
            parse_mode=ParseMode.HTML,
            reply_markup=GENDER_SELECT_KB,
        )
        return

    await message.answer(
        f"👋 Welcome back, <b>{user['n']}</b> {_gender_label(user)}!\n\n"
        "Ready to chat with a stranger?",
        parse_mode=ParseMode.HTML,
        reply_markup=MAIN_MENU_KB,
    )


# ---------------------------------------------------------------------------
# Gender selection callbacks
# ---------------------------------------------------------------------------

@router.callback_query(F.data.in_({"gender_m", "gender_f"}))
async def cb_set_gender(cb: CallbackQuery) -> None:
    await cb.answer()
    gender = "M" if cb.data == "gender_m" else "F"
    await db.set_gender(cb.from_user.id, gender)
    user = await db.get_user(cb.from_user.id)
    icon = "👦" if gender == "M" else "👧"
    await cb.message.edit_text(
        f"✅ Gender set to {icon}\n\n"
        f"Your alias: <b>{user['n']}</b>\n\n"
        "Ready to chat with a stranger?",
        parse_mode=ParseMode.HTML,
        reply_markup=MAIN_MENU_KB,
    )


# ---------------------------------------------------------------------------
# Tags / interests
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "my_tags")
async def cb_my_tags(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    user = await db.get_user(cb.from_user.id)
    current = ", ".join(f"#{t}" for t in user.get("tags", [])) if user.get("tags") else "none"
    await state.set_state(UserStates.entering_tags)
    await cb.message.answer(
        f"🏷️ <b>Your current tags:</b> {current}\n\n"
        "Send up to <b>3 hashtags</b> separated by spaces.\n"
        "Example: <code>#gaming #movies #kpop</code>\n\n"
        "These help match you with people who share your interests!",
        parse_mode=ParseMode.HTML,
    )


@router.message(UserStates.entering_tags)
async def handle_tags_input(message: Message, state: FSMContext) -> None:
    await state.clear()
    if not message.text:
        await message.answer("⚠️ Please send hashtags as text.")
        return

    raw_tags = [
        t.lstrip("#").lower().strip()
        for t in message.text.split()
        if t.startswith("#") and len(t) > 1
    ]

    if not raw_tags:
        await message.answer(
            "⚠️ No valid hashtags found. Use format: <code>#gaming #movies #kpop</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    tags = list(dict.fromkeys(raw_tags))[:3]
    await db.set_tags(message.from_user.id, tags)

    display = " ".join(f"#{t}" for t in tags)
    await message.answer(
        f"✅ Tags saved: <b>{display}</b>\n\n"
        "These will be used to find like-minded strangers!",
        parse_mode=ParseMode.HTML,
        reply_markup=MAIN_MENU_KB,
    )


# ---------------------------------------------------------------------------
# Find stranger — show target gender selection
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
        await cb.message.answer(
            "You are already in a chat. Use <b>🔚 Stop Chat</b> to exit first.",
            parse_mode=ParseMode.HTML,
            reply_markup=STRANGER_KB,
        )
        return

    if not user.get("g"):
        await cb.message.answer(
            "Please set your gender first:",
            reply_markup=GENDER_SELECT_KB,
        )
        return

    tags = user.get("tags", [])
    tag_hint = (
        f"\n🏷️ Your tags: {' '.join('#' + t for t in tags)}" if tags
        else "\n💡 Tip: Set /tags to find like-minded strangers!"
    )

    await cb.message.answer(
        f"Who would you like to talk to?{tag_hint}",
        parse_mode=ParseMode.HTML,
        reply_markup=TARGET_GENDER_KB,
    )


@router.callback_query(F.data.in_({"tg_any", "tg_m", "tg_f"}))
async def cb_target_gender(cb: CallbackQuery) -> None:
    await cb.answer()
    mapping = {"tg_any": "any", "tg_m": "M", "tg_f": "F"}
    target_gender = mapping[cb.data]

    user = await db.get_or_create_user(cb.from_user.id, cb.from_user.username)
    if user.get("p_id"):
        await cb.message.answer("You are already in a chat.", reply_markup=STRANGER_KB)
        return

    labels = {"any": "Anyone 🌐", "M": "Boys 👦", "F": "Girls 👧"}
    await cb.message.answer(
        f"✅ Searching for: <b>{labels[target_gender]}</b>\n🔍 Looking for your match…",
        parse_mode=ParseMode.HTML,
    )
    await _do_find(cb.bot, cb.from_user.id, target_gender)


# ---------------------------------------------------------------------------
# /tags command shortcut
# ---------------------------------------------------------------------------

@router.message(Command("tags"))
async def cmd_tags(message: Message, state: FSMContext) -> None:
    user = await db.get_user(message.from_user.id)
    current = ", ".join(f"#{t}" for t in user.get("tags", [])) if user and user.get("tags") else "none"
    await state.set_state(UserStates.entering_tags)
    await message.answer(
        f"🏷️ <b>Your current tags:</b> {current}\n\n"
        "Send up to <b>3 hashtags</b> separated by spaces.\n"
        "Example: <code>#gaming #movies #kpop</code>",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# Stranger chat — incoming messages
# ---------------------------------------------------------------------------

@router.message(F.text == STOP_TEXT)
async def stop_chat(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    if not user:
        return

    if await db.is_in_queue(message.from_user.id):
        await db.leave_queue(message.from_user.id)
        await message.answer(
            "🔚 Search cancelled.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await message.answer("Want to try again?", reply_markup=MAIN_MENU_KB)
        return

    partner_id = user.get("p_id")
    if not partner_id:
        await message.answer(
            "You are not in a chat.", reply_markup=ReplyKeyboardRemove()
        )
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
        await _disconnect_stranger(
            message.bot, message.from_user.id, partner_id, notify_partner=True
        )

    await db.leave_queue(message.from_user.id)
    await message.answer("🔍 Finding next stranger…", parse_mode=ParseMode.HTML)
    await _do_find(message.bot, message.from_user.id, "any")


@router.message(F.text == REPORT_TEXT)
async def report_stranger(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    if not user or not user.get("p_id"):
        await message.answer("You are not in a chat.")
        return

    partner_id = user["p_id"]

    await _disconnect_stranger(
        message.bot, message.from_user.id, partner_id, notify_partner=False
    )

    new_count = await db.add_report(message.from_user.id, partner_id)

    await message.answer(
        "🚨 <b>Report submitted.</b>\n"
        "You have been disconnected from that user.\n"
        "Looking for a new stranger…",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )

    if new_count >= db.AUTO_BAN_REPORT_THRESHOLD:
        try:
            await message.bot.send_message(
                partner_id,
                "🚫 Your account has been suspended for 24 hours due to multiple reports.",
                reply_markup=ReplyKeyboardRemove(),
            )
        except Exception:
            pass
    else:
        try:
            await message.bot.send_message(
                partner_id,
                "⚠️ You have been reported and disconnected.",
                reply_markup=ReplyKeyboardRemove(),
            )
            await message.bot.send_message(partner_id, "Find someone new?", reply_markup=MAIN_MENU_KB)
        except Exception:
            pass

    await _do_find(message.bot, message.from_user.id, "any")


@router.message(F.chat.type == "private")
async def on_private_message(message: Message, state: FSMContext) -> None:
    """Main relay handler for all private messages while in a stranger chat."""
    if not message.from_user:
        return

    current_state = await state.get_state()
    if current_state == UserStates.entering_tags.state:
        return

    user = await db.get_user(message.from_user.id)
    if not user:
        return

    partner_id = user.get("p_id")
    if not partner_id:
        if message.text and not message.text.startswith("/"):
            await message.answer(
                "You are not in a chat. Use the menu below.",
                reply_markup=MAIN_MENU_KB,
            )
        return

    # Rate limit check
    if _is_rate_limited(message.from_user.id):
        await message.answer(
            "⚡ You're sending too fast! Please wait a moment.",
        )
        return

    # Bad words filter (text and captions only)
    check_text = message.text or message.caption or ""
    if check_text and _has_bad_words(check_text):
        await message.answer(
            "⚠️ Your message contains inappropriate content and was not sent.\n"
            "Please keep the conversation respectful.",
        )
        return

    # Determine reply_to_partner_msg_id for reply sync
    reply_to_partner_msg_id: int | None = None
    if message.reply_to_message:
        replied_to_msg_id = message.reply_to_message.message_id
        doc = await db.find_msg_by_copy(message.chat.id, replied_to_msg_id)
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


# ---------------------------------------------------------------------------
# Admin panel
# ---------------------------------------------------------------------------

@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not is_admin_pm(message):
        return
    await message.answer(
        "🛡️ <b>Admin Panel</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=ADMIN_MENU_KB,
    )


@router.callback_query(F.data == "adm_back")
async def adm_back(cb: CallbackQuery) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    await cb.answer()
    await cb.message.edit_text(
        "🛡️ <b>Admin Panel</b>", parse_mode=ParseMode.HTML, reply_markup=ADMIN_MENU_KB
    )


@router.callback_query(F.data == "adm_stats")
async def adm_stats(cb: CallbackQuery) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    await cb.answer()

    total   = await db.count_users()
    banned  = await db.count_banned()
    active  = await db.count_active_chatters()
    waiting = await db.count_waiting()

    text = (
        "📊 <b>System Statistics</b>\n\n"
        f"👤 Total registered: <b>{total}</b>\n"
        f"🚫 Banned users:     <b>{banned}</b>\n"
        f"💬 Active in chat:   <b>{active}</b>\n"
        f"🔍 Waiting in queue: <b>{waiting}</b>"
    )
    await cb.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=BACK_KB)


@router.callback_query(F.data == "adm_broadcast")
async def adm_broadcast_prompt(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    await cb.answer()
    await state.set_state(AdminStates.waiting_broadcast)
    await cb.message.edit_text(
        "📢 <b>Global Broadcast</b>\n\nSend the message (text or photo) to broadcast.",
        parse_mode=ParseMode.HTML,
        reply_markup=BACK_KB,
    )


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
                caption = message.caption or ""
                await message.bot.send_photo(uid, message.photo[-1].file_id, caption=f"📢 {caption}")
            elif message.text:
                await message.bot.send_message(
                    uid, f"📢 <b>Broadcast:</b>\n\n{message.text}", parse_mode=ParseMode.HTML
                )
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
        "🔨 <b>Ban User</b>\n\nReply with the Telegram <b>User ID</b> to ban.",
        parse_mode=ParseMode.HTML,
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

    if target.get("p_id"):
        await _disconnect_stranger(message.bot, target_id, target["p_id"], notify_partner=True)
    elif await db.is_in_queue(target_id):
        await db.leave_queue(target_id)

    try:
        await message.bot.send_message(
            target_id,
            "🚫 You have been permanently banned.",
            reply_markup=ReplyKeyboardRemove(),
        )
    except Exception:
        pass

    await message.answer(
        f"✅ User <code>{target_id}</code> (<b>{target.get('n', 'unknown')}</b>) has been banned.",
        parse_mode=ParseMode.HTML,
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
        "🔓 <b>Unban User</b>\n\nReply with the Telegram <b>User ID</b> to unban.",
        parse_mode=ParseMode.HTML,
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
        f"✅ User <code>{target_id}</code> (<b>{target.get('n', 'unknown')}</b>) has been unbanned.",
        parse_mode=ParseMode.HTML,
        reply_markup=ADMIN_MENU_KB,
    )


# ---------------------------------------------------------------------------
# Dummy web server (keeps Render.com service alive)
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
