import random
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient

from config import DB_NAME, MAX_ROOM_USERS, MONGO_URI

client = AsyncIOMotorClient(MONGO_URI)
db = client[DB_NAME]
users_col = db["u"]
rooms_col = db["r"]

ALIASES = [
    "မြေခွေးလေး 🦊", "ဆင်မလေး 🐘", "ကျားကလေး 🐯", "ဝက်ဝံလေး 🐻",
    "ဒရယ်လေး 🦌", "ကြောင်မလေး 🐱", "ပြောင်ကလေး 🐇", "ဝက်ကလေး 🐷",
    "ဖားကလေး 🐸", "ငါးလေး 🐠", "မျောက်ကလေး 🐒", "ခြင်္သေ့ 🦁",
    "မြင်းကလေး 🐴", "နွားကလေး 🐮", "သိုးကလေး 🐑", "ဒေါင်းပေါင် 🦚",
    "ပျားကလေး 🐝", "ပိုးမွှားလေး 🦋", "ငှက်ကြည်ခိုး 🦅", "ပွေးကလေး 🐿️",
    "နှင်းဆီပန်း 🌹", "ကြာပွင့် 🌸", "နေကြာပန်း 🌻", "ကြာဖြူပန်း 🌼",
    "ကျာပွင့် 🌷", "ဂျပန်ပန်း 🌺", "ပင်လယ်ငါး 🐡", "ဆတ်ကလေး 🐆",
    "မြင်းကျားကလေး 🦓", "ဖားမြားကလေး 🐊", "ပင်လယ်ကြာ 🦈", "ကျောက်ငှက် 🦉",
    "ဝါးငှက် 🦜", "ဒိုင်းနိုဆော 🦕", "ရုနိုဆောကလေး 🦏", "ဆင်ဖြူ 🐘",
    "ကျားဖြူ 🐅", "ဘဲကလေး 🦆", "ကြက်တောင် 🐧", "ဓနိပင်ဆင် 🦔",
    "ပင်လယ်ကွမ်း 🦑", "ငုတ်ငှက် 🦢", "ဘီးလူးကလေး 🐙", "နဂါးလေး 🐲",
    "ယုန်ဖြူ 🐰", "ဝုဲကလေး 🦝", "ဖြူဝက်ဝံ 🐼", "ကိုလာကလေး 🐨",
    "ကင်္ကရားကလေး 🦘", "ပင်ချိုကလေး 🦩",
]


async def setup_indexes() -> None:
    await users_col.create_index("r_id")
    await users_col.create_index("s")
    await rooms_col.create_index("c")


async def get_or_create_user(user_id: int, username: str | None) -> dict:
    now = datetime.now(timezone.utc)
    alias = random.choice(ALIASES)
    on_insert: dict = {"j": now, "s": 1, "r_id": None, "n": alias}
    update: dict = {"$setOnInsert": on_insert}
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


async def join_room(user_id: int) -> str:
    room = await rooms_col.find_one({"c": {"$lt": MAX_ROOM_USERS}}, sort=[("_id", 1)])

    if room:
        room_id: str = room["_id"]
    else:
        count = await rooms_col.count_documents({})
        room_id = f"room_{count + 1}"
        await rooms_col.insert_one({"_id": room_id, "u_ids": [], "c": 0, "rv": []})

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
    await rooms_col.update_one(
        {"_id": room_id},
        {"$pull": {"u_ids": user_id, "rv": user_id}, "$inc": {"c": -1}},
    )
    await users_col.update_one({"_id": user_id}, {"$set": {"r_id": None}})
    return room_id


async def get_room_members(room_id: str, exclude_user_id: int) -> list[int]:
    room = await rooms_col.find_one({"_id": room_id}, {"u_ids": 1})
    if not room:
        return []
    return [uid for uid in room.get("u_ids", []) if uid != exclude_user_id]


async def get_room_member_count(room_id: str) -> int:
    room = await rooms_col.find_one({"_id": room_id}, {"c": 1})
    return room.get("c", 0) if room else 0


async def request_reveal(user_id: int, room_id: str) -> int | None:
    room = await rooms_col.find_one({"_id": room_id}, {"rv": 1, "u_ids": 1})
    if not room:
        return None

    rv: list[int] = room.get("rv", [])

    if user_id in rv:
        return None

    others_pending = [uid for uid in rv if uid != user_id]
    if others_pending:
        matched_uid = others_pending[0]
        await rooms_col.update_one(
            {"_id": room_id},
            {"$pull": {"rv": {"$in": [user_id, matched_uid]}}},
        )
        return matched_uid

    await rooms_col.update_one(
        {"_id": room_id},
        {"$addToSet": {"rv": user_id}},
    )
    return None


async def cancel_reveal(user_id: int, room_id: str) -> None:
    await rooms_col.update_one({"_id": room_id}, {"$pull": {"rv": user_id}})


async def get_stats() -> dict:
    total_users = await users_col.count_documents({})
    active_users = await users_col.count_documents({"s": 1})
    in_chat = await users_col.count_documents({"r_id": {"$ne": None}, "s": 1})
    total_rooms = await rooms_col.count_documents({})
    active_rooms = await rooms_col.count_documents({"c": {"$gt": 0}})
    return {
        "total_users": total_users,
        "active_users": active_users,
        "in_chat": in_chat,
        "total_rooms": total_rooms,
        "active_rooms": active_rooms,
    }


async def get_all_users() -> list[dict]:
    return await users_col.find({}, {"_id": 1, "u": 1, "n": 1, "j": 1, "s": 1}).to_list(None)


async def get_active_user_ids() -> list[int]:
    cursor = users_col.find({"s": 1}, {"_id": 1})
    return [doc["_id"] async for doc in cursor]


async def ban_user(user_id: int) -> None:
    await leave_room(user_id)
    await users_col.update_one({"_id": user_id}, {"$set": {"s": 0}})


async def unban_user(user_id: int) -> None:
    await users_col.update_one({"_id": user_id}, {"$set": {"s": 1}})
