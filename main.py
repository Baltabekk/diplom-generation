import asyncio
import logging
import random
import time
import json
import os
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters.command import Command
from aiogram.types import FSInputFile, ReplyKeyboardMarkup, KeyboardButton
from docx import Document
from docx.shared import Pt
from docx.enum.style import WD_STYLE_TYPE
import google.generativeai as genai
from google.api_core import retry, exceptions
from cachetools import TTLCache

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Установка констант и ключей API
ADMIN_ID = 1419048544
DAILY_LIMIT = 3
TELEGRAM_TOKEN = '6790686493:AAGYsdk5DVLrmccPT87Li49P1RBEik60-48'
GENAI_API_KEYS = [
    'AIzaSyCtrFiYRihVUm_L58vS-c_8MEyZX7VLLv0',
    'AIzaSyDr_zy732Xybb1xZ1LEpEH31h6PjgnWInQ',
    'AIzaSyDC77JAG0lzGalAJ0AXHUcGbllJXISRxKg',
    'AIzaSyClo-DqTkK3WgI1clFzzB9kgrxUI2WPBfQ',
    'AIzaSyDEB16tbcEC0PXyaSsMdEmAODyOxtFl13o',
    'AIzaSyA-4POY_6MCtS3IHcF5ZVJZzxoTRihvJr0',
    'AIzaSyAadHm1s8dwkOMYdSfPtO5ArzXx7ZyA0UE',
    'AIzaSyBUfVBbCcAPChN8hW-zu4q3C_etZpK5yVo',
    'AIzaSyB7SIZ7WWIUskFEyqD2q-lb3lq3WV0c2mY',
    'AIzaSyAzQv3icQbhrXIvL5iuRDy7PaJdJU3fAzU'
]

# Пути к файлам для хранения данных
USER_DATA_FILE = 'user_data.json'
FEEDBACK_FILE = 'feedback.json'

# Инициализация бота и диспетчера
bot = Bot(token=TELEGRAM_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Кэш для хранения сгенерированного контента
cache = TTLCache(maxsize=100, ttl=3600)

# Определение состояний
class GenerationStates(StatesGroup):
    SELECT_DOCUMENT_SIZE = State()
    GENERATING_SECTIONS = State()
    LEAVE_FEEDBACK = State()
    SEND_MESSAGE_TO_ALL = State()

# Пользовательская стратегия повторных попыток
custom_retry = retry.Retry(
    initial=1.0,
    maximum=60.0,
    multiplier=2.0,
    predicate=retry.if_exception_type(
        exceptions.DeadlineExceeded, exceptions.ServiceUnavailable
    )
)

# Функции для работы с данными
def load_data(file_path, default=None):
    if os.path.exists(file_path):
        with open(file_path, 'r') as file:
            return json.load(file)
    return default if default is not None else {}

def save_data(data, file_path):
    with open(file_path, 'w') as file:
        json.dump(data, file, indent=4, default=str)

# Загрузка данных при запуске бота
user_data = load_data(USER_DATA_FILE, {})
feedback_storage = load_data(FEEDBACK_FILE, [])

def get_user_data(user_id):
    user_id = str(user_id)
    if user_id not in user_data:
        user_data[user_id] = {
            'requests': 0,
            'last_reset': str(datetime.now()),
            'is_admin': user_id == str(ADMIN_ID),
            'start_date': str(datetime.now()),
            'username': None,
            'feedback': [],
            'requested_topic': None
        }
    return user_data[user_id]

def check_and_update_quota(user_id, is_document_generation=False):
    user_id = str(user_id)
    data = get_user_data(user_id)
    if data['is_admin']:
        return True, "Админ имеет неограниченный доступ."

    now = datetime.now()
    last_reset = datetime.fromisoformat(data['last_reset'])
    if (now - last_reset).days >= 1:
        data['requests'] = 0
        data['last_reset'] = str(now)

    if is_document_generation and data['requests'] >= DAILY_LIMIT:
        next_reset = last_reset + timedelta(days=1)
        return False, f"Достигнут дневной лимит. Следующее обновление: {next_reset.strftime('%Y-%m-%d %H:%M:%S')}."

    if is_document_generation:
        data['requests'] += 1
        remaining = DAILY_LIMIT - data['requests']
        save_data(user_data, USER_DATA_FILE)
        return True, f"Запрос принят. Осталось запросов сегодня: {remaining}."
    
    return True, f"Осталось запросов сегодня: {DAILY_LIMIT - data['requests']}."

def get_remaining_requests(user_id):
    data = get_user_data(str(user_id))
    if data['is_admin']:
        return "Неограниченно (админ)"
    remaining = DAILY_LIMIT - data['requests']
    return str(remaining)

def get_random_api_key():
    return random.choice(GENAI_API_KEYS)

def initialize_model(api_key):
    genai.configure(api_key=api_key)
    return genai.GenerativeModel('gemini-pro')

current_model = initialize_model(get_random_api_key())

@dp.message(Command("start"))
async def send_welcome(message: types.Message):
    user_data = get_user_data(message.from_user.id)
    user_data['username'] = message.from_user.username
    logger.info(f"Пользователь {message.from_user.id} начал взаимодействие")
    
    markup = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="/help"), KeyboardButton(text="/menu")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    
    await message.reply("Привет! Я бот, который поможет вам создать дипломную работу. Напишите /help для списка команд.", reply_markup=markup)

@dp.message(Command("help"))
async def send_help(message: types.Message):
    logger.info(f"Пользователь {message.from_user.id} запросил помощь")
    help_text = (
        "Доступные команды:\n"
        "/start - Начать взаимодействие\n"
        "/generate - Создать содержание и текст\n"
        "/quota - Лимиты\n"
        "/leave_feedback - Оставить отзыв\n"
        "/view_feedback - Посмотреть отзывы\n"
        "/contact_admins - Связаться с админами\n"
        "/admin_menu - Меню администратора"
    )
    await message.reply(help_text)

@dp.message(Command("menu"))
async def send_menu(message: types.Message):
    markup = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="/generate")],
            [KeyboardButton(text="/leave_feedback"), KeyboardButton(text="/view_feedback")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await message.reply("Выберите команду:", reply_markup=markup)

async def generate_content_with_cache(prompt):
    global current_model
    if prompt in cache:
        return cache[prompt]

    max_retries = len(GENAI_API_KEYS)
    for attempt in range(max_retries):
        try:
            response = await asyncio.to_thread(custom_retry(current_model.generate_content), prompt)
            content = response.text.strip()
            cache[prompt] = content
            return content
        except exceptions.ResourceExhausted:
            logger.warning(f"API ключ исчерпан. Переключение на следующий ключ. Попытка {attempt + 1} из {max_retries}")
            current_model = initialize_model(get_random_api_key())
        except Exception as e:
            logger.error(f"Ошибка при генерации контента: {str(e)}", exc_info=True)
            raise Exception("Все API ключи исчерпаны. Невозможно сгенерировать контент.")

@dp.message(Command("generate"))
async def generate_command(message: types.Message, state: FSMContext):
    markup = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="40 страниц")],
            [KeyboardButton(text="60 страниц")],
            [KeyboardButton(text="100 страниц")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await message.reply("Выберите размер документа:", reply_markup=markup)
    await state.set_state(GenerationStates.SELECT_DOCUMENT_SIZE)

@dp.message(GenerationStates.SELECT_DOCUMENT_SIZE)
async def process_document_size(message: types.Message, state: FSMContext):
    size = message.text.lower()
    word_count = 0
    
    if "40" in size:
        word_count = 200
    elif "60" in size:
        word_count = 300
    elif "100" in size:
        word_count = 500
    else:
        await message.reply("Пожалуйста, выберите размер документа из предложенных вариантов.")
        return

    await state.update_data(word_count=word_count)
    await message.reply("Отлично! Теперь введите тему вашей дипломной работы.")
    await state.set_state(GenerationStates.GENERATING_SECTIONS)

@dp.message(GenerationStates.GENERATING_SECTIONS)
async def receive_topic(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    topic = message.text.strip()
    logger.info(f"Пользователь {user_id} указал тему: {topic}")
    
    user_data = get_user_data(user_id)
    user_data['requested_topic'] = topic
    
    can_proceed, quota_message = check_and_update_quota(user_id, is_document_generation=True)
    if not can_proceed:
        await message.reply(quota_message)
        return
    
    await message.reply(f"{quota_message}\nГенерация содержания...")
    prompt = f"Создайте подробное содержание для дипломной работы на тему '{topic}'. Включите введение, 5-7 основных разделов с 3-4 подразделами каждый, и заключение."
    
    try:
        content = await generate_content_with_cache(prompt)
        logger.info(f"Содержание сгенерировано для пользователя {user_id}")
        await message.reply(f"Содержание:\n{content}")
        
        sections = ["Введение"] + [section.strip() for section in content.split('\n') if section.strip()] + ["Заключение"]
        await state.update_data(topic=topic, sections=sections)
        
        status_message = await message.reply("Начинаю генерацию документа. Это займет некоторое время.\nПрогресс: 0%")
        
        word_count = (await state.get_data()).get('word_count', 500)
        await generate_sections(message, state, status_message, word_count)
    except Exception as e:
        logger.error(f"Ошибка при генерации содержания: {str(e)}", exc_info=True)
        await message.reply("Произошла ошибка при генерации содержания. Пожалуйста, попробуйте еще раз или обратитесь к администратору.")

async def generate_section(topic, section, word_count=500):
    if section == "Введение":
        prompt = f"Напишите подробное введение (около {word_count} слов) для дипломной работы на тему '{topic}'. Включите актуальность темы, цели и задачи исследования."
    elif section == "Заключение":
        prompt = f"Напишите подробное заключение (около {word_count} слов) для дипломной работы на тему '{topic}'. Подведите итоги исследования, сформулируйте основные выводы."
    else:
        prompt = f"Напишите подробный текст (около {word_count} слов) для раздела '{section}' дипломной работы на тему '{topic}'. Включите теоретическую базу, анализ и практические аспекты."
    
    return section, await generate_content_with_cache(prompt)

async def generate_sections(message: types.Message, state: FSMContext, status_message: types.Message, word_count=500):
    data = await state.get_data()
    sections = data.get('sections', [])
    topic = data.get('topic')
    total_sections = len(sections)
    start_time = time.time()
    results = []

    for i, section in enumerate(sections):
        try:
            section_result = await generate_section(topic, section, word_count)
            results.append(section_result)
            await update_progress(status_message, start_time, i + 1, total_sections)
        except Exception as e:
            logger.error(f"Ошибка при генерации раздела '{section}': {str(e)}", exc_info=True)
            await message.reply(f"Произошла ошибка при генерации раздела '{section}'. Пропускаю этот раздел.")

    await state.update_data(results=results)
    
    user_id = message.from_user.id
    can_proceed, quota_message = check_and_update_quota(user_id, is_document_generation=True)
    if not can_proceed:
        await message.reply(quota_message)
        return

    await finalize_document(message, state, status_message)

async def update_progress(status_message: types.Message, start_time: float, completed_sections: int, total_sections: int):
    progress = int((completed_sections / total_sections) * 100)
    elapsed_time = int(time.time() - start_time)
    await status_message.edit_text(f"Генерация документа.\nПрошло времени: {elapsed_time // 60} мин {elapsed_time % 60} сек\nПрогресс: {progress}%")

@dp.message(Command("quota"))
async def check_quota(message: types.Message):
    user_id = message.from_user.id
    remaining = get_remaining_requests(user_id)
    await message.reply(f"Оставшиеся запросы на сегодня: {remaining}")

@dp.message(Command("leave_feedback"))
async def leave_feedback(message: types.Message, state: FSMContext):
    await message.reply("Пожалуйста, напишите ваш отзыв.")
    await state.set_state(GenerationStates.LEAVE_FEEDBACK)

@dp.message(GenerationStates.LEAVE_FEEDBACK)
async def process_feedback(message: types.Message, state: FSMContext):
    feedback = message.text.strip()
    feedback_storage.append(feedback)
    user_id = str(message.from_user.id)
    user_data[user_id]['feedback'].append(feedback)
    save_data(feedback_storage, FEEDBACK_FILE)
    save_data(user_data, USER_DATA_FILE)
    await message.reply("Ваш отзыв был успешно оставлен.")
    await state.clear()

@dp.message(Command("view_feedback"))
async def view_feedback(message: types.Message):
    if not feedback_storage:
        await message.reply("Отзывов пока нет.")
    else:
        feedback_list = "\n".join(feedback_storage)
        await message.reply(f"Отзывы:\n{feedback_list}")

@dp.message(Command("contact_admins"))
async def contact_admins(message: types.Message):
    await message.reply("Напишите ваше сообщение для администраторов.")

@dp.message(Command("admin_menu"))
async def admin_menu(message: types.Message):
    if str(message.from_user.id) != str(ADMIN_ID):
        await message.reply("У вас нет прав для доступа к этому меню.")
        return

    markup = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="/view_participants")],
            [KeyboardButton(text="/view_feedback")],
            [KeyboardButton(text="/send_message_to_all")]
        ],
        resize_keyboard=True
    )
    await message.reply("Меню администратора:", reply_markup=markup)

@dp.message(Command("view_participants"))
async def view_participants(message: types.Message):
    if str(message.from_user.id) != str(ADMIN_ID):
        await message.reply("У вас нет прав для выполнения этой команды.")
        return
    
    participants = "\n".join([f"ID: {user_id}, Username: {data.get('username', 'Не указан')}" for user_id, data in user_data.items()])
    await message.reply(f"Список участников:\n{participants}")

@dp.message(Command("send_message_to_all"))
async def send_message_to_all(message: types.Message, state: FSMContext):
    if str(message.from_user.id) != str(ADMIN_ID):
        await message.reply("У вас нет прав для выполнения этой команды.")
        return
    
    await message.reply("Введите сообщение для всех пользователей.")
    await state.set_state(GenerationStates.SEND_MESSAGE_TO_ALL)

@dp.message(GenerationStates.SEND_MESSAGE_TO_ALL)
async def process_send_message_to_all(message: types.Message, state: FSMContext):
    if str(message.from_user.id) != str(ADMIN_ID):
        await message.reply("У вас нет прав для выполнения этой команды.")
        await state.clear()
        return
    
    message_text = message.text.strip()
    for user_id in user_data.keys():
        try:
            await bot.send_message(user_id, message_text)
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение пользователю {user_id}: {str(e)}")
    await message.reply("Сообщение отправлено всем пользователям.")
    await state.clear()

async def finalize_document(message: types.Message, state: FSMContext, status_message: types.Message):
    data = await state.get_data()
    topic = data.get('topic')
    results = data.get('results', [])
    
    if not results:
        logger.error(f"Ошибка при создании документа для пользователя {message.from_user.id}")
        await message.reply("Ошибка при создании документа.")
        await state.clear()
        return
    
    logger.info(f"Создание итогового документа для пользователя {message.from_user.id}")
    await status_message.edit_text("Создание итогового документа... Прогресс: 100%")
    
    # Создание документа и его форматирование
    doc = Document()
    styles = doc.styles
    toc_style = styles.add_style('TOC', WD_STYLE_TYPE.PARAGRAPH)
    toc_style.font.size = Pt(12)
    toc_style.font.name = 'Times New Roman'
    
    doc.add_heading(f"Дипломная работа на тему: {topic}", level=0)
    doc.add_paragraph("Оглавление")
    
    for section, _ in results:
        doc.add_paragraph(section, style='TOC')
    
    doc.add_page_break()
    
    for section, text in results:
        doc.add_heading(section, level=1)
        paragraphs = text.split('\n\n')
        
        for para in paragraphs:
            new_para = doc.add_paragraph()
            if para.startswith("* ") and not para.startswith("**"):
                para = "•" + para[1:].strip()
            
            pos = 0
            while pos < len(para):
                if para[pos:pos + 2] == '**':
                    end_pos = para.find('**', pos + 2)
                    if end_pos != -1:
                        run = new_para.add_run(para[pos + 2:end_pos])
                        run.bold = True
                        pos = end_pos + 2
                    else:
                        new_para.add_run(para[pos:])
                        break
                else:
                    new_para.add_run(para[pos])
                    pos += 1
    
    # Сохранение документа
    file_path = f"{message.from_user.id}_{topic.replace(' ', '_')}.docx"
    doc.save(file_path)
    logger.info(f"Документ сохранен: {file_path}")
    
    # Отправка документа пользователю
    input_file = FSInputFile(file_path)
    logger.info(f"Отправка документа пользователю {message.from_user.id}")
    await message.reply_document(input_file, caption=f"Ваш документ на тему '{topic}' готов!")
    
    # Очистка состояния
    await state.clear()

async def periodic_save():
    while True:
        await asyncio.sleep(300)  # Сохраняем каждые 5 минут
        save_data(user_data, USER_DATA_FILE)
        save_data(feedback_storage, FEEDBACK_FILE)
        logger.info("Данные пользователей и отзывы сохранены")

async def main():
    logger.info("Бот запущен")
    asyncio.create_task(periodic_save())
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
