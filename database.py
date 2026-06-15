import random
from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorClient

from config import ALIASES, MONGO_URL

_client: AsyncIOMotorClient | None = None

MSG_TTL_HOURS = 24
TEMP_BAN_HOURS = 24
AUTO_BAN_REPORT_THRESHOLD = 3


def get_db():
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(MONGO_URL)
    return _client["anon_chat"]


# ---------------------------------------------------------------------------
# Indexes — call once at startup
# ---------------------------------------------------------------------------

async def ensure_indexes() -> None:
    db = get_db()
    await db["msg"].create_index("exp", expireAfterSeconds=0)
    await db["q"].create_index("t")


# ---------------------------------------------------------------------------
# User helpers
# ---------------------------------------------------------------------------

async def get_user(user_id: int) -> dict | None:
    return await get_db()["u"].find_one({"_id": user_id})


async def get_or_create_user(user_id: int, username: str | None) -> dict:
    db = get_db()
    user = await db["u"].find_one({"_id": user_id})
    if user:
        return user

    used = await db["u"].distinct("n")
    available = [a for a in ALIASES if a not in used]
    alias = random.choice(available) if available else random.choice(ALIASES)

    user = {
        "_id": user_id,
        "u": username,
        "n": alias,
        "g": None,           # gender: "M" | "F" | None
        "tags": [],          # interests: up to 3 strings (without #)
        "p_id": None,        # stranger partner ID
        "s": 1,              # status: 1=active, 0=banned
        "ban_exp": None,     # temp ban expiry datetime
        "report_count": 0,   # number of reports received
        "j": datetime.now(timezone.utc),
    }
    await db["u"].insert_one(user)
    return user


async def set_gender(user_id: int, gender: str) -> None:
    """Set user gender: 'M' or 'F'."""
    await get_db()["u"].update_one({"_id": user_id}, {"$set": {"g": gender}})


async def set_tags(user_id: int, tags: list[str]) -> None:
    """Save up to 3 interest tags (without #) for the user."""
    await get_db()["u"].update_one({"_id": user_id}, {"$set": {"tags": tags[:3]}})


async def ban_user(user_id: int) -> None:
    await get_db()["u"].update_one({"_id": user_id}, {"$set": {"s": 0, "ban_exp": None}})


async def unban_user(user_id: int) -> None:
    await get_db()["u"].update_one(
        {"_id": user_id}, {"$set": {"s": 1, "ban_exp": None, "report_count": 0}}
    )


async def set_temp_ban(user_id: int) -> None:
    """Apply a 24-hour temporary ban."""
    exp = datetime.now(timezone.utc) + timedelta(hours=TEMP_BAN_HOURS)
    await get_db()["u"].update_one({"_id": user_id}, {"$set": {"s": 0, "ban_exp": exp}})


async def lift_expired_temp_ban(user_id: int) -> bool:
    """
    Check if the user's temp ban has expired and lift it automatically.
    Returns True if the user is now active (ban lifted or wasn't banned).
    """
    user = await get_db()["u"].find_one({"_id": user_id})
    if not user:
        return False
    if user["s"] == 1:
        return True
    ban_exp = user.get("ban_exp")
    if ban_exp and datetime.now(timezone.utc) >= ban_exp:
        await get_db()["u"].update_one(
            {"_id": user_id}, {"$set": {"s": 1, "ban_exp": None}}
        )
        return True
    return False


async def count_users() -> int:
    return await get_db()["u"].count_documents({})


async def count_banned() -> int:
    return await get_db()["u"].count_documents({"s": 0})


async def count_active_chatters() -> int:
    return await get_db()["u"].count_documents({"p_id": {"$ne": None}, "s": 1})


async def get_all_active_users() -> list[dict]:
    cursor = get_db()["u"].find({"s": 1})
    return await cursor.to_list(length=None)


# ---------------------------------------------------------------------------
# Report system
# ---------------------------------------------------------------------------

async def add_report(reporter_id: int, reported_id: int) -> int:
    """
    Log a report and increment the reported user's count.
    Returns the new total report count.
    If threshold reached, applies a 24-hour temp ban automatically.
    """
    db = get_db()

    now = datetime.now(timezone.utc)
    await db["reports"].insert_one({
        "reporter": reporter_id,
        "reported": reported_id,
        "t": now,
    })

    result = await db["u"].find_one_and_update(
        {"_id": reported_id},
        {"$inc": {"report_count": 1}},
        return_document=True,
    )

    new_count = result["report_count"] if result else 1

    if new_count >= AUTO_BAN_REPORT_THRESHOLD:
        await set_temp_ban(reported_id)

    return new_count


# ---------------------------------------------------------------------------
# Stranger queue helpers  (collection: 'q')
# ---------------------------------------------------------------------------

async def enter_queue(
    user_id: int,
    gender: str | None,
    target_gender: str,
    tags: list[str],
) -> None:
    await get_db()["q"].update_one(
        {"_id": user_id},
        {"$set": {
            "t": datetime.now(timezone.utc),
            "g": gender,
            "tg": target_gender,
            "tags": tags,
        }},
        upsert=True,
    )


async def leave_queue(user_id: int) -> None:
    await get_db()["q"].delete_one({"_id": user_id})


async def is_in_queue(user_id: int) -> bool:
    doc = await get_db()["q"].find_one({"_id": user_id})
    return doc is not None


async def get_queue_entry(user_id: int) -> dict | None:
    return await get_db()["q"].find_one({"_id": user_id})


async def find_and_match(
    user_id: int,
    user_gender: str | None,
    target_gender: str,
    user_tags: list[str],
    strict: bool = True,
) -> int | None:
    """
    Try to find and atomically match a partner from the queue.

    If strict=True:  require at least one shared tag (falls back to gender-only if no tags).
    If strict=False: match by gender only (no tag requirement).

    Gender compatibility rules:
      - My target_gender must match partner's gender (or target is 'any').
      - Partner's target_gender must match my gender (or partner's target is 'any').
    """
    db = get_db()

    def _gender_query() -> dict:
        q: dict = {"_id": {"$ne": user_id}}
        if target_gender != "any":
            q["g"] = target_gender
        if user_gender:
            q["$or"] = [{"tg": "any"}, {"tg": user_gender}]
        else:
            q["tg"] = "any"
        return q

    if strict and user_tags:
        tag_q = _gender_query()
        tag_q["tags"] = {"$in": user_tags}
        match = await db["q"].find_one_and_delete(tag_q)
        if match:
            return match["_id"]

    gender_q = _gender_query()
    match = await db["q"].find_one_and_delete(gender_q)
    if match:
        return match["_id"]

    if not strict:
        any_match = await db["q"].find_one_and_delete({"_id": {"$ne": user_id}})
        return any_match["_id"] if any_match else None

    return None


async def set_partner(user_id: int, partner_id: int | None) -> None:
    await get_db()["u"].update_one({"_id": user_id}, {"$set": {"p_id": partner_id}})


async def count_waiting() -> int:
    return await get_db()["q"].count_documents({})


# ---------------------------------------------------------------------------
# Message copy tracking  (collection: 'msg')
#
# Schema:
#   _id : str                 — 8-char URL-safe key
#   c   : [[int, int], ...]   — ALL copies: [[chat_id, msg_id], ...]
#                               includes SENDER's original message too
#   exp : datetime            — TTL field; MongoDB auto-deletes after 24 h
#
# Storing the sender's original enables:
#   - Reaction mirroring (existing)
#   - Reply sync: bot replies to sender's original when partner uses Telegram reply
#   - Edit sync: bot edits relayed copy when sender edits their message
# ---------------------------------------------------------------------------

async def create_msg(key: str, copies: list[list[int]]) -> None:
    exp = datetime.now(timezone.utc) + timedelta(hours=MSG_TTL_HOURS)
    await get_db()["msg"].insert_one({"_id": key, "c": copies, "exp": exp})


async def find_msg_by_copy(chat_id: int, msg_id: int) -> dict | None:
    return await get_db()["msg"].find_one({"c": [chat_id, msg_id]})
