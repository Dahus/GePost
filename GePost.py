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
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('pixiv_bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Файл конфигурации
CONFIG_FILE = 'config.json'

def load_config():
    """Загружает конфигурацию из файла или переменных окружения"""
    # Пробуем загрузить из файла (для локальной разработки)
    script_dir = Path(__file__).parent
    config_path = script_dir / CONFIG_FILE
    
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        logger.info("Конфигурация загружена из файла")
        return config
    
    # Если файла нет - читаем из env (для Railway)
    logger.info("Файл config.json не найден, читаю из переменных окружения")
    
    config = {
        'pixiv_refresh_token': os.getenv('PIXIV_REFRESH_TOKEN'),
        'telegram_bot_token': os.getenv('TELEGRAM_BOT_TOKEN'),
        'telegram_channel_id': os.getenv('TELEGRAM_CHANNEL_ID'),
        'telegram_thread_id': os.getenv('TELEGRAM_THREAD_ID'),
        'interval_hours': int(os.getenv('INTERVAL_HOURS', 3)),
        'interval_minutes': int(os.getenv('INTERVAL_MINUTES', 0)),
        'post_immediately_on_start': os.getenv('POST_IMMEDIATELY_ON_START', 'false').lower() == 'true',
        'quiet_hours': {
            'enabled': os.getenv('QUIET_HOURS_ENABLED', 'false').lower() == 'true',
            'start_hour': int(os.getenv('QUIET_HOURS_START', 0)),
            'end_hour': int(os.getenv('QUIET_HOURS_END', 0))
        }
    }
    
    # Проверка обязательных параметров
    if not config['pixiv_refresh_token'] or not config['telegram_bot_token'] or not config['telegram_channel_id']:
        raise ValueError("Не заданы обязательные переменные окружения: PIXIV_REFRESH_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID")
    
    logger.info("Конфигурация загружена из переменных окружения")
    return config

async def get_last_post_time(bot_token, channel_id):
    """Получает время последнего поста в канале"""
    try:
        bot = Bot(token=bot_token)
        # Получаем последние сообщения из канала
        updates = await bot.get_updates(limit=100)
        
        # Ищем последнее сообщение в нужном канале
        channel_messages = [
            update.channel_post for update in updates 
            if update.channel_post and str(update.channel_post.chat.id) == str(channel_id)
        ]
        
        if channel_messages:
            last_message = max(channel_messages, key=lambda x: x.date)
            logger.info(f"Последний пост был: {last_message.date}")
            return last_message.date
        
        # Альтернативный метод: получаем информацию о канале
        chat = await bot.get_chat(channel_id)
        logger.info(f"Канал: {chat.title if hasattr(chat, 'title') else channel_id}")
        
        return None
    except Exception as e:
        logger.warning(f"Не удалось получить время последнего поста: {e}")
        return None

async def send_to_telegram(image_url, caption, bot_token, channel_id, thread_id=None):
    """Отправляет изображение и ссылку в Telegram канал"""
    bot = Bot(token=bot_token)
    
    try:
        # Параметры для отправки
        send_params = {
            'chat_id': channel_id,
            'photo': image_url,
            'caption': caption,
            'parse_mode': 'HTML'
        }
        
        # Если указан thread_id, добавляем его
        if thread_id:
            send_params['message_thread_id'] = thread_id
            logger.info(f"Отправка в топик: {thread_id}")
        
        message = await bot.send_photo(**send_params)
        logger.info(f"Отправлено в Telegram канал: {channel_id}")
        return message.date
    except Exception as e:
        logger.error(f"Ошибка отправки в Telegram: {e}")
        return None

def get_random_pixiv_art(refresh_token):
    """Получает случайную иллюстрацию из закладок Pixiv"""
    try:
        api = AppPixivAPI()
        api.auth(refresh_token=refresh_token)
        
        logger.info(f"Авторизован как: {api.user_id}")
        logger.info("Собираю информацию о страницах...")
        
        # Собираем все страницы
        pages_data = []
        json_result = api.user_bookmarks_illust(api.user_id, restrict="public")
        
        while json_result:
            illusts = json_result.get('illusts', [])
            if illusts:
                pages_data.append({
                    'illusts': illusts,
                    'page_num': len(pages_data) + 1
                })
            
            if len(pages_data) % 10 == 0:
                logger.info(f"   Собрано {len(pages_data)} страниц...")
            
            next_url = json_result.get('next_url')
            if not next_url:
                break
            
            next_qs = api.parse_qs(next_url)
            json_result = api.user_bookmarks_illust(**next_qs)
            
            if len(pages_data) >= 100:
                logger.info("   Достигнут лимит в 100 страниц")
                break
        
        logger.info(f"Всего собрано: {len(pages_data)} страниц ({len(pages_data) * 30} закладок)")
        
        if not pages_data:
            logger.error("Не удалось получить закладки")
            return None, None
        
        # Выбираем случайную иллюстрацию
        random_page_data = random.choice(pages_data)
        random_illust = random.choice(random_page_data['illusts'])
        
        logger.info(f"Выбрана страница: {random_page_data['page_num']}")
        logger.info(f"Случайная иллюстрация:")
        logger.info(f"   Название: {random_illust['title']}")
        logger.info(f"   Автор: {random_illust['user']['name']}")
        logger.info(f"   ID: {random_illust['id']}")
        logger.info(f"   Лайков: {random_illust['total_bookmarks']}")
        logger.info(f"   Просмотров: {random_illust['total_view']}")
        
        # Получаем URL изображения
        import re
        medium_url = random_illust['image_urls']['medium']
        img_url = re.sub(r'/c/\d+x\d+_\d+/', '/', medium_url)
        
        logger.info(f"URL изображения: {img_url}")
        
        # Формируем красивое описание для Telegram
        author = random_illust['user']['name']
        title = random_illust['title']
        artwork_url = f"https://www.pixiv.net/artworks/{random_illust['id']}"
        
        # Создаем caption с гиперссылкой, спрятанной в названии
        caption = f"<b>{author}</b> | <a href=\"{artwork_url}\">{title}</a>"
        
        return img_url, caption
        
    except Exception as e:
        logger.error(f"Ошибка при получении арта из Pixiv: {e}")
        return None, None

def is_quiet_hours(config):
    """Проверяет, не тихие ли сейчас часы"""
    quiet = config.get('quiet_hours', {})
    
    if not quiet.get('enabled', False):
        return False
    
    now = datetime.now(MOSCOW_TZ)  # МСК время!
    current_hour = now.hour
    start = quiet.get('start_hour', 0)
    end = quiet.get('end_hour', 0)
    
    # Если диапазон через полночь (например 23-5)
    if start > end:
        return current_hour >= start or current_hour < end
    # Обычный диапазон (например 1-5)
    else:
        return start <= current_hour < end

async def post_random_art(config):
    """Публикует случайную картинку"""
    logger.info(f"\n{'='*50}")
    logger.info(f"Начинаю новую публикацию - {datetime.now(MOSCOW_TZ).strftime('%Y-%m-%d %H:%M:%S')} МСК")
    logger.info(f"{'='*50}\n")
    
    img_url, caption = get_random_pixiv_art(config['pixiv_refresh_token'])
    
    if img_url:
        # Получаем thread_id из конфига, если он есть
        thread_id = config.get('telegram_thread_id')
        
        post_time = await send_to_telegram(
            img_url, 
            caption, 
            config['telegram_bot_token'], 
            config['telegram_channel_id'],
            thread_id
        )
        if post_time:
            logger.info("Публикация завершена успешно")
            return post_time
    else:
        logger.error("Не удалось получить изображение")
    
    return None

def format_time(seconds):
    """Форматирует секунды в читаемый вид"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    parts = []
    if hours > 0:
        parts.append(f"{hours}ч")
    if minutes > 0:
        parts.append(f"{minutes}м")
    if secs > 0 or not parts:
        parts.append(f"{secs}с")
    
    return " ".join(parts)

async def countdown_timer(total_seconds):
    """Показывает обратный отсчет до следующей публикации"""
    logger.info(f"\n{'='*50}")
    logger.info(f"ТАЙМЕР: Следующая публикация через {format_time(total_seconds)}")
    logger.info(f"{'='*50}")
    
    start_time = datetime.now(MOSCOW_TZ)
    target_time = start_time + timedelta(seconds=total_seconds)
    
    logger.info(f"Текущее время: {start_time.strftime('%H:%M:%S')} МСК")
    logger.info(f"Публикация в: {target_time.strftime('%H:%M:%S')} МСК")
    
    # Показываем обновления таймера
    last_log = datetime.now()
    update_interval = 60  # Обновление каждую минуту
    
    while total_seconds > 0:
        await asyncio.sleep(1)
        total_seconds -= 1
        now = datetime.now(MOSCOW_TZ)  # МСК время для логов
        
        # Обновляем каждую минуту
        if (now - last_log).total_seconds() >= update_interval:
            logger.info(f"[ТАЙМЕР] Осталось: {format_time(total_seconds)}")
            last_log = now
        
        # В последнюю минуту - каждые 10 секунд
        if 10 < total_seconds <= 60 and total_seconds % 10 == 0:
            logger.info(f"[ТАЙМЕР] Осталось: {format_time(total_seconds)}")
        
        # В последние 10 секунд - каждую секунду
        if total_seconds <= 10:
            logger.info(f"[ТАЙМЕР] {total_seconds} секунд...")
    
    logger.info(f"[ТАЙМЕР] Время вышло! Начинаю публикацию...\n")

async def run_bot():
    """Основной цикл бота"""
    config = load_config()
    
    logger.info("=" * 50)
    logger.info("БОТ ЗАПУЩЕН!")
    logger.info(f"Интервал публикаций: {config['interval_hours']}ч {config['interval_minutes']}м")
    logger.info(f"Канал: {config['telegram_channel_id']}")
    logger.info(f"Пост при запуске: {'ВКЛ' if config['post_immediately_on_start'] else 'ВЫКЛ'}")
    
    quiet = config.get('quiet_hours', {})
    if quiet.get('enabled'):
        logger.info(f"Тихие часы: {quiet['start_hour']}:00 - {quiet['end_hour']}:00")
    
    logger.info("=" * 50 + "\n")
    
    interval_seconds = config['interval_hours'] * 3600 + config['interval_minutes'] * 60
    
    # Постим сразу при запуске, если включен флаг и не тихие часы
    if config['post_immediately_on_start'] and not is_quiet_hours(config):
        await post_random_art(config)
    
    # Бесконечный цикл с интервалом
    while True:
        await countdown_timer(interval_seconds)
        
        # Проверяем тихие часы перед постом
        if is_quiet_hours(config):
            logger.info("⏸️  ТИХИЕ ЧАСЫ - публикация пропущена")
            logger.info(f"   Следующая попытка через {format_time(interval_seconds)}\n")
        else:
            await post_random_art(config)

async def main():
    """Точка входа"""
    try:
        await run_bot()
    except KeyboardInterrupt:
        logger.info("\nБот остановлен пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    asyncio.run(main())