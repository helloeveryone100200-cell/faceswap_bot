from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient
from config import MONGO_URI, DB_NAME

client = AsyncIOMotorClient(MONGO_URI)
db = client[DB_NAME]
users_col = db["users"]
settings_col = db["settings"]


async def setup_indexes() -> None:
    await users_col.create_index("s")
    await users_col.create_index("j")
    await users_col.create_index("u")


async def get_or_create_user(user_id: int, username: str | None) -> dict:
    now = datetime.now(timezone.utc)
    doc: dict = {"$setOnInsert": {"j": now, "s": 1, "p_c": 0, "v_c": 0}}
    if username:
        doc["$set"] = {"u": username}
    result = await users_col.find_one_and_update(
        {"_id": user_id},
        doc,
        upsert=True,
        return_document=True,
    )
    return result


async def increment_counter(user_id: int, field: str) -> None:
    await users_col.update_one({"_id": user_id}, {"$inc": {field: 1}})


async def get_stats() -> dict:
    pipeline = [
        {
            "$group": {
                "_id": None,
                "total_users": {"$sum": 1},
                "active_users": {"$sum": {"$cond": [{"$eq": ["$s", 1]}, 1, 0]}},
                "total_photos": {"$sum": "$p_c"},
                "total_videos": {"$sum": "$v_c"},
            }
        }
    ]
    async for doc in users_col.aggregate(pipeline):
        return doc
    return {"total_users": 0, "active_users": 0, "total_photos": 0, "total_videos": 0}


async def get_all_users() -> list[dict]:
    return await users_col.find({}, {"_id": 1, "u": 1, "j": 1, "s": 1}).to_list(None)


async def get_active_user_ids() -> list[int]:
    cursor = users_col.find({"s": 1}, {"_id": 1})
    return [doc["_id"] async for doc in cursor]


async def ban_user(user_id: int) -> None:
    await users_col.update_one({"_id": user_id}, {"$set": {"s": 0}})


async def unban_user(user_id: int) -> None:
    await users_col.update_one({"_id": user_id}, {"$set": {"s": 1}})


async def get_welcome_settings() -> dict:
    doc = await settings_col.find_one({"_id": "welcome"})
    if not doc:
        return {
            "text": None,
            "btn_text": None,
            "btn_url": None,
        }
    return doc


async def set_welcome_settings(text: str, btn_text: str, btn_url: str) -> None:
    await settings_col.update_one(
        {"_id": "welcome"},
        {"$set": {"text": text, "btn_text": btn_text, "btn_url": btn_url}},
        upsert=True,
    )
