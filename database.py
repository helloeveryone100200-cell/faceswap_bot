import random
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient

from config import ALIASES, DB_NAME, MAX_USERS_PER_ROOM, MONGO_URI

client = AsyncIOMotorClient(MONGO_URI)
db = client[DB_NAME]
users_col = db["u"]
rooms_col = db["r"]


async def setup_indexes() -> None:
    await users_col.create_index("r_id")
    await users_col.create_index("s")
    await rooms_col.create_index("c")


# ─── User ─────────────────────────────────────────────────────────────────────

async def get_or_create_user(user_id: int, username: str | None) -> dict:
    now = datetime.now(timezone.utc)
    alias = random.choice(ALIASES)
    update: dict = {
        "$setOnInsert": {"j": now, "s": 1, "r_id": None, "n": alias, "req": []},
    }
    if username:
        update["$set"] = {"u": username}
    doc = await users_col.find_one_and_update(
        {"_id": user_id},
        update,
        upsert=True,
        return_document=True,
    )
    return doc


async def get_user(user_id: int) -> dict | None:
    return await users_col.find_one({"_id": user_id})


async def ban_user(user_id: int) -> None:
    room_id = await leave_room(user_id)
    await users_col.update_one({"_id": user_id}, {"$set": {"s": 0}})
    return room_id


async def unban_user(user_id: int) -> None:
    await users_col.update_one({"_id": user_id}, {"$set": {"s": 1}})


# ─── Rooms ───────────────────────────────────────────────────────────────────

async def join_room(user_id: int) -> str:
    room = await rooms_col.find_one({"c": {"$lt": MAX_USERS_PER_ROOM}}, sort=[("_id", 1)])

    if room:
        room_id: str = room["_id"]
    else:
        count = await rooms_col.count_documents({})
        room_id = f"room_{count + 1}"
        await rooms_col.insert_one({"_id": room_id, "u_ids": [], "c": 0})

    await rooms_col.update_one(
        {"_id": room_id},
        {"$addToSet": {"u_ids": user_id}, "$inc": {"c": 1}},
    )
    await users_col.update_one({"_id": user_id}, {"$set": {"r_id": room_id}})
    return room_id


async def leave_room(user_id: int) -> str | None:
    user = await users_col.find_one({"_id": user_id}, {"r_id": 1})
    if not user or not user.get("r_id"):
        return None
    room_id: str = user["r_id"]

    result = await rooms_col.find_one_and_update(
        {"_id": room_id},
        {"$pull": {"u_ids": user_id}, "$inc": {"c": -1}},
        return_document=True,
    )
    if result and result.get("c", 1) <= 0:
        await rooms_col.delete_one({"_id": room_id})

    await users_col.update_one(
        {"_id": user_id},
        {"$set": {"r_id": None}, "$set": {"r_id": None}},
    )
    await users_col.update_one({"_id": user_id}, {"$set": {"r_id": None}})
    return room_id


async def get_room_members(room_id: str, exclude_user_id: int | None = None) -> list[int]:
    room = await rooms_col.find_one({"_id": room_id}, {"u_ids": 1})
    if not room:
        return []
    ids = room.get("u_ids", [])
    return [uid for uid in ids if uid != exclude_user_id] if exclude_user_id else ids


async def get_room_member_count(room_id: str) -> int:
    room = await rooms_col.find_one({"_id": room_id}, {"c": 1})
    return room.get("c", 0) if room else 0


# ─── Mutual Reveal ───────────────────────────────────────────────────────────

async def request_reveal(user_id: int, room_id: str) -> int | None:
    """
    Adds all room members to user's req list.
    Returns matched user_id if someone already had user_id in their req, else None.
    """
    members = await get_room_members(room_id, exclude_user_id=user_id)
    if not members:
        return None

    existing = await users_col.find_one(
        {"_id": {"$in": members}, "req": user_id},
        {"_id": 1},
    )

    if existing:
        matched_uid: int = existing["_id"]
        await users_col.update_one({"_id": matched_uid}, {"$pull": {"req": user_id}})
        await users_col.update_one({"_id": user_id}, {"$pull": {"req": matched_uid}})
        return matched_uid

    await users_col.update_one(
        {"_id": user_id},
        {"$addToSet": {"req": {"$each": members}}},
    )
    return None


# ─── Stats ────────────────────────────────────────────────────────────────────

async def get_stats() -> dict:
    total = await users_col.count_documents({})
    banned = await users_col.count_documents({"s": 0})
    in_chat = await users_col.count_documents({"r_id": {"$ne": None}, "s": 1})
    active_rooms = await rooms_col.count_documents({"c": {"$gt": 0}})
    return {
        "total": total,
        "banned": banned,
        "active": total - banned,
        "in_chat": in_chat,
        "active_rooms": active_rooms,
    }


async def get_active_user_ids() -> list[int]:
    cursor = users_col.find({"s": 1}, {"_id": 1})
    return [doc["_id"] async for doc in cursor]


async def get_all_users() -> list[dict]:
    return await users_col.find(
        {}, {"_id": 1, "u": 1, "n": 1, "j": 1, "s": 1, "r_id": 1}
    ).sort("j", -1).to_list(None)


async def set_user_alias(user_id: int, new_alias: str) -> str:
    """
    Updates user's anonymous alias.
    Returns 'ok' on success, 'conflict' if another member in same room uses that name.
    """
    user = await users_col.find_one({"_id": user_id}, {"r_id": 1})
    room_id = user.get("r_id") if user else None
    if room_id:
        conflict = await users_col.find_one(
            {"_id": {"$ne": user_id}, "r_id": room_id, "n": new_alias}
        )
        if conflict:
            return "conflict"
    await users_col.update_one({"_id": user_id}, {"$set": {"n": new_alias}})
    return "ok"


async def get_user_by_alias_in_room(alias: str, room_id: str) -> dict | None:
    room = await rooms_col.find_one({"_id": room_id}, {"u_ids": 1})
    if not room:
        return None
    members = room.get("u_ids", [])
    return await users_col.find_one({"_id": {"$in": members}, "n": alias})


async def get_active_rooms() -> list[dict]:
    rooms = await rooms_col.find({"c": {"$gt": 0}}).to_list(None)
    result = []
    for room in rooms:
        members = await users_col.find(
            {"_id": {"$in": room.get("u_ids", [])}}, {"_id": 1, "n": 1}
        ).to_list(None)
        result.append({
            "_id": room["_id"],
            "c": room.get("c", 0),
            "members": members,
        })
    return result


async def request_reveal_targeted(requester_id: int, target_uid: int) -> bool:
    """
    Targeted reveal: requester → target only.
    Returns True if mutual match (target already had requester in their req).
    """
    target_doc = await users_col.find_one({"_id": target_uid}, {"req": 1})
    if target_doc and requester_id in target_doc.get("req", []):
        await users_col.update_one({"_id": target_uid}, {"$pull": {"req": requester_id}})
        await users_col.update_one({"_id": requester_id}, {"$pull": {"req": target_uid}})
        return True
    await users_col.update_one(
        {"_id": requester_id},
        {"$addToSet": {"req": target_uid}},
    )
    return False
