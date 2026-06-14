import asyncio
import os
import tempfile
from gradio_client import Client, handle_file
from config import HF_SPACE


def _swap_images_sync(source_path: str, target_path: str) -> str:
    client = Client(HF_SPACE)
    result = client.predict(
        source_img=handle_file(source_path),
        target_img=handle_file(target_path),
        api_name="/run_swap",
    )
    if isinstance(result, (list, tuple)):
        return result[0]
    return result


async def swap_faces_photo(source_path: str, target_path: str) -> str:
    loop = asyncio.get_event_loop()
    output_path = await loop.run_in_executor(
        None, _swap_images_sync, source_path, target_path
    )
    return output_path


def _swap_video_sync(source_path: str, target_path: str) -> str:
    client = Client(HF_SPACE)
    result = client.predict(
        source_img=handle_file(source_path),
        target_video=handle_file(target_path),
        api_name="/run_swap_video",
    )
    if isinstance(result, (list, tuple)):
        return result[0]
    return result


async def swap_faces_video(source_path: str, target_path: str) -> str:
    loop = asyncio.get_event_loop()
    output_path = await loop.run_in_executor(
        None, _swap_video_sync, source_path, target_path
    )
    return output_path


def save_temp_bytes(data: bytes, suffix: str = ".jpg") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path
