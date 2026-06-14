import asyncio
import logging
import os
from datetime import datetime

from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
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
from config import ADMIN_ID, BOT_TOKEN

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

router = Router()


# ─── Keyboards ───────────────────────────────────────────────────────────────

def kb_main() -> InlineKeyboardMarkup:
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


# ─── Helpers ─────────────────────────────────────────────────────────────────

def admin_only(func):
    import functools

    @functools.wraps(func)
    async def wrapper(message: Message, *args, **kwargs):
        if not message.from_user or message.from_user.id != ADMIN_ID:
            await message.answer("⛔ **ဤ Command သည် Admin များအတွက်သာ ဖြစ်သည်။**", parse_mode=ParseMode.MARKDOWN)
            return
        return await func(message, *args, **kwargs)

    return wrapper


async def broadcast_to_room(
    bot: Bot,
    room_id: str,
    sender_id: int,
    alias: str,
    message: Message,
) -> None:
    members = await db.get_room_members(room_id, exclude_user_id=sender_id)
    kb = kb_in_room(room_id)

    for uid in members:
        try:
            if message.text:
                await bot.send_message(
                    uid,
                    f"[**{alias}**]: {message.text}",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=kb,
                )
            elif message.photo:
                photo: PhotoSize = message.photo[-1]
                caption = f"[**{alias}**]" + (f": {message.caption}" if message.caption else "")
                await bot.send_photo(uid, photo.file_id, caption=caption, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
            elif message.voice:
                voice: Voice = message.voice
                await bot.send_voice(uid, voice.file_id, caption=f"[**{alias}**] 🎤", parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
            elif message.animation:
                anim: Animation = message.animation
                await bot.send_animation(uid, anim.file_id, caption=f"[**{alias}**] 🎞️", parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
            elif message.sticker:
                stk: Sticker = message.sticker
                await bot.send_sticker(uid, stk.file_id)
                await bot.send_message(uid, f"[**{alias}**] 🃏 Sticker ပို့သည်", parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        except Exception as e:
            logger.warning("Failed to send to %d: %s", uid, e)


# ─── /start ──────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    user = message.from_user
    if not user:
        return
    user_doc = await db.get_or_create_user(user.id, user.username)

    if user_doc.get("s", 1) == 0:
        await message.answer("🚫 **သင်သည် ဤ Bot မှ Ban ကျထားပါသည်။**", parse_mode=ParseMode.MARKDOWN)
        return

    alias = user_doc.get("n", "အမည်မသိ")

    if user_doc.get("r_id"):
        room_id = user_doc["r_id"]
        count = await db.get_room_member_count(room_id)
        await message.answer(
            f"💬 **သင် စကားဝိုင်းထဲ ရှိနေပါပြီ!**\n\n"
            f"🏷️ သင့်အမည်: **{alias}**\n"
            f"🚪 Room: `{room_id}` · 👥 {count} ဦး\n\n"
            f"_မက်ဆေ့ပို့ရန် ရိုက်ထည့်ပါ_",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_in_room(room_id),
        )
        return

    await message.answer(
        f"👋 မင်္ဂလာပါ **{user.first_name}**!\n\n"
        "🌐 **Anonymous Group Chat Bot** မှ ကြိုဆိုပါသည်!\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "✨ **ဤ Bot အကြောင်း:**\n\n"
        "🎭 သင်၏ အမည်ကို ဖျောက်ပြီး တစ်ကမ္ဘာလုံးနှင့် အမည်ဝှက်ကာ စကားပြောနိုင်သည်\n"
        "💬 Photo, Voice, GIF, Sticker များ ပို့နိုင်သည်\n"
        "❤️ ချစ်သူများနှင့် Mutual Reveal ဖြင့် Identity ချိတ်ဆက်နိုင်သည်\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📜 **စည်းကမ်းများ:**\n\n"
        "🚫 Spam မပို့ရ\n"
        "🚫 ကောင်းမွန်မှုမဲ့သော စကားများ မသုံးရ\n"
        "✅ ပျော်ရွှင်ပြီး ရိုးသားစွာ ဆက်ဆံပါ\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "👇 ဝင်ရောက်ရန် အောက်ပါ ခလုတ်ကို နှိပ်ပါ",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_main(),
    )


# ─── Join room ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "join_room")
async def cb_join_room(callback: CallbackQuery, bot: Bot) -> None:
    user = callback.from_user
    user_doc = await db.get_or_create_user(user.id, user.username)

    if user_doc.get("s", 1) == 0:
        await callback.answer("🚫 Ban ကျထားသောကြောင့် ဝင်ခွင့်မရပါ။", show_alert=True)
        return

    if user_doc.get("r_id"):
        await callback.answer("သင် ဝင်ရောက်နေပြီဖြစ်သည်!", show_alert=True)
        return

    room_id = await db.join_room(user.id)
    alias = user_doc.get("n", "အမည်မသိ")
    count = await db.get_room_member_count(room_id)

    await callback.message.edit_text(
        f"🎉 **စကားဝိုင်းသို့ ဝင်ရောက်ပြီးပါပြီ!**\n\n"
        f"🏷️ သင့်အမည်: **{alias}**\n"
        f"🚪 Room: `{room_id}` · 👥 {count} ဦး\n\n"
        f"💬 _မက်ဆေ့ရိုက်ပြီး Entre နှိပ်ပါ — Room ထဲ လူများထံ ရောက်မည်!_\n\n"
        f"❤️ ချစ်သူချိတ်ရန် **'အကောင့်ချင်းချိတ်ရန်'** နှိပ်ပါ",
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

    await callback.answer()


# ─── Leave room ──────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("leave:"))
async def cb_leave_room(callback: CallbackQuery, bot: Bot) -> None:
    user = callback.from_user
    user_doc = await db.get_user(user.id)
    if not user_doc or not user_doc.get("r_id"):
        await callback.answer("သင် Room ထဲ မရှိပါ။", show_alert=True)
        return

    room_id = user_doc["r_id"]
    alias = user_doc.get("n", "အမည်မသိ")
    await db.leave_room(user.id)

    await callback.message.edit_text(
        "👋 **စကားဝိုင်းမှ ထွက်ပြီးပါပြီ**\n\n"
        "ပြန်ဝင်လိုပါက `/start` ရိုက်ပါ",
        parse_mode=ParseMode.MARKDOWN,
    )

    members = await db.get_room_members(room_id, exclude_user_id=user.id)
    count = await db.get_room_member_count(room_id)
    for uid in members:
        try:
            await bot.send_message(
                uid,
                f"🚪 **{alias}** — Room မှ ထွက်သွားပါပြီ\n_(ယခု {count} ဦး ကျန်သည်)_",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb_in_room(room_id),
            )
        except Exception:
            pass

    await callback.answer()


# ─── Mutual Reveal ───────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("reveal:"))
async def cb_reveal(callback: CallbackQuery, bot: Bot) -> None:
    user = callback.from_user
    room_id = callback.data.split(":", 1)[1]

    user_doc = await db.get_user(user.id)
    if not user_doc or user_doc.get("r_id") != room_id:
        await callback.answer("သင် ဤ Room ထဲ မရှိတော့ပါ။", show_alert=True)
        return

    alias = user_doc.get("n", "အမည်မသိ")
    my_username = f"@{user.username}" if user.username else f"ID: `{user.id}`"

    matched_uid = await db.request_reveal(user.id, room_id)

    if matched_uid:
        matched_doc = await db.get_user(matched_uid)
        matched_alias = matched_doc.get("n", "အမည်မသိ") if matched_doc else "အမည်မသိ"
        matched_user = await bot.get_chat(matched_uid)
        matched_username = (
            f"@{matched_user.username}" if matched_user.username else f"ID: `{matched_uid}`"
        )

        celebration = (
            "🎊 **MATCH ဖြစ်သွားပါပြီ!** 🎉\n\n"
            "💫 သင်တို့နှစ်ဦးစလုံး ချိတ်ဆက်လိုကြောင်း ဆန္ဒပြပြီးပါပြီ!\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
        )
        await bot.send_message(
            user.id,
            celebration
            + f"🏷️ သင်ချိတ်ဆက်ရမည့်သူ: **{matched_alias}**\n"
            + f"📲 Telegram: {matched_username}\n\n"
            + "💌 Private ဆက်သွယ်ပြီး တွေ့ဆုံနိုင်ပါပြီ! ✨",
            parse_mode=ParseMode.MARKDOWN,
        )
        await bot.send_message(
            matched_uid,
            celebration
            + f"🏷️ သင်ချိတ်ဆက်ရမည့်သူ: **{alias}**\n"
            + f"📲 Telegram: {my_username}\n\n"
            + "💌 Private ဆက်သွယ်ပြီး တွေ့ဆုံနိုင်ပါပြီ! ✨",
            parse_mode=ParseMode.MARKDOWN,
        )
        await callback.answer("🎉 Match ဖြစ်သွားပါပြီ!", show_alert=True)
        return

    await callback.answer("❤️ Request ပို့လိုက်ပါပြီ! တစ်ဦးဦး ပြန်ဆက်ကြည့်မည်...", show_alert=True)

    members = await db.get_room_members(room_id, exclude_user_id=user.id)
    for uid in members:
        try:
            await bot.send_message(
                uid,
                "💌 **Room ထဲမှ တစ်ဦးက သင်နှင့် ချိတ်ဆက်လိုပါသည်!**\n\n"
                "❤️ **'အကောင့်ချင်းချိတ်ရန်'** ကို နှိပ်ပါ — Match ဖြစ်ပါက နှစ်ဦးစလုံး\n"
                "Telegram Username ကိုထုတ်ပြပေးပါမည်! 🎊",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb_in_room(room_id),
            )
        except Exception:
            pass


# ─── Message forwarding ──────────────────────────────────────────────────────

@router.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message, bot: Bot) -> None:
    user = message.from_user
    if not user:
        return
    user_doc = await db.get_user(user.id)
    if not user_doc or not user_doc.get("r_id"):
        await message.answer(
            "💬 Room ထဲ မရှိသေးပါ။ `/start` ရိုက်ပြီး ဝင်ရောက်ပါ။",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    if user_doc.get("s", 1) == 0:
        return
    await broadcast_to_room(bot, user_doc["r_id"], user.id, user_doc["n"], message)


@router.message(F.photo)
async def handle_photo(message: Message, bot: Bot) -> None:
    user = message.from_user
    if not user:
        return
    user_doc = await db.get_user(user.id)
    if not user_doc or not user_doc.get("r_id") or user_doc.get("s", 1) == 0:
        return
    await broadcast_to_room(bot, user_doc["r_id"], user.id, user_doc["n"], message)


@router.message(F.voice)
async def handle_voice(message: Message, bot: Bot) -> None:
    user = message.from_user
    if not user:
        return
    user_doc = await db.get_user(user.id)
    if not user_doc or not user_doc.get("r_id") or user_doc.get("s", 1) == 0:
        return
    await broadcast_to_room(bot, user_doc["r_id"], user.id, user_doc["n"], message)


@router.message(F.animation)
async def handle_animation(message: Message, bot: Bot) -> None:
    user = message.from_user
    if not user:
        return
    user_doc = await db.get_user(user.id)
    if not user_doc or not user_doc.get("r_id") or user_doc.get("s", 1) == 0:
        return
    await broadcast_to_room(bot, user_doc["r_id"], user.id, user_doc["n"], message)


@router.message(F.sticker)
async def handle_sticker(message: Message, bot: Bot) -> None:
    user = message.from_user
    if not user:
        return
    user_doc = await db.get_user(user.id)
    if not user_doc or not user_doc.get("r_id") or user_doc.get("s", 1) == 0:
        return
    await broadcast_to_room(bot, user_doc["r_id"], user.id, user_doc["n"], message)


# ─── Admin commands ───────────────────────────────────────────────────────────

@router.message(Command("admin"))
@admin_only
async def cmd_admin(message: Message) -> None:
    stats = await db.get_stats()
    await message.answer(
        "🛡️ **Admin Control Panel**\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📊 **Bot Statistics:**\n"
        f"👥 Total Users: `{stats['total_users']}`\n"
        f"✅ Active Users: `{stats['active_users']}`\n"
        f"💬 In Chat Now: `{stats['in_chat']}`\n"
        f"🚪 Total Rooms: `{stats['total_rooms']}`\n"
        f"🔥 Active Rooms: `{stats['active_rooms']}`\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔧 **Commands:**\n"
        "`/userlist` — User စာရင်း\n"
        "`/broadcast all <msg>` — အားလုံးထံ ကြော်ငြာ\n"
        "`/broadcast <uid> <msg>` — တစ်ဦးထံ ကြော်ငြာ\n"
        "`/ban <uid>` — Ban ပိတ်ရန်\n"
        "`/unban <uid>` — Ban ဖြေရန်",
        parse_mode=ParseMode.MARKDOWN,
    )


@router.message(Command("userlist"))
@admin_only
async def cmd_userlist(message: Message) -> None:
    users = await db.get_all_users()
    if not users:
        await message.answer("📭 **User မရှိသေးပါ**", parse_mode=ParseMode.MARKDOWN)
        return

    lines = ["👥 **Registered Users**\n━━━━━━━━━━━━━━━━━━━━\n"]
    for i, u in enumerate(users, 1):
        joined = u.get("j")
        date_str = joined.strftime("%Y-%m-%d") if isinstance(joined, datetime) else "N/A"
        uname = f"@{u['u']}" if u.get("u") else "_no username_"
        status = "✅" if u.get("s", 1) == 1 else "🚫"
        alias = u.get("n", "?")
        lines.append(f"{i}. {status} `{u['_id']}` {uname} — {alias} — {date_str}")

    chunk: list[str] = []
    char_count = 0
    for line in lines:
        if char_count + len(line) > 3800:
            await message.answer("\n".join(chunk), parse_mode=ParseMode.MARKDOWN)
            chunk = []
            char_count = 0
        chunk.append(line)
        char_count += len(line)
    if chunk:
        await message.answer("\n".join(chunk), parse_mode=ParseMode.MARKDOWN)


@router.message(Command("broadcast"))
@admin_only
async def cmd_broadcast(message: Message, bot: Bot) -> None:
    args = message.text.split(maxsplit=2) if message.text else []
    if len(args) < 3:
        await message.answer(
            "⚠️ **အသုံးပြုပုံ:**\n"
            "`/broadcast all <message>`\n"
            "`/broadcast <user_id> <message>`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    target, msg_text = args[1], args[2]

    if target.lower() == "all":
        user_ids = await db.get_active_user_ids()
        status = await message.answer(
            f"📡 **{len(user_ids)} ဦးထံ ကြော်ငြာနေသည်...**",
            parse_mode=ParseMode.MARKDOWN,
        )
        ok, fail = 0, 0
        for uid in user_ids:
            try:
                await bot.send_message(uid, msg_text, parse_mode=ParseMode.MARKDOWN)
                ok += 1
            except Exception:
                fail += 1
        await status.edit_text(
            f"✅ **Broadcast ပြီး!**\n📤 `{ok}` · ❌ `{fail}`",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        try:
            uid = int(target)
            await bot.send_message(uid, msg_text, parse_mode=ParseMode.MARKDOWN)
            await message.answer(f"✅ `{uid}` ထံ **ပို့ပြီး**", parse_mode=ParseMode.MARKDOWN)
        except ValueError:
            await message.answer("❌ User ID မှားနေသည်", parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await message.answer(f"❌ **မပို့နိုင်:**\n`{e}`", parse_mode=ParseMode.MARKDOWN)


@router.message(Command("ban"))
@admin_only
async def cmd_ban(message: Message) -> None:
    args = message.text.split() if message.text else []
    if len(args) < 2:
        await message.answer("⚠️ `/ban <user_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        uid = int(args[1])
        await db.ban_user(uid)
        await message.answer(f"🚫 `{uid}` **Ban ပြီး**", parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await message.answer("❌ User ID မှားနေသည်", parse_mode=ParseMode.MARKDOWN)


@router.message(Command("unban"))
@admin_only
async def cmd_unban(message: Message) -> None:
    args = message.text.split() if message.text else []
    if len(args) < 2:
        await message.answer("⚠️ `/unban <user_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        uid = int(args[1])
        await db.unban_user(uid)
        await message.answer(f"✅ `{uid}` **Unban ပြီး**", parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await message.answer("❌ User ID မှားနေသည်", parse_mode=ParseMode.MARKDOWN)


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
    if not ADMIN_ID:
        raise ValueError("ADMIN_ID is not set!")

    await db.setup_indexes()
    logger.info("Indexes created.")

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    logger.info("Anonymous Chat Bot starting...")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


async def main() -> None:
    await asyncio.gather(run_web_server(), run_bot())


if __name__ == "__main__":
    asyncio.run(main())
