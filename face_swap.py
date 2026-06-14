import asyncio
import os
import tempfile
import time
import httpx
from config import HF_SPACE

HF_BASE = f"https://{HF_SPACE.replace('/', '-')}.hf.space"
TIMEOUT = 300


async def _upload_file(client: httpx.AsyncClient, file_path: str, mime: str) -> str:
    with open(file_path, "rb") as f:
        data = f.read()
    filename = os.path.basename(file_path)
    resp = await client.post(
        f"{HF_BASE}/upload",
        files={"files": (filename, data, mime)},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    result = resp.json()
    if isinstance(result, list):
        return result[0]
    return result


async def _predict(client: httpx.AsyncClient, api_name: str, data: list) -> dict:
    resp = await client.post(
        f"{HF_BASE}/run/predict",
        json={"fn_index": 0, "data": data},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


async def _queue_predict(client: httpx.AsyncClient, fn_index: int, data: list) -> dict:
    session_hash = f"replit_{int(time.time())}"

    join_resp = await client.post(
        f"{HF_BASE}/queue/join",
        json={"fn_index": fn_index, "data": data, "session_hash": session_hash},
        timeout=60,
    )
    join_resp.raise_for_status()

    while True:
        status_resp = await client.get(
            f"{HF_BASE}/queue/status",
            params={"session_hash": session_hash},
            timeout=60,
        )
        result = status_resp.json()
        if result.get("status") == "complete":
            return result.get("output", {})
        if result.get("status") == "failed":
            raise RuntimeError(f"Queue failed: {result}")
        await asyncio.sleep(3)


def _save_output(data: bytes, suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


def save_temp_bytes(data: bytes, suffix: str = ".jpg") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


async def swap_faces_photo(source_path: str, target_path: str) -> str:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        source_url = await _upload_file(client, source_path, "image/jpeg")
        target_url = await _upload_file(client, target_path, "image/jpeg")

        try:
            result = await _predict(client, "/run_swap", [source_url, target_url])
            output_data = result.get("data", [None])[0]
        except Exception:
            result = await _queue_predict(client, 0, [source_url, target_url])
            output_data = result.get("data", [None])[0]

        if isinstance(output_data, str) and output_data.startswith("http"):
            dl = await client.get(output_data, timeout=TIMEOUT)
            return _save_output(dl.content, ".jpg")

        if isinstance(output_data, dict) and "url" in output_data:
            dl = await client.get(output_data["url"], timeout=TIMEOUT)
            return _save_output(dl.content, ".jpg")

        raise RuntimeError("Unexpected output format from face swap API")


async def swap_faces_video(source_path: str, target_path: str) -> str:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        source_url = await _upload_file(client, source_path, "image/jpeg")
        target_url = await _upload_file(client, target_path, "video/mp4")

        try:
            result = await _predict(client, "/run_swap_video", [source_url, target_url])
            output_data = result.get("data", [None])[0]
        except Exception:
            result = await _queue_predict(client, 1, [source_url, target_url])
            output_data = result.get("data", [None])[0]

        if isinstance(output_data, str) and output_data.startswith("http"):
            dl = await client.get(output_data, timeout=TIMEOUT)
            return _save_output(dl.content, ".mp4")

        if isinstance(output_data, dict) and "url" in output_data:
            dl = await client.get(output_data["url"], timeout=TIMEOUT)
            return _save_output(dl.content, ".mp4")

        raise RuntimeError("Unexpected output format from face swap API")
