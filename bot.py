import os
import re
import asyncio
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from yt_dlp import YoutubeDL

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, MessageHandler, ContextTypes, filters


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ALLOWED_CHAT_ID = os.getenv("ALLOWED_CHAT_ID", "").strip()
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "48"))
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024

COOKIE_FILE = "cookies.txt"

URL_PATTERN = re.compile(r"(https?://[^\s]+)", re.IGNORECASE)

SUPPORTED_HOSTS = (
    "tiktok.com",
    "vm.tiktok.com",
    "vt.tiktok.com",
    "instagram.com",
    "www.instagram.com",
    "youtube.com",
    "www.youtube.com",
    "youtu.be",
)


def is_allowed_chat(chat_id: int) -> bool:
    return not ALLOWED_CHAT_ID or str(chat_id) == ALLOWED_CHAT_ID


def is_supported_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        full = url.lower()

        if "youtube.com" in host and "/shorts/" not in full:
            return False

        return any(h in host for h in SUPPORTED_HOSTS)
    except Exception:
        return False


def find_supported_url(text: str) -> str | None:
    for url in URL_PATTERN.findall(text or ""):
        clean = url.strip().rstrip(").,]")
        if is_supported_url(clean):
            return clean
    return None


def expand_url(url: str) -> str:
    try:
        r = requests.get(
            url,
            allow_redirects=True,
            timeout=15,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                    "Mobile/15E148 Safari/604.1"
                )
            },
        )
        return r.url or url
    except Exception:
        return url


def download_video(url: str, output_dir: str) -> str:
    expanded_url = expand_url(url)
    output_template = os.path.join(output_dir, "%(extractor)s_%(id)s.%(ext)s")

    ydl_opts = {
        "outtmpl": output_template,
        "format": "best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": False,
        "ignoreerrors": False,
        "nocheckcertificate": True,
        "geo_bypass": True,
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 5,
        "max_filesize": MAX_FILE_BYTES,
        "extractor_args": {
            "youtube": {
                "player_client": ["ios", "android", "web_safari", "web"]
            },
            "tiktok": {
                "api_hostname": ["api16-normal-c-useast1a.tiktokv.com"]
            },
        },
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                "Mobile/15E148 Safari/604.1"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    }

    if Path(COOKIE_FILE).exists():
        ydl_opts["cookiefile"] = COOKIE_FILE

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(expanded_url, download=True)

        requested = info.get("requested_downloads")
        if requested and requested[0].get("filepath"):
            return requested[0]["filepath"]

        file_path = ydl.prepare_filename(info)
        mp4_path = str(Path(file_path).with_suffix(".mp4"))

        if os.path.exists(mp4_path):
            return mp4_path
        if os.path.exists(file_path):
            return file_path

        files = list(Path(output_dir).glob("*"))
        if files:
            return str(files[0])

        raise FileNotFoundError("Файл не найден после скачивания")


async def safe_delete(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message or not message.text:
        return

    chat_id = message.chat_id
    print(f"CHAT_ID={chat_id} TEXT={message.text[:150]}", flush=True)

    if not is_allowed_chat(chat_id):
        return

    url = find_supported_url(message.text)
    if not url:
        return

    await safe_delete(context, chat_id, message.message_id)

    status = await context.bot.send_message(chat_id=chat_id, text="⏳ Agafonya скачивает видео...")
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)

        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = await asyncio.to_thread(download_video, url, tmpdir)

            file_size = os.path.getsize(video_path)
            if file_size > MAX_FILE_BYTES:
                await status.edit_text(f"❌ Видео больше {MAX_FILE_MB} МБ.")
                return

            await status.edit_text("📤 Отправляю видео...")

            with open(video_path, "rb") as video:
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

        await safe_delete(context, chat_id, status.message_id)

    except Exception as e:
        err = str(e)
        await status.edit_text(
            "❌ Не получилось скачать видео.\n\n"
            "Если это TikTok/Instagram — возможно нужны cookies.\n\n"
            f"Ошибка: {err[:600]}"
        )


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не найден. В Railway → worker → Variables добавь BOT_TOKEN.")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Agafonya bot started", flush=True)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()