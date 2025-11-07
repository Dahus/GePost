import random
import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from pixivpy3 import AppPixivAPI
from telegram import Bot
import logging
import sys
from zoneinfo import ZoneInfo

# Настройка логирования с поддержкой UTF-8
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('pixiv_bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Определяем московский часовой пояс
MOSCOW_TZ = ZoneInfo("Europe/Moscow")

# Файл конфигурации
CONFIG_FILE = 'config.json'

# Настройки для безопасной работы с Pixiv API
PIXIV_REQUEST_DELAY = 2.0  # Задержка между запросами к Pixiv (секунды)
MAX_PAGES_TO_FETCH = 40    # Максимум страниц для сбора
ILLUSTS_PER_PAGE = 30      # Иллюстраций на странице

def load_config():
    """Loads configuration from file or environment variables"""
    script_dir = Path(__file__).parent
    config_path = script_dir / CONFIG_FILE
    
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        logger.info("Config loaded from file")
        return config
    
    logger.info("Config file not found, reading from environment variables")
    
    config = {
        'pixiv_refresh_token': os.getenv('PIXIV_REFRESH_TOKEN'),
        'telegram_bot_token': os.getenv('TELEGRAM_BOT_TOKEN'),
        'telegram_channel_id': os.getenv('TELEGRAM_CHANNEL_ID'),
        'telegram_thread_id': os.getenv('TELEGRAM_THREAD_ID'),
        'interval_hours': int(os.getenv('INTERVAL_HOURS', 3)),
        'interval_minutes': int(os.getenv('INTERVAL_MINUTES', 0)),
        'interval_deviation_minutes': int(os.getenv('INTERVAL_DEVIATION_MINUTES', 0)),
        'post_immediately_on_start': os.getenv('POST_IMMEDIATELY_ON_START', 'false').lower() == 'true',
        'quiet_hours': {
            'enabled': os.getenv('QUIET_HOURS_ENABLED', 'false').lower() == 'true',
            'start_hour': int(os.getenv('QUIET_HOURS_START', 0)),
            'end_hour': int(os.getenv('QUIET_HOURS_END', 0))
        }
    }
    
    if not config['pixiv_refresh_token'] or not config['telegram_bot_token'] or not config['telegram_channel_id']:
        raise ValueError("Missing required environment variables: PIXIV_REFRESH_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID")
    
    logger.info("Config loaded from environment")
    return config

def calculate_next_interval(base_seconds, deviation_minutes):
    """Вычисляет следующий интервал с учётом случайного отклонения"""
    if deviation_minutes == 0:
        return base_seconds
    
    deviation_seconds = random.randint(-deviation_minutes * 60, deviation_minutes * 60)
    result_seconds = max(60, base_seconds + deviation_seconds)
    
    if deviation_seconds != 0:
        sign = "+" if deviation_seconds > 0 else ""
        logger.info(f"Interval deviation: {sign}{format_time(abs(deviation_seconds))}")
    
    return result_seconds

async def get_last_post_time(bot_token, channel_id):
    """Получает время последнего поста в канале"""
    try:
        bot = Bot(token=bot_token)
        updates = await bot.get_updates(limit=100)
        
        channel_messages = [
            update.channel_post for update in updates 
            if update.channel_post and str(update.channel_post.chat.id) == str(channel_id)
        ]
        
        if channel_messages:
            last_message = max(channel_messages, key=lambda x: x.date)
            logger.info(f"Last post time: {last_message.date}")
            return last_message.date
        
        chat = await bot.get_chat(channel_id)
        logger.info(f"Channel: {chat.title if hasattr(chat, 'title') else channel_id}")
        
        return None
    except Exception as e:
        logger.warning(f"Failed to get last post time: {e}")
        return None

async def send_to_telegram(image_url, caption, bot_token, channel_id, thread_id=None):
    """Отправляет изображение и ссылку в Telegram канал"""
    bot = Bot(token=bot_token)
    
    try:
        send_params = {
            'chat_id': channel_id,
            'photo': image_url,
            'caption': caption,
            'parse_mode': 'HTML'
        }
        
        if thread_id:
            send_params['message_thread_id'] = thread_id
            logger.info(f"Posting to thread: {thread_id}")
        
        message = await bot.send_photo(**send_params)
        logger.info(f"Posted to Telegram channel: {channel_id}")
        return message.date
    except Exception as e:
        logger.error(f"Failed to send to Telegram: {e}")
        return None

async def get_random_pixiv_art_safe(refresh_token):
    """Безопасное получение случайной иллюстрации из закладок Pixiv"""
    try:
        api = AppPixivAPI()
        api.auth(refresh_token=refresh_token)
        
        logger.info(f"Authenticated as user: {api.user_id}")
        logger.info("Fetching bookmarks...")
        
        json_result = api.user_bookmarks_illust(api.user_id, restrict="public")
        
        if not json_result or not json_result.get('illusts'):
            logger.error("Failed to fetch bookmarks")
            return None, None
        
        first_page_illusts = json_result.get('illusts', [])
        total_estimate = len(first_page_illusts) * MAX_PAGES_TO_FETCH
        
        logger.info(f"Estimated bookmarks: ~{total_estimate} (max {MAX_PAGES_TO_FETCH} pages)")
        
        all_illusts = []
        all_illusts.extend(first_page_illusts)
        
        pages_collected = 1
        next_url = json_result.get('next_url')
        
        while next_url and pages_collected < MAX_PAGES_TO_FETCH:
            await asyncio.sleep(PIXIV_REQUEST_DELAY)
            
            next_qs = api.parse_qs(next_url)
            json_result = api.user_bookmarks_illust(**next_qs)
            
            if not json_result:
                break
                
            illusts = json_result.get('illusts', [])
            if not illusts:
                break
            
            all_illusts.extend(illusts)
            pages_collected += 1
            
            if pages_collected % 10 == 0:
                logger.info(f"Progress: {pages_collected}/{MAX_PAGES_TO_FETCH} pages loaded")
            
            next_url = json_result.get('next_url')
        
        logger.info(f"Total collected: {len(all_illusts)} illustrations")
        
        if not all_illusts:
            logger.error("No illustrations found")
            return None, None
        
        random_illust = random.choice(all_illusts)
        
        logger.info(f"Selected artwork:")
        logger.info(f"  Title: {random_illust['title']}")
        logger.info(f"  Author: {random_illust['user']['name']}")
        logger.info(f"  ID: {random_illust['id']}")
        logger.info(f"  Bookmarks: {random_illust['total_bookmarks']}, Views: {random_illust['total_view']}")
        
        import re
        medium_url = random_illust['image_urls']['medium']
        img_url = re.sub(r'/c/\d+x\d+_\d+/', '/', medium_url)
        
        author = random_illust['user']['name']
        title = random_illust['title']
        artwork_url = f"https://www.pixiv.net/artworks/{random_illust['id']}"
        
        caption = f"<b>{author}</b> | <a href=\"{artwork_url}\">{title}</a>"
        
        return img_url, caption
        
    except Exception as e:
        logger.error(f"Failed to fetch artwork from Pixiv: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None, None

def is_quiet_hours(config):
    """Проверяет, не тихие ли сейчас часы"""
    quiet = config.get('quiet_hours', {})
    
    if not quiet.get('enabled', False):
        return False
    
    now = datetime.now(MOSCOW_TZ)
    current_hour = now.hour
    start = quiet.get('start_hour', 0)
    end = quiet.get('end_hour', 0)
    
    if start > end:
        return current_hour >= start or current_hour < end
    else:
        return start <= current_hour < end

async def post_random_art(config):
    """Публикует случайную картинку"""
    logger.info("=" * 60)
    logger.info(f"Starting new post - {datetime.now(MOSCOW_TZ).strftime('%Y-%m-%d %H:%M:%S')} MSK")
    logger.info("=" * 60)
    
    img_url, caption = await get_random_pixiv_art_safe(config['pixiv_refresh_token'])
    
    if img_url:
        thread_id = config.get('telegram_thread_id')
        
        post_time = await send_to_telegram(
            img_url, 
            caption, 
            config['telegram_bot_token'], 
            config['telegram_channel_id'],
            thread_id
        )
        if post_time:
            logger.info("Post completed successfully")
            return post_time
    else:
        logger.error("Failed to get image")
    
    return None

def format_time(seconds):
    """Форматирует секунды в читаемый вид"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if secs > 0 or not parts:
        parts.append(f"{secs}s")
    
    return " ".join(parts)

async def countdown_timer(total_seconds):
    """Показывает обратный отсчет до следующей публикации"""
    start_time = datetime.now(MOSCOW_TZ)
    target_time = start_time + timedelta(seconds=total_seconds)
    
    logger.info("=" * 60)
    logger.info(f"Next post in: {format_time(total_seconds)}")
    logger.info(f"Current time: {start_time.strftime('%H:%M:%S')} MSK")
    logger.info(f"Target time:  {target_time.strftime('%H:%M:%S')} MSK")
    logger.info("=" * 60)
    
    last_log = datetime.now(MOSCOW_TZ)
    update_interval = 60
    
    while total_seconds > 0:
        await asyncio.sleep(1)
        total_seconds -= 1
        now = datetime.now(MOSCOW_TZ)
        
        if (now - last_log).total_seconds() >= update_interval:
            logger.info(f"Time remaining: {format_time(total_seconds)}")
            last_log = now
        
        if 10 < total_seconds <= 60 and total_seconds % 10 == 0:
            logger.info(f"Time remaining: {format_time(total_seconds)}")
        
        if total_seconds <= 10:
            logger.info(f"Countdown: {total_seconds}s")
    
    logger.info("Timer expired, starting post...")

async def run_bot():
    """Основной цикл бота"""
    config = load_config()
    
    logger.info("=" * 60)
    logger.info("BOT STARTED")
    logger.info(f"Post interval: {config['interval_hours']}h {config['interval_minutes']}m")
    
    deviation = config.get('interval_deviation_minutes', 0)
    if deviation > 0:
        logger.info(f"Interval deviation: ±{deviation} minutes")
    else:
        logger.info(f"Interval deviation: disabled")
    
    logger.info(f"Channel: {config['telegram_channel_id']}")
    logger.info(f"Post on startup: {'enabled' if config['post_immediately_on_start'] else 'disabled'}")
    
    quiet = config.get('quiet_hours', {})
    if quiet.get('enabled'):
        logger.info(f"Quiet hours: {quiet['start_hour']}:00 - {quiet['end_hour']}:00")
    
    logger.info(f"Pixiv settings: max {MAX_PAGES_TO_FETCH} pages, {PIXIV_REQUEST_DELAY}s delay")
    logger.info("=" * 60)
    
    base_interval_seconds = config['interval_hours'] * 3600 + config['interval_minutes'] * 60
    deviation_minutes = config.get('interval_deviation_minutes', 0)
    
    if config['post_immediately_on_start'] and not is_quiet_hours(config):
        await post_random_art(config)
    
    while True:
        next_interval = calculate_next_interval(base_interval_seconds, deviation_minutes)
        
        await countdown_timer(next_interval)
        
        if is_quiet_hours(config):
            logger.info("QUIET HOURS - post skipped")
            logger.info(f"Next attempt in: {format_time(next_interval)}")
        else:
            await post_random_art(config)

async def main():
    """Точка входа"""
    try:
        await run_bot()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Critical error: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    asyncio.run(main())