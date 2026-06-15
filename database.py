import random
from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorClient

from config import ALIASES, MAX_USERS_PER_ROOM, MONGO_URL

_client: AsyncIOMotorClient | None = None

MSG_TTL_HOURS = 24


def get_db():
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(MONGO_URL)
    return _client["anon_chat"]


# ---------------------------------------------------------------------------
# Indexes (call once at startup)
# ---------------------------------------------------------------------------

async def ensure_indexes() -> None:
    """Create all necessary indexes. Safe to call every startup (no-op if exists)."""
    db = get_db()
    # TTL index: auto-delete msg docs after their 'exp' timestamp
    await db["msg"].create_index("exp", expireAfterSeconds=0)


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
        "r_id": None,
        "s": 1,
        "j": datetime.now(timezone.utc),
    }
    await db["u"].insert_one(user)
    return user


async def set_user_room(user_id: int, room_id: str | None) -> None:
    await get_db()["u"].update_one({"_id": user_id}, {"$set": {"r_id": room_id}})


async def ban_user(user_id: int) -> None:
    await get_db()["u"].update_one({"_id": user_id}, {"$set": {"s": 0}})


async def unban_user(user_id: int) -> None:
    await get_db()["u"].update_one({"_id": user_id}, {"$set": {"s": 1}})


async def count_users() -> int:
    return await get_db()["u"].count_documents({})


async def count_banned() -> int:
    return await get_db()["u"].count_documents({"s": 0})


async def count_active_chatters() -> int:
    return await get_db()["u"].count_documents({"r_id": {"$ne": None}, "s": 1})


async def get_all_active_users() -> list[dict]:
    cursor = get_db()["u"].find({"s": 1})
    return await cursor.to_list(length=None)


async def get_users_paginated(page: int, per_page: int = 15) -> tuple[list[dict], int]:
    db = get_db()
    total = await db["u"].count_documents({})
    cursor = db["u"].find().sort("j", -1).skip(page * per_page).limit(per_page)
    users = await cursor.to_list(length=per_page)
    return users, total


# ---------------------------------------------------------------------------
# Room helpers
# ---------------------------------------------------------------------------

async def get_or_assign_room(user_id: int) -> str:
    db = get_db()

    cursor = db["r"].find().sort("_id", 1)
    rooms = await cursor.to_list(length=None)

    target_room = None
    for room in rooms:
        if room["c"] < MAX_USERS_PER_ROOM:
            target_room = room
            break

    if target_room is None:
        next_num = (len(rooms) + 1) if rooms else 1
        room_id = f"room_{next_num}"
        await db["r"].insert_one({"_id": room_id, "u_ids": [user_id], "c": 1})
        return room_id

    room_id = target_room["_id"]
    await db["r"].update_one(
        {"_id": room_id},
        {"$addToSet": {"u_ids": user_id}, "$inc": {"c": 1}},
    )
    return room_id


async def remove_user_from_room(user_id: int, room_id: str) -> None:
    db = get_db()
    await db["r"].update_one(
        {"_id": room_id},
        {"$pull": {"u_ids": user_id}, "$inc": {"c": -1}},
    )
    remaining = await db["r"].find_one({"_id": room_id})
    if remaining and remaining["c"] <= 0:
        await db["r"].delete_one({"_id": room_id})


async def get_room_members(room_id: str) -> list[int]:
    room = await get_db()["r"].find_one({"_id": room_id})
    if not room:
        return []
    return room.get("u_ids", [])


async def count_active_rooms() -> int:
    return await get_db()["r"].count_documents({})


# ---------------------------------------------------------------------------
# Stranger queue helpers  (collection: 'q')
# ---------------------------------------------------------------------------

async def enter_queue(user_id: int) -> None:
    await get_db()["q"].update_one(
        {"_id": user_id},
        {"$set": {"t": datetime.now(timezone.utc)}},
        upsert=True,
    )


async def leave_queue(user_id: int) -> None:
    await get_db()["q"].delete_one({"_id": user_id})


async def find_and_match(user_id: int) -> int | None:
    match = await get_db()["q"].find_one_and_delete({"_id": {"$ne": user_id}})
    return match["_id"] if match else None


async def set_partner(user_id: int, partner_id: int | None) -> None:
    await get_db()["u"].update_one({"_id": user_id}, {"$set": {"p_id": partner_id}})


async def count_waiting() -> int:
    return await get_db()["q"].count_documents({})


# ---------------------------------------------------------------------------
# Message copy tracking  (collection: 'msg')
#
# Minimal schema — only what's needed for native reaction mirroring:
#   _id : str          — 8-char URL-safe key
#   c   : [[int,int]]  — copies: [[chat_id, msg_id], ...]
#   exp : datetime     — TTL field; MongoDB auto-deletes after MSG_TTL_HOURS
#
# TTL index on 'exp' (expireAfterSeconds=0) is created by ensure_indexes().
# ---------------------------------------------------------------------------

async def create_msg(key: str, copies: list[list[int]]) -> None:
    """Store copy locations for a broadcasted message."""
    exp = datetime.now(timezone.utc) + timedelta(hours=MSG_TTL_HOURS)
    await get_db()["msg"].insert_one({
        "_id": key,
        "c": copies,
        "exp": exp,
    })


async def find_msg_by_copy(chat_id: int, msg_id: int) -> dict | None:
    """
    Find the msg doc that contains the exact copy (chat_id, msg_id).
    Used to look up sibling copies when a native reaction arrives.
    """
    return await get_db()["msg"].find_one({"c": [chat_id, msg_id]})
