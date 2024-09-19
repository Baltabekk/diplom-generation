import asyncio
import logging
import random
import time
import json
import os
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import FSInputFile, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
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
TELEGRAM_TOKEN = '7414905635:AAHBlef17Zjo0x13nrTCV0X410fiyY1TOKQ'
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
    WAITING_FOR_TOPIC = State()
    GENERATING_CONTENT = State()
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
            'requested_topic': None,
            'referral_link': f"https://t.me/gendiplom_bot?start={user_id}",
            'referred_by': None,
            'referral_count': 0,
            'bonus_requests': 0
        }
    elif 'referral_link' not in user_data[user_id]:
        user_data[user_id]['referral_link'] = f"https://t.me/gendiplom_bot?start={user_id}"
    return user_data[user_id]

def process_referral(new_user_id, referrer_id):
    new_user_data = get_user_data(new_user_id)
    if new_user_data['referred_by']:
        return False, "Вы уже были приглашены другим пользователем."
    
    referrer_data = get_user_data(referrer_id)
    new_user_data['referred_by'] = referrer_id
    referrer_data['referral_count'] += 1
    referrer_data['bonus_requests'] += 2  # Увеличенный бонус для пригласившего
    new_user_data['bonus_requests'] += 3  # Увеличенный бонус для нового пользователя
    save_data(user_data, USER_DATA_FILE)
    return True, "Вы успешно присоединились по реферальной ссылке! Вы получили 3 дополнительных запроса."

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

    total_requests = DAILY_LIMIT + data['bonus_requests']

    if is_document_generation and data['requests'] >= total_requests:
        next_reset = last_reset + timedelta(days=1)
        return False, f"Достигнут дневной лимит. Следующее обновление: {next_reset.strftime('%Y-%m-%d %H:%M:%S')}."

    if is_document_generation:
        data['requests'] += 1
        if data['requests'] > DAILY_LIMIT and data['bonus_requests'] > 0:
            data['bonus_requests'] -= 1
        remaining = total_requests - data['requests']
        save_data(user_data, USER_DATA_FILE)
        return True, f"Запрос принят. Осталось запросов сегодня: {remaining}."
    
    return True, f"Осталось запросов сегодня: {total_requests - data['requests']}."

def get_remaining_requests(user_id):
    data = get_user_data(str(user_id))
    if data['is_admin']:
        return "Неограниченно (админ)"
    total_requests = DAILY_LIMIT + data['bonus_requests']
    remaining = total_requests - data['requests']
    return str(remaining)

def get_random_api_key():
    return random.choice(GENAI_API_KEYS)

def initialize_model(api_key):
    genai.configure(api_key=api_key)
    return genai.GenerativeModel('gemini-pro')

current_model = initialize_model(get_random_api_key())

# Функция для создания главной клавиатуры
def get_main_menu_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Создать дипломную работу")],
            [KeyboardButton(text="Оставить отзыв"), KeyboardButton(text="Посмотреть отзывы")],
            [KeyboardButton(text="FAQ"), KeyboardButton(text="О нас")],
            [KeyboardButton(text="Моя реферальная ссылка")]
        ],
        resize_keyboard=True
    )

@dp.message(Command("start"))
async def start_command(message: types.Message):
    user_id = message.from_user.id
    user_data = get_user_data(user_id)
    
    # Проверка на реферальную ссылку
    args = message.get_url()
    if args:
        referrer_id = args
        success, referral_message = await process_referral(user_id, referrer_id)
        if success:
            await message.reply(referral_message)
    
    welcome_text = f"Привет, {message.from_user.first_name}!\nДобро пожаловать в наш бот."
    await message.reply(welcome_text, reply_markup=get_main_menu_keyboard())

async def process_referral(new_user_id, referrer_id):
    new_user_data = get_user_data(new_user_id)
    if new_user_data['referred_by']:
        return False, "Вы уже были приглашены другим пользователем."
    
    referrer_data = get_user_data(referrer_id)
    if str(new_user_id) == str(referrer_id):
        return False, "Вы не можете использовать свою собственную реферальную ссылку."
    
    new_user_data['referred_by'] = referrer_id
    referrer_data['referral_count'] += 1
    referrer_data['bonus_requests'] += 2  # Увеличенный бонус для пригласившего
    new_user_data['bonus_requests'] += 3  # Увеличенный бонус для нового пользователя
    save_data(user_data, USER_DATA_FILE)
    
    # Отправка уведомления пригласившему пользователю
    try:
        await bot.send_message(int(referrer_id), f"Поздравляем! Вы пригласили нового пользователя и получили 2 дополнительных запроса. Ваш текущий бонус: {referrer_data['bonus_requests']} запросов.")
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление пользователю {referrer_id}: {str(e)}")
    
    return True, "Вы успешно присоединились по реферальной ссылке! Вы получили 3 дополнительных запроса."


@dp.message(Command("help"))
async def send_help(message: types.Message):
    logger.info(f"Пользователь {message.from_user.id} запросил помощь")
    help_text = (
        "Доступные команды:\n"
        "/start - Показать главное меню\n"
        "/generate - Создать содержание и текст\n"
        "/quota - Проверить оставшиеся запросы\n"
        "/leave_feedback - Оставить отзыв\n"
        "/view_feedback - Посмотреть отзывы\n"
        "/contact_admins - Связаться с админами\n"
        "/admin_menu - Меню администратора (только для админов)\n"
        "/faq - Часто задаваемые вопросы\n"
        "/about_us - О нашей компании\n"
        "/my_referral - Ваша реферальная ссылка\n\n"
        "Вы также можете использовать кнопки в главном меню для доступа к этим функциям."
    )
    await message.reply(help_text, reply_markup=get_main_menu_keyboard())

@dp.message(Command("menu"))
async def send_menu(message: types.Message):
    await message.reply("Главное меню:", reply_markup=get_main_menu_keyboard())

@dp.message(Command("generate"))
@dp.message(F.text == "Создать дипломную работу")
async def generate_command(message: types.Message, state: FSMContext):
    markup = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="40 страниц"), KeyboardButton(text="60 страниц"), KeyboardButton(text="100 страниц")],
            [KeyboardButton(text="Отменить генерацию")],
            [KeyboardButton(text="Главное меню")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await message.reply("Выберите размер документа или отмените генерацию:", reply_markup=markup)
    await state.set_state(GenerationStates.SELECT_DOCUMENT_SIZE)

@dp.message(GenerationStates.SELECT_DOCUMENT_SIZE, F.text == "Отменить генерацию")
async def cancel_generation(message: types.Message, state: FSMContext):
    await state.clear()
    await message.reply("Генерация отменена.", reply_markup=get_main_menu_keyboard())

@dp.message(F.text == "Главное меню")
async def main_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await message.reply("Вы вернулись в главное меню.", reply_markup=get_main_menu_keyboard())

@dp.message(GenerationStates.SELECT_DOCUMENT_SIZE)
async def process_document_size(message: types.Message, state: FSMContext):
    if message.text == "Отменить генерацию":
        await state.clear()
        await message.reply("Генерация отменена.", reply_markup=get_main_menu_keyboard())
        return
    
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
    await state.set_state(GenerationStates.WAITING_FOR_TOPIC)

@dp.message(GenerationStates.WAITING_FOR_TOPIC)
async def receive_topic(message: types.Message, state: FSMContext):
    if message.text == "Отменить генерацию":
        await state.clear()
        await message.reply("Генерация отменена.", reply_markup=get_main_menu_keyboard())
        return
    
    topic = message.text.strip()
    await state.update_data(topic=topic)
    await message.reply(f"Тема принята: {topic}\nТеперь я начну генерацию содержания.")
    await state.set_state(GenerationStates.GENERATING_CONTENT)
    await generate_content(message, state)

@dp.message(GenerationStates.GENERATING_CONTENT)
async def ignore_messages_during_generation(message: types.Message):
    await message.reply("Пожалуйста, подождите. Идет генерация содержания. Вы получите уведомление, когда документ будет готов.")

@dp.message(F.text == "Моя реферальная ссылка")
async def my_referral(message: types.Message):
    user_id = str(message.from_user.id)
    user_data = get_user_data(user_id)
    referral_link = f"https://t.me/gendiplom_bot?start={user_id}"
    referral_count = user_data['referral_count']
    bonus_requests = user_data['bonus_requests']
    
    response = (
        f"Ваша реферальная ссылка: {referral_link}\n\n"
        f"Количество приглашенных пользователей: {referral_count}\n"
        f"Ваш текущий бонус: {bonus_requests} дополнительных запросов\n\n"
        "Отправьте эту ссылку друзьям. За каждого нового пользователя вы получите 2 дополнительных запроса, а ваш друг - 3 запроса!"
    )
    
    await message.reply(response, reply_markup=get_main_menu_keyboard())


async def generate_content_with_cache(prompt):
    if prompt in cache:
        return cache[prompt]
    
    try:
        response = await asyncio.to_thread(current_model.generate_content, prompt)
        content = response.text
        cache[prompt] = content
        return content
    except Exception as e:
        logger.error(f"Error generating content: {str(e)}")
        raise

async def generate_section(topic, section, word_count=500):
    if section == "Введение":
        prompt = f"Напишите подробное введение (около {word_count} слов) для дипломной работы на тему '{topic}'. Включите актуальность темы, цели и задачи исследования."
    elif section == "Заключение":
        prompt = f"Напишите подробное заключение (около {word_count} слов) для дипломной работы на тему '{topic}'. Подведите итоги исследования, сформулируйте основные выводы."
    else:
        prompt = f"Напишите подробный текст (около {word_count} слов) для раздела '{section}' дипломной работы на тему '{topic}'. Включите теоретическую базу, анализ и практические аспекты."
    
    return section, await generate_content_with_cache(prompt)

async def generate_content(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    topic = data['topic']
    logger.info(f"Пользователь {user_id} указал тему: {topic}")
    
    user_data = get_user_data(user_id)
    user_data['requested_topic'] = topic
    
    can_proceed, quota_message = check_and_update_quota(user_id, is_document_generation=True)
    if not can_proceed:
        await message.reply(quota_message, reply_markup=get_main_menu_keyboard())
        return
    
    await message.reply(f"{quota_message}\nГенерация содержания...")
    prompt = f"Создайте подробное содержание для дипломной работы на тему '{topic}'. Включите введение, 5-7 основных разделов с 3-4 подразделами каждый, и заключение."
    
    try:
        content = await generate_content_with_cache(prompt)
        logger.info(f"Содержание сгенерировано для пользователя {user_id}")
        await message.reply(f"Содержание:\n{content}")
        
        sections = ["Введение"] + [section.strip() for section in content.split('\n') if section.strip()] + ["Заключение"]
        await state.update_data(sections=sections)
        
        status_message = await message.reply("Начинаю генерацию документа. Это займет некоторое время.\nПрогресс: 0%")
        
        word_count = data.get('word_count', 500)
        await generate_sections(message, state, status_message, word_count)
    except Exception as e:
        logger.error(f"Ошибка при генерации содержания: {str(e)}", exc_info=True)
        await message.reply("Произошла ошибка при генерации содержания. Пожалуйста, попробуйте еще раз или обратитесь к администратору.", reply_markup=get_main_menu_keyboard())

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
    await finalize_document(message, state, status_message)

async def update_progress(status_message: types.Message, start_time: float, completed_sections: int, total_sections: int):
    progress = int((completed_sections / total_sections) * 100)
    elapsed_time = int(time.time() - start_time)
    await status_message.edit_text(f"Генерация документа.\nПрошло времени: {elapsed_time // 60} мин {elapsed_time % 60} сек\nПрогресс: {progress}%")

@dp.message(Command("quota"))
async def check_quota(message: types.Message):
    user_id = message.from_user.id
    remaining = get_remaining_requests(user_id)
    await message.reply(f"Оставшиеся запросы на сегодня: {remaining}", reply_markup=get_main_menu_keyboard())

@dp.message(Command("leave_feedback"))

@dp.message(F.text == "Оставить отзыв")
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
    await message.reply("Спасибо за ваш отзыв! Он был успешно сохранен.", reply_markup=get_main_menu_keyboard())
    await state.clear()

@dp.message(Command("view_feedback"))
@dp.message(F.text == "Посмотреть отзывы")
async def view_feedback(message: types.Message):
    if not feedback_storage:
        await message.reply("Отзывов пока нет.", reply_markup=get_main_menu_keyboard())
    else:
        feedback_list = "\n\n".join([f"Отзыв {i+1}: {feedback}" for i, feedback in enumerate(feedback_storage[-5:])])
        await message.reply(f"Последние 5 отзывов:\n\n{feedback_list}", reply_markup=get_main_menu_keyboard())

@dp.message(Command("contact_admins"))
async def contact_admins(message: types.Message):
    await message.reply("Для связи с администраторами, пожалуйста, напишите на email: admin@example.com", reply_markup=get_main_menu_keyboard())

@dp.message(Command("admin_menu"))
async def admin_menu(message: types.Message):
    if str(message.from_user.id) != str(ADMIN_ID):
        await message.reply("У вас нет прав для доступа к этому меню.", reply_markup=get_main_menu_keyboard())
        return

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Просмотр участников", callback_data="view_participants")],
        [InlineKeyboardButton(text="Просмотр отзывов", callback_data="view_all_feedback")],
        [InlineKeyboardButton(text="Отправить сообщение всем", callback_data="send_message_to_all")]
    ])
    await message.reply("Меню администратора:", reply_markup=markup)

@dp.callback_query(F.data == "view_participants")
async def view_participants(callback_query: types.CallbackQuery):
    if str(callback_query.from_user.id) != str(ADMIN_ID):
        await callback_query.answer("У вас нет прав для выполнения этой команды.")
        return
    
    participants = "\n".join([f"ID: {user_id}, Username: {data.get('username', 'Не указан')}" for user_id, data in user_data.items()])
    await callback_query.message.reply(f"Список участников:\n{participants}")

@dp.callback_query(F.data == "view_all_feedback")
async def view_all_feedback(callback_query: types.CallbackQuery):
    if str(callback_query.from_user.id) != str(ADMIN_ID):
        await callback_query.answer("У вас нет прав для выполнения этой команды.")
        return
    
    if not feedback_storage:
        await callback_query.message.reply("Отзывов пока нет.")
    else:
        feedback_list = "\n\n".join([f"Отзыв {i+1}: {feedback}" for i, feedback in enumerate(feedback_storage)])
        await callback_query.message.reply(f"Все отзывы:\n\n{feedback_list}")

@dp.callback_query(F.data == "send_message_to_all")
async def send_message_to_all(callback_query: types.CallbackQuery, state: FSMContext):
    if str(callback_query.from_user.id) != str(ADMIN_ID):
        await callback_query.answer("У вас нет прав для выполнения этой команды.")
        return
    
    await callback_query.message.reply("Введите сообщение для всех пользователей.")
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
            await bot.send_message(int(user_id), message_text)
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
        await message.reply("Ошибка при создании документа.", reply_markup=get_main_menu_keyboard())
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
    await message.reply_document(input_file, caption=f"Ваш документ на тему '{topic}' готов!", reply_markup=get_main_menu_keyboard())
    
    # Очистка состояния
    await state.clear()

@dp.message(Command("faq"))
@dp.message(F.text == "FAQ")
async def faq(message: types.Message):
    faq_text = (
        "Часто задаваемые вопросы:\n\n"
        "1. Как использовать бота?\n"
        "   Отправьте команду /generate, выберите размер документа и введите тему.\n\n"
        "2. Сколько запросов я могу сделать в день?\n"
        "   Обычные пользователи могут сделать 3 запроса в день.\n\n"
        "3. Как оставить отзыв?\n"
        "   Используйте команду /leave_feedback или кнопку 'Оставить отзыв'.\n\n"
        "4. Как связаться с администраторами?\n"
        "   Используйте команду /contact_admins.\n\n"
        "5. Как работает реферальная система?\n"
        "   За каждого приглашенного пользователя вы получаете 2 дополнительных запроса."
    )
    await message.reply(faq_text, reply_markup=get_main_menu_keyboard())

@dp.message(Command("about_us"))
@dp.message(F.text == "О нас")
async def about_us(message: types.Message):
    about_text = (
        "О нашей компании:\n\n"
        "Мы - команда энтузиастов, разрабатывающая инновационные решения "
        "для помощи студентам в их академической деятельности. "
        "Наш бот использует передовые технологии искусственного интеллекта "
        "для генерации высококачественного контента для дипломных работ.\n\n"
        "Мы стремимся облегчить процесс написания дипломных работ, "
        "предоставляя структурированную информацию и идеи для дальнейшего развития. "
        "Помните, что сгенерированный контент следует использовать как основу "
        "для вашей собственной работы, дополняя его своими исследованиями и выводами."
    )
    await message.reply(about_text)

async def periodic_save():
    while True:
        await asyncio.sleep(300)  # Сохраняем каждые 5 минут
        save_data(user_data, USER_DATA_FILE)
        save_data(feedback_storage, FEEDBACK_FILE)
        logger.info("Данные пользователей и отзывы сохранены")

async def main():
    # Инициализация бота и диспетчера
    dp.startup.register(on_startup)
    
    # Запуск периодического сохранения
    asyncio.create_task(periodic_save())
    
    # Запуск поллинга
    await dp.start_polling(bot)

async def on_startup(dispatcher: Dispatcher):
    logger.info("Бот запущен")

if __name__ == '__main__':
    asyncio.run(main())
