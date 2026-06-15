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
# Message reaction helpers  (collection: 'msg')
#
# Schema (minimal):
#   _id : str        — short unique key (8-char URL-safe base64)
#   c   : [[int,int]] — copies: list of [chat_id, message_id]
#   r   : dict       — {emoji: count}  (sparse: only non-zero stored)
#   u   : dict       — {str(user_id): emoji}  (for toggle / one-per-user)
#   exp : datetime   — TTL field; MongoDB auto-deletes doc after this time
#
# TTL index on 'exp' (expireAfterSeconds=0) is created by ensure_indexes().
# ---------------------------------------------------------------------------

async def create_msg(key: str, copies: list[list[int]]) -> None:
    """Store a new broadcast message with its recipient (chat_id, msg_id) pairs."""
    exp = datetime.now(timezone.utc) + timedelta(hours=MSG_TTL_HOURS)
    await get_db()["msg"].insert_one({
        "_id": key,
        "c": copies,   # [[chat_id, msg_id], ...]
        "r": {},       # reaction counts
        "u": {},       # user → emoji (for toggle)
        "exp": exp,
    })


async def toggle_reaction(
    key: str, user_id: int, emoji: str
) -> tuple[dict, list[list[int]]] | None:
    """
    Toggle a user's reaction on a message.

    - If user reacts with the same emoji → remove it (toggle off).
    - If user reacts with a different emoji → switch.
    - If user has no reaction → add it.

    Returns (reaction_counts, copies) so the caller can update keyboards,
    or None if the message doc has expired / not found.
    """
    db = get_db()
    doc = await db["msg"].find_one({"_id": key})
    if not doc:
        return None

    uid = str(user_id)
    counts: dict = dict(doc.get("r", {}))
    users: dict = dict(doc.get("u", {}))
    prev = users.get(uid)

    if prev == emoji:
        # Same emoji → remove reaction
        counts[prev] = counts.get(prev, 1) - 1
        if counts[prev] <= 0:
            counts.pop(prev, None)
        users.pop(uid, None)
    else:
        # Remove previous reaction if any
        if prev:
            counts[prev] = counts.get(prev, 1) - 1
            if counts[prev] <= 0:
                counts.pop(prev, None)
        # Add new reaction
        counts[emoji] = counts.get(emoji, 0) + 1
        users[uid] = emoji

    await db["msg"].update_one(
        {"_id": key},
        {"$set": {"r": counts, "u": users}},
    )
    return counts, doc["c"]
