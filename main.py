import os
import json
from datetime import datetime, timedelta
import asyncio
import g4f
import logging
import re
import sqlite3
import pytz
import shutil
import tempfile
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
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from transliterate import translit
import platform
import requests
from bs4 import BeautifulSoup
import vk_api
from newspaper import Article, Config
from urllib.parse import urlparse

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

# Инициализируем SQLite
def init_db():
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    
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
    
    conn.commit()
    conn.close()

init_db()

# Создаем планировщик (но не запускаем)
scheduler = AsyncIOScheduler(timezone=pytz.UTC)

# Константа для VK API токена
VK_TOKEN = "vk1.a.Qmg-4o5lDmzl3vQZlbfjK0Uxl8lCUSWdj3bqonibDhtuf-ZgyGY6wY3lxmW3h157AVv4lrmT9GQTWrunvCnZUC6LyYx3lf6LjTUPoCEUg2dkrIUSEh353RjhyZNKkI_S0TohfTNKr8ZbPPFvRd9z9ULh8QW6x_eYNGmMh25mJwCQBLnyBDwLemvK1skmE16GgM45V08Je6XZm8pK0V1EPA"

# Конфигурация провайдеров и моделей
PROVIDER_HIERARCHY = [
    {
        'provider': g4f.Provider.DDG,
        'models': ['gpt-4', 'gpt-4o-mini', 'claude-3-haiku', 'llama-3.1-70b', 'mixtral-8x7b']
    },
    {
        'provider': g4f.Provider.Blackbox,
        'models': ['blackboxai', 'gpt-4', 'gpt-4o', 'o3-mini', 'gemini-1.5-flash', 'gemini-1.5-pro', 
                  'blackboxai-pro', 'llama-3.1-8b', 'llama-3.1-70b', 'llama-3.1-405b', 'llama-3.3-70b', 
                  'mixtral-small-28b', 'deepseek-chat', 'dbrx-instruct', 'qwq-32b', 'hermes-2-dpo', 'deepseek-r1']
    },
    {
        'provider': g4f.Provider.DeepInfraChat,
        'models': ['llama-3.1-8b', 'llama-3.2-90b', 'llama-3.3-70b', 'deepseek-v3', 'mixtral-small-28b',
                  'deepseek-r1', 'phi-4', 'wizardlm-2-8x22b', 'qwen-2.5-72b', 'yi-34b', 'qwen-2-72b',
                  'dolphin-2.6', 'dolphin-2.9', 'dbrx-instruct', 'airoboros-70b', 'lzlv-70b', 'wizardlm-2-7b']
    },
    {
        'provider': g4f.Provider.ChatGptEs,
        'models': ['gpt-4', 'gpt-4o', 'gpt-4o-mini']
    },
    {
        'provider': g4f.Provider.Liaobots,
        'models': ['grok-2', 'gpt-4o-mini', 'gpt-4o', 'gpt-4', 'o1-preview', 'o1-mini', 'deepseek-r1',
                  'deepseek-v3', 'claude-3-opus', 'claude-3.5-sonnet', 'claude-3-sonnet', 'gemini-1.5-flash',
                  'gemini-1.5-pro', 'gemini-2.0-flash', 'gemini-2.0-flash-thinking']
    },
    {
        'provider': g4f.Provider.Jmuz,
        'models': ['gpt-4', 'gpt-4o', 'gpt-4o-mini', 'llama-3-8b', 'llama-3-70b', 'llama-3.1-8b', 
                  'llama-3.1-70b', 'llama-3.1-405b', 'llama-3.2-11b', 'llama-3.2-90b', 'llama-3.3-70b',
                  'claude-3-haiku', 'claude-3-sonnet', 'claude-3-opus', 'claude-3.5-sonnet', 
                  'gemini-1.5-flash', 'gemini-1.5-pro', 'gemini-exp', 'deepseek-chat', 'deepseek-r1', 'qwq-32b']
    },
    {
        'provider': g4f.Provider.Glider,
        'models': ['llama-3.1-8b', 'llama-3.1-70b', 'llama-3.2-3b', 'deepseek-r1']
    },
    {
        'provider': g4f.Provider.PollinationsAI,
        'models': ['gpt-4o', 'gpt-4o-mini', 'llama-3.1-8b', 'llama-3.3-70b', 'deepseek-chat', 
                  'deepseek-r1', 'qwen-2.5-coder-32b', 'gemini-2.0-flash', 'evil', 'flux-pro']
    },
    {
        'provider': g4f.Provider.HuggingChat,
        'models': ['llama-3.2-11b', 'llama-3.3-70b', 'mistral-nemo', 'phi-3.5-mini', 'deepseek-r1',
                  'qwen-2.5-coder-32b', 'qwq-32b', 'nemotron-70b']
    },
    {
        'provider': g4f.Provider.HuggingFace,
        'models': ['llama-3.2-11b', 'llama-3.3-70b', 'mistral-nemo', 'deepseek-r1', 
                  'qwen-2.5-coder-32b', 'qwq-32b', 'nemotron-70b']
    },
    {
        'provider': g4f.Provider.HuggingSpace,
        'models': ['command-r', 'command-r-plus', 'command-r7b', 'qwen-2-72b', 'qwen-2.5-1m', 
                  'qvq-72b', 'sd-3.5', 'flux-dev', 'flux-schnell']
    },
    {
        'provider': g4f.Provider.Cloudflare,
        'models': ['llama-2-7b', 'llama-3-8b', 'llama-3.1-8b', 'qwen-1.5-7b']
    },
    {
        'provider': g4f.Provider.ChatGLM,
        'models': ['glm-4']
    },
    {
        'provider': g4f.Provider.GigaChat,
        'models': ['GigaChat:latest']
    },
    {
        'provider': g4f.Provider.Gemini,
        'models': ['gemini', 'gemini-1.5-flash', 'gemini-1.5-pro']
    },
    {
        'provider': g4f.Provider.GeminiPro,
        'models': ['gemini-1.5-flash', 'gemini-1.5-pro', 'gemini-2.0-flash']
    },
    {
        'provider': g4f.Provider.Pi,
        'models': ['pi']
    },
    {
        'provider': g4f.Provider.PerplexityLabs,
        'models': ['sonar', 'sonar-pro', 'sonar-reasoning', 'sonar-reasoning-pro']
    }
]

# Инициализируем клиенты
bot = Bot(token=token)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Инициализируем клиент Telethon
client = TelegramClient('telegram_session', int(os.getenv('API_ID')), os.getenv('API_HASH'))

# Структура для хранения данных
class UserData:
    def __init__(self):
        self.users = {}  # {user_id: {'folders': {}, 'prompts': {}, 'ai_settings': {}, 'vk_groups': {}, 'websites': {}}}
        
    def get_user_data(self, user_id: int) -> dict:
        """Получаем или создаем данные пользователя"""
        if str(user_id) not in self.users:
            self.users[str(user_id)] = {
                'folders': {},
                'prompts': {},
                'ai_settings': {
                    'provider_index': 0,
                    'model': PROVIDER_HIERARCHY[0]['models'][0]
                },
                'vk_groups': {},
                'websites': {}
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
                
                # Добавляем поля vk_groups и websites, если их нет
                for user_id, user_data in instance.users.items():
                    if 'vk_groups' not in user_data:
                        user_data['vk_groups'] = {}
                    if 'websites' not in user_data:
                        user_data['websites'] = {}
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
    waiting_for_vk_groups = State()
    waiting_for_websites = State()

def save_report(user_id: int, folder: str, content: str):
    """Сохраняем отчет в БД"""
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('INSERT INTO reports (user_id, folder, content) VALUES (?, ?, ?)',
              (user_id, folder, content))
    conn.commit()
    conn.close()

def get_user_reports(user_id: int, limit: int = 10) -> list:
    """Получаем последние отчеты пользователя"""
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('SELECT folder, content, created_at FROM reports WHERE user_id = ? ORDER BY created_at DESC LIMIT ?',
              (user_id, limit))
    reports = c.fetchall()
    conn.close()
    return reports

def save_schedule(user_id: int, folder: str, time: str):
    """Сохраняем расписание в БД"""
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('INSERT INTO schedules (user_id, folder, time) VALUES (?, ?, ?)',
              (user_id, folder, time))
    conn.commit()
    conn.close()

def get_active_schedules() -> list:
    """Получаем все активные расписания"""
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('SELECT user_id, folder, time FROM schedules WHERE is_active = 1')
    schedules = c.fetchall()
    conn.close()
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
    
    # Создаем PDF с поддержкой русского
    c = canvas.Canvas(filename, pagesize=A4)
    width, height = A4
    
    # Регистрируем шрифт DejaVu (поддерживает русский)
    font_path = get_font_path()
    pdfmetrics.registerFont(TTFont('DejaVu', font_path))
    
    # Пишем заголовок
    c.setFont('DejaVu', 16)  # Увеличенный размер для основного заголовка
    c.drawString(50, height - 50, f'Анализ папки: {folder}')
    
    # Пишем контент
    y = height - 100  # Начальная позиция для текста
    
    for line in content.split('\n'):
        if line.strip():  # Пропускаем пустые строки
            # Проверяем на заголовки разных уровней
            if line.strip().startswith('###'):
                # H3 заголовок
                c.setFont('DejaVu', 14)
                header_text = line.strip().replace('###', '').strip()
                c.drawString(50, y, header_text)
                y -= 30
                c.setFont('DejaVu', 12)
            elif line.strip().startswith('####'):
                # H4 заголовок
                c.setFont('DejaVu', 13)
                header_text = line.strip().replace('####', '').strip()
                c.drawString(70, y, header_text)  # Больший отступ для подзаголовка
                y -= 25
                c.setFont('DejaVu', 12)
            elif '**' in line.strip():
                # Ищем все вхождения жирного текста
                parts = line.split('**')
                x = 50  # Начальная позиция по X
                
                for i, part in enumerate(parts):
                    if i % 2 == 0:  # Обычный текст
                        if part.strip():
                            c.setFont('DejaVu', 12)
                            c.drawString(x, y, part)
                            x += c.stringWidth(part, 'DejaVu', 12)
                    else:  # Жирный текст
                        if part.strip():
                            c.setFont('DejaVu', 14)  # Делаем жирный текст чуть больше
                            c.drawString(x, y, part)
                            x += c.stringWidth(part, 'DejaVu', 14)
                
                y -= 20
                c.setFont('DejaVu', 12)  # Возвращаем обычный шрифт
            else:
                # Обычный текст
                c.setFont('DejaVu', 12)
                # Если строка слишком длинная, разбиваем ее
                words = line.split()
                current_line = ''
                for word in words:
                    test_line = current_line + ' ' + word if current_line else word
                    # Если строка становится слишком длинной, печатаем ее и начинаем новую
                    if c.stringWidth(test_line, 'DejaVu', 12) > width - 100:
                        c.drawString(50, y, current_line)
                        y -= 20
                        current_line = word
                    else:
                        current_line = test_line
                
                # Печатаем оставшуюся строку
                if current_line:
                    c.drawString(50, y, current_line)
                    y -= 20
            
            # Если достигли конца страницы, создаем новую
            if y < 50:
                c.showPage()
                c.setFont('DejaVu', 12)
                y = height - 50
    
    c.save()
    return filename

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
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
    keyboard.add(*buttons)
    await message.answer(
        f"Привет! Я бот для анализа Telegram каналов.\n"
        f"Мой юзернейм: @{me.username}\n"
        "Что хочешь сделать?",
        reply_markup=keyboard
    )

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
    
    # Инициализируем VK-группы и сайты для новой папки
    if folder_name not in user_data.get_user_data(message.from_user.id)['vk_groups']:
        user_data.get_user_data(message.from_user.id)['vk_groups'][folder_name] = []
    if folder_name not in user_data.get_user_data(message.from_user.id)['websites']:
        user_data.get_user_data(message.from_user.id)['websites'][folder_name] = []
    
    user_data.save()
    
    # Создаем клавиатуру для выбора типа источника
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("Добавить Telegram-каналы", 
                                     callback_data=f"add_channels_{folder_name}"))
    markup.add(types.InlineKeyboardButton("Добавить группы ВКонтакте", 
                                     callback_data=f"add_vk_groups_{folder_name}"))
    markup.add(types.InlineKeyboardButton("Добавить веб-сайты", 
                                     callback_data=f"add_websites_{folder_name}"))
    markup.add(types.InlineKeyboardButton("Готово", 
                                     callback_data="back_to_folders"))
    
    await state.finish()  # Завершаем состояние ожидания имени папки
    await message.answer(
        f"✅ Папка '{folder_name}' создана! Теперь выбери, что ты хочешь добавить в эту папку:",
        reply_markup=markup
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
    
    # Добавляем обработку кнопки отмены
    if message.text.lower() == 'отмена':
        await state.finish()
        await message.answer("Добавление каналов отменено.")
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
        
        # Отправляем обновленное меню с обновленным списком каналов
        user_info = user_data.get_user_data(message.from_user.id)
        
        # Создаем сообщение со списком источников
        content = f"📁 <b>Папка:</b> {folder_name}\n\n"
        
        channels = user_info['folders'].get(folder_name, [])
        if channels:
            content += "<b>Telegram-каналы:</b>\n"
            for i, channel in enumerate(channels, 1):
                content += f"{i}. {channel}\n"
            content += "\n"
        
        vk_groups = user_info['vk_groups'].get(folder_name, [])
        if vk_groups:
            content += "<b>Группы ВКонтакте:</b>\n"
            for i, group in enumerate(vk_groups, 1):
                content += f"{i}. {group}\n"
            content += "\n"
            
        websites = user_info['websites'].get(folder_name, [])
        if websites:
            content += "<b>Веб-сайты:</b>\n"
            for i, site in enumerate(websites, 1):
                content += f"{i}. {site}\n"
                
        # Создаем клавиатуру
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("Добавить Telegram-каналы", 
                                       callback_data=f"add_channels_{folder_name}"))
        markup.add(types.InlineKeyboardButton("Добавить группы ВКонтакте", 
                                       callback_data=f"add_vk_groups_{folder_name}"))
        markup.add(types.InlineKeyboardButton("Добавить веб-сайты", 
                                       callback_data=f"add_websites_{folder_name}"))
        markup.add(types.InlineKeyboardButton("Изменить промпт", 
                                       callback_data=f"edit_prompt_{folder_name}"))
        markup.add(types.InlineKeyboardButton("Удалить папку", 
                                       callback_data=f"delete_folder_{folder_name}"))
        markup.add(types.InlineKeyboardButton("« Назад к папкам", 
                                       callback_data="back_to_folders"))
        
        # Отправляем новое сообщение с обновленным меню
        await message.answer(content, reply_markup=markup, parse_mode='HTML')

@dp.message_handler(lambda message: message.text == "📋 Список папок")
async def list_folders(message: types.Message):
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
async def cmd_list_folders(message: types.Message):
    await list_folders(message)

@dp.callback_query_handler(lambda c: c.data.startswith('edit_folder_'))
async def edit_folder_menu(callback_query: types.CallbackQuery):
    # Получаем имя папки
    folder_name = callback_query.data.replace('edit_folder_', '')
    
    # Создаем клавиатуру для редактирования папки
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("Добавить Telegram-каналы", 
                                       callback_data=f"add_channels_{folder_name}"))
    markup.add(types.InlineKeyboardButton("Добавить группы ВКонтакте", 
                                       callback_data=f"add_vk_groups_{folder_name}"))
    markup.add(types.InlineKeyboardButton("Добавить веб-сайты", 
                                       callback_data=f"add_websites_{folder_name}"))
    markup.add(types.InlineKeyboardButton("Изменить промпт", 
                                       callback_data=f"edit_prompt_{folder_name}"))
    markup.add(types.InlineKeyboardButton("Удалить папку", 
                                       callback_data=f"delete_folder_{folder_name}"))
    markup.add(types.InlineKeyboardButton("« Назад к папкам", 
                                       callback_data="back_to_folders"))
    
    # Получаем список каналов, ВК групп и сайтов в папке
    user_info = user_data.get_user_data(callback_query.from_user.id)
    channels = user_info['folders'].get(folder_name, [])
    vk_groups = user_info['vk_groups'].get(folder_name, [])
    websites = user_info['websites'].get(folder_name, [])
    
    content = f"📁 <b>Папка:</b> {folder_name}\n\n"
    
    if channels:
        content += "<b>Telegram-каналы:</b>\n"
        for i, channel in enumerate(channels, 1):
            content += f"{i}. {channel}\n"
            # Добавляем кнопку удаления с индексом вместо полного URL
            markup.add(types.InlineKeyboardButton(f"❌ Удалить канал {i}", 
                                               callback_data=f"remove_channel_{folder_name}_{i-1}"))
        content += "\n"
    
    if vk_groups:
        content += "<b>Группы ВКонтакте:</b>\n"
        for i, group in enumerate(vk_groups, 1):
            content += f"{i}. {group}\n"
            # Добавляем кнопку удаления с индексом вместо полного URL
            markup.add(types.InlineKeyboardButton(f"❌ Удалить группу ВК {i}", 
                                               callback_data=f"remove_vk_group_{folder_name}_{i-1}"))
        content += "\n"
        
    if websites:
        content += "<b>Веб-сайты:</b>\n"
        for i, site in enumerate(websites, 1):
            content += f"{i}. {site}\n"
            # Добавляем кнопку удаления с индексом вместо полного URL
            markup.add(types.InlineKeyboardButton(f"❌ Удалить сайт {i}", 
                                               callback_data=f"remove_website_{folder_name}_{i-1}"))
    
    await bot.edit_message_text(chat_id=callback_query.message.chat.id,
                               message_id=callback_query.message.message_id,
                               text=content,
                               reply_markup=markup,
                               parse_mode='HTML')

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
    folder_name = callback_query.data.replace('delete_folder_', '')
    user_id = callback_query.from_user.id
    
    # Удаляем папку из всех структур данных
    user_info = user_data.get_user_data(user_id)
    if folder_name in user_info['folders']:
        del user_info['folders'][folder_name]
    if folder_name in user_info['prompts']:
        del user_info['prompts'][folder_name]
    if folder_name in user_info['vk_groups']:
        del user_info['vk_groups'][folder_name]
    if folder_name in user_info['websites']:
        del user_info['websites'][folder_name]
        user_data.save()
        
    await back_to_folders(callback_query)
        
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
async def ai_settings(message: types.Message):
    # Получаем текущие настройки пользователя
    user_settings = user_data.get_user_data(message.from_user.id)['ai_settings']
    current_provider = PROVIDER_HIERARCHY[user_settings['provider_index']]['provider'].__name__
    current_model = user_settings['model']
    
    # Создаем клавиатуру для выбора модели
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for provider_info in PROVIDER_HIERARCHY:
        for model in provider_info['models']:
            keyboard.add(
                types.InlineKeyboardButton(
                    f"{'✅ ' if model == current_model else ''}{model} ({provider_info['provider'].__name__})",
                    callback_data=f"select_model_{provider_info['provider'].__name__}_{model}"
                )
            )
    
    await message.answer(
        f"📊 Текущие настройки ИИ:\n\n"
        f"🔹 Провайдер: {current_provider}\n"
        f"🔹 Модель: {current_model}\n\n"
        f"ℹ️ Выберите предпочитаемую модель из списка:",
        reply_markup=keyboard
    )

@dp.callback_query_handler(lambda c: c.data.startswith('select_model_'))
async def process_model_selection(callback_query: types.CallbackQuery):
    _, provider_name, model = callback_query.data.split('_', 2)
    
    # Обновляем настройки пользователя
    for index, provider_info in enumerate(PROVIDER_HIERARCHY):
        if provider_info['provider'].__name__ == provider_name:
            user_data.get_user_data(callback_query.from_user.id)['ai_settings']['provider_index'] = index
            break
    user_data.get_user_data(callback_query.from_user.id)['ai_settings']['model'] = model
    user_data.save()
    
    await callback_query.message.edit_text(
        f"✅ Модель {model} от провайдера {provider_name} успешно выбрана!"
    )

async def try_gpt_request(prompt: str, posts_text: str, user_id: int):
    """Пытаемся получить ответ от GPT, перебирая провайдеров"""
    last_error = None
    rate_limited_providers = set()
    
    # Очищаем временные файлы и кэш
    try:
        # Чистим временные файлы
        temp_dir = tempfile.gettempdir()
        for filename in os.listdir(temp_dir):
            if filename.startswith('g4f_') or filename.startswith('gpt_'):
                try:
                    os.remove(os.path.join(temp_dir, filename))
                except:
                    pass
                    
        # Чистим кэш сессий
        cache_dirs = ['.cache', '__pycache__', 'tmp']
        for dir_name in cache_dirs:
            if os.path.exists(dir_name):
                try:
                    shutil.rmtree(dir_name)
                except:
                    pass
    except Exception as e:
        logger.warning(f"Ошибка при очистке кэша: {str(e)}")
    
    # Всегда начинаем с DDG
    providers_to_try = [PROVIDER_HIERARCHY[0]]  # DDG первый
    other_providers = PROVIDER_HIERARCHY[1:]  # Остальные в случайном порядке
    random.shuffle(other_providers)
    providers_to_try.extend(other_providers)
    
    # Генерируем случайный ID сессии
    session_id = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=32))
    
    for provider_info in providers_to_try:
        if provider_info['provider'] in rate_limited_providers:
            continue
            
        try:
            logger.info(f"Пробую провайдера {provider_info['provider'].__name__}")
            
            # Проверяем поддержку модели
            current_model = user_data.get_user_data(user_id)['ai_settings']['model']
            if current_model not in provider_info['models']:
                model_to_use = provider_info['models'][0]
                logger.info(f"Модель {current_model} не поддерживается, использую {model_to_use}")
            else:
                model_to_use = current_model
            
            # Добавляем случайные заголовки и параметры
            g4f.debug.logging = False
            g4f.check_version = False
            
            # Генерируем рандомные параметры для запроса
            headers = {
                'User-Agent': f'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/{random.randint(500, 600)}.{random.randint(1, 99)}',
                'Accept-Language': f'en-US,en;q=0.{random.randint(1, 9)}',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Cache-Control': 'no-cache',
                'Pragma': 'no-cache',
                'X-Session-ID': session_id,  # Уникальный ID для каждого запроса
                'X-Client-ID': f'{random.randint(1000, 9999)}-{random.randint(1000, 9999)}',
                'X-Request-ID': f'{random.randint(1000, 9999)}-{random.randint(1000, 9999)}'
            }
            
            # Добавляем случайную задержку
            await asyncio.sleep(random.uniform(1.0, 3.0))
            
            response = await g4f.ChatCompletion.create_async(
                model=model_to_use,
                messages=[{"role": "user", "content": f"{prompt}\n\nДанные для анализа:\n{posts_text}"}],
                provider=provider_info['provider'],
                headers=headers,
                proxy=None,
                timeout=30
            )
            
            if response and len(response.strip()) > 0:
                return response
            else:
                raise Exception("Пустой ответ от провайдера")
            
        except Exception as e:
            error_str = str(e)
            last_error = error_str
            logger.error(f"Ошибка с провайдером {provider_info['provider'].__name__}: {error_str}")
            
            if "429" in error_str or "ERR_INPUT_LIMIT" in error_str:
                rate_limited_providers.add(provider_info['provider'])
                logger.warning(f"Провайдер {provider_info['provider'].__name__} временно заблокирован")
                await asyncio.sleep(5.0)
            else:
                await asyncio.sleep(1.0)
                
            continue
    
    if len(rate_limited_providers) > 0:
        raise Exception(f"Все доступные провайдеры временно заблокированы. Попробуйте позже. Последняя ошибка: {last_error}")
    else:
        raise Exception(f"Все провайдеры перепробованы. Последняя ошибка: {last_error}")

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
        
        async for message in client.iter_messages(channel, limit=100):
            if message.date < time_threshold:
                break
                
            if message.text and len(message.text.strip()) > 0:
                posts.append(message.text)
        
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
    logger.info(f"Запуск запланированного анализа для пользователя {user_id}, папка {folder}")
    
    try:
        # Получаем данные пользователя
        user_info = user_data.get_user_data(user_id)
        
        # Устанавливаем период анализа - по умолчанию 1 день (24 часа)
        hours = 24
        
        # Получаем каналы для анализа
        channels = user_info['folders'].get(folder, [])
        if not channels:
            logger.warning(f"Папка {folder} пуста или не существует")
            return
        
        all_posts = []
                
        # Собираем данные из Telegram каналов
        for channel in channels:
            posts = await get_channel_posts(channel, hours=hours)
            if posts:
                for post in posts:
                    all_posts.append(f"[Telegram канал: {channel}]\n{post}")
        
        # Собираем данные из групп ВКонтакте
        if folder in user_info['vk_groups'] and user_info['vk_groups'][folder]:
            vk_handler = VkAPIHandler(VK_TOKEN)
            for group in user_info['vk_groups'][folder]:
                try:
                    vk_posts = vk_handler.get_posts(group, hours=hours)
                    if vk_posts:
                        for post in vk_posts:
                            all_posts.append(f"[ВКонтакте группа: {group}, {post['date']}]\n{post['text']}")
                except Exception as e:
                    logger.error(f"Ошибка при получении постов из ВК {group}: {str(e)}")
        
        # Собираем данные из веб-сайтов
        if folder in user_info['websites'] and user_info['websites'][folder]:
            web_parser = WebsiteParser()
            for site in user_info['websites'][folder]:
                try:
                    articles = web_parser.extract_news(site, hours=hours)
                    if articles:
                        for article in articles:
                            all_posts.append(f"[Сайт: {site}, {article['date']}]\n{article['title']}\n{article['text'][:1000]}...")
                except Exception as e:
                    logger.error(f"Ошибка при анализе сайта {site}: {str(e)}")
                
        if not all_posts:
            logger.error(f"Не удалось получить посты для автоматического анализа папки {folder}")
            return
            
        posts_text = "\n\n---\n\n".join(all_posts)
        prompt = user_info['prompts'][folder]
        
        # Выполняем запрос к модели ИИ
        response = await try_gpt_request(prompt, posts_text, user_id)
        
        # Сохраняем отчет в БД
        save_report(user_id, folder, response)
        
        # Генерируем отчеты в обоих форматах
        txt_filename = generate_txt_report(response, folder)
        
        try:
            pdf_filename = generate_pdf_report(response, folder)
        except Exception as e:
            logger.error(f"Ошибка при создании PDF: {str(e)}")
            pdf_filename = None
        
        # Отправляем отчет пользователю
        await bot.send_message(
            user_id,
            f"✅ Автоматический анализ папки {folder} завершен!\n"
            "Отчет прикреплен ниже."
        )
        
        # Отправляем TXT файл
        with open(txt_filename, 'rb') as f:
            await bot.send_document(
                user_id,
                f,
                caption=f"Отчет по папке {folder} (TXT)"
            )
        os.remove(txt_filename)
        
        # Отправляем PDF если есть
        if pdf_filename:
            with open(pdf_filename, 'rb') as f:
                await bot.send_document(
                    user_id,
                    f,
                    caption=f"Отчет по папке {folder} (PDF)"
                )
            os.remove(pdf_filename)
        
    except Exception as e:
        logger.error(f"Ошибка при автоматическом анализе: {str(e)}")

@dp.message_handler(lambda message: message.text == "🔄 Запустить анализ")
async def start_analysis(message: types.Message):
    user_id = message.from_user.id
    user_info = user_data.get_user_data(user_id)
    
    # Получаем все папки
    folders = {}
    
    # Объединяем все источники (папки могут иметь только определенные типы источников)
    for folder in user_info['folders']:
        folders[folder] = True
    
    for folder in user_info['vk_groups']:
        folders[folder] = True
        
    for folder in user_info['websites']:
        folders[folder] = True
    
    if not folders:
        await message.answer("У вас нет созданных папок. Используйте '📁 Создать папку' для начала.")
        return
        
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    
    # Добавляем только папки, у которых есть хотя бы один источник данных
    for folder in folders:
        has_data = False
        # Проверяем наличие Telegram каналов
        if folder in user_info['folders'] and user_info['folders'][folder]:
            has_data = True
        # Проверяем наличие VK групп
        elif folder in user_info['vk_groups'] and user_info['vk_groups'][folder]:
            has_data = True
        # Проверяем наличие веб-сайтов
        elif folder in user_info['websites'] and user_info['websites'][folder]:
            has_data = True
            
        if has_data:
            keyboard.add(types.InlineKeyboardButton(
                    folder, callback_data=f"format_{folder}"
            ))
    
    # Добавляем кнопку "Анализировать все" и "Назад"
    keyboard.add(
        types.InlineKeyboardButton("Анализировать все", callback_data="analyze_all"),
        types.InlineKeyboardButton("Назад", callback_data="back")
    )
import g4f
import logging
import re
import sqlite3
import pytz
import shutil
import tempfile
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
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from transliterate import translit
import platform
import requests
from bs4 import BeautifulSoup
import vk_api
from newspaper import Article, Config
from urllib.parse import urlparse

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

# Инициализируем SQLite
def init_db():
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    
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
    
    conn.commit()
    conn.close()

init_db()

# Создаем планировщик (но не запускаем)
scheduler = AsyncIOScheduler(timezone=pytz.UTC)

# Константа для VK API токена
VK_TOKEN = "vk1.a.Qmg-4o5lDmzl3vQZlbfjK0Uxl8lCUSWdj3bqonibDhtuf-ZgyGY6wY3lxmW3h157AVv4lrmT9GQTWrunvCnZUC6LyYx3lf6LjTUPoCEUg2dkrIUSEh353RjhyZNKkI_S0TohfTNKr8ZbPPFvRd9z9ULh8QW6x_eYNGmMh25mJwCQBLnyBDwLemvK1skmE16GgM45V08Je6XZm8pK0V1EPA"

# Конфигурация провайдеров и моделей
PROVIDER_HIERARCHY = [
    {
        'provider': g4f.Provider.DDG,
        'models': ['gpt-4', 'gpt-4o-mini', 'claude-3-haiku', 'llama-3.1-70b', 'mixtral-8x7b']
    },
    {
        'provider': g4f.Provider.Blackbox,
        'models': ['blackboxai', 'gpt-4', 'gpt-4o', 'o3-mini', 'gemini-1.5-flash', 'gemini-1.5-pro', 
                  'blackboxai-pro', 'llama-3.1-8b', 'llama-3.1-70b', 'llama-3.1-405b', 'llama-3.3-70b', 
                  'mixtral-small-28b', 'deepseek-chat', 'dbrx-instruct', 'qwq-32b', 'hermes-2-dpo', 'deepseek-r1']
    },
    {
        'provider': g4f.Provider.DeepInfraChat,
        'models': ['llama-3.1-8b', 'llama-3.2-90b', 'llama-3.3-70b', 'deepseek-v3', 'mixtral-small-28b',
                  'deepseek-r1', 'phi-4', 'wizardlm-2-8x22b', 'qwen-2.5-72b', 'yi-34b', 'qwen-2-72b',
                  'dolphin-2.6', 'dolphin-2.9', 'dbrx-instruct', 'airoboros-70b', 'lzlv-70b', 'wizardlm-2-7b']
    },
    {
        'provider': g4f.Provider.ChatGptEs,
        'models': ['gpt-4', 'gpt-4o', 'gpt-4o-mini']
    },
    {
        'provider': g4f.Provider.Liaobots,
        'models': ['grok-2', 'gpt-4o-mini', 'gpt-4o', 'gpt-4', 'o1-preview', 'o1-mini', 'deepseek-r1',
                  'deepseek-v3', 'claude-3-opus', 'claude-3.5-sonnet', 'claude-3-sonnet', 'gemini-1.5-flash',
                  'gemini-1.5-pro', 'gemini-2.0-flash', 'gemini-2.0-flash-thinking']
    },
    {
        'provider': g4f.Provider.Jmuz,
        'models': ['gpt-4', 'gpt-4o', 'gpt-4o-mini', 'llama-3-8b', 'llama-3-70b', 'llama-3.1-8b', 
                  'llama-3.1-70b', 'llama-3.1-405b', 'llama-3.2-11b', 'llama-3.2-90b', 'llama-3.3-70b',
                  'claude-3-haiku', 'claude-3-sonnet', 'claude-3-opus', 'claude-3.5-sonnet', 
                  'gemini-1.5-flash', 'gemini-1.5-pro', 'gemini-exp', 'deepseek-chat', 'deepseek-r1', 'qwq-32b']
    },
    {
        'provider': g4f.Provider.Glider,
        'models': ['llama-3.1-8b', 'llama-3.1-70b', 'llama-3.2-3b', 'deepseek-r1']
    },
    {
        'provider': g4f.Provider.PollinationsAI,
        'models': ['gpt-4o', 'gpt-4o-mini', 'llama-3.1-8b', 'llama-3.3-70b', 'deepseek-chat', 
                  'deepseek-r1', 'qwen-2.5-coder-32b', 'gemini-2.0-flash', 'evil', 'flux-pro']
    },
    {
        'provider': g4f.Provider.HuggingChat,
        'models': ['llama-3.2-11b', 'llama-3.3-70b', 'mistral-nemo', 'phi-3.5-mini', 'deepseek-r1',
                  'qwen-2.5-coder-32b', 'qwq-32b', 'nemotron-70b']
    },
    {
        'provider': g4f.Provider.HuggingFace,
        'models': ['llama-3.2-11b', 'llama-3.3-70b', 'mistral-nemo', 'deepseek-r1', 
                  'qwen-2.5-coder-32b', 'qwq-32b', 'nemotron-70b']
    },
    {
        'provider': g4f.Provider.HuggingSpace,
        'models': ['command-r', 'command-r-plus', 'command-r7b', 'qwen-2-72b', 'qwen-2.5-1m', 
                  'qvq-72b', 'sd-3.5', 'flux-dev', 'flux-schnell']
    },
    {
        'provider': g4f.Provider.Cloudflare,
        'models': ['llama-2-7b', 'llama-3-8b', 'llama-3.1-8b', 'qwen-1.5-7b']
    },
    {
        'provider': g4f.Provider.ChatGLM,
        'models': ['glm-4']
    },
    {
        'provider': g4f.Provider.GigaChat,
        'models': ['GigaChat:latest']
    },
    {
        'provider': g4f.Provider.Gemini,
        'models': ['gemini', 'gemini-1.5-flash', 'gemini-1.5-pro']
    },
    {
        'provider': g4f.Provider.GeminiPro,
        'models': ['gemini-1.5-flash', 'gemini-1.5-pro', 'gemini-2.0-flash']
    },
    {
        'provider': g4f.Provider.Pi,
        'models': ['pi']
    },
    {
        'provider': g4f.Provider.PerplexityLabs,
        'models': ['sonar', 'sonar-pro', 'sonar-reasoning', 'sonar-reasoning-pro']
    }
]

# Инициализируем клиенты
bot = Bot(token=token)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Инициализируем клиент Telethon
client = TelegramClient('telegram_session', int(os.getenv('API_ID')), os.getenv('API_HASH'))

# Структура для хранения данных
class UserData:
    def __init__(self):
        self.users = {}  # {user_id: {'folders': {}, 'prompts': {}, 'ai_settings': {}, 'vk_groups': {}, 'websites': {}}}
        
    def get_user_data(self, user_id: int) -> dict:
        """Получаем или создаем данные пользователя"""
        if str(user_id) not in self.users:
            self.users[str(user_id)] = {
                'folders': {},
                'prompts': {},
                'ai_settings': {
                    'provider_index': 0,
                    'model': PROVIDER_HIERARCHY[0]['models'][0]
                },
                'vk_groups': {},
                'websites': {}
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
                
                # Добавляем поля vk_groups и websites, если их нет
                for user_id, user_data in instance.users.items():
                    if 'vk_groups' not in user_data:
                        user_data['vk_groups'] = {}
                    if 'websites' not in user_data:
                        user_data['websites'] = {}
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
    waiting_for_vk_groups = State()
    waiting_for_websites = State()

def save_report(user_id: int, folder: str, content: str):
    """Сохраняем отчет в БД"""
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('INSERT INTO reports (user_id, folder, content) VALUES (?, ?, ?)',
              (user_id, folder, content))
    conn.commit()
    conn.close()

def get_user_reports(user_id: int, limit: int = 10) -> list:
    """Получаем последние отчеты пользователя"""
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('SELECT folder, content, created_at FROM reports WHERE user_id = ? ORDER BY created_at DESC LIMIT ?',
              (user_id, limit))
    reports = c.fetchall()
    conn.close()
    return reports

def save_schedule(user_id: int, folder: str, time: str):
    """Сохраняем расписание в БД"""
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('INSERT INTO schedules (user_id, folder, time) VALUES (?, ?, ?)',
              (user_id, folder, time))
    conn.commit()
    conn.close()

def get_active_schedules() -> list:
    """Получаем все активные расписания"""
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('SELECT user_id, folder, time FROM schedules WHERE is_active = 1')
    schedules = c.fetchall()
    conn.close()
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
    
    # Создаем PDF с поддержкой русского
    c = canvas.Canvas(filename, pagesize=A4)
    width, height = A4
    
    # Регистрируем шрифт DejaVu (поддерживает русский)
    font_path = get_font_path()
    pdfmetrics.registerFont(TTFont('DejaVu', font_path))
    
    # Пишем заголовок
    c.setFont('DejaVu', 16)  # Увеличенный размер для основного заголовка
    c.drawString(50, height - 50, f'Анализ папки: {folder}')
    
    # Пишем контент
    y = height - 100  # Начальная позиция для текста
    
    for line in content.split('\n'):
        if line.strip():  # Пропускаем пустые строки
            # Проверяем на заголовки разных уровней
            if line.strip().startswith('###'):
                # H3 заголовок
                c.setFont('DejaVu', 14)
                header_text = line.strip().replace('###', '').strip()
                c.drawString(50, y, header_text)
                y -= 30
                c.setFont('DejaVu', 12)
            elif line.strip().startswith('####'):
                # H4 заголовок
                c.setFont('DejaVu', 13)
                header_text = line.strip().replace('####', '').strip()
                c.drawString(70, y, header_text)  # Больший отступ для подзаголовка
                y -= 25
                c.setFont('DejaVu', 12)
            elif '**' in line.strip():
                # Ищем все вхождения жирного текста
                parts = line.split('**')
                x = 50  # Начальная позиция по X
                
                for i, part in enumerate(parts):
                    if i % 2 == 0:  # Обычный текст
                        if part.strip():
                            c.setFont('DejaVu', 12)
                            c.drawString(x, y, part)
                            x += c.stringWidth(part, 'DejaVu', 12)
                    else:  # Жирный текст
                        if part.strip():
                            c.setFont('DejaVu', 14)  # Делаем жирный текст чуть больше
                            c.drawString(x, y, part)
                            x += c.stringWidth(part, 'DejaVu', 14)
                
                y -= 20
                c.setFont('DejaVu', 12)  # Возвращаем обычный шрифт
            else:
                # Обычный текст
                c.setFont('DejaVu', 12)
                # Если строка слишком длинная, разбиваем ее
                words = line.split()
                current_line = ''
                for word in words:
                    test_line = current_line + ' ' + word if current_line else word
                    # Если строка становится слишком длинной, печатаем ее и начинаем новую
                    if c.stringWidth(test_line, 'DejaVu', 12) > width - 100:
                        c.drawString(50, y, current_line)
                        y -= 20
                        current_line = word
                    else:
                        current_line = test_line
                
                # Печатаем оставшуюся строку
                if current_line:
                    c.drawString(50, y, current_line)
                    y -= 20
            
            # Если достигли конца страницы, создаем новую
            if y < 50:
                c.showPage()
                c.setFont('DejaVu', 12)
                y = height - 50
    
    c.save()
    return filename

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
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
    keyboard.add(*buttons)
    await message.answer(
        f"Привет! Я бот для анализа Telegram каналов.\n"
        f"Мой юзернейм: @{me.username}\n"
        "Что хочешь сделать?",
        reply_markup=keyboard
    )

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
    
    # Инициализируем VK-группы и сайты для новой папки
    if folder_name not in user_data.get_user_data(message.from_user.id)['vk_groups']:
        user_data.get_user_data(message.from_user.id)['vk_groups'][folder_name] = []
    if folder_name not in user_data.get_user_data(message.from_user.id)['websites']:
        user_data.get_user_data(message.from_user.id)['websites'][folder_name] = []
    
    user_data.save()
    
    # Создаем клавиатуру для выбора типа источника
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("Добавить Telegram-каналы", 
                                     callback_data=f"add_channels_{folder_name}"))
    markup.add(types.InlineKeyboardButton("Добавить группы ВКонтакте", 
                                     callback_data=f"add_vk_groups_{folder_name}"))
    markup.add(types.InlineKeyboardButton("Добавить веб-сайты", 
                                     callback_data=f"add_websites_{folder_name}"))
    markup.add(types.InlineKeyboardButton("Готово", 
                                     callback_data="back_to_folders"))
    
    await state.finish()  # Завершаем состояние ожидания имени папки
    await message.answer(
        f"✅ Папка '{folder_name}' создана! Теперь выбери, что ты хочешь добавить в эту папку:",
        reply_markup=markup
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
    
    # Добавляем обработку кнопки отмены
    if message.text.lower() == 'отмена':
        await state.finish()
        await message.answer("Добавление каналов отменено.")
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
        
        # Отправляем обновленное меню с обновленным списком каналов
        user_info = user_data.get_user_data(message.from_user.id)
        
        # Создаем сообщение со списком источников
        content = f"📁 <b>Папка:</b> {folder_name}\n\n"
        
        channels = user_info['folders'].get(folder_name, [])
        if channels:
            content += "<b>Telegram-каналы:</b>\n"
            for i, channel in enumerate(channels, 1):
                content += f"{i}. {channel}\n"
            content += "\n"
        
        vk_groups = user_info['vk_groups'].get(folder_name, [])
        if vk_groups:
            content += "<b>Группы ВКонтакте:</b>\n"
            for i, group in enumerate(vk_groups, 1):
                content += f"{i}. {group}\n"
            content += "\n"
            
        websites = user_info['websites'].get(folder_name, [])
        if websites:
            content += "<b>Веб-сайты:</b>\n"
            for i, site in enumerate(websites, 1):
                content += f"{i}. {site}\n"
                
        # Создаем клавиатуру
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("Добавить Telegram-каналы", 
                                       callback_data=f"add_channels_{folder_name}"))
        markup.add(types.InlineKeyboardButton("Добавить группы ВКонтакте", 
                                       callback_data=f"add_vk_groups_{folder_name}"))
        markup.add(types.InlineKeyboardButton("Добавить веб-сайты", 
                                       callback_data=f"add_websites_{folder_name}"))
        markup.add(types.InlineKeyboardButton("Изменить промпт", 
                                       callback_data=f"edit_prompt_{folder_name}"))
        markup.add(types.InlineKeyboardButton("Удалить папку", 
                                       callback_data=f"delete_folder_{folder_name}"))
        markup.add(types.InlineKeyboardButton("« Назад к папкам", 
                                       callback_data="back_to_folders"))
        
        # Отправляем новое сообщение с обновленным меню
        await message.answer(content, reply_markup=markup, parse_mode='HTML')

@dp.message_handler(lambda message: message.text == "📋 Список папок")
async def list_folders(message: types.Message):
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
async def cmd_list_folders(message: types.Message):
    await list_folders(message)

@dp.callback_query_handler(lambda c: c.data.startswith('edit_folder_'))
async def edit_folder_menu(callback_query: types.CallbackQuery):
    # Получаем имя папки
    folder_name = callback_query.data.replace('edit_folder_', '')
    
    # Создаем клавиатуру для редактирования папки
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("Добавить Telegram-каналы", 
                                       callback_data=f"add_channels_{folder_name}"))
    markup.add(types.InlineKeyboardButton("Добавить группы ВКонтакте", 
                                       callback_data=f"add_vk_groups_{folder_name}"))
    markup.add(types.InlineKeyboardButton("Добавить веб-сайты", 
                                       callback_data=f"add_websites_{folder_name}"))
    markup.add(types.InlineKeyboardButton("Изменить промпт", 
                                       callback_data=f"edit_prompt_{folder_name}"))
    markup.add(types.InlineKeyboardButton("Удалить папку", 
                                       callback_data=f"delete_folder_{folder_name}"))
    markup.add(types.InlineKeyboardButton("« Назад к папкам", 
                                       callback_data="back_to_folders"))
    
    # Получаем список каналов, ВК групп и сайтов в папке
    user_info = user_data.get_user_data(callback_query.from_user.id)
    channels = user_info['folders'].get(folder_name, [])
    vk_groups = user_info['vk_groups'].get(folder_name, [])
    websites = user_info['websites'].get(folder_name, [])
    
    content = f"📁 <b>Папка:</b> {folder_name}\n\n"
    
    if channels:
        content += "<b>Telegram-каналы:</b>\n"
        for i, channel in enumerate(channels, 1):
            content += f"{i}. {channel}\n"
            # Добавляем кнопку удаления с индексом вместо полного URL
            markup.add(types.InlineKeyboardButton(f"❌ Удалить канал {i}", 
                                               callback_data=f"remove_channel_{folder_name}_{i-1}"))
        content += "\n"
    
    if vk_groups:
        content += "<b>Группы ВКонтакте:</b>\n"
        for i, group in enumerate(vk_groups, 1):
            content += f"{i}. {group}\n"
            # Добавляем кнопку удаления с индексом вместо полного URL
            markup.add(types.InlineKeyboardButton(f"❌ Удалить группу ВК {i}", 
                                               callback_data=f"remove_vk_group_{folder_name}_{i-1}"))
        content += "\n"
        
    if websites:
        content += "<b>Веб-сайты:</b>\n"
        for i, site in enumerate(websites, 1):
            content += f"{i}. {site}\n"
            # Добавляем кнопку удаления с индексом вместо полного URL
            markup.add(types.InlineKeyboardButton(f"❌ Удалить сайт {i}", 
                                               callback_data=f"remove_website_{folder_name}_{i-1}"))
    
    await bot.edit_message_text(chat_id=callback_query.message.chat.id,
                               message_id=callback_query.message.message_id,
                               text=content,
                               reply_markup=markup,
                               parse_mode='HTML')

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
    folder_name = callback_query.data.replace('delete_folder_', '')
    user_id = callback_query.from_user.id
    
    # Удаляем папку из всех структур данных
    user_info = user_data.get_user_data(user_id)
    if folder_name in user_info['folders']:
        del user_info['folders'][folder_name]
    if folder_name in user_info['prompts']:
        del user_info['prompts'][folder_name]
    if folder_name in user_info['vk_groups']:
        del user_info['vk_groups'][folder_name]
    if folder_name in user_info['websites']:
        del user_info['websites'][folder_name]
        user_data.save()
        
    await back_to_folders(callback_query)
        
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
async def ai_settings(message: types.Message):
    # Получаем текущие настройки пользователя
    user_settings = user_data.get_user_data(message.from_user.id)['ai_settings']
    current_provider = PROVIDER_HIERARCHY[user_settings['provider_index']]['provider'].__name__
    current_model = user_settings['model']
    
    # Создаем клавиатуру для выбора модели
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for provider_info in PROVIDER_HIERARCHY:
        for model in provider_info['models']:
            keyboard.add(
                types.InlineKeyboardButton(
                    f"{'✅ ' if model == current_model else ''}{model} ({provider_info['provider'].__name__})",
                    callback_data=f"select_model_{provider_info['provider'].__name__}_{model}"
                )
            )
    
    await message.answer(
        f"📊 Текущие настройки ИИ:\n\n"
        f"🔹 Провайдер: {current_provider}\n"
        f"🔹 Модель: {current_model}\n\n"
        f"ℹ️ Выберите предпочитаемую модель из списка:",
        reply_markup=keyboard
    )

@dp.callback_query_handler(lambda c: c.data.startswith('select_model_'))
async def process_model_selection(callback_query: types.CallbackQuery):
    _, provider_name, model = callback_query.data.split('_', 2)
    
    # Обновляем настройки пользователя
    for index, provider_info in enumerate(PROVIDER_HIERARCHY):
        if provider_info['provider'].__name__ == provider_name:
            user_data.get_user_data(callback_query.from_user.id)['ai_settings']['provider_index'] = index
            break
    user_data.get_user_data(callback_query.from_user.id)['ai_settings']['model'] = model
    user_data.save()
    
    await callback_query.message.edit_text(
        f"✅ Модель {model} от провайдера {provider_name} успешно выбрана!"
    )

async def try_gpt_request(prompt: str, posts_text: str, user_id: int):
    """Пытаемся получить ответ от GPT, перебирая провайдеров"""
    last_error = None
    rate_limited_providers = set()
    
    # Очищаем временные файлы и кэш
    try:
        # Чистим временные файлы
        temp_dir = tempfile.gettempdir()
        for filename in os.listdir(temp_dir):
            if filename.startswith('g4f_') or filename.startswith('gpt_'):
                try:
                    os.remove(os.path.join(temp_dir, filename))
                except:
                    pass
                    
        # Чистим кэш сессий
        cache_dirs = ['.cache', '__pycache__', 'tmp']
        for dir_name in cache_dirs:
            if os.path.exists(dir_name):
                try:
                    shutil.rmtree(dir_name)
                except:
                    pass
    except Exception as e:
        logger.warning(f"Ошибка при очистке кэша: {str(e)}")
    
    # Всегда начинаем с DDG
    providers_to_try = [PROVIDER_HIERARCHY[0]]  # DDG первый
    other_providers = PROVIDER_HIERARCHY[1:]  # Остальные в случайном порядке
    random.shuffle(other_providers)
    providers_to_try.extend(other_providers)
    
    # Генерируем случайный ID сессии
    session_id = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=32))
    
    for provider_info in providers_to_try:
        if provider_info['provider'] in rate_limited_providers:
            continue
            
        try:
            logger.info(f"Пробую провайдера {provider_info['provider'].__name__}")
            
            # Проверяем поддержку модели
            current_model = user_data.get_user_data(user_id)['ai_settings']['model']
            if current_model not in provider_info['models']:
                model_to_use = provider_info['models'][0]
                logger.info(f"Модель {current_model} не поддерживается, использую {model_to_use}")
            else:
                model_to_use = current_model
            
            # Добавляем случайные заголовки и параметры
            g4f.debug.logging = False
            g4f.check_version = False
            
            # Генерируем рандомные параметры для запроса
            headers = {
                'User-Agent': f'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/{random.randint(500, 600)}.{random.randint(1, 99)}',
                'Accept-Language': f'en-US,en;q=0.{random.randint(1, 9)}',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Cache-Control': 'no-cache',
                'Pragma': 'no-cache',
                'X-Session-ID': session_id,  # Уникальный ID для каждого запроса
                'X-Client-ID': f'{random.randint(1000, 9999)}-{random.randint(1000, 9999)}',
                'X-Request-ID': f'{random.randint(1000, 9999)}-{random.randint(1000, 9999)}'
            }
            
            # Добавляем случайную задержку
            await asyncio.sleep(random.uniform(1.0, 3.0))
            
            response = await g4f.ChatCompletion.create_async(
                model=model_to_use,
                messages=[{"role": "user", "content": f"{prompt}\n\nДанные для анализа:\n{posts_text}"}],
                provider=provider_info['provider'],
                headers=headers,
                proxy=None,
                timeout=30
            )
            
            if response and len(response.strip()) > 0:
                return response
            else:
                raise Exception("Пустой ответ от провайдера")
            
        except Exception as e:
            error_str = str(e)
            last_error = error_str
            logger.error(f"Ошибка с провайдером {provider_info['provider'].__name__}: {error_str}")
            
            if "429" in error_str or "ERR_INPUT_LIMIT" in error_str:
                rate_limited_providers.add(provider_info['provider'])
                logger.warning(f"Провайдер {provider_info['provider'].__name__} временно заблокирован")
                await asyncio.sleep(5.0)
            else:
                await asyncio.sleep(1.0)
                
            continue
    
    if len(rate_limited_providers) > 0:
        raise Exception(f"Все доступные провайдеры временно заблокированы. Попробуйте позже. Последняя ошибка: {last_error}")
    else:
        raise Exception(f"Все провайдеры перепробованы. Последняя ошибка: {last_error}")

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
        
        async for message in client.iter_messages(channel, limit=100):
            if message.date < time_threshold:
                break
                
            if message.text and len(message.text.strip()) > 0:
                posts.append(message.text)
        
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
    logger.info(f"Запуск запланированного анализа для пользователя {user_id}, папка {folder}")
    
    try:
        # Получаем данные пользователя
        user_info = user_data.get_user_data(user_id)
        
        # Устанавливаем период анализа - по умолчанию 1 день (24 часа)
        hours = 24
        
        # Получаем каналы для анализа
        channels = user_info['folders'].get(folder, [])
        if not channels:
            logger.warning(f"Папка {folder} пуста или не существует")
            return
        
        all_posts = []
                
        # Собираем данные из Telegram каналов
        for channel in channels:
            posts = await get_channel_posts(channel, hours=hours)
            if posts:
                for post in posts:
                    all_posts.append(f"[Telegram канал: {channel}]\n{post}")
        
        # Собираем данные из групп ВКонтакте
        if folder in user_info['vk_groups'] and user_info['vk_groups'][folder]:
            vk_handler = VkAPIHandler(VK_TOKEN)
            for group in user_info['vk_groups'][folder]:
                try:
                    vk_posts = vk_handler.get_posts(group, hours=hours)
                    if vk_posts:
                        for post in vk_posts:
                            all_posts.append(f"[ВКонтакте группа: {group}, {post['date']}]\n{post['text']}")
                except Exception as e:
                    logger.error(f"Ошибка при получении постов из ВК {group}: {str(e)}")
        
        # Собираем данные из веб-сайтов
        if folder in user_info['websites'] and user_info['websites'][folder]:
            web_parser = WebsiteParser()
            for site in user_info['websites'][folder]:
                try:
                    articles = web_parser.extract_news(site, hours=hours)
                    if articles:
                        for article in articles:
                            all_posts.append(f"[Сайт: {site}, {article['date']}]\n{article['title']}\n{article['text'][:1000]}...")
                except Exception as e:
                    logger.error(f"Ошибка при анализе сайта {site}: {str(e)}")
                
        if not all_posts:
            logger.error(f"Не удалось получить посты для автоматического анализа папки {folder}")
            return
            
        posts_text = "\n\n---\n\n".join(all_posts)
        prompt = user_info['prompts'][folder]
        
        # Выполняем запрос к модели ИИ
        response = await try_gpt_request(prompt, posts_text, user_id)
        
        # Сохраняем отчет в БД
        save_report(user_id, folder, response)
        
        # Генерируем отчеты в обоих форматах
        txt_filename = generate_txt_report(response, folder)
        
        try:
            pdf_filename = generate_pdf_report(response, folder)
        except Exception as e:
            logger.error(f"Ошибка при создании PDF: {str(e)}")
            pdf_filename = None
        
        # Отправляем отчет пользователю
        await bot.send_message(
            user_id,
            f"✅ Автоматический анализ папки {folder} завершен!\n"
            "Отчет прикреплен ниже."
        )
        
        # Отправляем TXT файл
        with open(txt_filename, 'rb') as f:
            await bot.send_document(
                user_id,
                f,
                caption=f"Отчет по папке {folder} (TXT)"
            )
        os.remove(txt_filename)
        
        # Отправляем PDF если есть
        if pdf_filename:
            with open(pdf_filename, 'rb') as f:
                await bot.send_document(
                    user_id,
                    f,
                    caption=f"Отчет по папке {folder} (PDF)"
                )
            os.remove(pdf_filename)
        
    except Exception as e:
        logger.error(f"Ошибка при автоматическом анализе: {str(e)}")

@dp.message_handler(lambda message: message.text == "🔄 Запустить анализ")
async def start_analysis(message: types.Message):
    user_id = message.from_user.id
    user_info = user_data.get_user_data(user_id)
    
    # Получаем все папки
    folders = {}
    
    # Объединяем все источники (папки могут иметь только определенные типы источников)
    for folder in user_info['folders']:
        folders[folder] = True
    
    for folder in user_info['vk_groups']:
        folders[folder] = True
        
    for folder in user_info['websites']:
        folders[folder] = True
    
    if not folders:
        await message.answer("У вас нет созданных папок. Используйте '📁 Создать папку' для начала.")
        return
        
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    
    # Добавляем только папки, у которых есть хотя бы один источник данных
    for folder in folders:
        has_data = False
        # Проверяем наличие Telegram каналов
        if folder in user_info['folders'] and user_info['folders'][folder]:
            has_data = True
        # Проверяем наличие VK групп
        elif folder in user_info['vk_groups'] and user_info['vk_groups'][folder]:
            has_data = True
        # Проверяем наличие веб-сайтов
        elif folder in user_info['websites'] and user_info['websites'][folder]:
            has_data = True
            
        if has_data:
            keyboard.add(types.InlineKeyboardButton(
                folder, callback_data=f"format_{folder}"
            ))
    
    # Добавляем кнопку "Анализировать все" и "Назад"
    keyboard.add(
        types.InlineKeyboardButton(
            "📊 Анализировать все папки", callback_data="format_all"
        )
    )
    keyboard.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_main"))
    
    await message.answer(
        "Выберите папку для анализа:",
        reply_markup=keyboard
    )

@dp.callback_query_handler(lambda c: c.data.startswith('format_'))
async def choose_format(callback_query: types.CallbackQuery):
    folder = callback_query.data.replace('format_', '')
    
    # Добавляем выбор периода сканирования
    keyboard = types.InlineKeyboardMarkup(row_width=3)
    if folder == 'all':
        keyboard.add(
            types.InlineKeyboardButton("1 день", callback_data=f"period_all_1"),
            types.InlineKeyboardButton("3 дня", callback_data=f"period_all_3"),
            types.InlineKeyboardButton("Неделя", callback_data=f"period_all_7")
        )
    else:
        keyboard.add(
            types.InlineKeyboardButton("1 день", callback_data=f"period_{folder}_1"),
            types.InlineKeyboardButton("3 дня", callback_data=f"period_{folder}_3"),
            types.InlineKeyboardButton("Неделя", callback_data=f"period_{folder}_7")
        )
    
    keyboard.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_folders"))
    
    await callback_query.message.edit_text(
        f"Выберите период сканирования для {'всех папок' if folder == 'all' else f'папки {folder}'}:",
        reply_markup=keyboard
    )

# Обработчик выбора периода сканирования
@dp.callback_query_handler(lambda c: c.data.startswith('period_'))
async def choose_period(callback_query: types.CallbackQuery):
    parts = callback_query.data.split('_')
    if len(parts) < 3:
        await callback_query.message.answer("❌ Ошибка в параметрах периода")
        return
    
    # parts[0] = 'period'
    folder = parts[1]  # 'all' или имя папки
    period = parts[2]  # период в днях: '1', '4', '7'
    
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    if folder == 'all':
        keyboard.add(
            types.InlineKeyboardButton("📝 TXT", callback_data=f"analyze_all_{period}_txt"),
            types.InlineKeyboardButton("📊 PDF", callback_data=f"analyze_all_{period}_pdf"),
            types.InlineKeyboardButton("📎 Оба формата", callback_data=f"analyze_all_{period}_both")
        )
    else:
        keyboard.add(
            types.InlineKeyboardButton("📝 TXT", callback_data=f"analyze_{folder}_{period}_txt"),
            types.InlineKeyboardButton("📊 PDF", callback_data=f"analyze_{folder}_{period}_pdf"),
            types.InlineKeyboardButton("📎 Оба формата", callback_data=f"analyze_{folder}_{period}_both")
        )
    
    keyboard.add(types.InlineKeyboardButton("🔙 Назад", callback_data=f"format_{folder}"))
    
    # Отображаем период в виде текста для удобства
    period_text = "1 день" if period == "1" else "3 дня" if period == "3" else "7 дней"
    
    await callback_query.message.edit_text(
        f"Выбран период: {period_text}\nВыберите формат отчета для {'всех папок' if folder == 'all' else f'папки {folder}'}:",
        reply_markup=keyboard
    )

@dp.callback_query_handler(lambda c: c.data.startswith('analyze_'))
async def process_analysis_choice(callback_query: types.CallbackQuery):
    # Парсим параметры из callback_data
    parts = callback_query.data.split('_')
    
    # Проверяем формат callback_data
    if len(parts) < 3:
        await callback_query.message.answer("❌ Ошибка в параметрах анализа")
        return
        
    # parts[0] = 'analyze'
    choice = parts[1]  # 'all' или имя папки
    
    # Устанавливаем значения по умолчанию
    period_days = 1  # По умолчанию сканируем 1 день
    format_type = 'txt'  # По умолчанию формат txt
    
    # Обрабатываем параметры в зависимости от количества частей
    if len(parts) == 3:
        # Старый формат: analyze_folder_format
        format_type = parts[2]
    elif len(parts) >= 4:
        # Новый формат: analyze_folder_period_format
        period_days = int(parts[2])
        format_type = parts[3]
    
    # Преобразуем период в часы для дальнейшей обработки
    hours = period_days * 24
    
    user_id = callback_query.from_user.id
    user_info = user_data.get_user_data(user_id)
    
    period_text = "1 день" if period_days == 1 else f"{period_days} дней"
    await callback_query.message.edit_text(f"Начинаю анализ за {period_text}... Это может занять некоторое время")
    
    if choice == 'all':
        folders = user_info['folders'].items()
    else:
        if choice not in user_info['folders']:
            await callback_query.message.answer(f"❌ Папка {choice} не найдена")
            return
        folders = [(choice, user_info['folders'][choice])]
    
    for folder, channels in folders:
        await callback_query.message.answer(f"Анализирую папку {folder}...")
        
        all_posts = []
        
        # Собираем данные из Telegram каналов
        for channel in channels:
            if not is_valid_channel(channel):
                continue
                
            posts = await get_channel_posts(channel, hours=hours)
            if posts:
                for post in posts:
                    all_posts.append(f"[Telegram канал: {channel}]\n{post}")
            else:
                await callback_query.message.answer(f"⚠️ Не удалось получить посты из канала {channel}")
        
        # Собираем данные из групп ВКонтакте
        if folder in user_info['vk_groups'] and user_info['vk_groups'][folder]:
            vk_handler = VkAPIHandler(VK_TOKEN)
            for group in user_info['vk_groups'][folder]:
                try:
                    vk_posts = vk_handler.get_posts(group, hours=hours)
                    if vk_posts:
                        for post in vk_posts:
                            all_posts.append(f"[ВКонтакте группа: {group}, {post['date']}]\n{post['text']}")
                    else:
                        await callback_query.message.answer(f"⚠️ Не удалось получить посты из группы ВК {group}")
                except Exception as e:
                    await callback_query.message.answer(f"❌ Ошибка при получении постов из ВК {group}: {str(e)}")
        
        # Собираем данные из веб-сайтов
        if folder in user_info['websites'] and user_info['websites'][folder]:
            web_parser = WebsiteParser()
            for site in user_info['websites'][folder]:
                try:
                    articles = web_parser.extract_news(site, hours=hours)
                    if articles:
                        for article in articles:
                            all_posts.append(f"[Сайт: {site}, {article['date']}]\n{article['title']}\n{article['text'][:1000]}...")
                    else:
                        await callback_query.message.answer(f"⚠️ Не удалось получить статьи с сайта {site}")
                except Exception as e:
                    await callback_query.message.answer(f"❌ Ошибка при анализе сайта {site}: {str(e)}")
        
        if not all_posts:
            await callback_query.message.answer(f"❌ Не удалось получить данные из источников в папке {folder}")
            continue
            
        posts_text = "\n\n---\n\n".join(all_posts)
        prompt = user_info['prompts'][folder]
        
        try:
            response = await try_gpt_request(prompt, posts_text, user_id)
            
            # Сохраняем отчет в БД
            save_report(user_id, folder, response)
            
            files_to_send = []
            
            # Генерируем отчеты в выбранном формате
            if format_type in ['txt', 'both']:
                txt_filename = generate_txt_report(response, folder)
                files_to_send.append(txt_filename)
                
            if format_type in ['pdf', 'both']:
                try:
                    pdf_filename = generate_pdf_report(response, folder)
                    files_to_send.append(pdf_filename)
                except Exception as pdf_error:
                    logger.error(f"Ошибка при создании PDF: {str(pdf_error)}")
                    await callback_query.message.answer("⚠️ Не удалось создать PDF версию отчета")
            
            # Отправляем файлы
            for filename in files_to_send:
                with open(filename, 'rb') as f:
                    await callback_query.message.answer_document(
                        f,
                        caption=f"✅ Анализ для папки {folder} за {period_text} ({os.path.splitext(filename)[1][1:].upper()})"
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
    # format: remove_channel_folder_index
    data = callback_query.data
    # Находим последнее подчеркивание для определения индекса
    last_underscore_pos = data.rfind('_')
    if last_underscore_pos == -1:
        await bot.answer_callback_query(callback_query.id, "Ошибка в формате данных")
        return
    
    try:
        # Извлекаем индекс из последней части
        index = int(data[last_underscore_pos + 1:])
        
        # Извлекаем имя папки из данных без последней части
        folder_part = data[:last_underscore_pos]
        folder = folder_part.replace('remove_channel_', '')
        
        user = user_data.get_user_data(callback_query.from_user.id)
        
        if folder in user['folders'] and 0 <= index < len(user['folders'][folder]):
            # Удаляем канал по индексу
            user['folders'][folder].pop(index)
            user_data.save()
            await bot.answer_callback_query(callback_query.id, "Канал успешно удален")
        else:
            await bot.answer_callback_query(callback_query.id, "Канал не найден")
        
        # Удаляем старое сообщение
        await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
        
        # Генерируем контент для нового сообщения
        content = f"📁 <b>Папка:</b> {folder}\n\n"
        markup = types.InlineKeyboardMarkup()
        
        # Добавляем основные кнопки управления папкой
        markup.add(types.InlineKeyboardButton("Добавить Telegram-каналы", 
                                           callback_data=f"add_channels_{folder}"))
        markup.add(types.InlineKeyboardButton("Добавить группы ВКонтакте", 
                                           callback_data=f"add_vk_groups_{folder}"))
        markup.add(types.InlineKeyboardButton("Добавить веб-сайты", 
                                           callback_data=f"add_websites_{folder}"))
        markup.add(types.InlineKeyboardButton("Изменить промпт", 
                                           callback_data=f"edit_prompt_{folder}"))
        markup.add(types.InlineKeyboardButton("Удалить папку", 
                                           callback_data=f"delete_folder_{folder}"))
        markup.add(types.InlineKeyboardButton("« Назад к папкам", 
                                           callback_data="back_to_folders"))
        
        # Список каналов
        channels = user['folders'].get(folder, [])
        if channels:
            content += "<b>Telegram-каналы:</b>\n"
            for i, channel in enumerate(channels, 1):
                content += f"{i}. {channel}\n"
                markup.add(types.InlineKeyboardButton(f"❌ Удалить канал {i}", 
                                                   callback_data=f"remove_channel_{folder}_{i-1}"))
            content += "\n"
        
        # Список групп ВК
        vk_groups = user['vk_groups'].get(folder, [])
        if vk_groups:
            content += "<b>Группы ВКонтакте:</b>\n"
            for i, group in enumerate(vk_groups, 1):
                content += f"{i}. {group}\n"
                markup.add(types.InlineKeyboardButton(f"❌ Удалить группу ВК {i}", 
                                                   callback_data=f"remove_vk_group_{folder}_{i-1}"))
            content += "\n"
        
        # Список сайтов
        websites = user['websites'].get(folder, [])
        if websites:
            content += "<b>Веб-сайты:</b>\n"
            for i, site in enumerate(websites, 1):
                content += f"{i}. {site}\n"
                markup.add(types.InlineKeyboardButton(f"❌ Удалить сайт {i}", 
                                                   callback_data=f"remove_website_{folder}_{i-1}"))
        
        # Отправляем новое сообщение с обновленным меню
        await bot.send_message(
            chat_id=callback_query.message.chat.id,
            text=content,
            reply_markup=markup,
            parse_mode='HTML'
        )
    except (ValueError, IndexError) as e:
        logger.error(f"Ошибка при удалении канала: {str(e)}")
        await bot.answer_callback_query(callback_query.id, "Ошибка при удалении канала")

@dp.callback_query_handler(lambda c: c.data.startswith('remove_vk_group_'))
async def remove_vk_group(callback_query: types.CallbackQuery):
    # format: remove_vk_group_folder_index
    data = callback_query.data
    # Находим последнее подчеркивание для определения индекса
    last_underscore_pos = data.rfind('_')
    if last_underscore_pos == -1:
        await bot.answer_callback_query(callback_query.id, "Ошибка в формате данных")
        return
    
    try:
        # Извлекаем индекс из последней части
        index = int(data[last_underscore_pos + 1:])
        
        # Извлекаем имя папки из данных без последней части
        folder_part = data[:last_underscore_pos]
        folder = folder_part.replace('remove_vk_group_', '')
        
        user = user_data.get_user_data(callback_query.from_user.id)
        
        if folder in user['vk_groups'] and 0 <= index < len(user['vk_groups'][folder]):
            # Удаляем группу ВК по индексу
            user['vk_groups'][folder].pop(index)
            user_data.save()
            await bot.answer_callback_query(callback_query.id, "Группа ВК успешно удалена")
        else:
            await bot.answer_callback_query(callback_query.id, "Группа ВК не найдена")
        
        # Удаляем старое сообщение
        await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
        
        # Генерируем контент для нового сообщения
        content = f"📁 <b>Папка:</b> {folder}\n\n"
        markup = types.InlineKeyboardMarkup()
        
        # Добавляем основные кнопки управления папкой
        markup.add(types.InlineKeyboardButton("Добавить Telegram-каналы", 
                                           callback_data=f"add_channels_{folder}"))
        markup.add(types.InlineKeyboardButton("Добавить группы ВКонтакте", 
                                           callback_data=f"add_vk_groups_{folder}"))
        markup.add(types.InlineKeyboardButton("Добавить веб-сайты", 
                                           callback_data=f"add_websites_{folder}"))
        markup.add(types.InlineKeyboardButton("Изменить промпт", 
                                           callback_data=f"edit_prompt_{folder}"))
        markup.add(types.InlineKeyboardButton("Удалить папку", 
                                           callback_data=f"delete_folder_{folder}"))
        markup.add(types.InlineKeyboardButton("« Назад к папкам", 
                                           callback_data="back_to_folders"))
        
        # Список каналов
        channels = user['folders'].get(folder, [])
        if channels:
            content += "<b>Telegram-каналы:</b>\n"
            for i, channel in enumerate(channels, 1):
                content += f"{i}. {channel}\n"
                markup.add(types.InlineKeyboardButton(f"❌ Удалить канал {i}", 
                                                   callback_data=f"remove_channel_{folder}_{i-1}"))
            content += "\n"
        
        # Список групп ВК
        vk_groups = user['vk_groups'].get(folder, [])
        if vk_groups:
            content += "<b>Группы ВКонтакте:</b>\n"
            for i, group in enumerate(vk_groups, 1):
                content += f"{i}. {group}\n"
                markup.add(types.InlineKeyboardButton(f"❌ Удалить группу ВК {i}", 
                                                   callback_data=f"remove_vk_group_{folder}_{i-1}"))
            content += "\n"
        
        # Список сайтов
        websites = user['websites'].get(folder, [])
        if websites:
            content += "<b>Веб-сайты:</b>\n"
            for i, site in enumerate(websites, 1):
                content += f"{i}. {site}\n"
                markup.add(types.InlineKeyboardButton(f"❌ Удалить сайт {i}", 
                                                   callback_data=f"remove_website_{folder}_{i-1}"))
        
        # Отправляем новое сообщение с обновленным меню
        await bot.send_message(
            chat_id=callback_query.message.chat.id,
            text=content,
            reply_markup=markup,
            parse_mode='HTML'
        )
    except (ValueError, IndexError) as e:
        logger.error(f"Ошибка при удалении группы ВК: {str(e)}")
        await bot.answer_callback_query(callback_query.id, "Ошибка при удалении группы ВК")

@dp.callback_query_handler(lambda c: c.data.startswith('remove_website_'))
async def remove_website(callback_query: types.CallbackQuery):
    # format: remove_website_folder_index
    data = callback_query.data
    # Находим последнее подчеркивание для определения индекса
    last_underscore_pos = data.rfind('_')
    if last_underscore_pos == -1:
        await bot.answer_callback_query(callback_query.id, "Ошибка в формате данных")
        return
    
    try:
        # Извлекаем индекс из последней части
        index = int(data[last_underscore_pos + 1:])
        
        # Извлекаем имя папки из данных без последней части
        folder_part = data[:last_underscore_pos]
        folder = folder_part.replace('remove_website_', '')
        
        user = user_data.get_user_data(callback_query.from_user.id)
        
        if folder in user['websites'] and 0 <= index < len(user['websites'][folder]):
            # Удаляем сайт по индексу
            user['websites'][folder].pop(index)
            user_data.save()
            await bot.answer_callback_query(callback_query.id, "Сайт успешно удален")
        else:
            await bot.answer_callback_query(callback_query.id, "Сайт не найден")
        
        # Удаляем старое сообщение
        await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
        
        # Генерируем контент для нового сообщения
        content = f"📁 <b>Папка:</b> {folder}\n\n"
        markup = types.InlineKeyboardMarkup()
        
        # Добавляем основные кнопки управления папкой
        markup.add(types.InlineKeyboardButton("Добавить Telegram-каналы", 
                                           callback_data=f"add_channels_{folder}"))
        markup.add(types.InlineKeyboardButton("Добавить группы ВКонтакте", 
                                           callback_data=f"add_vk_groups_{folder}"))
        markup.add(types.InlineKeyboardButton("Добавить веб-сайты", 
                                           callback_data=f"add_websites_{folder}"))
        markup.add(types.InlineKeyboardButton("Изменить промпт", 
                                           callback_data=f"edit_prompt_{folder}"))
        markup.add(types.InlineKeyboardButton("Удалить папку", 
                                           callback_data=f"delete_folder_{folder}"))
        markup.add(types.InlineKeyboardButton("« Назад к папкам", 
                                           callback_data="back_to_folders"))
        
        # Список каналов
        channels = user['folders'].get(folder, [])
        if channels:
            content += "<b>Telegram-каналы:</b>\n"
            for i, channel in enumerate(channels, 1):
                content += f"{i}. {channel}\n"
                markup.add(types.InlineKeyboardButton(f"❌ Удалить канал {i}", 
                                                   callback_data=f"remove_channel_{folder}_{i-1}"))
            content += "\n"
        
        # Список групп ВК
        vk_groups = user['vk_groups'].get(folder, [])
        if vk_groups:
            content += "<b>Группы ВКонтакте:</b>\n"
            for i, group in enumerate(vk_groups, 1):
                content += f"{i}. {group}\n"
                markup.add(types.InlineKeyboardButton(f"❌ Удалить группу ВК {i}", 
                                                   callback_data=f"remove_vk_group_{folder}_{i-1}"))
            content += "\n"
        
        # Список сайтов
        websites = user['websites'].get(folder, [])
        if websites:
            content += "<b>Веб-сайты:</b>\n"
            for i, site in enumerate(websites, 1):
                content += f"{i}. {site}\n"
                markup.add(types.InlineKeyboardButton(f"❌ Удалить сайт {i}", 
                                                   callback_data=f"remove_website_{folder}_{i-1}"))
        
        # Отправляем новое сообщение с обновленным меню
        await bot.send_message(
            chat_id=callback_query.message.chat.id,
            text=content,
            reply_markup=markup,
            parse_mode='HTML'
        )
    except (ValueError, IndexError) as e:
        logger.error(f"Ошибка при удалении сайта: {str(e)}")
        await bot.answer_callback_query(callback_query.id, "Ошибка при удалении сайта")

@dp.callback_query_handler(lambda c: c.data.startswith('edit_prompt_'))
async def edit_prompt_callback(callback_query: types.CallbackQuery, state: FSMContext):
    folder_name = callback_query.data.replace('edit_prompt_', '')
    user_id = callback_query.from_user.id
    user_info = user_data.get_user_data(user_id)
    
    # Сохраняем выбранную папку в состоянии
    await state.update_data(selected_folder=folder_name)
    
    # Отправляем текущий промпт и просим ввести новый
    await BotStates.waiting_for_prompt.set()
    await bot.send_message(
        user_id,
        f"Текущий промпт для папки {folder_name}:\n"
        f"{user_info['prompts'][folder_name]}\n\n"
        "Введи новый промпт:",
        reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 Назад")
    )

# Класс для работы с API ВКонтакте
class VkAPIHandler:
    def __init__(self, token):
        self.vk_session = vk_api.VkApi(token=token)
        self.vk = self.vk_session.get_api()
    
    def extract_group_id(self, url):
        # Поддержка форматов: vk.com/group, vk.com/public12345, @username
        match = re.search(r'(?:vk\.com/|@)([a-zA-Z0-9_\.]+)', url)
        if match:
            return match.group(1)
        # Поддержка числовых ID
        match = re.search(r'club(\d+)', url)
        if match:
            return f"-{match.group(1)}"
        return None
    
    def get_posts(self, group_url, hours=24):
        group_id = self.extract_group_id(group_url)
        if not group_id:
            return []
        
        # Вычисляем timestamp для фильтрации
        time_threshold = int((datetime.now() - timedelta(hours=hours)).timestamp())
        
        try:
            # Получаем посты со стены
            response = self.vk.wall.get(domain=group_id, count=50)
            posts = []
            
            for item in response['items']:
                # Пропускаем старые посты
                if item['date'] < time_threshold:
                    continue
                
                post_text = item.get('text', '')
                post_date = datetime.fromtimestamp(item['date'])
                
                # Собираем текст из вложений (если есть)
                attachments_text = ""
                if 'attachments' in item:
                    for attachment in item['attachments']:
                        if attachment['type'] == 'photo' and 'text' in attachment['photo']:
                            attachments_text += attachment['photo']['text'] + "\n"
                        elif attachment['type'] == 'link' and 'title' in attachment['link']:
                            attachments_text += attachment['link']['title'] + "\n"
                
                posts.append({
                    'text': post_text + "\n" + attachments_text,
                    'date': post_date,
                    'likes': item.get('likes', {}).get('count', 0),
                    'reposts': item.get('reposts', {}).get('count', 0),
                    'source': group_url,
                    'id': item['id']
                })
            
            return posts
        except Exception as e:
            print(f"Ошибка при получении постов из ВК: {e}")
            return []

# Класс для парсинга веб-сайтов
class WebsiteParser:
    def __init__(self):
        # Конфигурация для newspaper
        self.config = Config()
        self.config.browser_user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        self.config.request_timeout = 10
    
    def extract_news(self, url, hours=24):
        try:
            # Извлекаем базовый URL для поиска новостных статей
            base_url = self._get_base_url(url)
            
            # Сначала пытаемся найти ссылки на новостные статьи на странице
            article_links = self._find_article_links(url)
            
            articles = []
            time_threshold = datetime.now() - timedelta(hours=hours)
            
            # Обрабатываем каждую найденную ссылку на статью
            for link in article_links[:10]:  # Ограничиваем количество статей
                try:
                    full_url = self._get_full_url(link, base_url)
                    article = Article(full_url, config=self.config)
                    article.download()
                    article.parse()
                    
                    # Извлекаем дату, если есть
                    if article.publish_date and article.publish_date > time_threshold:
                        articles.append({
                            'title': article.title,
                            'text': article.text,
                            'date': article.publish_date,
                            'url': full_url
                        })
                except Exception as e:
                    print(f"Ошибка при парсинге статьи {link}: {e}")
                    continue
            
            # Если не нашли статей, парсим саму страницу
            if not articles:
                try:
                    article = Article(url, config=self.config)
                    article.download()
                    article.parse()
                    
                    articles.append({
                        'title': article.title,
                        'text': article.text[:5000],  # Ограничиваем длину текста
                        'date': datetime.now(),  # Предполагаем, что это актуальный контент
                        'url': url
                    })
                except Exception as e:
                    print(f"Ошибка при парсинге основной страницы {url}: {e}")
            
            return articles
            
        except Exception as e:
            print(f"Общая ошибка при парсинге сайта {url}: {e}")
            return []
    
    def _get_base_url(self, url):
        # Извлекаем базовый URL (схема + домен)
        match = re.search(r'(https?://[^/]+)', url)
        return match.group(1) if match else url
    
    def _get_full_url(self, link, base_url):
        # Формируем полный URL если ссылка относительная
        if link.startswith('http'):
            return link
        return f"{base_url.rstrip('/')}/{link.lstrip('/')}"
    
    def _find_article_links(self, url):
        try:
            response = requests.get(url, headers={'User-Agent': self.config.browser_user_agent})
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Ищем ссылки в типичных контейнерах для новостей
            links = []
            
            # Ищем по классам, обычно используемым для новостных элементов
            news_containers = soup.select('.news, .article, .post, article, .news-item, .entry')
            for container in news_containers:
                a_tags = container.find_all('a', href=True)
                for a in a_tags:
                    links.append(a['href'])
            
            # Если не нашли по контейнерам, ищем все ссылки с ключевыми словами
            if not links:
                for a in soup.find_all('a', href=True):
                    href = a['href']
                    if 'news' in href or 'article' in href or 'post' in href:
                        links.append(href)
            
            return list(set(links))  # Удаляем дубликаты
        except Exception as e:
            print(f"Ошибка при поиске ссылок на статьи: {e}")
            return []

# Добавляем обработчики для ВК групп
@dp.callback_query_handler(lambda c: c.data.startswith('add_vk_groups_'))
async def add_vk_groups_start(callback_query: types.CallbackQuery, state: FSMContext):
    folder_name = callback_query.data.replace('add_vk_groups_', '')
    
    # Сохраняем имя папки в состоянии
    await state.update_data(folder=folder_name)
    
    await BotStates.waiting_for_vk_groups.set()
    await callback_query.message.answer(
        "Отправьте ссылки на группы ВКонтакте для добавления в папку.\n"
        "Каждая ссылка должна быть в отдельном сообщении. "
        "Когда закончите, отправьте /done"
    )
    await callback_query.answer()

@dp.message_handler(state=BotStates.waiting_for_vk_groups)
async def process_vk_groups(message: types.Message, state: FSMContext):
    if message.text == '/done':
        # Заканчиваем добавление групп
        data = await state.get_data()
        folder = data.get('folder')
        
        # Подтверждаем добавление
        await message.answer(f"Группы ВКонтакте добавлены в папку {folder}")
        
        # Возвращаемся к меню редактирования папки
        await state.finish()
        
        # Возвращаем пользователя к списку папок с обновленными данными
        user_info = user_data.get_user_data(message.from_user.id)
        
        # Создаем сообщение со списком источников
        content = f"📁 <b>Папка:</b> {folder}\n\n"
        
        channels = user_info['folders'].get(folder, [])
        if channels:
            content += "<b>Telegram-каналы:</b>\n"
            for i, channel in enumerate(channels, 1):
                content += f"{i}. {channel}\n"
            content += "\n"
        
        vk_groups = user_info['vk_groups'].get(folder, [])
        if vk_groups:
            content += "<b>Группы ВКонтакте:</b>\n"
            for i, group in enumerate(vk_groups, 1):
                content += f"{i}. {group}\n"
            content += "\n"
            
        websites = user_info['websites'].get(folder, [])
        if websites:
            content += "<b>Веб-сайты:</b>\n"
            for i, site in enumerate(websites, 1):
                content += f"{i}. {site}\n"
                
        # Создаем клавиатуру
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("Добавить Telegram-каналы", 
                                       callback_data=f"add_channels_{folder}"))
        markup.add(types.InlineKeyboardButton("Добавить группы ВКонтакте", 
                                       callback_data=f"add_vk_groups_{folder}"))
        markup.add(types.InlineKeyboardButton("Добавить веб-сайты", 
                                       callback_data=f"add_websites_{folder}"))
        markup.add(types.InlineKeyboardButton("Изменить промпт", 
                                       callback_data=f"edit_prompt_{folder}"))
        markup.add(types.InlineKeyboardButton("Удалить папку", 
                                       callback_data=f"delete_folder_{folder}"))
        markup.add(types.InlineKeyboardButton("« Назад к папкам", 
                                       callback_data="back_to_folders"))
        
        # Добавляем кнопки удаления для каждого элемента
        if channels:
            for i, channel in enumerate(channels, 1):
                markup.add(types.InlineKeyboardButton(f"❌ Удалить канал {i}", 
                                                callback_data=f"remove_channel_{folder}_{i-1}"))
        
        if vk_groups:
            for i, group in enumerate(vk_groups, 1):
                markup.add(types.InlineKeyboardButton(f"❌ Удалить группу ВК {i}", 
                                                callback_data=f"remove_vk_group_{folder}_{i-1}"))
        
        if websites:
            for i, site in enumerate(websites, 1):
                markup.add(types.InlineKeyboardButton(f"❌ Удалить сайт {i}", 
                                                callback_data=f"remove_website_{folder}_{i-1}"))
        
        # Отправляем новое сообщение с обновленным меню
        await message.answer(content, reply_markup=markup, parse_mode='HTML')
        return
    
    # Добавляем обработку кнопки отмены
    if message.text.lower() == 'отмена':
        await state.finish()
        await message.answer("Добавление групп ВКонтакте отменено.")
        return
    
    # Проверяем, что ссылка на группу ВК валидная
    vk_link = message.text.strip()
    if not (vk_link.startswith('https://vk.com/') or 
            vk_link.startswith('http://vk.com/') or 
            vk_link.startswith('vk.com/')):
        await message.answer("Некорректная ссылка. Пожалуйста, отправьте ссылку в формате vk.com/groupname")
        return
    
    # Стандартизируем ссылку
    if not vk_link.startswith('https://'):
        if vk_link.startswith('http://'):
            vk_link = 'https://' + vk_link[7:]
        else:
            vk_link = 'https://' + vk_link
    
    # Получаем данные из состояния
    data = await state.get_data()
    folder = data.get('folder')
    
    # Получаем данные пользователя
    user_info = user_data.get_user_data(message.from_user.id)
    
    # Проверяем, существует ли ключ 'vk_groups' и создаем его при необходимости
    if 'vk_groups' not in user_info:
        user_info['vk_groups'] = {}
    
    # Проверяем, существует ли ключ папки в 'vk_groups' и создаем его при необходимости
    if folder not in user_info['vk_groups']:
        user_info['vk_groups'][folder] = []
    
    # Добавляем группу ВК в папку
    if vk_link not in user_info['vk_groups'][folder]:
        user_info['vk_groups'][folder].append(vk_link)
        user_data.save()
        await message.answer(f"Группа {vk_link} добавлена в папку {folder}")
    else:
        await message.answer(f"Группа {vk_link} уже есть в папке {folder}")

# Добавляем обработчики для веб-сайтов
@dp.callback_query_handler(lambda c: c.data.startswith('add_websites_'))
async def add_websites_start(callback_query: types.CallbackQuery, state: FSMContext):
    folder_name = callback_query.data.replace('add_websites_', '')
    
    # Сохраняем имя папки в состоянии
    await state.update_data(folder=folder_name)
    
    await BotStates.waiting_for_websites.set()
    await callback_query.message.answer(
        "Отправьте ссылки на веб-сайты для добавления в папку.\n"
        "Каждая ссылка должна быть в отдельном сообщении. "
        "Когда закончите, отправьте /done"
    )
    await callback_query.answer()

@dp.message_handler(state=BotStates.waiting_for_websites)
async def process_websites(message: types.Message, state: FSMContext):
    if message.text == '/done':
        # Заканчиваем добавление сайтов
        data = await state.get_data()
        folder = data.get('folder')
        
        # Подтверждаем добавление
        await message.answer(f"Веб-сайты добавлены в папку {folder}")
        
        # Возвращаемся к меню редактирования папки
        await state.finish()
        
        # Возвращаем пользователя к списку папок с обновленными данными
        user_info = user_data.get_user_data(message.from_user.id)
        
        # Создаем сообщение со списком источников
        content = f"📁 <b>Папка:</b> {folder}\n\n"
        
        channels = user_info['folders'].get(folder, [])
        if channels:
            content += "<b>Telegram-каналы:</b>\n"
            for i, channel in enumerate(channels, 1):
                content += f"{i}. {channel}\n"
            content += "\n"
        
        vk_groups = user_info['vk_groups'].get(folder, [])
        if vk_groups:
            content += "<b>Группы ВКонтакте:</b>\n"
            for i, group in enumerate(vk_groups, 1):
                content += f"{i}. {group}\n"
            content += "\n"
            
        websites = user_info['websites'].get(folder, [])
        if websites:
            content += "<b>Веб-сайты:</b>\n"
            for i, site in enumerate(websites, 1):
                content += f"{i}. {site}\n"
                
        # Создаем клавиатуру
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("Добавить Telegram-каналы", 
                                       callback_data=f"add_channels_{folder}"))
        markup.add(types.InlineKeyboardButton("Добавить группы ВКонтакте", 
                                       callback_data=f"add_vk_groups_{folder}"))
        markup.add(types.InlineKeyboardButton("Добавить веб-сайты", 
                                       callback_data=f"add_websites_{folder}"))
        markup.add(types.InlineKeyboardButton("Изменить промпт", 
                                       callback_data=f"edit_prompt_{folder}"))
        markup.add(types.InlineKeyboardButton("Удалить папку", 
                                       callback_data=f"delete_folder_{folder}"))
        markup.add(types.InlineKeyboardButton("« Назад к папкам", 
                                       callback_data="back_to_folders"))
        
        # Добавляем кнопки удаления для каждого элемента
        if channels:
            for i, channel in enumerate(channels, 1):
                markup.add(types.InlineKeyboardButton(f"❌ Удалить канал {i}", 
                                                callback_data=f"remove_channel_{folder}_{i-1}"))
        
        if vk_groups:
            for i, group in enumerate(vk_groups, 1):
                markup.add(types.InlineKeyboardButton(f"❌ Удалить группу ВК {i}", 
                                                callback_data=f"remove_vk_group_{folder}_{i-1}"))
        
        if websites:
            for i, site in enumerate(websites, 1):
                markup.add(types.InlineKeyboardButton(f"❌ Удалить сайт {i}", 
                                                callback_data=f"remove_website_{folder}_{i-1}"))
        
        # Отправляем новое сообщение с обновленным меню
        await message.answer(content, reply_markup=markup, parse_mode='HTML')
        return
    
    # Добавляем обработку кнопки отмены
    if message.text.lower() == 'отмена':
        await state.finish()
        await message.answer("Добавление веб-сайтов отменено.")
        return
    
    # Проверяем, что ссылка на сайт валидная
    website = message.text.strip()
    if not (website.startswith('http://') or website.startswith('https://')):
        website = 'https://' + website
    
    try:
        # Простая проверка валидности URL
        result = urlparse(website)
        if not all([result.scheme, result.netloc]):
            await message.answer("Некорректная ссылка. Пожалуйста, отправьте ссылку в формате example.com или https://example.com")
            return
    except:
        await message.answer("Некорректная ссылка. Пожалуйста, отправьте ссылку в формате example.com или https://example.com")
        return
    
    # Получаем данные из состояния
    data = await state.get_data()
    folder = data.get('folder')
    
    # Получаем данные пользователя
    user_info = user_data.get_user_data(message.from_user.id)
    
    # Проверяем, существует ли ключ 'websites' и создаем его при необходимости
    if 'websites' not in user_info:
        user_info['websites'] = {}
    
    # Проверяем, существует ли ключ папки в 'websites' и создаем его при необходимости
    if folder not in user_info['websites']:
        user_info['websites'][folder] = []
    
    # Добавляем сайт в папку
    if website not in user_info['websites'][folder]:
        user_info['websites'][folder].append(website)
        user_data.save()
        await message.answer(f"Сайт {website} добавлен в папку {folder}")
    else:
        await message.answer(f"Сайт {website} уже есть в папке {folder}")

async def main():
    # Запускаем клиент Telethon
    await client.start()
    
    # Получаем инфу о боте
    me = await bot.get_me()
    logger.info(f"Бот @{me.username} запущен!")
    
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
    
    # Запускаем бота
    await dp.start_polling()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Останавливаем планировщик при выходе
        scheduler.shutdown()
        logger.info("Бот остановлен") 