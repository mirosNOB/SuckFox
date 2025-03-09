import os
import json
from datetime import datetime, timedelta
import asyncio
import logging
import re
import sqlite3
import pytz
import shutil
import tempfile
import time
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.functions.messages import GetHistoryRequest
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.errors import ChannelPrivateError, UsernameNotOccupiedError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import random
from fpdf import FPDF
from transliterate import translit
import platform
from ai_service import (
    try_gpt_request, 
    get_available_models,
    get_user_model,
    user_models,
    MONICA_MODELS,
    OPENROUTER_MODELS
)
import aiohttp
from typing import List, Optional

# Настраиваем логирование
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Загружаем переменные окружения
logger.info("Загружаем .env файл...")
load_dotenv()
token = os.getenv('BOT_TOKEN')
logger.info(f"Токен: {token}")

if not token:
    raise ValueError("BOT_TOKEN не найден в .env файле!")

def get_db_connection(max_attempts=5, retry_delay=1):
    """Получение соединения с базой данных с обработкой блокировки"""
    attempt = 0
    while attempt < max_attempts:
        try:
            conn = sqlite3.connect('bot.db', timeout=20)  # Увеличиваем timeout
            return conn
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e):
                attempt += 1
                if attempt < max_attempts:
                    time.sleep(retry_delay)
                    continue
            raise
    raise sqlite3.OperationalError("Could not acquire database lock after multiple attempts")

def init_db():
    """Инициализация базы данных"""
    conn = get_db_connection()
    c = conn.cursor()
    
    try:
        # Таблица для отчетов
        c.execute('''CREATE TABLE IF NOT EXISTS reports
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      user_id INTEGER,
                      folder TEXT,
                      content TEXT,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        # Таблица для расписания
        c.execute('''CREATE TABLE IF NOT EXISTS schedules
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      user_id INTEGER,
                      folder TEXT,
                      time TEXT,
                      is_active BOOLEAN DEFAULT 1)''')
        
        # Таблица для управления доступом
        c.execute('''CREATE TABLE IF NOT EXISTS access_control
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      user_id INTEGER,
                      is_admin BOOLEAN,
                      added_by INTEGER,
                      added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        conn.commit()
    finally:
        conn.close()

# Создаем планировщик (но не запускаем)
scheduler = AsyncIOScheduler(timezone=pytz.UTC)

# Декоратор для проверки доступа
def require_access(func):
    async def wrapper(message: types.Message, *args, **kwargs):
        if not is_user_allowed(message.from_user.id):
            await message.answer("⛔️ У вас нет доступа к боту. Обратитесь к администратору.")
            return
        # Удаляем raw_state и command из kwargs если они есть
        kwargs.pop('raw_state', None)
        kwargs.pop('command', None)
        return await func(message, *args, **kwargs)
    return wrapper

# Декоратор для проверки прав администратора
def require_admin(func):
    async def wrapper(message: types.Message, *args, **kwargs):
        if not is_user_admin(message.from_user.id):
            await message.answer("⛔️ Эта функция доступна только администраторам.")
            return
        # Удаляем raw_state и command из kwargs если они есть
        kwargs.pop('raw_state', None)
        kwargs.pop('command', None)
        return await func(message, *args, **kwargs)
    return wrapper

# Функции для управления доступом
def is_user_allowed(user_id: int) -> bool:
    """Проверяем, есть ли у пользователя доступ к боту"""
    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute('SELECT 1 FROM access_control WHERE user_id = ?', (user_id,))
        result = c.fetchone() is not None
        return result
    finally:
        conn.close()

def is_user_admin(user_id: int) -> bool:
    """Проверяем, является ли пользователь администратором"""
    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute('SELECT is_admin FROM access_control WHERE user_id = ?', (user_id,))
        result = c.fetchone()
        return result[0] if result else False
    finally:
        conn.close()

# Инициализируем клиенты
bot = Bot(token=token, timeout=20)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Инициализируем клиент Telethon
client = TelegramClient('telegram_session', int(os.getenv('API_ID')), os.getenv('API_HASH'))

# Структура для хранения данных
class UserData:
    def __init__(self):
        self.users = {}  # {user_id: {'folders': {}, 'prompts': {}, 'ai_settings': {}}}
        
    def get_user_data(self, user_id: int) -> dict:
        """Получаем или создаем данные пользователя"""
        if str(user_id) not in self.users:
            self.users[str(user_id)] = {
                'folders': {},
                'prompts': {},
                'ai_settings': {
                    'provider_index': 0,
                    'model': get_user_model(user_id)
                }
            }
        return self.users[str(user_id)]
        
    def save(self):
        with open('user_data.json', 'w', encoding='utf-8') as f:
            json.dump({'users': self.users}, f, ensure_ascii=False)
    
    @classmethod
    def load(cls):
        instance = cls()
        try:
            with open('user_data.json', 'r', encoding='utf-8') as f:
                data = json.load(f)
                instance.users = data.get('users', {})
        except FileNotFoundError:
            pass
        return instance

user_data = UserData.load()

# Состояния для FSM
class BotStates(StatesGroup):
    waiting_for_folder_name = State()
    waiting_for_channels = State()
    waiting_for_prompt = State()
    waiting_for_folder_to_edit = State()
    waiting_for_model_selection = State()
    waiting_for_schedule_folder = State()
    waiting_for_schedule_time = State()
    waiting_for_user_id = State()
    waiting_for_adding_user_type = State()

class AccessControlStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_user_id_remove = State()

def save_report(user_id: int, folder: str, content: str):
    """Сохраняем отчет в БД"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('INSERT INTO reports (user_id, folder, content) VALUES (?, ?, ?)',
              (user_id, folder, content))
    conn.commit()

def get_user_reports(user_id: int, limit: int = 10) -> list:
    """Получаем последние отчеты пользователя"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT folder, content, created_at FROM reports WHERE user_id = ? ORDER BY created_at DESC LIMIT ?',
              (user_id, limit))
    reports = c.fetchall()
    return reports

def save_schedule(user_id: int, folder: str, time: str):
    """Сохраняем расписание в БД"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('INSERT INTO schedules (user_id, folder, time) VALUES (?, ?, ?)',
              (user_id, folder, time))
    conn.commit()

def get_active_schedules() -> list:
    """Получаем все активные расписания"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT user_id, folder, time FROM schedules WHERE is_active = 1')
    schedules = c.fetchall()
    return schedules

def generate_txt_report(content: str, folder: str) -> str:
    """Генерирует отчет в формате TXT"""
    filename = f"analysis_{folder}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(content)
    return filename

# Определяем путь к шрифту в зависимости от ОС
def get_font_path():
    os_type = platform.system().lower()
    if os_type == 'linux':
        paths = [
            "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
            "/usr/share/fonts/TTF/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        ]
    elif os_type == 'windows':
        paths = [
            "C:\\Windows\\Fonts\\DejaVuSans.ttf",
            os.path.join(os.getenv('LOCALAPPDATA'), 'Microsoft\\Windows\\Fonts\\DejaVuSans.ttf'),
            "DejaVuSans.ttf"  # В текущей директории
        ]
    else:  # MacOS и другие
        paths = [
            "/Library/Fonts/DejaVuSans.ttf",
            "/System/Library/Fonts/DejaVuSans.ttf",
            "DejaVuSans.ttf"  # В текущей директории
        ]
    
    # Проверяем наличие файла
    for path in paths:
        if os.path.exists(path):
            return path
            
    # Если шрифт не найден - скачиваем
    logger.info("Шрифт не найден, скачиваю...")
    try:
        import requests
        url = "https://github.com/dejavu-fonts/dejavu-fonts/raw/master/ttf/DejaVuSans.ttf"
        response = requests.get(url)
        with open("DejaVuSans.ttf", "wb") as f:
            f.write(response.content)
        return "DejaVuSans.ttf"
    except Exception as e:
        logger.error(f"Не удалось скачать шрифт: {str(e)}")
        raise Exception("Не удалось найти или скачать шрифт DejaVuSans.ttf")

def generate_pdf_report(content: str, folder: str) -> str:
    """Генерирует отчет в формате PDF"""
    filename = f"analysis_{folder}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    
    # Создаем PDF
    pdf = FPDF()
    pdf.add_page()
    
    # Добавляем шрифт с поддержкой русского
    font_path = get_font_path()
    pdf.add_font('DejaVu', '', font_path, uni=True)
    pdf.set_font('DejaVu', '', 12)
    
    # Настраиваем отступы
    margin = 20
    pdf.set_margins(margin, margin, margin)
    pdf.set_auto_page_break(True, margin)
    
    # Пишем заголовок
    pdf.set_font_size(16)
    pdf.cell(0, 10, f'Анализ папки: {folder}', 0, 1, 'L')
    pdf.ln(10)
    
    # Возвращаемся к обычному размеру шрифта
    pdf.set_font_size(12)
    
    # Разбиваем контент на строки и обрабатываем форматирование
    for line in content.split('\n'):
        if not line.strip():  # Пропускаем пустые строки
            pdf.ln(5)
            continue
        
        if line.strip().startswith('###'):  # H3 заголовок
            pdf.set_font_size(14)
            pdf.cell(0, 10, line.strip().replace('###', '').strip(), 0, 1, 'L')
            pdf.set_font_size(12)
            pdf.ln(5)
        elif line.strip().startswith('####'):  # H4 заголовок
            pdf.set_font_size(13)
            pdf.cell(0, 10, line.strip().replace('####', '').strip(), 0, 1, 'L')
            pdf.set_font_size(12)
            pdf.ln(5)
        else:  # Обычный текст
            pdf.multi_cell(0, 10, line.strip())
            pdf.ln(5)
    
    # Сохраняем PDF
    try:
        pdf.output(filename, 'F')
    except Exception as e:
        logger.error(f"Ошибка при сохранении PDF: {str(e)}")
        # Пробуем сохранить с транслитерацией имени файла
        safe_filename = translit(filename, 'ru', reversed=True)
        pdf.output(safe_filename, 'F')
        os.rename(safe_filename, filename)  # Переименовываем обратно
    
    return filename

@dp.message_handler(commands=['start'])
@require_access
async def cmd_start(message: types.Message, state: FSMContext = None, **kwargs):
    me = await bot.get_me()
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    buttons = [
        "📁 Создать папку",
        "📋 Список папок",
        "✏️ Изменить промпт",
        "⚙️ Настройка ИИ",
        "🔄 Запустить анализ",
        "📊 История отчетов",
        "⏰ Настроить расписание"
    ]
    
    # Добавляем кнопки администратора
    if is_user_admin(message.from_user.id):
        buttons.extend([
            "👥 Управление доступом"
        ])
    
    keyboard.add(*buttons)
    await message.answer(
        f"Привет! Я бот для анализа Telegram каналов.\n"
        f"Мой юзернейм: @{me.username}\n"
        "Что хочешь сделать?",
        reply_markup=keyboard
    )

@dp.message_handler(commands=['init_admin'])
async def cmd_init_admin(message: types.Message):
    """Инициализация первого администратора"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM access_control')
    count = c.fetchone()[0]
    
    if count == 0:
        # Если нет пользователей, добавляем первого админа
        c.execute('INSERT INTO access_control (user_id, is_admin, added_by) VALUES (?, 1, ?)',
                 (message.from_user.id, message.from_user.id))
        conn.commit()
        await message.answer("✅ Вы успешно зарегистрированы как администратор!")
    else:
        await message.answer("❌ Администратор уже инициализирован")
    
    conn.close()

@dp.message_handler(lambda message: message.text == "👥 Управление доступом")
@require_admin
async def access_control_menu(message: types.Message, state: FSMContext = None, **kwargs):
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        types.InlineKeyboardButton("➕ Добавить пользователя", callback_data="add_user"),
        types.InlineKeyboardButton("➖ Удалить пользователя", callback_data="remove_user"),
        types.InlineKeyboardButton("📋 Список пользователей", callback_data="list_users")
    )
    await message.answer("Управление доступом к боту:", reply_markup=keyboard)

@dp.callback_query_handler(lambda c: c.data == "list_users")
async def list_users(callback_query: types.CallbackQuery):
    users = get_allowed_users(callback_query.from_user.id)
    if not users:
        await callback_query.message.answer("Список пользователей пуст")
        return
        
    text = "📋 Список пользователей:\n\n"
    for user_id, is_admin, added_at in users:
        dt = datetime.fromisoformat(added_at.replace('Z', '+00:00'))
        text += f"{'👑' if is_admin else '👤'} ID: {user_id}\n"
        text += f"Добавлен: {dt.strftime('%Y-%m-%d %H:%M')}\n\n"
    
    await callback_query.message.answer(text)

@dp.message_handler(lambda message: message.text == "📁 Создать папку")
async def create_folder(message: types.Message):
    await BotStates.waiting_for_folder_name.set()
    await message.answer("Введи название папки:")

@dp.message_handler(state=BotStates.waiting_for_folder_name)
async def process_folder_name(message: types.Message, state: FSMContext):
    folder_name = message.text
    await state.update_data(current_folder=folder_name)
    user_data.get_user_data(message.from_user.id)['folders'][folder_name] = []
    user_data.get_user_data(message.from_user.id)['prompts'][folder_name] = "Проанализируй посты и составь краткий отчет"
    user_data.save()
    
    await BotStates.waiting_for_channels.set()
    await message.answer(
        "Отправь ссылки на каналы для этой папки.\n"
        "Каждую ссылку с новой строки.\n"
        "Когда закончишь, напиши 'готово'"
    )

def is_valid_channel(channel_link: str) -> bool:
    """Проверяем, что ссылка похожа на канал"""
    return bool(re.match(r'^@[\w\d_]+$', channel_link))

@dp.message_handler(state=BotStates.waiting_for_channels)
async def process_channels(message: types.Message, state: FSMContext):
    if message.text.lower() == 'готово':
        await state.finish()
        await message.answer("Папка создана! Используй /folders чтобы увидеть список папок")
        return

    data = await state.get_data()
    folder_name = data['current_folder']
    
    channels = [ch.strip() for ch in message.text.split('\n')]
    valid_channels = []
    
    for channel in channels:
        if not is_valid_channel(channel):
            await message.answer(f"❌ Канал {channel} не похож на правильную ссылку. Используй формат @username")
            continue
        valid_channels.append(channel)
    
    if valid_channels:
        user_data.get_user_data(message.from_user.id)['folders'][folder_name].extend(valid_channels)
        user_data.save()
        await message.answer(f"✅ Каналы добавлены в папку {folder_name}")

@dp.message_handler(lambda message: message.text == "📋 Список папок")
@require_access
async def list_folders(message: types.Message, state: FSMContext = None):
    if not user_data.get_user_data(message.from_user.id)['folders']:
        await message.answer("Пока нет созданных папок")
        return

    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for folder in user_data.get_user_data(message.from_user.id)['folders']:
        keyboard.add(
            types.InlineKeyboardButton(
                f"📁 {folder}",
                callback_data=f"edit_folder_{folder}"
            )
        )
    
    await message.answer("Выберите папку для редактирования:", reply_markup=keyboard)

@dp.message_handler(commands=['folders'])
@require_access
async def cmd_list_folders(message: types.Message, state: FSMContext = None):
    await list_folders(message)

@dp.callback_query_handler(lambda c: c.data.startswith('edit_folder_'))
async def edit_folder_menu(callback_query: types.CallbackQuery):
    folder = callback_query.data.replace('edit_folder_', '')
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    
    # Добавляем кнопки для каждого канала
    channels = user_data.get_user_data(callback_query.from_user.id)['folders'][folder]
    for channel in channels:
        keyboard.add(
            types.InlineKeyboardButton(
                f"❌ {channel}",
                callback_data=f"remove_channel_{folder}_{channel}"  # Не убираем @ из канала
            )
        )
    
    # Добавляем основные кнопки управления
    keyboard.add(
        types.InlineKeyboardButton("➕ Добавить каналы", callback_data=f"add_channels_{folder}"),
        types.InlineKeyboardButton("❌ Удалить папку", callback_data=f"delete_folder_{folder}")
    )
    keyboard.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_folders"))
    
    await callback_query.message.edit_text(
        f"Редактирование папки {folder}:\n"
        f"Нажми на канал чтобы удалить его:\n" + 
        "\n".join(f"- {channel}" for channel in channels),
        reply_markup=keyboard
    )

@dp.callback_query_handler(lambda c: c.data.startswith('add_channels_'))
async def add_channels_start(callback_query: types.CallbackQuery, state: FSMContext):
    folder = callback_query.data.replace('add_channels_', '')
    await state.update_data(current_folder=folder)
    await BotStates.waiting_for_channels.set()
    
    await callback_query.message.answer(
        "Отправь ссылки на каналы для добавления.\n"
        "Каждую ссылку с новой строки.\n"
        "Когда закончишь, напиши 'готово'"
    )

@dp.callback_query_handler(lambda c: c.data.startswith('delete_folder_'))
async def delete_folder(callback_query: types.CallbackQuery):
    folder = callback_query.data.replace('delete_folder_', '')
    user = user_data.get_user_data(callback_query.from_user.id)
    
    if folder in user['folders']:
        del user['folders'][folder]
        del user['prompts'][folder]
        user_data.save()
        
        await callback_query.message.edit_text(f"✅ Папка {folder} удалена")
        
@dp.callback_query_handler(lambda c: c.data == "back_to_folders")
async def back_to_folders(callback_query: types.CallbackQuery):
    await callback_query.message.delete()  # Удаляем сообщение с инлайн клавиатурой
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    buttons = [
        "📁 Создать папку",
        "📋 Список папок",
        "✏️ Изменить промпт",
        "⚙️ Настройка ИИ",
        "🔄 Запустить анализ",
        "📊 История отчетов",
        "⏰ Настроить расписание"
    ]
    keyboard.add(*buttons)
    await callback_query.message.answer("Главное меню:", reply_markup=keyboard)

@dp.message_handler(lambda message: message.text == "✏️ Изменить промпт")
async def edit_prompt_start(message: types.Message):
    if not user_data.get_user_data(message.from_user.id)['folders']:
        await message.answer("Сначала создай хотя бы одну папку!")
        return

    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for folder in user_data.get_user_data(message.from_user.id)['folders']:
        keyboard.add(folder)
    keyboard.add("🔙 Назад")
    
    await BotStates.waiting_for_folder_to_edit.set()
    await message.answer("Выбери папку для изменения промпта:", reply_markup=keyboard)

@dp.message_handler(state=BotStates.waiting_for_folder_to_edit)
async def process_folder_selection(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад":
        await state.finish()
        await back_to_main_menu(message, state)
        return

    if message.text not in user_data.get_user_data(message.from_user.id)['folders']:
        await message.answer("Такой папки нет. Попробуй еще раз")
        return

    await state.update_data(selected_folder=message.text)
    await BotStates.waiting_for_prompt.set()
    await message.answer(
        f"Текущий промпт для папки {message.text}:\n"
        f"{user_data.get_user_data(message.from_user.id)['prompts'][message.text]}\n\n"
        "Введи новый промпт:"
    )

@dp.message_handler(state=BotStates.waiting_for_prompt)
async def process_new_prompt(message: types.Message, state: FSMContext):
    data = await state.get_data()
    folder = data['selected_folder']
    
    user_data.get_user_data(message.from_user.id)['prompts'][folder] = message.text
    user_data.save()
    
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    buttons = [
        "📁 Создать папку",
        "📋 Список папок",
        "✏️ Изменить промпт",
        "⚙️ Настройка ИИ",
        "🔄 Запустить анализ",
        "📊 История отчетов",
        "⏰ Настроить расписание"
    ]
    keyboard.add(*buttons)
    
    await state.finish()
    await message.answer(
        f"Промпт для папки {folder} обновлен!",
        reply_markup=keyboard
    )

@dp.message_handler(lambda message: message.text == "⚙️ Настройка ИИ")
async def ai_settings(message: types.Message, state: FSMContext = None, **kwargs):
    # Получаем текущую модель пользователя
    current_model = get_user_model(message.from_user.id)
    all_models = get_available_models()
    model_info = all_models[current_model]
    
    # Определяем сервис модели
    service = "Monica AI"
    if current_model in OPENROUTER_MODELS:
        service = "OpenRouter"
    
    # Создаем клавиатуру
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("📝 Выбрать модель", callback_data="choose_model"))
    
    await message.answer(
        f"📊 Текущие настройки ИИ:\n\n"
        f"🔹 Модель: {model_info['name']}\n"
        f"🔧 Сервис: {service}\n"
        f"📝 Описание: {model_info['description']}\n"
        f"📊 Макс. токенов: {model_info['max_tokens']}\n\n"
        f"ℹ️ Выберите, что хотите настроить:",
        reply_markup=keyboard
    )

@dp.callback_query_handler(lambda c: c.data == "choose_model")
async def show_models(callback_query: types.CallbackQuery, state: FSMContext = None):
    # Получаем текущую модель
    current_model = get_user_model(callback_query.from_user.id)
    all_models = get_available_models()
    
    # Создаем клавиатуру для выбора модели
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    
    # Добавляем заголовок для моделей Monica AI
    keyboard.add(
        types.InlineKeyboardButton(
            "--- MONICA AI МОДЕЛИ ---",
            callback_data="no_action"
        )
    )
    
    # Добавляем модели Monica AI
    for model_id, model_info in MONICA_MODELS.items():
        keyboard.add(
            types.InlineKeyboardButton(
                f"{'✅ ' if model_id == current_model else ''}{model_info['name']}",
                callback_data=f"select_model_{model_id}"
            )
        )
    
    # Добавляем заголовок для моделей OpenRouter
    keyboard.add(
        types.InlineKeyboardButton(
            "--- OPENROUTER МОДЕЛИ ---",
            callback_data="no_action"
        )
    )
    
    # Добавляем модели OpenRouter
    for model_id, model_info in OPENROUTER_MODELS.items():
        keyboard.add(
            types.InlineKeyboardButton(
                f"{'✅ ' if model_id == current_model else ''}{model_info['name']}",
                callback_data=f"select_model_{model_id}"
            )
        )
    
    keyboard.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_settings"))
    
    await callback_query.message.edit_text(
        f"Текущая модель: {all_models[current_model]['name']}\n\n"
        f"Выберите новую модель из списка:",
        reply_markup=keyboard
    )

@dp.callback_query_handler(lambda c: c.data.startswith("select_model_"))
async def process_model_selection(callback_query: types.CallbackQuery, state: FSMContext = None):
    # Получаем выбранную модель из callback_data
    selected_model = callback_query.data.replace("select_model_", "")
    
    # Обновляем модель пользователя
    user_models[callback_query.from_user.id] = selected_model
    all_models = get_available_models()
    model_info = all_models[selected_model]
    
    # Определяем сервис модели
    service = "Monica AI"
    if selected_model in OPENROUTER_MODELS:
        service = "OpenRouter"
    
    # Создаем клавиатуру
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("📝 Выбрать модель", callback_data="choose_model"))
    
    # Отправляем подтверждение
    await callback_query.message.edit_text(
        f"📊 Текущие настройки ИИ:\n\n"
        f"✅ Модель успешно изменена!\n\n"
        f"🔹 Модель: {model_info['name']}\n"
        f"🔧 Сервис: {service}\n"
        f"📝 Описание: {model_info['description']}\n"
        f"📊 Макс. токенов: {model_info['max_tokens']}\n\n"
        f"ℹ️ Выберите, что хотите настроить:",
        reply_markup=keyboard
    )
    
    await callback_query.answer("✅ Модель успешно изменена!")

@dp.callback_query_handler(lambda c: c.data == "back_to_settings")
async def back_to_settings(callback_query: types.CallbackQuery, state: FSMContext = None):
    await ai_settings(callback_query.message, state)

@dp.callback_query_handler(lambda c: c.data == "no_action")
async def no_action(callback_query: types.CallbackQuery):
    # Просто отвечаем на callback_query, чтобы убрать часы загрузки
    await callback_query.answer()

async def get_channel_posts(channel_link: str, hours: int = 24) -> list:
    """Получаем посты из канала за последние hours часов"""
    try:
        logger.info(f"Получаю посты из канала {channel_link}")
        
        if not is_valid_channel(channel_link):
            logger.error(f"Невалидная ссылка на канал: {channel_link}")
            return []
            
        try:
            # Пытаемся присоединиться к каналу
            channel = await client.get_entity(channel_link)
            try:
                await client(JoinChannelRequest(channel))
                logger.info(f"Успешно присоединился к каналу {channel_link}")
            except Exception as e:
                logger.warning(f"Не удалось присоединиться к каналу {channel_link}: {str(e)}")
                # Продолжаем работу, возможно мы уже подписаны
        except (ChannelPrivateError, UsernameNotOccupiedError) as e:
            logger.error(f"Не удалось получить доступ к каналу {channel_link}: {str(e)}")
            return []
        
        # Получаем историю сообщений
        posts = []
        time_threshold = datetime.now(channel.date.tzinfo) - timedelta(hours=hours)
        
        async for message in client.iter_messages(channel, limit=None):
            if message.date < time_threshold:
                break
                
            if message.text and len(message.text.strip()) > 0:
                posts.append({
                    'text': message.text,
                    'date': message.date.strftime('%Y-%m-%d %H:%M:%S')
                })
        
        logger.info(f"Получено {len(posts)} постов из канала {channel_link}")
        return posts
        
    except Exception as e:
        logger.error(f"Ошибка при получении постов из канала {channel_link}: {str(e)}")
        return []

@dp.message_handler(lambda message: message.text == "📊 История отчетов")
async def show_reports(message: types.Message):
    reports = get_user_reports(message.from_user.id)
    if not reports:
        await message.answer("У вас пока нет сохраненных отчетов")
        return
        
    text = "📊 Последние отчеты:\n\n"
    for folder, content, created_at in reports:
        dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        text += f"📁 {folder} ({dt.strftime('%Y-%m-%d %H:%M')})\n"
        
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for folder, _, _ in reports:
        keyboard.add(types.InlineKeyboardButton(
            f"📄 Отчет по {folder}",
            callback_data=f"report_{folder}"
        ))
        
    await message.answer(text, reply_markup=keyboard)

@dp.callback_query_handler(lambda c: c.data.startswith('report_'))
async def show_report_content(callback_query: types.CallbackQuery):
    folder = callback_query.data.replace('report_', '')
    reports = get_user_reports(callback_query.from_user.id)
    
    for rep_folder, content, created_at in reports:
        if rep_folder == folder:
            dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            await callback_query.message.answer(
                f"📊 Отчет по папке {folder}\n"
                f"📅 {dt.strftime('%Y-%m-%d %H:%M')}\n\n"
                f"{content}"
            )
            break

@dp.message_handler(lambda message: message.text == "⏰ Настроить расписание")
async def setup_schedule_start(message: types.Message):
    user = user_data.get_user_data(message.from_user.id)
    if not user['folders']:
        await message.answer("Сначала создайте хотя бы одну папку!")
        return
        
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for folder in user['folders']:
        keyboard.add(folder)
    keyboard.add("🔙 Назад")
    
    await BotStates.waiting_for_schedule_folder.set()
    await message.answer(
        "Выберите папку для настройки расписания:",
        reply_markup=keyboard
    )

@dp.message_handler(state=BotStates.waiting_for_schedule_folder)
async def process_schedule_folder(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад":
        await state.finish()
        await back_to_main_menu(message, state)
        return
        
    user = user_data.get_user_data(message.from_user.id)
    if message.text not in user['folders']:
        await message.answer("Такой папки нет. Попробуйте еще раз")
        return
        
    await state.update_data(schedule_folder=message.text)
    await BotStates.waiting_for_schedule_time.set()
    
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add("🔙 Назад")
    
    await message.answer(
        "Введите время для ежедневного анализа в формате HH:MM (например, 09:00):",
        reply_markup=keyboard
    )

@dp.message_handler(state=BotStates.waiting_for_schedule_time)
async def process_schedule_time(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад":
        await state.finish()
        await back_to_main_menu(message, state)
        return

    if not re.match(r'^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$', message.text):
        await message.answer("Неверный формат времени. Используйте формат HH:MM (например, 09:00)")
        return
        
    data = await state.get_data()
    folder = data['schedule_folder']
    
    # Сохраняем расписание
    save_schedule(message.from_user.id, folder, message.text)
    
    # Добавляем задачу в планировщик
    hour, minute = map(int, message.text.split(':'))
    job_id = f"analysis_{message.from_user.id}_{folder}"
    
    scheduler.add_job(
        run_scheduled_analysis,
        'cron',
        hour=hour,
        minute=minute,
        id=job_id,
        replace_existing=True,
        args=[message.from_user.id, folder]
    )
    
    await state.finish()
    await message.answer(
        f"✅ Расписание установлено! Папка {folder} будет анализироваться ежедневно в {message.text}",
        reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add(*[
            "📁 Создать папку",
            "📋 Список папок",
            "✏️ Изменить промпт",
            "⚙️ Настройка ИИ",
            "🔄 Запустить анализ",
            "📊 История отчетов",
            "⏰ Настроить расписание"
        ])
    )

async def run_scheduled_analysis(user_id: int, folder: str):
    """Запуск анализа по расписанию"""
    try:
        user = user_data.get_user_data(user_id)
        channels = user['folders'][folder]
        
        all_posts = []
        for channel in channels:
            if not is_valid_channel(channel):
                continue
                
            posts = await get_channel_posts(channel)
            if posts:
                all_posts.extend(posts)
                
        if not all_posts:
            logger.error(f"Не удалось получить посты для автоматического анализа папки {folder}")
            return
            
        posts_text = "\n\n---\n\n".join([
            f"[{post['date']}]\n{post['text']}" for post in all_posts
        ])
        prompt = user['prompts'][folder]
        
        response = await try_gpt_request(prompt, posts_text, user_id, bot, user_data)
        
        # Сохраняем отчет
        save_report(user_id, folder, response)
        
        # Логируем успешное завершение отчета
        logger.info("отчет удался")
        
        # Отправляем уведомление пользователю
        await bot.send_message(
            user_id,
            f"✅ Автоматический анализ папки {folder} завершен!\n"
            f"Используйте '📊 История отчетов' чтобы просмотреть результат."
        )
        
    except Exception as e:
        error_msg = f"❌ Ошибка при автоматическом анализе: {str(e)}"
        logger.error(error_msg)
        await message.answer(error_msg)

@dp.message_handler(lambda message: message.text == "🔄 Запустить анализ")
async def start_analysis(message: types.Message):
    user = user_data.get_user_data(message.from_user.id)
    if not user['folders']:
        await message.answer("Сначала создайте хотя бы одну папку!")
        return
        
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    
    # Добавляем кнопки для каждой папки
    for folder in user['folders']:
        keyboard.add(types.InlineKeyboardButton(
            f"📁 {folder}",
            callback_data=f"format_{folder}"
        ))
    
    # Добавляем кнопку "Анализировать все" и "Назад"
    keyboard.add(types.InlineKeyboardButton(
        "📊 Анализировать все папки",
        callback_data="format_all"
    ))
    keyboard.add(types.InlineKeyboardButton(
        "🔙 В главное меню",
        callback_data="back_to_main"
    ))
    
    await message.answer(
        "Выберите папку для анализа:",
        reply_markup=keyboard
    )

@dp.callback_query_handler(lambda c: c.data.startswith('format_'))
async def choose_format(callback_query: types.CallbackQuery):
    # Проверяем, содержит ли callback_data уже выбранный формат
    if '_txt' in callback_query.data or '_pdf' in callback_query.data:
        # Если формат уже выбран, передаем управление следующему обработчику
        await choose_period(callback_query)
        return
        
    folder = callback_query.data.replace('format_', '')
    
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    # Добавляем кнопки выбора формата
    keyboard.add(
        types.InlineKeyboardButton("📝 TXT", callback_data=f"period_{folder}_txt"),
        types.InlineKeyboardButton("📄 PDF", callback_data=f"period_{folder}_pdf")
    )
    keyboard.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_folders"))
    
    await callback_query.message.edit_text(
        f"Выберите формат отчета для {'всех папок' if folder == 'all' else f'папки {folder}'}:",
        reply_markup=keyboard
    )

@dp.callback_query_handler(lambda c: c.data.startswith('period_'))
async def choose_period(callback_query: types.CallbackQuery):
    # Парсим параметры из callback_data
    parts = callback_query.data.split('_')
    folder = parts[1]
    report_format = parts[2]  # txt или pdf
    
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    
    # Добавляем кнопки выбора периода
    periods = [
        ("24 часа", "24"),
        ("3 дня", "72")
    ]
    
    for period_name, hours in periods:
        if folder == 'all':
            keyboard.add(types.InlineKeyboardButton(
                f"📅 {period_name}",
                callback_data=f"analyze_all_{hours}_{report_format}"
            ))
        else:
            keyboard.add(types.InlineKeyboardButton(
                f"📅 {period_name}",
                callback_data=f"analyze_{folder}_{hours}_{report_format}"
            ))
    
    keyboard.add(types.InlineKeyboardButton("🔙 Назад", callback_data=f"format_{folder}"))
    
    await callback_query.message.edit_text(
        f"Выберите период анализа для {'всех папок' if folder == 'all' else f'папки {folder}'}:",
        reply_markup=keyboard
    )

@dp.callback_query_handler(lambda c: c.data.startswith('analyze_'))
async def process_analysis_choice(callback_query: types.CallbackQuery):
    # Парсим параметры из callback_data
    params = callback_query.data.replace('analyze_', '').split('_')
    if len(params) != 3:  # folder_hours_format
        await callback_query.message.answer("❌ Ошибка в параметрах анализа")
        return
        
    choice, hours, report_format = params
    hours = int(hours)
    user = user_data.get_user_data(callback_query.from_user.id)
    
    await callback_query.message.edit_text("Начинаю анализ... Это может занять некоторое время")
    
    if choice == 'all':
        folders = user['folders'].items()
    else:
        folders = [(choice, user['folders'][choice])]
    
    for folder, channels in folders:
        await callback_query.message.answer(f"Анализирую папку {folder}...")
        
        all_posts = []
        for channel in channels:
            if not is_valid_channel(channel):
                continue
                
            posts = await get_channel_posts(channel, hours=hours)
            if posts:
                all_posts.extend(posts)
            else:
                await callback_query.message.answer(f"⚠️ Не удалось получить посты из канала {channel}")
        
        if not all_posts:
            await callback_query.message.answer(f"❌ Не удалось получить посты из каналов в папке {folder}")
            continue
            
        # Сортируем посты по дате
        all_posts.sort(key=lambda x: x['date'], reverse=True)
        posts_text = "\n\n---\n\n".join([
            f"[{post['date']}]\n{post['text']}" for post in all_posts
        ])
        
        prompt = user['prompts'][folder]
        
        try:
            response = await try_gpt_request(prompt, posts_text, callback_query.from_user.id, bot, user_data)
            
            # Сохраняем отчет в БД
            save_report(callback_query.from_user.id, folder, response)
            
            # Генерируем отчет в выбранном формате
            if report_format == 'txt':
                filename = generate_txt_report(response, folder)
            else:  # pdf
                try:
                    filename = generate_pdf_report(response, folder)
                except Exception as pdf_error:
                    logger.error(f"Ошибка при создании PDF: {str(pdf_error)}")
                    await callback_query.message.answer("❌ Не удалось создать PDF версию отчета")
                    continue
            
            # Отправляем файл
            with open(filename, 'rb') as f:
                await callback_query.message.answer_document(
                    f,
                    caption=f"✅ Анализ для папки {folder} ({report_format.upper()})"
                )
            os.remove(filename)
            
        except Exception as e:
            error_msg = f"❌ Ошибка при анализе папки {folder}: {str(e)}"
            logger.error(error_msg)
            await callback_query.message.answer(error_msg)
    
    await callback_query.message.answer("✅ Анализ завершен!")

@dp.message_handler(lambda message: message.text == "🔙 Назад", state="*")
async def back_to_main_menu(message: types.Message, state: FSMContext):
    await state.finish()
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    buttons = [
        "📁 Создать папку",
        "📋 Список папок",
        "✏️ Изменить промпт",
        "⚙️ Настройка ИИ",
        "🔄 Запустить анализ",
        "📊 История отчетов",
        "⏰ Настроить расписание"
    ]
    await message.answer("Главное меню:", reply_markup=keyboard)

@dp.callback_query_handler(lambda c: c.data.startswith('remove_channel_'))
async def remove_channel(callback_query: types.CallbackQuery):
    try:
        # Парсим данные из callback
        parts = callback_query.data.split('_')
        if len(parts) < 4:  # remove_channel_folder_channelname
            logger.error(f"Неверный формат callback_data: {callback_query.data}")
            await callback_query.answer("❌ Ошибка формата данных")
            return
            
        folder = parts[2]  # Третий элемент - имя папки
        channel = '_'.join(parts[3:])  # Все остальное - имя канала
        
        # Проверяем не является ли это кнопкой отмены
        if "отмена" in folder.lower() or "отмена" in channel.lower():
            await callback_query.answer("Отменено")
            return
            
        user = user_data.get_user_data(callback_query.from_user.id)
        
        logger.info(f"Попытка удаления канала {channel} из папки {folder}")
        logger.info(f"Доступные папки: {list(user['folders'].keys())}")
        logger.info(f"Каналы в папке {folder}: {user['folders'].get(folder, [])}")
        
        if folder not in user['folders']:
            logger.error(f"Папка {folder} не найдена")
            await callback_query.answer("❌ Папка не найдена")
            return
            
        if channel not in user['folders'][folder]:
            logger.error(f"Канал {channel} не найден в папке {folder}")
            await callback_query.answer("❌ Канал не найден в папке")
            return
            
        # Удаляем канал
        user['folders'][folder].remove(channel)
        user_data.save()
        
        logger.info(f"Канал {channel} успешно удален из папки {folder}")
        
        # Обновляем клавиатуру
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        
        # Добавляем оставшиеся каналы
        for ch in user['folders'][folder]:
            keyboard.add(
                types.InlineKeyboardButton(
                    f"❌ {ch}",
                    callback_data=f"remove_channel_{folder}_{ch}"
                )
            )
        
        # Добавляем кнопки управления
        keyboard.add(
            types.InlineKeyboardButton("➕ Добавить каналы", callback_data=f"add_channels_{folder}"),
            types.InlineKeyboardButton("❌ Удалить папку", callback_data=f"delete_folder_{folder}")
        )
        keyboard.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_folders"))
        
        # Обновляем сообщение
        await callback_query.message.edit_text(
            f"Редактирование папки {folder}:\n"
            f"Нажми на канал чтобы удалить его:\n" + 
            "\n".join(f"- {ch}" for ch in user['folders'][folder]),
            reply_markup=keyboard
        )
        
        await callback_query.answer("✅ Канал удален")
        
    except Exception as e:
        logger.error(f"Ошибка при удалении канала: {str(e)}")
        await callback_query.answer("❌ Произошла ошибка при удалении канала")

def add_user_access(admin_id: int, user_id: int, is_admin: bool = False) -> bool:
    """Добавляем пользователя в список разрешенных"""
    if not is_user_admin(admin_id):
        return False
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('INSERT INTO access_control (user_id, is_admin, added_by) VALUES (?, ?, ?)',
                 (user_id, is_admin, admin_id))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        return False

def remove_user_access(admin_id: int, user_id: int) -> bool:
    """Удаляем пользователя из списка разрешенных"""
    if not is_user_admin(admin_id):
        return False
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('DELETE FROM access_control WHERE user_id = ? AND user_id != ?', (user_id, admin_id))
    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected > 0

def get_allowed_users(admin_id: int) -> list:
    """Получаем список разрешенных пользователей"""
    if not is_user_admin(admin_id):
        return []
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT user_id, is_admin, added_at FROM access_control')
    users = c.fetchall()
    conn.close()
    return users

@dp.callback_query_handler(lambda c: c.data == "add_user")
async def add_user_start(callback_query: types.CallbackQuery):
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("👤 Обычный пользователь", callback_data="add_regular_user"),
        types.InlineKeyboardButton("👑 Администратор", callback_data="add_admin_user"),
        types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_access_control")
    )
    await callback_query.message.edit_text(
        "Выберите тип пользователя для добавления:",
        reply_markup=keyboard
    )

@dp.callback_query_handler(lambda c: c.data in ["add_regular_user", "add_admin_user"])
async def process_user_type(callback_query: types.CallbackQuery, state: FSMContext):
    user_type = "admin" if callback_query.data == "add_admin_user" else "regular"
    await state.update_data(adding_user_type=user_type)
    await BotStates.waiting_for_user_id.set()
    
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add("🔙 Отмена")
    
    await callback_query.message.edit_text(
        "Введите ID пользователя для добавления.\n"
        "ID можно получить, если пользователь перешлет сообщение от @userinfobot"
    )

@dp.message_handler(state=BotStates.waiting_for_user_id)
async def process_add_user(message: types.Message, state: FSMContext):
    if message.text == "🔙 Отмена":
        await state.finish()
        await back_to_main_menu(message, state)
        return
        
    try:
        user_id = int(message.text)
        data = await state.get_data()
        is_admin = data.get('adding_user_type') == 'admin'
        
        if add_user_access(message.from_user.id, user_id, is_admin):
            keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
            buttons = [
                "📁 Создать папку",
                "📋 Список папок",
                "✏️ Изменить промпт",
                "⚙️ Настройка ИИ",
                "🔄 Запустить анализ",
                "📊 История отчетов",
                "⏰ Настроить расписание",
                "👥 Управление доступом"
            ]
            keyboard.add(*buttons)
            
            await message.answer(
                f"✅ Пользователь {user_id} успешно добавлен как "
                f"{'администратор' if is_admin else 'пользователь'}!",
                reply_markup=keyboard
            )
        else:
            await message.answer("❌ Не удалось добавить пользователя. Возможно, он уже добавлен.")
    except ValueError:
        await message.answer("❌ Неверный формат ID. Введите числовой ID пользователя.")
    finally:
        await state.finish()

@dp.callback_query_handler(lambda c: c.data == "remove_user")
async def remove_user_start(callback_query: types.CallbackQuery):
    users = get_allowed_users(callback_query.from_user.id)
    if not users:
        await callback_query.message.answer("Список пользователей пуст")
        return
        
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for user_id, is_admin, _ in users:
        if user_id != callback_query.from_user.id:  # Не даем удалить самого себя
            keyboard.add(types.InlineKeyboardButton(
                f"{'👑' if is_admin else '👤'} {user_id}",
                callback_data=f"remove_user_{user_id}"
            ))
    keyboard.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_access_control"))
    
    await callback_query.message.edit_text(
        "Выберите пользователя для удаления:",
        reply_markup=keyboard
    )

@dp.callback_query_handler(lambda c: c.data.startswith("remove_user_"))
async def process_remove_user(callback_query: types.CallbackQuery):
    user_id = int(callback_query.data.replace("remove_user_", ""))
    if remove_user_access(callback_query.from_user.id, user_id):
        await callback_query.message.edit_text(f"✅ Пользователь {user_id} удален")
    else:
        await callback_query.message.edit_text("❌ Не удалось удалить пользователя")

@dp.callback_query_handler(lambda c: c.data == "back_to_access_control")
async def back_to_access_control(callback_query: types.CallbackQuery):
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        types.InlineKeyboardButton("➕ Добавить пользователя", callback_data="add_user"),
        types.InlineKeyboardButton("➖ Удалить пользователя", callback_data="remove_user"),
        types.InlineKeyboardButton("📋 Список пользователей", callback_data="list_users")
    )
    await callback_query.message.edit_text(
        "Управление доступом к боту:",
        reply_markup=keyboard
    )

async def get_free_proxies() -> List[str]:
    """Получение списка бесплатных прокси"""
    proxies = []
    
    # Список API с бесплатными прокси
    proxy_apis = [
        "https://proxyfreeonly.com/api/free-proxy-list?limit=500&page=1&sortBy=lastChecked&sortType=desc",
        "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
        "https://www.proxy-list.download/api/v1/get?type=http"
    ]
    
    async with aiohttp.ClientSession() as session:
        for api in proxy_apis:
            try:
                async with session.get(api, timeout=10) as response:
                    if response.status == 200:
                        if 'proxyfreeonly.com' in api:
                            # Специальная обработка для proxyfreeonly.com
                            data = await response.json()
                            for proxy in data:
                                if proxy.get('protocols') and proxy.get('ip') and proxy.get('port'):
                                    for protocol in proxy['protocols']:
                                        proxy_str = f"{protocol}://{proxy['ip']}:{proxy['port']}"
                                        if proxy.get('anonymityLevel') == 'elite' and proxy.get('upTime', 0) > 80:
                                            proxies.append(proxy_str)
                        else:
                            # Обработка других API
                            text = await response.text()
                            proxy_list = [
                                f"http://{proxy.strip()}" 
                                for proxy in text.split('\n') 
                                if proxy.strip() and ':' in proxy
                            ]
                            proxies.extend(proxy_list)
                            
            except Exception as e:
                logger.warning(f"Ошибка при получении прокси из {api}: {str(e)}")
                continue
    
    return list(set(proxies))  # Убираем дубликаты

class ProxyManager:
    def __init__(self):
        self.proxies = []
        self.last_update = None
        self.cache_duration = 1800  # 30 минут
        self.working_proxies = {}  # Кэш рабочих прокси
        self.failed_proxies = set()  # Множество неработающих прокси
        
    async def test_proxy(self, proxy: str) -> bool:
        """Проверка работоспособности прокси"""
        if proxy in self.failed_proxies:
            return False
            
        if proxy in self.working_proxies:
            # Проверяем, не устарел ли кэш
            last_check = self.working_proxies[proxy]['last_check']
            if (datetime.now() - last_check).total_seconds() < 300:  # 5 минут
                return True
                
        try:
            start_time = time.time()
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    'https://api.ipify.org?format=json',
                    proxy=proxy,
                    timeout=5
                ) as response:
                    if response.status == 200:
                        response_time = time.time() - start_time
                        self.working_proxies[proxy] = {
                            'last_check': datetime.now(),
                            'response_time': response_time
                        }
                        return True
                    return False
        except Exception as e:
            self.failed_proxies.add(proxy)
            if proxy in self.working_proxies:
                del self.working_proxies[proxy]
            return False

    async def get_proxy(self) -> Optional[str]:
        """Получает рабочий прокси из кэша или обновляет список"""
        if self.should_update_cache():
            await self.update_cache()
            
        # Сначала проверяем уже известные рабочие прокси
        working_proxies = list(self.working_proxies.keys())
        random.shuffle(working_proxies)
        
        for proxy in working_proxies[:5]:  # Проверяем только первые 5
            if await self.test_proxy(proxy):
                return proxy
        
        # Если нет рабочих прокси в кэше, проверяем новые
        random.shuffle(self.proxies)
        for proxy in self.proxies:
            if proxy not in self.failed_proxies and await self.test_proxy(proxy):
                return proxy
        
        # Если все прокси не работают, обновляем кэш
        if self.proxies:
            await self.update_cache()
            # Пробуем еще раз
            random.shuffle(self.proxies)
            for proxy in self.proxies:
                if proxy not in self.failed_proxies and await self.test_proxy(proxy):
                    return proxy
        
        return None

    def should_update_cache(self) -> bool:
        """Проверяет, нужно ли обновить кэш"""
        if not self.last_update:
            return True
        return (datetime.now() - self.last_update).total_seconds() > self.cache_duration

    async def update_cache(self):
        """Обновляет кэш прокси"""
        self.proxies = await get_free_proxies()
        self.last_update = datetime.now()
        # Очищаем устаревшие данные
        self.failed_proxies.clear()
        old_time = datetime.now() - timedelta(minutes=30)
        self.working_proxies = {
            k: v for k, v in self.working_proxies.items() 
            if v['last_check'] > old_time
        }
        logger.info(f"Кэш прокси обновлен. Получено {len(self.proxies)} прокси")

async def main():
    try:
        # Инициализируем базу данных
        init_db()
        
        # Запускаем клиент Telethon
        await client.start()
        
        # Запускаем планировщик
        scheduler.start()
        
        # Восстанавливаем сохраненные расписания
        for user_id, folder, time in get_active_schedules():
            hour, minute = map(int, time.split(':'))
            job_id = f"analysis_{user_id}_{folder}"
            scheduler.add_job(
                run_scheduled_analysis,
                'cron',
                hour=hour,
                minute=minute,
                id=job_id,
                replace_existing=True,
                args=[user_id, folder]
            )
            logger.info(f"Восстановлено расписание: {job_id} в {time}")
        
        # Получаем инфу о боте с обработкой таймаута
        try:
            async with asyncio.timeout(10):
                me = await bot.get_me()
                logger.info(f"Бот @{me.username} запущен!")
        except asyncio.TimeoutError:
            logger.error("Таймаут при получении информации о боте")
            raise
        
        # Запускаем поллинг
        await dp.start_polling()
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {str(e)}")
        raise
    finally:
        # Закрываем все соединения
        await dp.storage.close()
        await dp.storage.wait_closed()
        await bot.session.close()
        await client.disconnect()
        scheduler.shutdown()

if __name__ == '__main__':
    # Настраиваем политику событийного цикла
    if platform.system() == 'Windows':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    # Создаем и запускаем событийный цикл
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {str(e)}")
    finally:
        # Закрываем все незакрытые таски
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        
        # Запускаем все отмененные таски для корректного завершения
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        
        loop.close()
        logger.info("Бот остановлен") 
