import os
import re
import asyncio
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from yt_dlp import YoutubeDL

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, MessageHandler, ContextTypes, filters


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "48"))
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024

URL_PATTERN = re.compile(r"(https?://[^\s]+)", re.IGNORECASE)

ALLOWED_DOMAINS = [
    "tiktok.com",
    "vm.tiktok.com",
    "vt.tiktok.com",
    "instagram.com",
    "www.instagram.com",
    "youtube.com/shorts",
    "www.youtube.com/shorts",
    "youtu.be",
]


def is_allowed_url(url: str) -> bool:
    url_lower = url.lower()
    return any(domain in url_lower for domain in ALLOWED_DOMAINS)


def find_supported_url(text: str) -> str | None:
    urls = URL_PATTERN.findall(text or "")
    for url in urls:
        clean_url = url.strip().rstrip(").,]")
        if is_allowed_url(clean_url):
            return clean_url
    return None


def download_video(url: str, output_dir: str) -> str:
    output_template = os.path.join(output_dir, "%(title).80s.%(ext)s")

    ydl_opts = {
        "outtmpl": output_template,
        "format": "mp4/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "max_filesize": MAX_FILE_BYTES,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
    }

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        file_path = ydl.prepare_filename(info)

        if not file_path.endswith(".mp4"):
            possible_mp4 = str(Path(file_path).with_suffix(".mp4"))
            if os.path.exists(possible_mp4):
                file_path = possible_mp4

        if os.path.exists(file_path):
            return file_path

        files = list(Path(output_dir).glob("*"))
        if files:
            return str(files[0])

        raise FileNotFoundError("Видео не найдено после скачивания")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message

    if not message or not message.text:
        return

    url = find_supported_url(message.text)

    if not url:
        return

    chat_id = message.chat_id
    message_id = message.message_id

    print(f"CHAT_ID={chat_id} URL={url}", flush=True)

    status_msg = None

    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)

        # Сначала удаляем ссылку
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception as delete_error:
            print(f"Не смог удалить сообщение: {delete_error}", flush=True)

        status_msg = await context.bot.send_message(chat_id=chat_id, text="⏳ Agafonya скачивает видео...")

        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = await asyncio.to_thread(download_video, url, tmpdir)

            file_size = os.path.getsize(video_path)

            if file_size > MAX_FILE_BYTES:
                await status_msg.edit_text(f"❌ Видео больше {MAX_FILE_MB} МБ.")
                return

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

        if status_msg:
            try:
                await status_msg.delete()
            except Exception:
                pass

    except Exception as e:
        error_text = str(e)

        if status_msg:
            await status_msg.edit_text(f"❌ Не получилось скачать видео.\n\nОшибка: {error_text[:600]}")
        else:
            await context.bot.send_message(chat_id=chat_id, text=f"❌ Ошибка: {error_text[:600]}")


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не найден. Добавь BOT_TOKEN в Railway Variables.")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Agafonya bot started", flush=True)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
