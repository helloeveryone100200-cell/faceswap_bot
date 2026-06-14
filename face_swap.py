import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import time

import httpx
from PIL import Image

from config import HF_SPACE

logger = logging.getLogger(__name__)

HF_BASE = f"https://{HF_SPACE.replace('/', '-')}.hf.space"
TIMEOUT = 300
MAX_IMAGE_SIZE = 720
MAX_VIDEO_HEIGHT = 720


def compress_image(src_path: str, max_size: int = MAX_IMAGE_SIZE) -> str:
    fd, out_path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    try:
        with Image.open(src_path) as img:
            img = img.convert("RGB")
            w, h = img.size
            if w > max_size or h > max_size:
                ratio = min(max_size / w, max_size / h)
                new_w = int(w * ratio)
                new_h = int(h * ratio)
                img = img.resize((new_w, new_h), Image.LANCZOS)
                logger.info("Image resized: %dx%d → %dx%d", w, h, new_w, new_h)
            img.save(out_path, "JPEG", quality=85, optimize=True)
    except Exception as e:
        logger.warning("Image compression failed (%s), using original", e)
        shutil.copy2(src_path, out_path)
    return out_path


def compress_video(src_path: str, max_height: int = MAX_VIDEO_HEIGHT) -> str:
    if not shutil.which("ffmpeg"):
        logger.warning("ffmpeg not found, skipping video compression")
        return src_path

    fd, out_path = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    try:
        cmd = [
            "ffmpeg", "-y",
            "-i", src_path,
            "-vf", f"scale=-2:'min({max_height},ih)'",
            "-c:v", "libx264",
            "-crf", "28",
            "-preset", "fast",
            "-c:a", "aac",
            "-b:a", "96k",
            out_path,
        ]
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode()[-500:])
        orig_mb = os.path.getsize(src_path) / 1024 / 1024
        comp_mb = os.path.getsize(out_path) / 1024 / 1024
        logger.info("Video compressed: %.1f MB → %.1f MB", orig_mb, comp_mb)
    except Exception as e:
        logger.warning("Video compression failed (%s), using original", e)
        os.remove(out_path)
        return src_path
    return out_path


async def _compress_image_async(path: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, compress_image, path)


async def _compress_video_async(path: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, compress_video, path)


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
    compressed_source = await _compress_image_async(source_path)
    compressed_target = await _compress_image_async(target_path)
    temp_files = []
    if compressed_source != source_path:
        temp_files.append(compressed_source)
    if compressed_target != target_path:
        temp_files.append(compressed_target)

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            source_url = await _upload_file(client, compressed_source, "image/jpeg")
            target_url = await _upload_file(client, compressed_target, "image/jpeg")

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
    finally:
        for p in temp_files:
            try:
                os.remove(p)
            except OSError:
                pass


async def swap_faces_video(source_path: str, target_path: str) -> str:
    compressed_source = await _compress_image_async(source_path)
    compressed_target = await _compress_video_async(target_path)
    temp_files = []
    if compressed_source != source_path:
        temp_files.append(compressed_source)
    if compressed_target != target_path:
        temp_files.append(compressed_target)

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            source_url = await _upload_file(client, compressed_source, "image/jpeg")
            target_url = await _upload_file(client, compressed_target, "video/mp4")

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
    finally:
        for p in temp_files:
            try:
                os.remove(p)
            except OSError:
                pass
