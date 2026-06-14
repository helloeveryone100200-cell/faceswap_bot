import asyncio
import logging
import os
import tempfile
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    PhotoSize,
    Video,
)

import database as db
import face_swap as fs
from config import ADMIN_ID, BOT_TOKEN

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

router = Router()


class SwapStates(StatesGroup):
    waiting_source = State()
    waiting_target = State()
    media_type = State()


def admin_only(func):
    import functools

    @functools.wraps(func)
    async def wrapper(message: Message, *args, **kwargs):
        if message.from_user and message.from_user.id != ADMIN_ID:
            await message.answer("⛔ **ဤ Command သည် Admin များအတွက်သာ ဖြစ်ပါသည်။**", parse_mode=ParseMode.MARKDOWN)
            return
        return await func(message, *args, **kwargs)

    return wrapper


def is_banned(user_doc: dict) -> bool:
    return user_doc.get("s", 1) == 0


async def build_welcome_keyboard(bot: Bot) -> InlineKeyboardMarkup:
    me = await bot.get_me()
    share_url = f"https://t.me/share/url?url=https://t.me/{me.username}&text=🤩%20AI%20Face%20Swap%20Bot%20ကို%20သုံးကြည့်ပါ%21"
    welcome_cfg = await db.get_welcome_settings()

    buttons = [[InlineKeyboardButton(text="👥 Share Bot", url=share_url)]]
    if welcome_cfg.get("btn_text") and welcome_cfg.get("btn_url"):
        buttons.append(
            [InlineKeyboardButton(text=welcome_cfg["btn_text"], url=welcome_cfg["btn_url"])]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot) -> None:
    user = message.from_user
    if not user:
        return
    user_doc = await db.get_or_create_user(user.id, user.username)
    if is_banned(user_doc):
        await message.answer("🚫 **သင်သည် ဤ Bot မှ Ban ကျထားပါသည်။**", parse_mode=ParseMode.MARKDOWN)
        return

    welcome_cfg = await db.get_welcome_settings()
    if welcome_cfg.get("text"):
        text = welcome_cfg["text"]
    else:
        text = (
            f"👋 မင်္ဂလာပါ **{user.first_name}**!\n\n"
            "🤖 **AI Face Swap Bot** မှ ကြိုဆိုပါသည်!\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "✨ **Bot Features:**\n\n"
            "📸 **Photo Face Swap** — ဓာတ်ပုံများကို AI ဖြင့် မျက်နှာ လဲလှယ်ပေးပါသည်\n"
            "🎬 **Video Face Swap** — Video များကိုလည်း AI ဖြင့် ပြောင်းလဲပေးပါသည်\n"
            "⚡ **Fast Processing** — မြန်ဆန်ပြီး တိကျသော ရလဒ်များ\n"
            "🔒 **Privacy First** — သင့်ဓာတ်ပုံများ ပြုပြင်ပြီးနောက် ချက်ချင်း ဖျက်ပစ်ပါသည်\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🚀 **သုံးနည်း (Step by Step):**\n\n"
            "1️⃣ `/swap` ကို ရိုက်ပါ သို့ ဓာတ်ပုံ/Video တစ်ပုံ ပို့ပါ\n"
            "2️⃣ **Source ဓာတ်ပုံ** (မျက်နှာ ယူမည့် ပုံ) ပို့ပါ\n"
            "3️⃣ **Target ဓာတ်ပုံ/Video** (မျက်နှာ ထည့်မည့် ပုံ/Video) ပို့ပါ\n"
            "4️⃣ AI က ချက်ချင်း ပြုပြင်ပေးပါမည်! 🎉\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📌 **Commands:**\n"
            "`/swap` — Face Swap စတင်ရန်\n"
            "`/cancel` — လုပ်ငန်းကို ဖျက်သိမ်းရန်\n"
            "`/help` — အကူအညီ ရယူရန်"
        )

    kb = await build_welcome_keyboard(bot)
    await message.answer(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = (
        "🆘 **အကူအညီ — Help Center**\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📸 **Photo Face Swap:**\n"
        "1. `/swap` ရိုက်ပါ\n"
        "2. Source ဓာတ်ပုံ (မျက်နှာ ယူမည့် ပုံ) ပို့ပါ\n"
        "3. Target ဓာတ်ပုံ (မျက်နှာ ထည့်မည့် ပုံ) ပို့ပါ\n\n"
        "🎬 **Video Face Swap:**\n"
        "1. `/swap` ရိုက်ပါ\n"
        "2. Source ဓာတ်ပုံ (မျက်နှာ ယူမည့် ပုံ) ပို့ပါ\n"
        "3. Target Video ပို့ပါ\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ **သတိပြုရမည့် အချက်များ:**\n"
        "• ဓာတ်ပုံများသည် **မျက်နှာ ပါရမည်**\n"
        "• Video သည် **30 မိနစ်** အောက် ဖြစ်ရမည်\n"
        "• Processing အတွင်း သည်းခံစောင့်ဆိုင်းပေးပါ\n\n"
        "❓ မေးမြန်းရန် — Admin ထံ ဆက်သွယ်ပါ"
    )
    await message.answer(text, parse_mode=ParseMode.MARKDOWN)


@router.message(Command("swap"))
async def cmd_swap(message: Message, state: FSMContext) -> None:
    user = message.from_user
    if not user:
        return
    user_doc = await db.get_or_create_user(user.id, user.username)
    if is_banned(user_doc):
        await message.answer("🚫 **သင်သည် Ban ကျထားပါသည်။**", parse_mode=ParseMode.MARKDOWN)
        return

    await state.set_state(SwapStates.waiting_source)
    await message.answer(
        "📸 **Step 1/2 — Source ဓာတ်ပုံ**\n\n"
        "မျက်နှာ **ယူမည့် ပုံ** ကို ပေးပို့ပါ။\n"
        "_(ဖျက်သိမ်းရန် /cancel ရိုက်ပါ)_",
        parse_mode=ParseMode.MARKDOWN,
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ **လုပ်ငန်းကို ဖျက်သိမ်းလိုက်ပါပြီ။**\n\nပြန်စရန် `/swap` ရိုက်ပါ။", parse_mode=ParseMode.MARKDOWN)


@router.message(SwapStates.waiting_source, F.photo)
async def receive_source_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    photo: PhotoSize = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    file_bytes = await bot.download_file(file.file_path)
    source_path = fs.save_temp_bytes(file_bytes.read(), ".jpg")

    await state.update_data(source_path=source_path, media_type="photo")
    await state.set_state(SwapStates.waiting_target)
    await message.answer(
        "✅ **Source ဓာတ်ပုံ လက်ခံပြီး!**\n\n"
        "📸 **Step 2/2 — Target ဓာတ်ပုံ သို့ Video**\n\n"
        "မျက်နှာ **ထည့်မည့် ဓာတ်ပုံ** သို့မဟုတ် **Video** ကို ပေးပို့ပါ။",
        parse_mode=ParseMode.MARKDOWN,
    )


@router.message(SwapStates.waiting_target, F.photo)
async def receive_target_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    source_path: str = data["source_path"]

    photo: PhotoSize = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    file_bytes = await bot.download_file(file.file_path)
    target_path = fs.save_temp_bytes(file_bytes.read(), ".jpg")

    processing_msg = await message.answer(
        "⚡ **AI စနစ်က သင့်ပုံကို ပြုပြင်နေပါပြီ...**\n\n"
        "🔄 ခေတ္တ သည်းခံစောင့်ဆိုင်းပေးပါ...\n"
        "_(ဤလုပ်ငန်းစဉ် ၁–၂ မိနစ် ကြာနိုင်ပါသည်)_",
        parse_mode=ParseMode.MARKDOWN,
    )
    await state.clear()

    try:
        output_path = await fs.swap_faces_photo(source_path, target_path)
        await processing_msg.delete()
        with open(output_path, "rb") as f:
            await message.answer_photo(
                f,
                caption=(
                    "✨ **Face Swap ပြီးပါပြီ!** 🎉\n\n"
                    "📸 ရလဒ် ဓာတ်ပုံ အောက်တွင် တွေ့နိုင်ပါသည်\n"
                    "ထပ်မံ လုပ်ဆောင်ရန် `/swap` ရိုက်ပါ"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        await db.increment_counter(message.from_user.id, "p_c")
    except Exception as e:
        logger.error("Photo swap error: %s", e)
        await processing_msg.edit_text(
            "❌ **အမှားတစ်ခု ဖြစ်ပွားပါသည်**\n\n"
            "ဓာတ်ပုံများတွင် မျက်နှာ ရှိမရှိ စစ်ဆေးပြီး ထပ်စမ်းကြည့်ပါ။\n"
            "ပြဿနာ ဆက်ဖြစ်နေပါက Admin ထံ ဆက်သွယ်ပါ။",
            parse_mode=ParseMode.MARKDOWN,
        )
    finally:
        for p in [source_path, target_path]:
            try:
                os.remove(p)
            except OSError:
                pass


@router.message(SwapStates.waiting_target, F.video)
async def receive_target_video(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    source_path: str = data["source_path"]

    video: Video = message.video
    file = await bot.get_file(video.file_id)
    file_bytes = await bot.download_file(file.file_path)
    target_path = fs.save_temp_bytes(file_bytes.read(), ".mp4")

    processing_msg = await message.answer(
        "🎬 **AI စနစ်က သင့် Video ကို ပြုပြင်နေပါပြီ...**\n\n"
        "🔄 ခေတ္တ သည်းခံစောင့်ဆိုင်းပေးပါ...\n"
        "_(Video ပြုပြင်ချိန် ပိုကြာနိုင်ပါသည် — ၃–၁၀ မိနစ်)_",
        parse_mode=ParseMode.MARKDOWN,
    )
    await state.clear()

    try:
        output_path = await fs.swap_faces_video(source_path, target_path)
        await processing_msg.delete()
        with open(output_path, "rb") as f:
            await message.answer_video(
                f,
                caption=(
                    "✨ **Video Face Swap ပြီးပါပြီ!** 🎉\n\n"
                    "🎬 ရလဒ် Video အောက်တွင် တွေ့နိုင်ပါသည်\n"
                    "ထပ်မံ လုပ်ဆောင်ရန် `/swap` ရိုက်ပါ"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        await db.increment_counter(message.from_user.id, "v_c")
    except Exception as e:
        logger.error("Video swap error: %s", e)
        await processing_msg.edit_text(
            "❌ **Video ပြုပြင်ရာတွင် အမှားဖြစ်ပွားပါသည်**\n\n"
            "Video ရှည်လျားလွန်းနိုင်သည် သို့ မျက်နှာ မပေါ်နိုင်ပါ။\n"
            "ထပ်စမ်းကြည့်ပါ သို့ Admin ထံ ဆက်သွယ်ပါ။",
            parse_mode=ParseMode.MARKDOWN,
        )
    finally:
        for p in [source_path, target_path]:
            try:
                os.remove(p)
            except OSError:
                pass


@router.message(Command("admin"))
@admin_only
async def cmd_admin(message: Message) -> None:
    stats = await db.get_stats()
    text = (
        "🛡️ **Admin Control Panel**\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📊 **Bot Statistics:**\n"
        f"👥 စုစုပေါင်း Users: `{stats.get('total_users', 0)}`\n"
        f"✅ Active Users: `{stats.get('active_users', 0)}`\n"
        f"📸 Photo Swaps: `{stats.get('total_photos', 0)}`\n"
        f"🎬 Video Swaps: `{stats.get('total_videos', 0)}`\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔧 **Admin Commands:**\n\n"
        "`/userlist` — User စာရင်း ကြည့်ရန်\n"
        "`/broadcast all <msg>` — အားလုံးထံ ကြော်ငြာရန်\n"
        "`/broadcast <user_id> <msg>` — တစ်ဦးထံ ကြော်ငြာရန်\n"
        "`/ban <user_id>` — User ကို Ban ပိတ်ရန်\n"
        "`/unban <user_id>` — User ကို Unban ဖြေရှင်းရန်\n"
        "`/setwelcome <text> | <btn_text> | <btn_url>` — Welcome Message ပြောင်းရန်"
    )
    await message.answer(text, parse_mode=ParseMode.MARKDOWN)


@router.message(Command("userlist"))
@admin_only
async def cmd_userlist(message: Message) -> None:
    users = await db.get_all_users()
    if not users:
        await message.answer("📭 **User တစ်ဦးမှ မရှိသေးပါ။**", parse_mode=ParseMode.MARKDOWN)
        return

    lines = ["👥 **Registered Users List**\n━━━━━━━━━━━━━━━━━━━━\n"]
    for i, u in enumerate(users, 1):
        joined = u.get("j")
        date_str = joined.strftime("%Y-%m-%d") if isinstance(joined, datetime) else "N/A"
        username = f"@{u['u']}" if u.get("u") else "_(no username)_"
        status = "✅" if u.get("s", 1) == 1 else "🚫"
        lines.append(f"{i}. {status} `{u['_id']}` — {username} — {date_str}")

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
            "`/broadcast all <message>` — အားလုံးထံ\n"
            "`/broadcast <user_id> <message>` — တစ်ဦးထံ",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    target = args[1]
    msg_text = args[2]

    if target.lower() == "all":
        user_ids = await db.get_active_user_ids()
        success = 0
        failed = 0
        status_msg = await message.answer(
            f"📡 **Broadcasting to {len(user_ids)} users...**", parse_mode=ParseMode.MARKDOWN
        )
        for uid in user_ids:
            try:
                await bot.send_message(uid, msg_text, parse_mode=ParseMode.MARKDOWN)
                success += 1
            except Exception:
                failed += 1
        await status_msg.edit_text(
            f"✅ **Broadcast ပြီးပါပြီ!**\n\n"
            f"📤 ပို့ပြီး: `{success}`\n"
            f"❌ မရောက်သည်: `{failed}`",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        try:
            uid = int(target)
            await bot.send_message(uid, msg_text, parse_mode=ParseMode.MARKDOWN)
            await message.answer(f"✅ `{uid}` ထံ **ပို့ပြီးပါပြီ!**", parse_mode=ParseMode.MARKDOWN)
        except ValueError:
            await message.answer("❌ User ID မှားနေပါသည်။", parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await message.answer(f"❌ **ကြော်ငြာ မပို့နိုင်ပါ:**\n`{e}`", parse_mode=ParseMode.MARKDOWN)


@router.message(Command("ban"))
@admin_only
async def cmd_ban(message: Message) -> None:
    args = message.text.split() if message.text else []
    if len(args) < 2:
        await message.answer("⚠️ **အသုံးပြုပုံ:** `/ban <user_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        uid = int(args[1])
        await db.ban_user(uid)
        await message.answer(f"🚫 User `{uid}` ကို **Ban ပိတ်ပြီးပါပြီ**။", parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await message.answer("❌ User ID မှားနေပါသည်။", parse_mode=ParseMode.MARKDOWN)


@router.message(Command("unban"))
@admin_only
async def cmd_unban(message: Message) -> None:
    args = message.text.split() if message.text else []
    if len(args) < 2:
        await message.answer("⚠️ **အသုံးပြုပုံ:** `/unban <user_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        uid = int(args[1])
        await db.unban_user(uid)
        await message.answer(f"✅ User `{uid}` ကို **Unban ဖြေပြီးပါပြီ**။", parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await message.answer("❌ User ID မှားနေပါသည်။", parse_mode=ParseMode.MARKDOWN)


@router.message(Command("setwelcome"))
@admin_only
async def cmd_setwelcome(message: Message) -> None:
    if not message.text:
        return
    raw = message.text.partition(" ")[2]
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 3:
        await message.answer(
            "⚠️ **အသုံးပြုပုံ:**\n"
            "`/setwelcome <text> | <btn_text> | <btn_url>`\n\n"
            "**ဥပမာ:**\n"
            "`/setwelcome မင်္ဂလာပါ! ကြိုဆိုပါသည် | 🌐 Website | https://example.com`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    text, btn_text, btn_url = parts[0], parts[1], parts[2]
    await db.set_welcome_settings(text, btn_text, btn_url)
    await message.answer(
        "✅ **Welcome Message ပြောင်းပြီးပါပြီ!**\n\n"
        "User တစ်ဦး `/start` နှိပ်သောအခါ အသစ် Message တွေ့ရပါမည်။",
        parse_mode=ParseMode.MARKDOWN,
    )


@router.message(F.photo & ~F.caption.startswith("/"))
async def handle_unsolicited_photo(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current is not None:
        return
    await message.answer(
        "📸 Face Swap စတင်ရန် `/swap` ကို ရိုက်ပါ။",
        parse_mode=ParseMode.MARKDOWN,
    )


@router.message(F.video & ~F.caption.startswith("/"))
async def handle_unsolicited_video(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current is not None:
        return
    await message.answer(
        "🎬 Face Swap စတင်ရန် `/swap` ကို ရိုက်ပါ။",
        parse_mode=ParseMode.MARKDOWN,
    )


async def main() -> None:
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is not set!")
    if not ADMIN_ID:
        raise ValueError("ADMIN_ID environment variable is not set!")

    await db.setup_indexes()
    logger.info("Database indexes created.")

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    logger.info("Bot is starting...")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())
