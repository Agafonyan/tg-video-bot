# Agafonya Telegram Video Bot

Бот удаляет сообщение со ссылкой TikTok / Instagram Reels / YouTube Shorts, скачивает видео и отправляет его обратно в чат.

## Файлы

- `bot.py` — код бота
- `requirements.txt` — библиотеки
- `Procfile` — запуск на Railway
- `nixpacks.toml` — установка ffmpeg на Railway
- `.env.example` — пример переменных

## Railway

1. Залей эти файлы в GitHub репозиторий.
2. Railway → New project → GitHub Repository.
3. Выбери репозиторий.
4. Variables → добавь:

```env
BOT_TOKEN=твой_токен_бота
MAX_FILE_MB=48
```

5. Deploy.

## Как сделать бота админом

1. Добавь бота в группу.
2. Сделай администратором.
3. Дай право удалять сообщения.

## Ограничение по группе

После первого сообщения в группе открой Railway → Logs.
Там будет строка:

```text
CHAT_ID=-100....
```

Скопируй это число и добавь в Railway Variables:

```env
ALLOWED_CHAT_ID=-100....
```

Тогда бот будет работать только в этой группе.

## Важно

Instagram/TikTok иногда меняют защиту. Если перестало качать — сделай redeploy или обнови `yt-dlp`.