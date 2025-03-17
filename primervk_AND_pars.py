import vk_api
import requests
from bs4 import BeautifulSoup
import logging
import asyncio
from typing import List, Dict, Optional

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class VKService:
    def __init__(self, token: str):
        """Инициализация сервиса ВКонтакте"""
        self.vk_session = vk_api.VkApi(token=token)
        self.vk = self.vk_session.get_api()
    
    def get_group_posts(self, group_id: str, count: int = 100) -> List[Dict]:
        """Получение постов из группы ВКонтакте"""
        try:
            # Убираем минус из ID группы если он есть
            group_id = group_id.lstrip('-')
            
            # Получаем посты
            posts = self.vk.wall.get(owner_id=f"-{group_id}", count=count)
            return posts['items']
        except Exception as e:
            logger.error(f"Ошибка при получении постов из ВК: {e}")
            return []

class WebParser:
    def __init__(self):
        """Инициализация парсера сайтов"""
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })

    def parse_website(self, url: str) -> str:
        """Парсинг контента с веб-сайта"""
        try:
            response = self.session.get(url)
            response.raise_for_status()
            
            # Парсим HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Удаляем ненужные элементы
            for tag in soup(['script', 'style']):
                tag.decompose()
            
            # Получаем текст
            text = soup.get_text(separator='\n', strip=True)
            return text
        except Exception as e:
            logger.error(f"Ошибка при парсинге сайта {url}: {e}")
            return ""

# Пример использования
async def example():
    # Инициализация сервисов
    vk_token = "vk1.a.Qmg-4o5lDmzl3vQZlbfjK0Uxl8lCUSWdj3bqonibDhtuf-ZgyGY6wY3lxmW3h157AVv4lrmT9GQTWrunvCnZUC6LyYx3lf6LjTUPoCEUg2dkrIUSEh353RjhyZNKkI_S0TohfTNKr8ZbPPFvRd9z9ULh8QW6x_eYNGmMh25mJwCQBLnyBDwLemvK1skmE16GgM45V08Je6XZm8pK0V1EPA"
    vk_service = VKService(vk_token)
    web_parser = WebParser()
    
    # Пример получения постов из группы ВК
    group_id = "1"  # ID группы ВКонтакте
    posts = vk_service.get_group_posts(group_id, count=5)
    print("\nПосты из ВК:")
    for post in posts:
        print(f"- {post.get('text', '')[:100]}...")
    
    # Пример парсинга веб-сайта
    url = "https://example.com"
    content = web_parser.parse_website(url)
    print(f"\nКонтент с сайта {url}:")
    print(content[:200])

if __name__ == "__main__":
    asyncio.run(example())
