import os
import json
import logging
import random
import aiohttp
import asyncio
import traceback
from typing import Optional, List, Dict
from datetime import datetime
from aiogram import Bot

# Настраиваем логирование
logger = logging.getLogger(__name__)

# Доступные модели Monica AI
MONICA_MODELS = {
    "gpt-4o": {
        "name": "GPT-4 Optimized",
        "description": "Оптимизированная версия GPT-4",
        "max_tokens": "8,000"
    },
    "claude-3-5-sonnet-20241022": {
        "name": "Claude 3.5 Sonnet", 
        "description": "Мощная модель с большим контекстом",
        "max_tokens": "200,000"
    },
    "claude-3-haiku-20240307": {
        "name": "Claude 3 Haiku",
        "description": "Быстрая и эффективная модель Claude 3",
        "max_tokens": "4,000"
    },
    "o1-mini": {
        "name": "O1 Mini",
        "description": "Компактная и быстрая модель",
        "max_tokens": "2,000"
    }
}

# Хранилище выбранных моделей пользователей
user_models: Dict[int, str] = {}

def get_available_models():
    """Получение списка доступных моделей"""
    return MONICA_MODELS

def get_user_model(user_id: int) -> str:
    """Получение модели пользователя или модели по умолчанию"""
    return user_models.get(user_id, "gpt-4o")

async def try_gpt_request(prompt: str, posts_text: str, user_id: int, bot: Bot, user_data: dict):
    """Запрос к Monica AI API"""
    status_message = None
    try:
        text_length = len(posts_text)
        selected_model = get_user_model(user_id)
        model_info = MONICA_MODELS[selected_model]
        
        # Отправляем сообщение о начале анализа
        status_message = await bot.send_message(
            user_id,
            f"🔄 Начинаю анализ...\n"
            f"Размер данных: {text_length} символов\n"
            f"Используем: Monica AI - {model_info['name']}"
        )
        
        # Загружаем API ключ из .env
        api_key = os.getenv("MONICA_API_KEY")
        if not api_key:
            error_msg = "❌ API ключ Monica не найден в .env файле"
            logger.error(error_msg)
            if status_message:
                await status_message.edit_text(error_msg)
            raise Exception(error_msg)
        
        # Подготавливаем запрос к Monica AI
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        
        # Подготавливаем сообщения
        messages = [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": "Ты мой личный ассистент для анализа данных. Ты всегда отвечаешь кратко и по делу, без лишних слов."
                    }
                ]
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"{prompt}\n\nДанные для анализа:\n{posts_text}"
                    }
                ]
            }
        ]
        
        data = {
            "model": selected_model,
            "messages": messages
        }
        
        if status_message:
            await status_message.edit_text(
                f"🔄 Отправляю запрос к Monica AI...\n"
                f"Модель: {model_info['name']}\n"
                f"Размер данных: {text_length} символов\n"
                f"Ожидаемое время ответа: может занять несколько минут"
            )
        
        # Логируем отправляемые данные для отладки
        logger.info(f"Отправляем запрос к Monica API, модель: {selected_model}, размер данных: {text_length}")
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://openapi.monica.im/v1/chat/completions",
                    headers=headers,
                    json=data,
                    timeout=None  # Убираем таймаут полностью
                ) as response:
                    response_text = await response.text()
                    logger.info(f"Получен ответ от Monica API, статус: {response.status}")
                    
                    if response.status == 200:
                        try:
                            result = json.loads(response_text)
                            # Извлекаем только текстовый ответ
                            response_text = result['choices'][0]['message']['content']
                            if status_message:
                                await status_message.delete()
                            return response_text
                        except (json.JSONDecodeError, KeyError, IndexError) as e:
                            error_msg = f"❌ Ошибка при обработке ответа от Monica AI: {str(e)}, ответ: {response_text[:200]}..."
                            logger.error(error_msg)
                            if status_message:
                                await status_message.edit_text(error_msg)
                            raise Exception(error_msg)
                    else:
                        error_msg = f"❌ Ошибка Monica API ({response.status}): {response_text[:200]}..."
                        logger.error(error_msg)
                        if status_message:
                            await status_message.edit_text(error_msg)
                        raise Exception(error_msg)
        except asyncio.TimeoutError:
            error_msg = f"❌ Превышено время ожидания ответа от Monica AI. Возможно, запрос слишком большой или сервер перегружен."
            logger.error(error_msg)
            if status_message:
                await status_message.edit_text(error_msg)
            raise Exception(error_msg)
        except aiohttp.ClientError as e:
            error_msg = f"❌ Ошибка соединения с Monica AI: {str(e) or 'Неизвестная ошибка соединения'}"
            logger.error(error_msg)
            if status_message:
                await status_message.edit_text(error_msg)
            raise Exception(error_msg)
                    
    except Exception as e:
        error_msg = f"❌ Неожиданная ошибка при запросе к Monica AI: {str(e) or 'Неизвестная ошибка'}"
        logger.error(error_msg)
        # Добавляем трассировку стека для более подробной информации
        logger.error(f"Трассировка ошибки: {traceback.format_exc()}")
        
        if status_message:
            await status_message.edit_text(error_msg)
        raise Exception(error_msg)

# Экспортируем для использования в других модулях
__all__ = [
    'try_gpt_request',
    'get_available_models',
    'get_user_model',
    'user_models',
    'MONICA_MODELS'
]