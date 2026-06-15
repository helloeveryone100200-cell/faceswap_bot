import random
from datetime import datetime, timedelta, timezone

from bson import ObjectId
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
    await db["matches"].create_index("t")
    await db["reports"].create_index("t")


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
    await get_db()["u"].update_one({"_id": user_id}, {"$set": {"g": gender}})


async def set_tags(user_id: int, tags: list[str]) -> None:
    await get_db()["u"].update_one({"_id": user_id}, {"$set": {"tags": tags[:3]}})


async def set_alias(user_id: int, alias: str) -> None:
    """Admin: force-change a user's display alias."""
    await get_db()["u"].update_one({"_id": user_id}, {"$set": {"n": alias}})


async def ban_user(user_id: int) -> None:
    await get_db()["u"].update_one({"_id": user_id}, {"$set": {"s": 0, "ban_exp": None}})


async def unban_user(user_id: int) -> None:
    await get_db()["u"].update_one(
        {"_id": user_id}, {"$set": {"s": 1, "ban_exp": None, "report_count": 0}}
    )


async def set_temp_ban(user_id: int) -> None:
    exp = datetime.now(timezone.utc) + timedelta(hours=TEMP_BAN_HOURS)
    await get_db()["u"].update_one({"_id": user_id}, {"$set": {"s": 0, "ban_exp": exp}})


async def lift_expired_temp_ban(user_id: int) -> bool:
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
# User Management (Admin)
# ---------------------------------------------------------------------------

USERS_PER_PAGE = 5


async def get_users_paginated(page: int) -> tuple[list[dict], int]:
    """Return (users_on_page, total_count) sorted newest first."""
    db = get_db()
    total = await db["u"].count_documents({})
    cursor = db["u"].find().sort("j", -1).skip(page * USERS_PER_PAGE).limit(USERS_PER_PAGE)
    users = await cursor.to_list(length=USERS_PER_PAGE)
    return users, total


async def search_users(query: str) -> list[dict]:
    """Search by numeric Telegram ID or by @username substring (case-insensitive)."""
    db = get_db()
    if query.lstrip("-").isdigit():
        user = await db["u"].find_one({"_id": int(query)})
        return [user] if user else []
    cursor = db["u"].find({"u": {"$regex": query.lstrip("@"), "$options": "i"}})
    return await cursor.to_list(length=10)


# ---------------------------------------------------------------------------
# Report system
# ---------------------------------------------------------------------------

REPORTS_PER_PAGE = 5


async def add_report(reporter_id: int, reported_id: int) -> int:
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


async def get_reports_paginated(page: int) -> tuple[list[dict], int]:
    """Return (reports_on_page, total_count) sorted newest first."""
    db = get_db()
    total = await db["reports"].count_documents({})
    cursor = db["reports"].find().sort("t", -1).skip(page * REPORTS_PER_PAGE).limit(REPORTS_PER_PAGE)
    reports = await cursor.to_list(length=REPORTS_PER_PAGE)
    return reports, total


async def dismiss_report(report_id_str: str) -> None:
    """Delete a single report document by its ObjectId string."""
    await get_db()["reports"].delete_one({"_id": ObjectId(report_id_str)})


async def clear_user_reports(user_id: int) -> None:
    """Delete all reports against a user and reset their report_count."""
    await get_db()["reports"].delete_many({"reported": user_id})
    await get_db()["u"].update_one({"_id": user_id}, {"$set": {"report_count": 0}})


async def count_reports_total() -> int:
    return await get_db()["reports"].count_documents({})


# ---------------------------------------------------------------------------
# Match logging  (collection: 'matches')
# ---------------------------------------------------------------------------

async def log_match(user_a_id: int, user_b_id: int) -> None:
    await get_db()["matches"].insert_one({
        "u": [user_a_id, user_b_id],
        "t": datetime.now(timezone.utc),
    })


async def count_matches_total() -> int:
    return await get_db()["matches"].count_documents({})


# ---------------------------------------------------------------------------
# Advanced Stats helpers
# ---------------------------------------------------------------------------

def _today_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def count_new_users_today() -> int:
    return await get_db()["u"].count_documents({"j": {"$gte": _today_start()}})


async def count_matches_today() -> int:
    return await get_db()["matches"].count_documents({"t": {"$gte": _today_start()}})


async def count_reports_today() -> int:
    return await get_db()["reports"].count_documents({"t": {"$gte": _today_start()}})


async def count_temp_banned() -> int:
    now = datetime.now(timezone.utc)
    return await get_db()["u"].count_documents({
        "s": 0,
        "ban_exp": {"$gt": now},
    })


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
    return await get_db()["q"].find_one({"_id": user_id}) is not None


async def get_queue_entry(user_id: int) -> dict | None:
    return await get_db()["q"].find_one({"_id": user_id})


async def find_and_match(
    user_id: int,
    user_gender: str | None,
    target_gender: str,
    user_tags: list[str],
    strict: bool = True,
) -> int | None:
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
# ---------------------------------------------------------------------------

async def create_msg(key: str, copies: list[list[int]]) -> None:
    exp = datetime.now(timezone.utc) + timedelta(hours=MSG_TTL_HOURS)
    await get_db()["msg"].insert_one({"_id": key, "c": copies, "exp": exp})


async def find_msg_by_copy(chat_id: int, msg_id: int) -> dict | None:
    return await get_db()["msg"].find_one({"c": [chat_id, msg_id]})
