import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command
import aiohttp

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = '7414905635:AAHBlef17Zjo0x13nrTCV0X410fiyY1TOKQ'

bot = Bot(token=TELEGRAM_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

class GenerationStates(StatesGroup):
    WAITING_FOR_GENERATOR = State()

GENERATOR_URLS = [
    'http://generator1:8080/generate',
    'http://generator2:8080/generate',
    'http://generator3:8080/generate',
    'http://generator4:8080/generate',
    'http://generator5:8080/generate',
]

request_queue = asyncio.Queue()

@dp.message(Command("start"))
async def send_welcome(message: types.Message):
    await message.reply("Привет! Я бот для генерации дипломных работ. Используйте /generate [тема] для начала.")

@dp.message(Command("generate"))
async def generate_content(message: types.Message, state: FSMContext):
    topic = message.text[len('/generate '):].strip()
    if not topic:
        await message.reply("Пожалуйста, укажите тему после команды /generate.")
        return

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
            await bot.send_message(chat_id, "Все генераторы заняты. Ваш запрос остается в очереди.")
            await request_queue.put((chat_id, topic))
        await asyncio.sleep(1)

async def get_available_generator():
    for url in GENERATOR_URLS:
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(f"{url}/status") as response:
                    if response.status == 200 and await response.text() == "available":
                        return url
            except:
                pass
    return None

async def send_request_to_generator(chat_id, topic, generator_url):
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(generator_url, json={"topic": topic}) as response:
                if response.status == 200:
                    result = await response.json()
                    await bot.send_message(chat_id, f"Ваша дипломная работа готова: {result['url']}")
                else:
                    await bot.send_message(chat_id, "Произошла ошибка при генерации. Попробуйте позже.")
        except:
            await bot.send_message(chat_id, "Произошла ошибка при обращении к генератору. Попробуйте позже.")

async def main():
    asyncio.create_task(process_queue())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
