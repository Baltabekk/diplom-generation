import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command
import aiohttp
from aiogram.utils.token import validate_token

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '7414905635:AAHBlef17Zjo0x13nrTCV0X410fiyY1TOKQ')

logger.debug(f"Token being used: {TELEGRAM_TOKEN[:4]}...{TELEGRAM_TOKEN[-4:]}")

try:
    validate_token(TELEGRAM_TOKEN)
    logger.info("Token validation passed")
except Exception as e:
    logger.error(f"Token validation failed: {str(e)}")
    raise

bot = Bot(token=TELEGRAM_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

class GenerationStates(StatesGroup):
    WAITING_FOR_GENERATOR = State()

GENERATOR_URLS = [
    'https://generator1.herokuapp.com/generate',
    'https://generator2.herokuapp.com/generate',
    'https://generator3.herokuapp.com/generate',
    'https://generator4.herokuapp.com/generate',
    'https://generator5.herokuapp.com/generate',
]

request_queue = asyncio.Queue()

@dp.message(Command("start"))
async def send_welcome(message: types.Message):
    logger.info(f"User {message.from_user.id} started the bot")
    await message.reply("Привет! Я бот для генерации дипломных работ. Используйте /generate [тема] для начала.")

@dp.message(Command("generate"))
async def generate_content(message: types.Message, state: FSMContext):
    topic = message.text[len('/generate '):].strip()
    if not topic:
        await message.reply("Пожалуйста, укажите тему после команды /generate.")
        return

    logger.info(f"User {message.from_user.id} requested generation for topic: {topic}")
    await message.reply("Ваш запрос добавлен в очередь. Пожалуйста, ожидайте.")
    await request_queue.put((message.chat.id, topic))
    await state.set_state(GenerationStates.WAITING_FOR_GENERATOR)

async def process_queue():
    while True:
        chat_id, topic = await request_queue.get()
        generator_url = await get_available_generator()
        if generator_url:
            asyncio.create_task(send_request_to_generator(chat_id, topic, generator_url))
        else:
            logger.warning("All generators are busy")
            await bot.send_message(chat_id, "Все генераторы заняты. Ваш запрос остается в очереди.")
            await request_queue.put((chat_id, topic))
        await asyncio.sleep(1)

async def get_available_generator():
    for url in GENERATOR_URLS:
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(f"{url}/status") as response:
                    if response.status == 200 and await response.text() == "available":
                        logger.info(f"Found available generator: {url}")
                        return url
            except:
                logger.error(f"Error checking generator status: {url}", exc_info=True)
    return None

async def send_request_to_generator(chat_id, topic, generator_url):
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(generator_url, json={"topic": topic}) as response:
                if response.status == 200:
                    result = await response.json()
                    logger.info(f"Successfully generated content for user {chat_id}")
                    await bot.send_message(chat_id, f"Ваша дипломная работа готова: {result['url']}")
                else:
                    logger.error(f"Generator returned non-200 status: {response.status}")
                    await bot.send_message(chat_id, "Произошла ошибка при генерации. Попробуйте позже.")
        except:
            logger.error(f"Error communicating with generator: {generator_url}", exc_info=True)
            await bot.send_message(chat_id, "Произошла ошибка при обращении к генератору. Попробуйте позже.")

async def main():
    logger.info("Starting the bot")
    asyncio.create_task(process_queue())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
