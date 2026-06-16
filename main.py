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
# Live active-user cache  (refreshed every 60 s by background loop)
# ---------------------------------------------------------------------------

GLOBAL_NORMAL_ACTIVE: int = 0
GLOBAL_ADULT_ACTIVE:  int = 0


async def _refresh_active_counts() -> None:
    """Background loop: query MongoDB once per minute, update in-memory cache."""
    global GLOBAL_NORMAL_ACTIVE, GLOBAL_ADULT_ACTIVE
    while True:
        try:
            GLOBAL_NORMAL_ACTIVE = await db.count_active_by_mode("normal")
            GLOBAL_ADULT_ACTIVE  = await db.count_active_by_mode("adult")
        except Exception as _exc:
            log.warning("Active-count cache refresh failed: %s", _exc)
        await asyncio.sleep(60)


# ---------------------------------------------------------------------------
# Rate limiter
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
# Icebreakers
# ---------------------------------------------------------------------------

ICEBREAKERS: list[str] = [
    "🍕🍔 What's your all-time favorite food?",
    "🎬 What movie can you watch over and over again?",
    "🎵 What song is stuck in your head right now?",
    "🌏 If you could visit any country tomorrow, where would you go?",
    "🎮 What's your favorite game to play (mobile, PC, or board)?",
    "🐾 Do you prefer cats 🐱 or dogs 🐶?",
    "🌙 Are you a night owl or an early bird?",
    "📚 Last book or manga/webtoon you really enjoyed?",
    "☕🧋 Coffee or boba tea?",
    "🦸 If you had one superpower, what would it be?",
]

# ---------------------------------------------------------------------------
# Gifts
# ---------------------------------------------------------------------------

GIFTS: list[tuple[str, str]] = [
    ("☕", "Coffee"),
    ("🧋", "Bubble Tea"),
    ("🍦", "Ice Cream"),
    ("🍕", "Pizza"),
    ("🌹", "Rose"),
    ("❤️", "Heart"),
    ("🍫", "Chocolate"),
    ("🧸", "Teddy Bear"),
    ("✨", "Magic Star"),
    ("🎉", "Party Popper"),
]

GIFT_KB = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="☕ Coffee",      callback_data="gft_0"),
        InlineKeyboardButton(text="🧋 Bubble Tea",  callback_data="gft_1"),
    ],
    [
        InlineKeyboardButton(text="🍦 Ice Cream",   callback_data="gft_2"),
        InlineKeyboardButton(text="🍕 Pizza",       callback_data="gft_3"),
    ],
    [
        InlineKeyboardButton(text="🌹 Rose",        callback_data="gft_4"),
        InlineKeyboardButton(text="❤️ Heart",       callback_data="gft_5"),
    ],
    [
        InlineKeyboardButton(text="🍫 Chocolate",   callback_data="gft_6"),
        InlineKeyboardButton(text="🧸 Teddy Bear",  callback_data="gft_7"),
    ],
    [
        InlineKeyboardButton(text="✨ Magic Star",   callback_data="gft_8"),
        InlineKeyboardButton(text="🎉 Party Popper", callback_data="gft_9"),
    ],
    [InlineKeyboardButton(text="❌ Cancel",          callback_data="gft_cancel")],
])

# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------

MAIN_MENU_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🔍 Find a Stranger",     callback_data="find_stranger")],
    [InlineKeyboardButton(text="🏷️ My Interests / Tags", callback_data="my_tags")],
    [InlineKeyboardButton(text="👤 My Profile",          callback_data="my_profile")],
])


def _mode_select_kb() -> InlineKeyboardMarkup:
    """Returns mode-select keyboard with live cached active-user counts."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"🌐 Normal Mode ({GLOBAL_NORMAL_ACTIVE} Active)",
            callback_data="ms_normal",
        )],
        [InlineKeyboardButton(
            text=f"🔥 18+ Adult Mode ({GLOBAL_ADULT_ACTIVE} Active)",
            callback_data="ms_adult",
        )],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="go_main_menu")],
    ])


GENDER_SELECT_KB = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="Male 👦",   callback_data="gender_m"),
        InlineKeyboardButton(text="Female 👧", callback_data="gender_f"),
    ],
    [InlineKeyboardButton(text="⬅️ Back", callback_data="go_main_menu")],
])


def _target_gender_kb(mode: str) -> InlineKeyboardMarkup:
    m = "n" if mode == "normal" else "a"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Anyone 🌐",     callback_data=f"find_{m}_any")],
        [
            InlineKeyboardButton(text="Find Boys 👦",  callback_data=f"find_{m}_m"),
            InlineKeyboardButton(text="Find Girls 👧", callback_data=f"find_{m}_f"),
        ],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="go_mode_select")],
    ])


STRANGER_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="⏭️ Next Stranger"), KeyboardButton(text="🔚 Stop Chat")],
        [KeyboardButton(text="🎁 Send Gift"),      KeyboardButton(text="🚨 Report Stranger")],
    ],
    resize_keyboard=True,
)

ADMIN_MENU_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📈 Advanced Stats",     callback_data="adm_stats")],
    [InlineKeyboardButton(text="👥 User Management",    callback_data="ul_0")],
    [InlineKeyboardButton(text="🚨 Reports Dashboard",  callback_data="rp_0")],
    [InlineKeyboardButton(text="🔎 Search User",        callback_data="adm_search")],
    [InlineKeyboardButton(text="📢 Global Broadcast",   callback_data="adm_broadcast")],
    [InlineKeyboardButton(text="🔨 Ban User",           callback_data="adm_ban")],
    [InlineKeyboardButton(text="🔓 Unban User",         callback_data="adm_unban")],
])

BACK_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="⬅️ Back to Admin Panel", callback_data="adm_back")]
])

NEXT_TEXT   = "⏭️ Next Stranger"
STOP_TEXT   = "🔚 Stop Chat"
REPORT_TEXT = "🚨 Report Stranger"
GIFT_TEXT   = "🎁 Send Gift"


# ---------------------------------------------------------------------------
# FSM States
# ---------------------------------------------------------------------------

class UserStates(StatesGroup):
    entering_tags = State()


class ProfileStates(StatesGroup):
    changing_alias = State()


class AdminStates(StatesGroup):
    waiting_broadcast = State()
    waiting_ban_id    = State()
    waiting_unban_id  = State()
    waiting_alias     = State()
    waiting_search    = State()


# ---------------------------------------------------------------------------
# Rating: in-memory set to prevent double-rating
# ---------------------------------------------------------------------------

_rated: set[tuple[int, int]] = set()


def _rating_kb(partner_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="👍 Good Chat", callback_data=f"rate_up_{partner_id}"),
        InlineKeyboardButton(text="👎 Bored",     callback_data=f"rate_dn_{partner_id}"),
    ]])


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
# Formatting helpers
# ---------------------------------------------------------------------------

def _gender_label(user: dict) -> str:
    g = user.get("g")
    return "👦" if g == "M" else ("👧" if g == "F" else "👤")


def _status_label(user: dict) -> str:
    if user.get("s") == 0:
        return "⏳ Temp Ban" if user.get("ban_exp") else "🚫 Banned"
    return "💬 In Chat" if user.get("p_id") else "✅ Active"


def _streak_line(user: dict) -> str:
    streak = user.get("streak", 0)
    if streak >= 7:
        return f"\n🔥 <b>{streak}-Day Streak!</b> Keep it up!"
    elif streak >= 2:
        return f"\n🔥 {streak}-Day Streak!"
    return ""


def _user_card(user: dict) -> str:
    uid     = user["_id"]
    alias   = user.get("n", "—")
    uname   = f"@{user['u']}" if user.get("u") else "—"
    gender  = _gender_label(user)
    status  = _status_label(user)
    tags    = " ".join(f"#{t}" for t in user.get("tags", [])) or "—"
    reports = user.get("report_count", 0)
    karma   = user.get("karma_points", 0)
    joined  = user.get("j")
    join_str = joined.strftime("%Y-%m-%d") if joined else "—"
    return (
        f"👤 <b>{alias}</b> {gender}\n"
        f"🆔 <code>{uid}</code>  |  {uname}\n"
        f"📊 Status: {status}\n"
        f"⭐ Karma: <b>{karma}</b>\n"
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
        [InlineKeyboardButton(text="✏️ Change Alias",  callback_data=f"ua_{s}")],
        [InlineKeyboardButton(text="🗑️ Clear Reports", callback_data=f"ucr_{s}")],
        [InlineKeyboardButton(text="⬅️ Back to Admin Panel", callback_data="adm_back")],
    ])


def _pagination_kb(current_page: int, total: int, per_page: int, prefix: str) -> InlineKeyboardMarkup:
    total_pages = max(1, (total + per_page - 1) // per_page)
    row = []
    if current_page > 0:
        row.append(InlineKeyboardButton(text="◀️ Prev", callback_data=f"{prefix}_{current_page - 1}"))
    row.append(InlineKeyboardButton(text=f"{current_page + 1}/{total_pages}", callback_data="noop"))
    if (current_page + 1) * per_page < total:
        row.append(InlineKeyboardButton(text="Next ▶️", callback_data=f"{prefix}_{current_page + 1}"))
    return InlineKeyboardMarkup(inline_keyboard=[
        row,
        [InlineKeyboardButton(text="⬅️ Back to Admin Panel", callback_data="adm_back")],
    ])


# ===========================================================================
# UI / Message Cleanup Helpers
# ===========================================================================

async def _try_delete(bot: Bot, chat_id: int, msg_id: int) -> None:
    """Silently delete a single message. Never raises."""
    try:
        await bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except Exception:
        pass


async def _delete_search_msg(bot: Bot, user_id: int) -> None:
    """Delete the stored 'Searching...' status message for a user."""
    msg_id = await db.get_search_msg(user_id)
    if msg_id:
        await _try_delete(bot, user_id, msg_id)


async def _bulk_delete_chat(
    bot: Bot, user_id: int,
    start_msg_id: int | None,
    end_msg_id: int | None,
) -> None:
    """
    Range-delete all bot messages in [start_msg_id, end_msg_id] from a user's chat.

    Uses Telegram's native deleteMessages batch API (up to 100 IDs per call),
    which is ~100x faster than one-by-one deletion and far less likely to hit
    rate limits.  Any IDs the bot never sent (user's own messages, already-
    deleted messages) are silently ignored by Telegram — no extra error handling
    needed beyond the outer try/except.
    """
    if not start_msg_id or not end_msg_id or end_msg_id < start_msg_id:
        return
    all_ids = list(range(start_msg_id, end_msg_id + 1))
    BATCH = 100  # Telegram deleteMessages maximum
    for i in range(0, len(all_ids), BATCH):
        batch = all_ids[i : i + BATCH]
        try:
            await bot.delete_messages(chat_id=user_id, message_ids=batch)
        except Exception:
            # Batch failed (e.g. all messages already deleted) — fall back to
            # individual deletes so we don't silently miss partial success.
            await asyncio.gather(
                *[_try_delete(bot, user_id, mid) for mid in batch],
                return_exceptions=True,
            )


async def _bg_update_last(user_id: int, msg_id: int) -> None:
    """Background task: slide the session's last-message pointer forward."""
    try:
        await db.update_chat_last(user_id, msg_id)
    except Exception:
        pass


# ===========================================================================
# Core chat helpers
# ===========================================================================

async def _connect_strangers(bot: Bot, user_a_id: int, user_b_id: int, mode: str = "normal") -> None:
    import random
    await db.leave_queue(user_a_id)
    await db.leave_queue(user_b_id)
    await db.set_partner(user_a_id, user_b_id)
    await db.set_partner(user_b_id, user_a_id)
    await db.set_chat_mode(user_a_id, mode)
    await db.set_chat_mode(user_b_id, mode)
    await db.log_match(user_a_id, user_b_id)

    await db.update_streak(user_a_id)
    await db.update_streak(user_b_id)

    user_a = await db.get_user(user_a_id)
    user_b = await db.get_user(user_b_id)
    if not user_a or not user_b:
        return

    # ── Delete any stale "Searching..." status messages ──────────────────────
    await asyncio.gather(
        _delete_search_msg(bot, user_a_id),
        _delete_search_msg(bot, user_b_id),
        return_exceptions=True,
    )

    icebreaker = random.choice(ICEBREAKERS)
    mode_tag = " 🔥 <i>Adult Mode</i>" if mode == "adult" else " 🌐 <i>Normal Mode</i>"
    tpl = (
        "🎉 <b>Connected!</b>{mode_tag}\n"
        "Partner: <b>{name} {icon}</b>\n\n"
        "🧊 <b>Icebreaker:</b> <i>{ice}</i>\n\n"
        "Start chatting! Use <b>🎁 Send Gift</b> to surprise your partner."
    )

    # ── Send "Connected!" and record start_msg_id for range deletion ──────────
    for (me_id, partner) in [(user_a_id, user_b), (user_b_id, user_a)]:
        try:
            sent = await bot.send_message(
                me_id,
                tpl.format(mode_tag=mode_tag, name=partner["n"],
                           icon=_gender_label(partner), ice=icebreaker),
                parse_mode=ParseMode.HTML, reply_markup=STRANGER_KB,
            )
            # Record this message as the start of the deletable chat range
            await db.set_chat_start(me_id, sent.message_id)
        except Exception as e:
            log.warning("connect notify %s failed: %s", me_id, e)


async def _disconnect_stranger(
    bot: Bot, user_id: int, partner_id: int,
    notify_partner: bool = True,
    send_rating: bool = False,
) -> None:
    # ── Snapshot session data BEFORE clearing (needed for range deletion) ─────
    sess_user, sess_partner = await asyncio.gather(
        db.get_session(user_id),
        db.get_session(partner_id),
    )

    # ── Clear partner / mode links in DB ─────────────────────────────────────
    await asyncio.gather(
        db.set_partner(user_id, None),
        db.set_partner(partner_id, None),
        db.set_chat_mode(user_id, None),
        db.set_chat_mode(partner_id, None),
    )

    # ── Erase session records (DB stays lean after every chat) ───────────────
    await asyncio.gather(
        db.clear_session(user_id),
        db.clear_session(partner_id),
    )

    # ── Fire bulk-delete tasks in the background (non-blocking) ──────────────
    if sess_user and sess_user.get("start") and sess_user.get("last"):
        asyncio.create_task(
            _bulk_delete_chat(bot, user_id, sess_user["start"], sess_user["last"])
        )
    if sess_partner and sess_partner.get("start") and sess_partner.get("last"):
        asyncio.create_task(
            _bulk_delete_chat(bot, partner_id, sess_partner["start"], sess_partner["last"])
        )

    # ── Notify disconnected partner ───────────────────────────────────────────
    if notify_partner:
        try:
            await bot.send_message(partner_id, "🔌 Your chat partner has disconnected.",
                                   reply_markup=ReplyKeyboardRemove())
            if send_rating:
                await bot.send_message(
                    partner_id, "How was the conversation?",
                    reply_markup=_rating_kb(user_id))
            await bot.send_message(partner_id, "Find someone new?",
                                   reply_markup=MAIN_MENU_KB)
        except Exception:
            pass


async def _do_find(bot: Bot, user_id: int, target_gender: str, mode: str = "normal") -> None:
    user = await db.get_user(user_id)
    if not user:
        return

    user_gender = user.get("g")
    user_tags   = user.get("tags", [])
    user_karma  = user.get("karma_points", 0)

    await db.enter_queue(user_id, user_gender, target_gender, user_tags,
                         mode=mode, karma=user_karma)

    partner_id = await db.find_and_match(
        user_id, user_gender, target_gender, user_tags,
        mode=mode, user_karma=user_karma, strict=True,
    )
    if partner_id:
        await _connect_strangers(bot, user_id, partner_id, mode)
        return

    waiting = await db.count_waiting()
    mode_label = "🔥 Adult Mode" if mode == "adult" else "🌐 Normal Mode"

    # ── Send "Searching..." and save the message_id for later cleanup ─────────
    search_msg = await bot.send_message(
        user_id,
        f"🔍 Searching [{mode_label}]… ({waiting} in queue)\n\nUse <b>🔚 Stop Chat</b> to cancel.",
        parse_mode=ParseMode.HTML, reply_markup=STRANGER_KB,
    )
    await db.save_search_msg(user_id, search_msg.message_id)

    asyncio.create_task(_fallback_match(bot, user_id, mode))


async def _fallback_match(bot: Bot, user_id: int, mode: str) -> None:
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
        user_id, user.get("g"), qe.get("tg", "any"), [],
        mode=mode, strict=False,
    )
    if partner_id:
        await _connect_strangers(bot, user_id, partner_id, mode)
        return

    await asyncio.sleep(30)
    if not await db.is_in_queue(user_id):
        return
    partner_id = await db.find_and_match(user_id, None, "any", [], mode=mode, strict=False)
    if partner_id:
        await _connect_strangers(bot, user_id, partner_id, mode)
        return
    try:
        waiting = await db.count_waiting()
        still_msg = await bot.send_message(
            user_id,
            f"⏳ Still searching… ({waiting} in queue)\nUse <b>🔚 Stop Chat</b> to cancel.",
            parse_mode=ParseMode.HTML,
        )
        # Update stored search_msg so the new status message gets cleaned up too
        await db.save_search_msg(user_id, still_msg.message_id)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Media relay
# ---------------------------------------------------------------------------

async def _relay_message(
    bot: Bot, to_id: int, alias: str, message: Message,
    reply_to_message_id: int | None = None,
    spoiler: bool = False,
) -> Message | None:
    kwargs: dict = {}
    if reply_to_message_id:
        kwargs["reply_to_message_id"] = reply_to_message_id
    try:
        if message.text:
            return await bot.send_message(
                to_id, f"<b>{alias}</b>\n{message.text}",
                parse_mode=ParseMode.HTML, **kwargs)
        elif message.sticker:
            if reply_to_message_id:
                await bot.send_message(to_id, f"<b>{alias}</b> sent a sticker:",
                                       parse_mode=ParseMode.HTML, **kwargs)
            return await bot.send_sticker(to_id, message.sticker.file_id)
        elif message.photo:
            cap = f"<b>{alias}</b>\n{message.caption}" if message.caption else f"<b>{alias}</b>"
            return await bot.send_photo(to_id, message.photo[-1].file_id,
                                        caption=cap, parse_mode=ParseMode.HTML,
                                        has_spoiler=spoiler, **kwargs)
        elif message.video:
            cap = f"<b>{alias}</b>\n{message.caption}" if message.caption else f"<b>{alias}</b> 🎬"
            return await bot.send_video(to_id, message.video.file_id,
                                        caption=cap, parse_mode=ParseMode.HTML,
                                        has_spoiler=spoiler, **kwargs)
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
                                        caption=f"<b>{alias}</b> 🎵 <i>{title}</i>",
                                        parse_mode=ParseMode.HTML, **kwargs)
        elif message.document:
            fname = message.document.file_name or "file"
            cap = (f"<b>{alias}</b>\n{message.caption}" if message.caption
                   else f"<b>{alias}</b> 📎 <i>{fname}</i>")
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
    spoiler: bool = False,
) -> None:
    msg_key = _sec.token_urlsafe(6)
    sent = await _relay_message(bot, partner_id, alias, message,
                                reply_to_partner_msg_id, spoiler=spoiler)
    if sent:
        await db.create_msg(msg_key, [[sender_id, sender_msg_id], [partner_id, sent.message_id]])
        # Slide the partner's session last-message pointer forward (background — non-blocking)
        asyncio.create_task(_bg_update_last(partner_id, sent.message_id))


# ---------------------------------------------------------------------------
# Reaction mirror
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
            log.warning("Reaction mirror failed: %s", e)


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
            log.warning("Edit sync failed: %s", e)


# ===========================================================================
# /start
# ===========================================================================

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
            await message.answer("🚫 You have been permanently banned.")
        return

    if user.get("p_id"):
        await message.answer("You are already in a chat. Use <b>🔚 Stop Chat</b> first.",
                             parse_mode=ParseMode.HTML, reply_markup=STRANGER_KB)
        return

    if not user.get("g"):
        await message.answer(
            f"👋 Welcome to <b>Anonymous Chat</b>!\n\nYour alias: <b>{user['n']}</b>\n\n"
            "First, please select your gender:",
            parse_mode=ParseMode.HTML, reply_markup=GENDER_SELECT_KB)
        return

    streak = await db.update_streak(message.from_user.id)
    user = await db.get_user(message.from_user.id)
    streak_line = _streak_line(user)
    await message.answer(
        f"👋 Welcome back, <b>{user['n']}</b> {_gender_label(user)}!{streak_line}\n\nReady to chat?",
        parse_mode=ParseMode.HTML, reply_markup=MAIN_MENU_KB)


# ---------------------------------------------------------------------------
# Global navigation callbacks
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "go_main_menu")
async def cb_go_main_menu(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.clear()
    user = await db.get_user(cb.from_user.id)
    if not user:
        await cb.message.edit_text("👋 Ready to chat?", reply_markup=MAIN_MENU_KB)
        return
    streak_line = _streak_line(user)
    try:
        await cb.message.edit_text(
            f"👋 <b>{user['n']}</b> {_gender_label(user)}{streak_line}\n\nReady to chat?",
            parse_mode=ParseMode.HTML, reply_markup=MAIN_MENU_KB)
    except Exception:
        await cb.message.answer(
            f"👋 <b>{user['n']}</b> {_gender_label(user)}{streak_line}\n\nReady to chat?",
            parse_mode=ParseMode.HTML, reply_markup=MAIN_MENU_KB)


@router.callback_query(F.data == "go_mode_select")
async def cb_go_mode_select(cb: CallbackQuery) -> None:
    await cb.answer()
    try:
        await cb.message.edit_text("Choose your chat mode:", reply_markup=_mode_select_kb())
    except Exception:
        await cb.message.answer("Choose your chat mode:", reply_markup=_mode_select_kb())


@router.callback_query(F.data == "go_profile")
async def cb_go_profile(cb: CallbackQuery) -> None:
    await cb.answer()
    user = await db.get_user(cb.from_user.id)
    if not user:
        return
    await _show_profile(cb.message.edit_text, user)


@router.callback_query(F.data == "gft_cancel")
async def cb_gift_cancel(cb: CallbackQuery) -> None:
    await cb.answer("Cancelled.")
    try:
        await cb.message.delete()
    except Exception:
        await cb.message.edit_text("🎁 Gift cancelled.")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass
    if current:
        user = await db.get_user(message.from_user.id)
        streak_line = _streak_line(user) if user else ""
        alias = user["n"] if user else "—"
        g = _gender_label(user) if user else ""
        cancel_text = f"👋 <b>{alias}</b> {g}{streak_line}\n\nReady to chat?"
        if prompt_msg_id:
            try:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id, message_id=prompt_msg_id,
                    text=cancel_text, parse_mode=ParseMode.HTML, reply_markup=MAIN_MENU_KB)
                return
            except Exception:
                pass
        await message.answer(cancel_text, parse_mode=ParseMode.HTML, reply_markup=MAIN_MENU_KB)
    else:
        await message.answer("Nothing to cancel.", reply_markup=MAIN_MENU_KB)


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
# Profile
# ---------------------------------------------------------------------------

@router.message(Command("profile"))
async def cmd_profile(message: Message) -> None:
    user = await db.get_or_create_user(message.from_user.id, message.from_user.username)
    await _show_profile(message.answer, user)


@router.callback_query(F.data == "my_profile")
async def cb_my_profile(cb: CallbackQuery) -> None:
    await cb.answer()
    user = await db.get_or_create_user(cb.from_user.id, cb.from_user.username)
    await _show_profile(cb.message.edit_text, user)


async def _show_profile(edit_or_send, user: dict) -> None:
    alias      = user.get("n", "—")
    gender     = _gender_label(user)
    tags       = " ".join(f"#{t}" for t in user.get("tags", [])) or "No tags set"
    karma      = user.get("karma_points", 0)
    streak     = user.get("streak", 0)
    streak_txt = f"{streak} 🔥" if streak > 0 else "—"
    text = (
        f"👤 <b>Your Profile</b>\n\n"
        f"🏷️ Alias:   <b>{alias}</b>\n"
        f"⚥ Gender:  {gender}\n"
        f"🎯 Tags:    {tags}\n"
        f"⭐ Karma:   <b>{karma}</b>\n"
        f"🔥 Streak:  <b>{streak_txt}</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Change Alias",  callback_data="prof_alias")],
        [InlineKeyboardButton(text="🔄 Change Gender", callback_data="prof_gender")],
        [InlineKeyboardButton(text="🏷️ Update Tags",   callback_data="my_tags")],
        [InlineKeyboardButton(text="🏠 Main Menu",     callback_data="prof_back")],
    ])
    await edit_or_send(text, parse_mode=ParseMode.HTML, reply_markup=kb)


@router.callback_query(F.data == "prof_back")
async def cb_prof_back(cb: CallbackQuery) -> None:
    await cb.answer()
    user = await db.get_user(cb.from_user.id)
    streak_line = _streak_line(user) if user else ""
    alias = user["n"] if user else "—"
    g = _gender_label(user) if user else ""
    await cb.message.edit_text(
        f"👋 <b>{alias}</b> {g}{streak_line}\n\nReady to chat?",
        parse_mode=ParseMode.HTML, reply_markup=MAIN_MENU_KB)


@router.callback_query(F.data == "prof_alias")
async def cb_prof_alias(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.set_state(ProfileStates.changing_alias)
    await state.update_data(prompt_msg_id=cb.message.message_id)
    await cb.message.edit_text(
        "✏️ Send your <b>new alias</b> (any text).\n\nSend /cancel to go back.",
        parse_mode=ParseMode.HTML)


@router.message(ProfileStates.changing_alias)
async def handle_profile_alias(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass
    if not message.text or not message.text.strip():
        if prompt_msg_id:
            await message.bot.edit_message_text(
                chat_id=message.chat.id, message_id=prompt_msg_id,
                text="⚠️ Please send a non-empty alias.\n\nSend /cancel to go back.",
                parse_mode=ParseMode.HTML)
        return
    new_alias = message.text.strip()[:32]
    await db.set_alias(message.from_user.id, new_alias)
    user = await db.get_user(message.from_user.id)
    if prompt_msg_id:
        await _show_profile(
            lambda text, **kw: message.bot.edit_message_text(
                chat_id=message.chat.id, message_id=prompt_msg_id, text=text, **kw),
            user)
    else:
        await _show_profile(message.answer, user)


@router.callback_query(F.data == "prof_gender")
async def cb_prof_gender(cb: CallbackQuery) -> None:
    await cb.answer()
    profile_gender_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Male 👦",   callback_data="gender_m"),
            InlineKeyboardButton(text="Female 👧", callback_data="gender_f"),
        ],
        [InlineKeyboardButton(text="⬅️ Back to Profile", callback_data="go_profile")],
    ])
    await cb.message.edit_text("🔄 Select your new gender:", reply_markup=profile_gender_kb)


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "my_tags")
async def cb_my_tags(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    user = await db.get_user(cb.from_user.id)
    current = " ".join(f"#{t}" for t in user.get("tags", [])) if user and user.get("tags") else "none"
    await state.set_state(UserStates.entering_tags)
    await state.update_data(prompt_msg_id=cb.message.message_id)
    await cb.message.edit_text(
        f"🏷️ <b>Current tags:</b> {current}\n\n"
        "Send up to <b>3 hashtags</b>:  <code>#gaming #movies #kpop</code>\n\n"
        "Send /cancel to go back.",
        parse_mode=ParseMode.HTML)


@router.message(UserStates.entering_tags)
async def handle_tags_input(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass

    async def _edit_prompt(text: str, **kw):
        if prompt_msg_id:
            await message.bot.edit_message_text(
                chat_id=message.chat.id, message_id=prompt_msg_id, text=text, **kw)
        else:
            await message.answer(text, **kw)

    if not message.text:
        await _edit_prompt("⚠️ Please send hashtags as text.\n\nSend /cancel to go back.")
        return
    raw = [t.lstrip("#").lower().strip()
           for t in message.text.split() if t.startswith("#") and len(t) > 1]
    if not raw:
        await _edit_prompt(
            "⚠️ No valid hashtags. Format: <code>#gaming #movies</code>\n\n"
            "Send /cancel to go back.", parse_mode=ParseMode.HTML)
        return
    tags = list(dict.fromkeys(raw))[:3]
    await db.set_tags(message.from_user.id, tags)
    user = await db.get_user(message.from_user.id)
    if prompt_msg_id:
        await _show_profile(
            lambda text, **kw: message.bot.edit_message_text(
                chat_id=message.chat.id, message_id=prompt_msg_id, text=text, **kw),
            user)
    else:
        await message.answer(
            f"✅ Tags saved: <b>{' '.join('#' + t for t in tags)}</b>",
            parse_mode=ParseMode.HTML, reply_markup=MAIN_MENU_KB)


@router.message(Command("tags"))
async def cmd_tags(message: Message, state: FSMContext) -> None:
    user = await db.get_user(message.from_user.id)
    current = " ".join(f"#{t}" for t in user.get("tags", [])) if user and user.get("tags") else "none"
    await state.set_state(UserStates.entering_tags)
    await message.answer(
        f"🏷️ <b>Current tags:</b> {current}\n\n"
        "Send up to <b>3 hashtags</b>:  <code>#gaming #movies #kpop</code>",
        parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# Find Stranger — mode → target gender → queue
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "find_stranger")
async def cb_find_stranger(cb: CallbackQuery) -> None:
    await cb.answer()
    user = await db.get_or_create_user(cb.from_user.id, cb.from_user.username)
    active = await db.lift_expired_temp_ban(cb.from_user.id)
    if not active:
        await cb.message.edit_text("🚫 You are banned and cannot use this feature.",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                                       InlineKeyboardButton(text="⬅️ Back", callback_data="go_main_menu")
                                   ]]))
        return
    if user.get("p_id"):
        await cb.message.edit_text(
            "You are already in a chat. Use <b>🔚 Stop Chat</b> first.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="⬅️ Back", callback_data="go_main_menu")
            ]]))
        return
    if not user.get("g"):
        await cb.message.edit_text("Please set your gender first:", reply_markup=GENDER_SELECT_KB)
        return
    await cb.message.edit_text("Choose your chat mode:", reply_markup=_mode_select_kb())


@router.callback_query(F.data.in_({"ms_normal", "ms_adult"}))
async def cb_mode_select(cb: CallbackQuery) -> None:
    await cb.answer()
    mode = "normal" if cb.data == "ms_normal" else "adult"
    user = await db.get_user(cb.from_user.id)
    if not user:
        return
    tags = user.get("tags", [])
    tag_hint = (f"\n🏷️ Tags: {' '.join('#' + t for t in tags)}"
                if tags else "\n💡 Tip: /tags to find like-minded strangers!")
    label = "Normal Mode 🌐" if mode == "normal" else "18+ Adult Mode 🔥"
    await cb.message.edit_text(
        f"✅ <b>{label}</b> selected.{tag_hint}\n\nWho would you like to chat with?",
        parse_mode=ParseMode.HTML,
        reply_markup=_target_gender_kb(mode))


@router.callback_query(F.data.regexp(r"^find_(n|a)_(any|m|f)$"))
async def cb_find_with_mode(cb: CallbackQuery) -> None:
    await cb.answer()
    _, m_code, tg_code = cb.data.split("_")
    mode          = "normal" if m_code == "n" else "adult"
    target_gender = {"any": "any", "m": "M", "f": "F"}[tg_code]
    user = await db.get_or_create_user(cb.from_user.id, cb.from_user.username)
    if user.get("p_id"):
        await cb.message.edit_text(
            "You are already in a chat.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="⬅️ Back", callback_data="go_main_menu")
            ]]))
        return
    labels = {"any": "Anyone 🌐", "M": "Boys 👦", "F": "Girls 👧"}
    await cb.message.edit_text(
        f"🔍 Looking for: <b>{labels[target_gender]}</b>…",
        parse_mode=ParseMode.HTML)
    await _do_find(cb.bot, cb.from_user.id, target_gender, mode)


# ---------------------------------------------------------------------------
# Stop / Next / Report
# ---------------------------------------------------------------------------

@router.message(F.text == STOP_TEXT)
async def stop_chat(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    if not user:
        return
    if await db.is_in_queue(message.from_user.id):
        await db.leave_queue(message.from_user.id)
        # Clean up "Searching..." message
        asyncio.create_task(_delete_search_msg(message.bot, message.from_user.id))
        await db.clear_session(message.from_user.id)
        await message.answer("🔚 Search cancelled.", reply_markup=ReplyKeyboardRemove())
        await message.answer("Want to try again?", reply_markup=MAIN_MENU_KB)
        return
    partner_id = user.get("p_id")
    if not partner_id:
        await message.answer("You are not in a chat.", reply_markup=ReplyKeyboardRemove())
        await message.answer("Return to menu:", reply_markup=MAIN_MENU_KB)
        return
    await _disconnect_stranger(message.bot, message.from_user.id, partner_id,
                               notify_partner=True, send_rating=True)
    await message.answer("🔚 You left the chat.", reply_markup=ReplyKeyboardRemove())
    await message.answer(
        "How was the conversation?",
        reply_markup=_rating_kb(partner_id))
    await message.answer("Find someone new?", reply_markup=MAIN_MENU_KB)


@router.message(F.text == NEXT_TEXT)
async def next_stranger(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    if not user:
        return
    partner_id = user.get("p_id")
    if partner_id:
        await _disconnect_stranger(message.bot, message.from_user.id, partner_id,
                                   notify_partner=True, send_rating=False)
    else:
        # Was queuing — cancel and clean search msg
        await db.leave_queue(message.from_user.id)
        asyncio.create_task(_delete_search_msg(message.bot, message.from_user.id))
        await db.clear_session(message.from_user.id)
    await message.answer("🔍 Finding next stranger…")
    await _do_find(message.bot, message.from_user.id, "any")


@router.message(F.text == REPORT_TEXT)
async def report_stranger(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    if not user or not user.get("p_id"):
        await message.answer("You are not in a chat.")
        return
    partner_id = user["p_id"]
    await _disconnect_stranger(message.bot, message.from_user.id, partner_id,
                               notify_partner=False, send_rating=False)
    new_count = await db.add_report(message.from_user.id, partner_id)
    await message.answer(
        "🚨 <b>Report submitted.</b> You have been disconnected.",
        parse_mode=ParseMode.HTML, reply_markup=ReplyKeyboardRemove())
    if new_count >= db.AUTO_BAN_REPORT_THRESHOLD:
        try:
            await message.bot.send_message(
                partner_id, "🚫 Suspended 24h due to multiple reports.",
                reply_markup=ReplyKeyboardRemove())
        except Exception:
            pass
    else:
        try:
            await message.bot.send_message(partner_id,
                "⚠️ You have been reported and disconnected.",
                reply_markup=ReplyKeyboardRemove())
            await message.bot.send_message(partner_id, "Find someone new?",
                                           reply_markup=MAIN_MENU_KB)
        except Exception:
            pass
    await message.answer("Find someone new?", reply_markup=MAIN_MENU_KB)


# ---------------------------------------------------------------------------
# Gift system
# ---------------------------------------------------------------------------

@router.message(F.text == GIFT_TEXT)
async def gift_menu(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    if not user or not user.get("p_id"):
        await message.answer("You can only send gifts while in a chat. 💬")
        return
    await message.answer("🎁 <b>Choose a gift to send:</b>",
                         parse_mode=ParseMode.HTML, reply_markup=GIFT_KB)


@router.callback_query(F.data.regexp(r"^gft_\d+$"))
async def cb_send_gift(cb: CallbackQuery) -> None:
    await cb.answer()
    idx = int(cb.data.split("_")[1])
    if idx >= len(GIFTS):
        return
    emoji, name = GIFTS[idx]
    user = await db.get_user(cb.from_user.id)
    if not user or not user.get("p_id"):
        await cb.message.edit_text("❌ You are no longer in a chat.")
        return
    partner_id = user["p_id"]
    try:
        await cb.message.delete()
    except Exception:
        pass
    try:
        await cb.bot.send_message(
            cb.from_user.id,
            f"🎁 You sent <b>{emoji} {name}</b> to Stranger!",
            parse_mode=ParseMode.HTML)
        await cb.bot.send_message(
            partner_id,
            f"✨ Stranger sent you <b>{emoji} {name}</b>! ✨",
            parse_mode=ParseMode.HTML)
    except Exception as e:
        log.warning("Gift send failed: %s", e)


# ---------------------------------------------------------------------------
# Karma rating
# ---------------------------------------------------------------------------

@router.callback_query(F.data.regexp(r"^rate_(up|dn)_\d+$"))
async def cb_rate(cb: CallbackQuery) -> None:
    await cb.answer()
    parts      = cb.data.split("_")
    direction  = parts[1]
    partner_id = int(parts[2])
    rater_id   = cb.from_user.id

    if (rater_id, partner_id) in _rated:
        await cb.answer("You already rated this conversation.", show_alert=True)
        return
    _rated.add((rater_id, partner_id))

    if direction == "up":
        new_karma = await db.add_karma(partner_id)
        await cb.message.edit_text(
            f"👍 Rated! Your partner earned +1 karma (now <b>{new_karma} ⭐</b>).",
            parse_mode=ParseMode.HTML)
    else:
        await cb.message.edit_text("👎 Noted. Thanks for the feedback!")


# ---------------------------------------------------------------------------
# Main relay — filter excludes commands so they reach their own handlers
# ---------------------------------------------------------------------------

@router.message(F.chat.type == "private", ~F.text.regexp(r"^/"))
async def on_private_message(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    current_state = await state.get_state()
    if current_state in (
        UserStates.entering_tags.state,
        ProfileStates.changing_alias.state,
        AdminStates.waiting_search.state,
        AdminStates.waiting_broadcast.state,
        AdminStates.waiting_ban_id.state,
        AdminStates.waiting_unban_id.state,
        AdminStates.waiting_alias.state,
    ):
        return
    user = await db.get_user(message.from_user.id)
    if not user:
        return
    partner_id = user.get("p_id")
    if not partner_id:
        if message.text:
            await message.answer("You are not in a chat. Use the menu below.",
                                 reply_markup=MAIN_MENU_KB)
        return
    if _is_rate_limited(message.from_user.id):
        await message.answer("⚡ Slow down! Please wait a moment.")
        return
    check_text = message.text or message.caption or ""
    if check_text and _has_bad_words(check_text):
        await message.answer(
            "⚠️ Your message contains inappropriate content and was not sent.")
        return
    reply_to_partner_msg_id: int | None = None
    if message.reply_to_message:
        doc = await db.find_msg_by_copy(message.chat.id, message.reply_to_message.message_id)
        if doc:
            for (copy_chat, copy_msg) in doc["c"]:
                if copy_chat == partner_id:
                    reply_to_partner_msg_id = copy_msg
                    break
    adult_mode = user.get("chat_mode") == "adult"
    await _send_to_stranger(
        message.bot,
        sender_id=message.from_user.id,
        sender_msg_id=message.message_id,
        partner_id=partner_id,
        alias=user["n"],
        message=message,
        reply_to_partner_msg_id=reply_to_partner_msg_id,
        spoiler=adult_mode,
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
# Advanced Stats
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "adm_stats")
async def adm_stats(cb: CallbackQuery) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    await cb.answer("Loading…")
    total, banned, temp_banned, active_chat, waiting, total_match, total_rep, \
        new_today, match_today, rep_today = await asyncio.gather(
        db.count_users(),
        db.count_banned(),
        db.count_temp_banned(),
        db.count_active_chatters(),
        db.count_waiting(),
        db.count_matches_total(),
        db.count_reports_total(),
        db.count_new_users_today(),
        db.count_matches_today(),
        db.count_reports_today(),
    )
    perm_banned = banned - temp_banned
    text = (
        "📈 <b>Advanced System Statistics</b>\n\n"
        "━━━━━━ Users ━━━━━━\n"
        f"👤 Total:         <b>{total}</b>\n"
        f"🆕 New today:     <b>{new_today}</b>\n"
        f"🚫 Perm banned:   <b>{perm_banned}</b>\n"
        f"⏳ Temp banned:   <b>{temp_banned}</b>\n\n"
        "━━━━━━ Activity ━━━━━━\n"
        f"💬 In chat:       <b>{active_chat}</b>\n"
        f"🔍 In queue:      <b>{waiting}</b>\n\n"
        "━━━━━━ Matches ━━━━━━\n"
        f"🤝 All-time:      <b>{total_match}</b>\n"
        f"🤝 Today:         <b>{match_today}</b>\n\n"
        "━━━━━━ Reports ━━━━━━\n"
        f"🚨 All-time:      <b>{total_rep}</b>\n"
        f"🚨 Today:         <b>{rep_today}</b>"
    )
    await cb.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=BACK_KB)


# ---------------------------------------------------------------------------
# User Management
# ---------------------------------------------------------------------------

@router.callback_query(F.data.regexp(r"^ul_\d+$"))
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
    user_buttons = []
    for u in users:
        uname = f"@{u['u']}" if u.get("u") else "—"
        lines.append(
            f"• <b>{u['n']}</b> {_gender_label(u)} | <code>{u['_id']}</code> | {uname} | {_status_label(u)}")
        user_buttons.append([InlineKeyboardButton(
            text=f"{u['n']} {_gender_label(u)}", callback_data=f"uv_{u['_id']}")])
    pagination = _pagination_kb(page, total, db.USERS_PER_PAGE, "ul")
    await cb.message.edit_text(
        "\n".join(lines), parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=user_buttons + pagination.inline_keyboard))


@router.callback_query(F.data.regexp(r"^uv_\d+$"))
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
        parse_mode=ParseMode.HTML, reply_markup=_user_action_kb(user_id))


@router.callback_query(F.data.regexp(r"^ub_\d+$"))
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
                               parse_mode=ParseMode.HTML, reply_markup=_user_action_kb(user_id))


@router.callback_query(F.data.regexp(r"^uu_\d+$"))
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
                               parse_mode=ParseMode.HTML, reply_markup=_user_action_kb(user_id))


@router.callback_query(F.data.regexp(r"^ua_\d+$"))
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
        f"✏️ New alias for <b>{user['n']}</b> (<code>{user_id}</code>):",
        parse_mode=ParseMode.HTML)


@router.message(AdminStates.waiting_alias)
async def adm_do_alias(message: Message, state: FSMContext) -> None:
    if not is_admin_pm(message):
        return
    if not message.text or not message.text.strip():
        await message.answer("⚠️ Send a non-empty alias text.")
        return
    data = await state.get_data()
    target_id = data.get("target_id")
    await state.clear()
    if not target_id:
        await message.answer("❌ Session expired.", reply_markup=ADMIN_MENU_KB)
        return
    old_user = await db.get_user(target_id)
    new_alias = message.text.strip()
    await db.set_alias(target_id, new_alias)
    await message.answer(
        f"✅ <b>{old_user.get('n', '?')}</b> → <b>{new_alias}</b>",
        parse_mode=ParseMode.HTML, reply_markup=ADMIN_MENU_KB)


@router.callback_query(F.data.regexp(r"^ucr_\d+$"))
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
                               parse_mode=ParseMode.HTML, reply_markup=_user_action_kb(user_id))


# ---------------------------------------------------------------------------
# Search User
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "adm_search")
async def adm_search_prompt(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    await cb.answer()
    await state.set_state(AdminStates.waiting_search)
    await cb.message.answer(
        "🔎 Send a <b>User ID</b> (numeric) or <b>@username</b>:",
        parse_mode=ParseMode.HTML)


@router.message(AdminStates.waiting_search)
async def adm_do_search(message: Message, state: FSMContext) -> None:
    if not is_admin_pm(message):
        return
    if not message.text:
        await message.answer("⚠️ Send a user ID or username.")
        return
    await state.clear()
    results = await db.search_users(message.text.strip())
    if not results:
        await message.answer(f"❌ No users found for: <code>{message.text.strip()}</code>",
                             parse_mode=ParseMode.HTML, reply_markup=ADMIN_MENU_KB)
        return
    buttons = [
        [InlineKeyboardButton(
            text=f"{u['n']} {_gender_label(u)} | {u['_id']}",
            callback_data=f"uv_{u['_id']}")]
        for u in results
    ]
    buttons.append([InlineKeyboardButton(text="⬅️ Back to Admin Panel", callback_data="adm_back")])
    await message.answer(f"🔎 Found <b>{len(results)}</b> result(s):",
                         parse_mode=ParseMode.HTML,
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ---------------------------------------------------------------------------
# Reports Dashboard
# ---------------------------------------------------------------------------

@router.callback_query(F.data.regexp(r"^rp_\d+$"))
async def adm_reports_list(cb: CallbackQuery) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    await cb.answer()
    page = int(cb.data.split("_")[1])
    reports, total = await db.get_reports_paginated(page)
    if not reports:
        await cb.message.edit_text("🚨 <b>Reports Dashboard</b>\n\nNo reports. ✅",
                                   parse_mode=ParseMode.HTML, reply_markup=BACK_KB)
        return
    lines = [f"🚨 <b>Reports Dashboard</b> (Total: {total})\n"]
    rpt_buttons = []
    for r in reports:
        ts       = r["t"].strftime("%m-%d %H:%M") if r.get("t") else "—"
        rid      = str(r["_id"])
        reported = r.get("reported", 0)
        reporter = r.get("reporter", 0)
        lines.append(f"• <code>{reported}</code> ← by <code>{reporter}</code>  [{ts}]")
        rpt_buttons.append([
            InlineKeyboardButton(text="🗑️ Dismiss",           callback_data=f"rd_{rid}"),
            InlineKeyboardButton(text=f"🔨 Ban {reported}",   callback_data=f"rb_{reported}"),
        ])
    pagination = _pagination_kb(page, total, db.REPORTS_PER_PAGE, "rp")
    await cb.message.edit_text(
        "\n".join(lines), parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=rpt_buttons + pagination.inline_keyboard))


@router.callback_query(F.data.regexp(r"^rd_[a-f0-9]{24}$"))
async def adm_dismiss_report(cb: CallbackQuery) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    report_id = cb.data[3:]
    try:
        await db.dismiss_report(report_id)
        await cb.answer("🗑️ Dismissed.")
    except Exception as e:
        await cb.answer(f"Error: {e}", show_alert=True)
        return
    reports, total = await db.get_reports_paginated(0)
    if not reports:
        await cb.message.edit_text("🚨 <b>Reports Dashboard</b>\n\nNo reports. ✅",
                                   parse_mode=ParseMode.HTML, reply_markup=BACK_KB)
        return
    lines = [f"🚨 <b>Reports Dashboard</b> (Total: {total})\n"]
    rpt_buttons = []
    for r in reports:
        ts       = r["t"].strftime("%m-%d %H:%M") if r.get("t") else "—"
        rid      = str(r["_id"])
        reported = r.get("reported", 0)
        reporter = r.get("reporter", 0)
        lines.append(f"• <code>{reported}</code> ← by <code>{reporter}</code>  [{ts}]")
        rpt_buttons.append([
            InlineKeyboardButton(text="🗑️ Dismiss",         callback_data=f"rd_{rid}"),
            InlineKeyboardButton(text=f"🔨 Ban {reported}", callback_data=f"rb_{reported}"),
        ])
    pagination = _pagination_kb(0, total, db.REPORTS_PER_PAGE, "rp")
    await cb.message.edit_text(
        "\n".join(lines), parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=rpt_buttons + pagination.inline_keyboard))


@router.callback_query(F.data.regexp(r"^rb_\d+$"))
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
# Broadcast / Ban / Unban
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "adm_broadcast")
async def adm_broadcast_prompt(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    await cb.answer()
    await state.set_state(AdminStates.waiting_broadcast)
    await cb.message.edit_text(
        "📢 <b>Broadcast</b>\n\nSend the message (text or photo).",
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
                await message.bot.send_photo(uid, message.photo[-1].file_id,
                                             caption=f"📢 {cap}")
            elif message.text:
                await message.bot.send_message(
                    uid, f"📢 <b>Broadcast:</b>\n\n{message.text}",
                    parse_mode=ParseMode.HTML)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await message.answer(f"✅ Done.\n✔️ Sent: {sent} | ❌ Failed: {failed}",
                         reply_markup=ADMIN_MENU_KB)


@router.callback_query(F.data == "adm_ban")
async def adm_ban_prompt(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    await cb.answer()
    await state.set_state(AdminStates.waiting_ban_id)
    await cb.message.edit_text(
        "🔨 <b>Ban User</b>\n\nSend the Telegram <b>User ID</b>.",
        parse_mode=ParseMode.HTML, reply_markup=BACK_KB)


@router.message(AdminStates.waiting_ban_id)
async def adm_do_ban(message: Message, state: FSMContext) -> None:
    if not is_admin_pm(message):
        return
    if not message.text or not message.text.strip().lstrip("-").isdigit():
        await message.answer("⚠️ Send a valid numeric User ID.")
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
        await message.bot.send_message(target_id, "🚫 Permanently banned.",
                                       reply_markup=ReplyKeyboardRemove())
    except Exception:
        pass
    await message.answer(
        f"✅ <code>{target_id}</code> (<b>{target.get('n', '?')}</b>) banned.",
        parse_mode=ParseMode.HTML, reply_markup=ADMIN_MENU_KB)


@router.callback_query(F.data == "adm_unban")
async def adm_unban_prompt(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_pm_cb(cb):
        await cb.answer()
        return
    await cb.answer()
    await state.set_state(AdminStates.waiting_unban_id)
    await cb.message.edit_text(
        "🔓 <b>Unban User</b>\n\nSend the Telegram <b>User ID</b>.",
        parse_mode=ParseMode.HTML, reply_markup=BACK_KB)


@router.message(AdminStates.waiting_unban_id)
async def adm_do_unban(message: Message, state: FSMContext) -> None:
    if not is_admin_pm(message):
        return
    if not message.text or not message.text.strip().lstrip("-").isdigit():
        await message.answer("⚠️ Send a valid numeric User ID.")
        return
    target_id = int(message.text.strip())
    await state.clear()
    target = await db.get_user(target_id)
    if not target:
        await message.answer("❌ User not found.", reply_markup=ADMIN_MENU_KB)
        return
    await db.unban_user(target_id)
    await message.answer(
        f"✅ <code>{target_id}</code> (<b>{target.get('n', '?')}</b>) unbanned.",
        parse_mode=ParseMode.HTML, reply_markup=ADMIN_MENU_KB)


# ---------------------------------------------------------------------------
# Dummy web server
# ---------------------------------------------------------------------------

async def dummy_web_server() -> None:
    async def handle(_req: web.Request) -> web.Response:
        return web.Response(text="OK")
    app = web.Application()
    app.router.add_get("/", handle)
    app.router.add_get("/health", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", DUMMY_PORT).start()
    log.info("Health server on port %s", DUMMY_PORT)


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
    asyncio.create_task(_refresh_active_counts())
    log.info("Bot starting…")
    await dp.start_polling(
        bot,
        allowed_updates=["message", "callback_query", "message_reaction", "edited_message"],
    )


if __name__ == "__main__":
    asyncio.run(main())
