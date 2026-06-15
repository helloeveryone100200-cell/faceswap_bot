import random
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient

from config import ALIASES, MAX_USERS_PER_ROOM, MONGO_URL

_client: AsyncIOMotorClient | None = None


def get_db():
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(MONGO_URL)
    return _client["anon_chat"]


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
