# Pixiv Telegram Bot

A bot for automatically posting random art from Pixiv bookmarks to a Telegram channel.

## Features
- Scheduled posting
- Quiet hours (doesn't post at night)
- Configuration via environment variables

## Installation
```bash
pip install -r requirements.txt
```

## Configuration
Set environment variables or create `config.json`:
```json
{
"pixiv_refresh_token": "your_token",
"telegram_bot_token": "your_token",
"telegram_channel_id": "@your_channel"
}
```