import os
import re
import asyncio
import tempfile
import shutil
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from yt_dlp import YoutubeDL

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, MessageHandler, ContextTypes, filters


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
# Если хочешь, чтобы бот работал только в одной группе:
# 1) Узнай chat_id группы через логи Railway после первого сообщения
# 2) Добавь переменную ALLOWED_CHAT_ID в Railway
ALLOWED_CHAT_ID = os.getenv("ALLOWED_CHAT_ID")

MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "48"))
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024

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
    if not ALLOWED_CHAT_ID:
        return True
    return str(chat_id) == str(ALLOWED_CHAT_ID)


def is_supported_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        full = url.lower()

        if any(h in host for h in SUPPORTED_HOSTS):
            if "youtube.com" in host and "/shorts/" not in full:
                return False
            return True

        return False
    except Exception:
        return False


def find_supported_url(text: str) -> str | None:
    urls = URL_PATTERN.findall(text or "")
    for url in urls:
        clean_url = url.strip().rstrip(").,]")
        if is_supported_url(clean_url):
            return clean_url
    return None


def cleanup_filename(path: str) -> str:
    return str(Path(path).resolve())


def download_video(url: str, output_dir: str) -> str:
    output_template = os.path.join(output_dir, "%(extractor)s_%(id)s.%(ext)s")

    ydl_opts = {
        "outtmpl": output_template,
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "max_filesize": MAX_FILE_BYTES,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                "Mobile/15E148 Safari/604.1"
            )
        },
    }

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

        requested = info.get("requested_downloads")
        if requested and requested[0].get("filepath"):
            return cleanup_filename(requested[0]["filepath"])

        file_path = ydl.prepare_filename(info)
        mp4_path = str(Path(file_path).with_suffix(".mp4"))

        if os.path.exists(mp4_path):
            return cleanup_filename(mp4_path)
        if os.path.exists(file_path):
            return cleanup_filename(file_path)

        # запасной поиск файла
        files = list(Path(output_dir).glob("*"))
        if files:
            return cleanup_filename(str(files[0]))

        raise FileNotFoundError("Файл не найден после скачивания")


async def safe_delete_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message

    if not message or not message.text:
        return

    chat_id = message.chat_id

    # Показывает chat_id группы в логах Railway
    print(f"CHAT_ID={chat_id} | TEXT={message.text[:120]}")

    if not is_allowed_chat(chat_id):
        return

    url = find_supported_url(message.text)

    if not url:
        return

    original_message_id = message.message_id

    # Удаляем ссылку почти сразу
    await safe_delete_message(context, chat_id, original_message_id)

    status_msg = None

    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)

        status_msg = await context.bot.send_message(
            chat_id=chat_id,
            text="⏳ Agafonya скачивает видео..."
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = await asyncio.to_thread(download_video, url, tmpdir)

            if not os.path.exists(video_path):
                await status_msg.edit_text("❌ Видео не найдено после скачивания.")
                return

            file_size = os.path.getsize(video_path)

            if file_size > MAX_FILE_BYTES:
                await status_msg.edit_text(
                    f"❌ Видео больше {MAX_FILE_MB} МБ. Telegram может не дать отправить такой файл."
                )
                return

            await status_msg.edit_text("📤 Отправляю видео...")

            with open(video_path, "rb") as video:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=video,
                    caption="✅ Agafonya загрузил видео",
                    supports_streaming=True,
                    read_timeout=120,
                    write_timeout=120,
                    connect_timeout=60,
                    pool_timeout=60,
                )

        await safe_delete_message(context, chat_id, status_msg.message_id)

    except Exception as e:
        error_text = str(e)

        if status_msg:
            await status_msg.edit_text(
                "❌ Не получилось скачать видео.\n"
                "Попробуй другую ссылку или обнови yt-dlp.\n\n"
                f"Ошибка: {error_text[:700]}"
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ Ошибка: {error_text[:700]}"
            )


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не найден. Добавь переменную BOT_TOKEN в Railway.")

    if not shutil.which("ffmpeg"):
        print("WARNING: ffmpeg не найден. Некоторые видео могут не склеиваться.")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Agafonya bot started")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()