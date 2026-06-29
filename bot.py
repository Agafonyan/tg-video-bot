import asyncio
import base64
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from yt_dlp import YoutubeDL

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, ContextTypes, MessageHandler, filters


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "48"))
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024

YOUTUBE_COOKIES_FILE = os.getenv("YOUTUBE_COOKIES_FILE", "").strip()
YOUTUBE_COOKIES = os.getenv("YOUTUBE_COOKIES", "").strip()
YOUTUBE_COOKIES_B64 = os.getenv("YOUTUBE_COOKIES_B64", "").strip()

URL_PATTERN = re.compile(r"(https?://[^\s]+)", re.IGNORECASE)

SUPPORTED_HOSTS = {
    "instagram.com",
    "www.instagram.com",
    "tiktok.com",
    "www.tiktok.com",
    "vm.tiktok.com",
    "vt.tiktok.com",
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".opus", ".ogg", ".webm"}


def normalize_host(url: str) -> str:
    return urlparse(url).netloc.lower().split("@")[-1].split(":")[0]


def is_supported_url(url: str) -> bool:
    host = normalize_host(url)
    return host in SUPPORTED_HOSTS or host.endswith(".tiktok.com")


def is_instagram_url(url: str) -> bool:
    return "instagram.com" in normalize_host(url)


def is_youtube_url(url: str) -> bool:
    host = normalize_host(url)
    return host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be"}


def is_youtube_music_url(url: str) -> bool:
    return normalize_host(url) == "music.youtube.com"


def find_supported_url(text: str) -> str | None:
    urls = URL_PATTERN.findall(text or "")
    for url in urls:
        clean_url = url.strip().rstrip(").,]")
        if is_supported_url(clean_url):
            return clean_url
    return None


def has_video_stream(info: dict) -> bool:
    if info.get("vcodec") not in (None, "none"):
        return True

    for media_format in info.get("formats") or []:
        if media_format.get("vcodec") not in (None, "none"):
            return True

    ext = f".{info.get('ext', '').lower()}"
    return ext in VIDEO_EXTENSIONS and bool(info.get("duration"))


def reject_instagram_images(info: dict, incomplete: bool = False) -> str | None:
    if incomplete:
        return None
    if info.get("_type") in {"playlist", "multi_video"}:
        return None
    if not has_video_stream(info):
        return "Пропускаю фото из Instagram-поста, нужен видеофайл"
    return None


def write_youtube_cookies(output_dir: str) -> str | None:
    if YOUTUBE_COOKIES_FILE:
        return YOUTUBE_COOKIES_FILE

    cookies_text = ""

    if YOUTUBE_COOKIES_B64:
        cookies_text = base64.b64decode(YOUTUBE_COOKIES_B64).decode("utf-8")
    elif YOUTUBE_COOKIES:
        cookies_text = YOUTUBE_COOKIES.replace("\\n", "\n")

    if not cookies_text:
        return None

    cookies_path = os.path.join(output_dir, "youtube_cookies.txt")
    with open(cookies_path, "w", encoding="utf-8") as cookies_file:
        cookies_file.write(cookies_text)
        if not cookies_text.endswith("\n"):
            cookies_file.write("\n")

    return cookies_path


def pick_downloaded_file(output_dir: str, allowed_extensions: set[str]) -> str:
    files = [
        path
        for path in Path(output_dir).iterdir()
        if path.is_file()
        and path.name != "youtube_cookies.txt"
        and path.suffix.lower() in allowed_extensions
    ]

    if not files:
        raise FileNotFoundError("Файл не найден после скачивания")

    return str(max(files, key=lambda path: path.stat().st_mtime))


def base_ydl_opts(url: str, output_dir: str) -> dict:
    output_template = os.path.join(output_dir, "%(title).80s.%(ext)s")
    ydl_opts = {
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "max_filesize": MAX_FILE_BYTES,
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 5,
        "ignoreerrors": False,
    }

    if is_youtube_url(url):
        cookie_file = write_youtube_cookies(output_dir)
        if cookie_file:
            ydl_opts["cookiefile"] = cookie_file

    return ydl_opts


def download_video(url: str, output_dir: str) -> str:
    ydl_opts = base_ydl_opts(url, output_dir)
    ydl_opts.update(
        {
            "format": "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b[ext=mp4]/bestvideo*+bestaudio/best",
            "merge_output_format": "mp4",
            "noplaylist": not is_instagram_url(url),
        }
    )

    if is_instagram_url(url):
        ydl_opts["match_filter"] = reject_instagram_images

    with YoutubeDL(ydl_opts) as ydl:
        ydl.extract_info(url, download=True)

    return pick_downloaded_file(output_dir, VIDEO_EXTENSIONS)


def download_audio(url: str, output_dir: str) -> str:
    ydl_opts = base_ydl_opts(url, output_dir)
    ydl_opts.update(
        {
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "noplaylist": True,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }
    )

    with YoutubeDL(ydl_opts) as ydl:
        ydl.extract_info(url, download=True)

    return pick_downloaded_file(output_dir, AUDIO_EXTENSIONS)


def download_media(url: str, output_dir: str) -> tuple[str, str]:
    if is_youtube_music_url(url):
        return download_audio(url, output_dir), "audio"

    return download_video(url, output_dir), "video"


def friendly_error(url: str, error: Exception) -> str:
    error_text = str(error)
    lowered = error_text.lower()

    if is_youtube_url(url) and any(
        marker in lowered
        for marker in ("sign in", "cookies", "confirm you're not a bot", "not a bot", "login")
    ):
        return (
            "YouTube просит cookies. Добавь в Railway переменную "
            "YOUTUBE_COOKIES_B64 или YOUTUBE_COOKIES_FILE с cookies.txt."
        )

    return error_text[:600]


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message

    if not message or not message.text:
        return

    url = find_supported_url(message.text)

    if not url:
        return

    chat_id = message.chat_id
    message_id = message.message_id
    media_kind = "audio" if is_youtube_music_url(url) else "video"

    print(f"CHAT_ID={chat_id} URL={url}", flush=True)

    status_msg = None

    try:
        action = ChatAction.UPLOAD_DOCUMENT if media_kind == "audio" else ChatAction.UPLOAD_VIDEO
        await context.bot.send_chat_action(chat_id=chat_id, action=action)

        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception as delete_error:
            print(f"Не смог удалить сообщение: {delete_error}", flush=True)

        label = "аудио" if media_kind == "audio" else "видео"
        status_msg = await context.bot.send_message(chat_id=chat_id, text=f"⏳ Agafonya скачивает {label}...")

        with tempfile.TemporaryDirectory() as tmpdir:
            media_path, media_kind = await asyncio.to_thread(download_media, url, tmpdir)
            file_size = os.path.getsize(media_path)

            if file_size > MAX_FILE_BYTES:
                await status_msg.edit_text(f"❌ Файл больше {MAX_FILE_MB} МБ.")
                return

            if media_kind == "audio":
                with open(media_path, "rb") as audio:
                    await context.bot.send_audio(
                        chat_id=chat_id,
                        audio=audio,
                        caption="✅ Agafonya загрузил аудио",
                        read_timeout=180,
                        write_timeout=180,
                        connect_timeout=60,
                        pool_timeout=60,
                    )
            else:
                with open(media_path, "rb") as video:
                    await context.bot.send_video(
                        chat_id=chat_id,
                        video=video,
                        caption="✅ Agafonya загрузил видео",
                        supports_streaming=True,
                        read_timeout=180,
                        write_timeout=180,
                        connect_timeout=60,
                        pool_timeout=60,
                    )

        if status_msg:
            try:
                await status_msg.delete()
            except Exception:
                pass

    except Exception as error:
        error_text = friendly_error(url, error)

        if status_msg:
            await status_msg.edit_text(f"❌ Не получилось скачать.\n\nОшибка: {error_text}")
        else:
            await context.bot.send_message(chat_id=chat_id, text=f"❌ Ошибка: {error_text}")


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не найден. Добавь BOT_TOKEN в Railway Variables.")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Agafonya bot started", flush=True)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
