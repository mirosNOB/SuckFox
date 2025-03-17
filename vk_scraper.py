import os
import logging
import aiohttp
import asyncio
import json
import re
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
import requests

# Настраиваем логирование
logger = logging.getLogger(__name__)

# Токен VK API
VK_TOKEN = "vk1.a.Qmg-4o5lDmzl3vQZlbfjK0Uxl8lCUSWdj3bqonibDhtuf-ZgyGY6wY3lxmW3h157AVv4lrmT9GQTWrunvCnZUC6LyYx3lf6LjTUPoCEUg2dkrIUSEh353RjhyZNKkI_S0TohfTNKr8ZbPPFvRd9z9ULh8QW6x_eYNGmMh25mJwCQBLnyBDwLemvK1skmE16GgM45V08Je6XZm8pK0V1EPA"

# Базовый URL для API ВКонтакте
VK_API_URL = "https://api.vk.com/method/"

# Заголовки для запросов к веб-сайтам
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}

# Проверка валидности ссылки ВКонтакте
def is_valid_vk_link(link: str) -> bool:
    """Проверяет, является ли ссылка действительной ссылкой на ресурс ВКонтакте."""
    patterns = [
        r'^https?://vk\.com/([a-zA-Z0-9_\.]+)$',
        r'^https?://m\.vk\.com/([a-zA-Z0-9_\.]+)$',
        r'^vk\.com/([a-zA-Z0-9_\.]+)$',
        r'^@([a-zA-Z0-9_\.]+)$',
        r'^([a-zA-Z0-9_\.]+)$'  # Просто ID или короткое имя
    ]
    
    for pattern in patterns:
        if re.match(pattern, link):
            return True
    return False

# Получение ID группы или пользователя по короткому имени
async def get_vk_id(screen_name: str) -> Optional[int]:
    """Получает ID группы или пользователя по короткому имени (screen_name)."""
    # Удаляем префиксы, если они есть
    if screen_name.startswith('https://'):
        screen_name = screen_name.split('/')[-1]
    elif screen_name.startswith('@'):
        screen_name = screen_name[1:]
    
    # Если это уже ID (число), возвращаем его
    if screen_name.isdigit():
        return int(screen_name)
    
    # Иначе запрашиваем ID через API
    async with aiohttp.ClientSession() as session:
        params = {
            "screen_name": screen_name,
            "access_token": VK_TOKEN,
            "v": "5.131"
        }
        async with session.get(f"{VK_API_URL}utils.resolveScreenName", params=params) as response:
            if response.status == 200:
                data = await response.json()
                if "response" in data and data["response"]:
                    obj_type = data["response"]["type"]
                    obj_id = data["response"]["object_id"]
                    # Для групп возвращаем отрицательный ID
                    if obj_type == "group":
                        return -obj_id
                    else:
                        return obj_id
            logger.error(f"Не удалось получить ID для {screen_name}: {await response.text()}")
            return None

# Получение постов из группы или со стены пользователя
async def get_vk_posts(source: str, hours: int = 24) -> List[Dict]:
    """Получает посты из указанного источника ВКонтакте за последние hours часов."""
    try:
        # Получаем ID источника
        source_id = await get_vk_id(source)
        if not source_id:
            logger.error(f"Не удалось определить ID источника: {source}")
            return []
        
        # Определяем метод API в зависимости от типа источника (группа или пользователь)
        method = "wall.get"
        owner_id = source_id
        
        # Вычисляем timestamp для фильтрации по времени
        current_time = int(time.time())
        time_threshold = current_time - (hours * 3600)
        
        # Параметры запроса
        params = {
            "owner_id": owner_id,
            "count": 100,  # Максимальное количество постов для получения
            "access_token": VK_TOKEN,
            "v": "5.131"
        }
        
        all_posts = []
        offset = 0
        max_requests = 5  # Ограничиваем количество запросов для избежания превышения лимитов API
        
        async with aiohttp.ClientSession() as session:
            for _ in range(max_requests):
                params["offset"] = offset
                async with session.get(f"{VK_API_URL}{method}", params=params) as response:
                    if response.status != 200:
                        logger.error(f"Ошибка при получении постов: {await response.text()}")
                        break
                    
                    data = await response.json()
                    if "error" in data:
                        logger.error(f"Ошибка API VK: {data['error']}")
                        break
                    
                    if "response" not in data or "items" not in data["response"]:
                        logger.error(f"Неожиданная структура ответа: {data}")
                        break
                    
                    items = data["response"]["items"]
                    if not items:
                        break
                    
                    # Фильтруем посты по времени
                    new_posts = [
                        {
                            "id": post["id"],
                            "date": datetime.fromtimestamp(post["date"]).strftime("%Y-%m-%d %H:%M:%S"),
                            "text": post.get("text", ""),
                            "link": f"https://vk.com/wall{owner_id}_{post['id']}"
                        }
                        for post in items
                        if post["date"] >= time_threshold and post.get("text")
                    ]
                    
                    all_posts.extend(new_posts)
                    
                    # Если получили меньше постов, чем запрашивали, значит это все
                    if len(items) < params["count"]:
                        break
                    
                    offset += len(items)
        
        return all_posts
    
    except Exception as e:
        logger.error(f"Ошибка при получении постов ВКонтакте: {str(e)}")
        return []

# Функции для парсинга веб-сайтов
async def parse_website(url: str) -> Optional[str]:
    """Парсит контент с указанного URL."""
    try:
        # Проверяем формат URL
        if not (url.startswith('http://') or url.startswith('https://')):
            url = 'https://' + url
        
        # Выполняем запрос
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        
        # Получаем контент страницы
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Удаляем ненужные элементы
        for script in soup(['script', 'style', 'head', 'footer', 'nav']):
            script.decompose()
        
        # Извлекаем основной текст
        text = soup.get_text(separator=' ', strip=True)
        
        # Очищаем текст от лишних пробелов
        text = re.sub(r'\s+', ' ', text)
        
        # Возвращаем результат
        return text
    
    except Exception as e:
        logger.error(f"Ошибка при парсинге сайта {url}: {str(e)}")
        return None

# Функция объединяющая все источники данных
async def get_content_from_sources(sources: List[str], hours: int = 24) -> List[Dict]:
    """
    Получает контент из различных источников (ВКонтакте, веб-сайты).
    
    Args:
        sources: Список источников (URL, ссылки ВКонтакте)
        hours: Количество часов, за которые нужно получить данные
        
    Returns:
        Список постов/контента из всех источников
    """
    all_content = []
    
    for source in sources:
        # Проверяем, является ли источник ссылкой на ВКонтакте
        if is_valid_vk_link(source):
            posts = await get_vk_posts(source, hours)
            all_content.extend(posts)
        else:
            # Пробуем распарсить как веб-сайт
            content = await parse_website(source)
            if content:
                all_content.append({
                    "id": f"web_{len(all_content)}",
                    "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "text": content,
                    "link": source
                })
    
    # Сортируем контент по дате (от новых к старым)
    all_content.sort(key=lambda x: x["date"], reverse=True)
    
    return all_content

# Экспортируем для использования в других модулях
__all__ = [
    'get_vk_posts',
    'parse_website',
    'get_content_from_sources',
    'is_valid_vk_link'
] 