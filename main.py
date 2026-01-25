# main.py
# (Предполагается, что этот файл находится в корне проекта)
# Основные импорты и инициализация остаются без изменений
import os
import json
import logging
from datetime import date, datetime, timedelta, timezone
from dotenv import load_dotenv
import httpx
import asyncio
import re
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
from aiohttp import web
from aiohttp.web_runner import GracefulExit
import aiohttp.web
import pytz
import uuid

# Добавляем импорт для работы с YooKassa
from yookassa import Configuration, Payment
import uuid
import threading

import aiohttp.web
import pytz

# === Загрузка окружения ===
load_dotenv()
API_TOKEN = os.getenv("BOT_TOKEN")
WEB_APP_URL = os.getenv("WEB_APP_URL", "http://5.129.196.58/miniapp.html")

# === Защита от свободного AI-чата ===
DISABLE_FREE_TEXT_AI = os.getenv("DISABLE_FREE_TEXT_AI", "1") == "1"

# === LLM Client (OpenAI) ===
try:
    from llm_client import call_tarot_model
    logging.info("✅ LLM client (OpenAI) loaded successfully")
except ImportError as e:
    logging.warning(f"⚠️ Failed to load llm_client: {e}")
    logging.warning("⚠️ Make sure llm_client.py is in the same directory as main.py")
    # For development: define stub functions
    def call_tarot_model(*args, **kwargs):
        raise NotImplementedError("llm_client module not found")

# === Добавляем глобальное хранилище активных потоков ===
active_flows = {}  # user_id -> {flow_id, flow_type, timestamp, timeout_task}

# === Вспомогательные функции для управления потоками ===
def generate_flow_id():
    """Генерирует уникальный идентификатор потока"""
    return str(uuid.uuid4())

async def cancel_previous_flow(user_id: int, new_flow_type: str):
    """Отменяет предыдущий активный поток пользователя, если он существует"""
    uid = str(user_id)
    if uid in active_flows:
        previous_flow = active_flows[uid]
        # Отменяем таймаут задачу, если она существует
        if 'timeout_task' in previous_flow and previous_flow['timeout_task']:
            previous_flow['timeout_task'].cancel()
        logging.info(f"🚫 Отменен предыдущий поток {previous_flow['flow_type']} для пользователя {uid}")
    
    # Создаем новую запись о потоке
    flow_id = generate_flow_id()
    active_flows[uid] = {
        'flow_id': flow_id,
        'flow_type': new_flow_type,
        'timestamp': datetime.now()
    }
    return flow_id

async def set_flow_timeout(user_id: int, state: FSMContext, timeout_seconds: int = 120):
    """Устанавливает таймаут для потока, после которого он автоматически закрывается"""
    uid = str(user_id)
    if uid not in active_flows:
        return
    
    flow_id = active_flows[uid]['flow_id']
    
    async def timeout_handler():
        await asyncio.sleep(timeout_seconds)
        # Проверяем, что поток все еще активен и не был заменен
        if uid in active_flows and active_flows[uid]['flow_id'] == flow_id:
            logging.info(f"⏰ Таймаут потока {active_flows[uid]['flow_type']} для пользователя {uid}")
            await state.clear()
            active_flows.pop(uid, None)
    
    # Сохраняем задачу таймаута для возможности отмены
    active_flows[uid]['timeout_task'] = asyncio.create_task(timeout_handler())

async def end_flow(user_id: int):
    """Завершает активный поток пользователя"""
    uid = str(user_id)
    if uid in active_flows:
        # Отменяем таймаут задачу, если она существует
        if 'timeout_task' in active_flows[uid] and active_flows[uid]['timeout_task']:
            active_flows[uid]['timeout_task'].cancel()
        active_flows.pop(uid, None)
        logging.info(f"✅ Поток для пользователя {uid} завершен")

async def finalize_spread(uid: int, *, chat_id: int | None = None, miniapp_message_id: int | None = None) -> None:
    """Финализирует расклад: удаляет miniapp-кнопку, очищает FSM и active_flows.
    
    Args:
        uid: ID пользователя
        chat_id: ID чата (опционально)
        miniapp_message_id: ID сообщения с miniapp-кнопкой (опционально)
    """
    deleted_miniapp = False
    fsm_cleared = False
    flow_ended = False
    
    # 1. Удаляем сообщение с miniapp-кнопкой
    if miniapp_message_id and chat_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=miniapp_message_id)
            deleted_miniapp = True
            logging.debug(f"[spread-finalize] uid={uid} удалено miniapp сообщение {miniapp_message_id}")
        except Exception as e:
            # Fallback: пытаемся убрать клавиатуру
            try:
                await bot.edit_message_reply_markup(chat_id=chat_id, message_id=miniapp_message_id, reply_markup=None)
                deleted_miniapp = True
                logging.debug(f"[spread-finalize] uid={uid} убрана клавиатура miniapp {miniapp_message_id}")
            except Exception as e2:
                logging.debug(f"[spread-finalize] uid={uid} не удалось удалить/изменить miniapp: {e}, {e2}")
    
    # 2. Очищаем FSM state и data
    try:
        key = StorageKey(bot_id=bot.id, chat_id=uid, user_id=uid)
        await storage.set_state(key=key, state=None)
        await storage.set_data(key=key, data={})
        fsm_cleared = True
        logging.debug(f"[spread-finalize] uid={uid} FSM очищен")
    except Exception as e:
        logging.debug(f"[spread-finalize] uid={uid} ошибка очистки FSM: {e}")
    
    # 3. Завершаем поток в active_flows
    try:
        await end_flow(uid)
        flow_ended = True
    except Exception as e:
        logging.debug(f"[spread-finalize] uid={uid} ошибка end_flow: {e}")
    
    logging.info(f"[spread-finalize] uid={uid} deleted_miniapp={deleted_miniapp} fsm_cleared={fsm_cleared} flow_ended={flow_ended}")

def is_valid_flow(user_id: int, flow_id: str) -> bool:
    """Проверяет, является ли поток актуальным"""
    uid = str(user_id)
    if uid not in active_flows:
        return False
    return active_flows[uid]['flow_id'] == flow_id

if not API_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан в .env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logging.info("📦 Используется aiogram 3.x")

# === Инициализация бота ===
bot = Bot(
    token=API_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# === Загрузка карт Таро ===
try:
    from tarot_cards import ALL_CARDS, get_card_meaning
    logging.info(f"✅ Успешно загружено {len(ALL_CARDS)} карт Таро")
except ImportError as e:
    logging.critical("❌ Не найдён файл tarot_cards.py или отсутствуют ALL_CARDS / get_card_meaning")
    raise e

# === Функции работы с ИИ - УДАЛЕНЫ ===
# Free-text AI chat полностью отключён. ИИ используется только для интерпретации расклада.


def clean_ai_response(text: str) -> str:
    """
    КАРДИНАЛЬНАЯ очистка: удаляем ВСЕ размышления ИИ
    и оставляем только чистый ответ таролога.
    """
    if not text:
        return text
    
    original_length = len(text)
    logging.info(f"🔥 КАРДИНАЛЬНАЯ очистка текста длиной {original_length} символов")
    
    # 1. Удаляем HTML/XML теги
    text = re.sub(r'<[^>]*>', '', text)
    
    # 2. Удаляем markdown кодовые блоки
    text = re.sub(r'```[a-zA-Z]*\n?', '', text)
    text = re.sub(r'```', '', text)
    
    # 3. УДАЛЯЕМ MARKDOWN ФОРМАТИРОВАНИЕ
    # Убираем жирный текст **text**
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    # Убираем курсив *text*
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    # Убираем заголовки ## и ###
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
    
    # 4. УДАЛЯЕМ ТЕХНИЧЕСКИЕ ШАБЛОННЫЕ ФРАЗЫ
    template_patterns = [
        r'\[подробное объяснение.*?\]',
        r'\[еще.*?предложения.*?\]', 
        r'\[подробный итоговый вывод.*?\]',
        r'\[практическая рекомендация.*?\]',
        r'\[краткий вывод.*?\]',
        r'\[переформулируй вопрос\]',
        r'\[название.*?карты.*?\]',
        r'\[.*?предложения.*?\]'
    ]
    
    for pattern in template_patterns:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)
    
    # 5. УДАЛЯЕМ РЕКОМЕНДАЦИИ В КОНЦЕ
    # Ищем и удаляем блоки с рекомендациями
    recommendation_patterns = [
        r'Рекомендация:.*?$',
        r'Рекомендую:.*?$', 
        r'Совет:.*?$',
        r'Стоит.*?следить.*?$',
        r'Следить за.*?$',
        r'Обратить внимание.*?$'
    ]
    
    for pattern in recommendation_patterns:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE | re.MULTILINE | re.DOTALL)
        
    # 6. Ищем начало ПРАВИЛЬНОГО ответа (приветствие)
    lines = text.split('\n')
    start_index = -1
    
    for i, line in enumerate(lines):
        line = line.strip()
        if (
            line.startswith('Приветствую') or 
            line.startswith('Здравствуйте') or
            'готов раскрыть' in line.lower()
        ):
            start_index = i
            break
    
    # Если нашли начало - берем все с этого момента
    if start_index >= 0:
        clean_lines = lines[start_index:]
        text = '\n'.join([line.strip() for line in clean_lines if line.strip()])
        logging.info(f"✅ Нашли начало правильного ответа на строке {start_index}")
    else:
        # Если не нашли - возвращаем пустоту, пусть перегенерирует
        logging.warning(f"⚠️ Не нашли правильное начало ответа!")
        return ""
    
    # 7. Очищаем пробелы
    text = re.sub(r'\n\s*\n\s*\n', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = text.strip()
    
    # 8. Проверяем длину
    if len(text) < 200:
        logging.warning(f"⚠️ Ответ слишком короткий после очистки: {len(text)} символов")
        return ""
    
    cleaned_length = len(text)
    logging.info(f"🔥 КАРДИНАЛЬНАЯ очистка завершена: {original_length} -> {cleaned_length} символов")
    
    return text
    
    # 1. Удаляем HTML/XML теги
    text = re.sub(r'<[^>]*>', '', text)
    
    # 2. Удаляем markdown кодовые блоки
    text = re.sub(r'```[a-zA-Z]*\n?', '', text)
    text = re.sub(r'```', '', text)
    
    # 3. Удаляем ТОЛЬКО конкретные технические фразы в начале
    # НО ОСТАВЛЯЕМ объяснения карт!
    lines = text.split('\n')
    clean_lines = []
    
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
            
        # Пропускаем только технические строки в НАЧАЛЕ (первые 5 строк)
        if i < 5 and any(bad_phrase in line.lower() for bad_phrase in [
            'карты, которые выпали',
            'нужно строго следовать',
            'сначала переформулировать',
            'потом перечислить',
            'затем краткий',
            'далее каждую',
            'важно упомянуть',
            'нужно дать',
            'проверить, чтобы',
            'также убедиться',
            'рекомендации практичны',
            'возможно,',
            'сложно все связать',
            'нужно логично',
            'в итоге ответ',
            'учитывая значения'
        ]):
            logging.info(f"⚠️ Пропускаем техническую строку: {line[:50]}...")
            continue
            
        # Оставляем все остальные строки, включая объяснения карт
        clean_lines.append(line)
    
    text = '\n\n'.join(clean_lines)
    
    # 4. Очищаем пробелы
    text = re.sub(r'\n\s*\n\s*\n', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = text.strip()
    
    # 5. Проверяем длину (для подробных ответов)
    if len(text) < 200:  # Увеличили минимум для подробных ответов
        logging.warning(f"⚠️ Ответ слишком короткий после очистки: {len(text)} символов")
        return ""
    
    cleaned_length = len(text)
    logging.info(f"✅ Умная очистка завершена: {original_length} -> {cleaned_length} символов")
    
    return text
    
    # 1. Удаляем HTML/XML теги
    text = re.sub(r'<[^>]*>', '', text)
    
    # 2. Удаляем markdown кодовые блоки
    text = re.sub(r'```[a-zA-Z]*\n?', '', text)
    text = re.sub(r'```', '', text)
    
    # 3. АГРЕССИВНО удаляем ВСЕ технические рассуждения
    # Удаляем любые строки с мета-рассуждениями
    lines = text.split('\n')
    clean_lines = []
    found_start = False
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        # Пропускаем технические строки до начала ответа
        if any(bad_phrase in line.lower() for bad_phrase in [
            'карты, которые выпали',
            'нужно строго следовать',
            'сначала переформулировать',
            'потом перечислить',
            'затем краткий',
            'далее каждую',
            'важно упомянуть',
            'нужно дать',
            'проверить, чтобы',
            'также убедиться',
            'рекомендации практичны',
            'возможно,',
            'сложно все связать',
            'нужно логично',
            'в итоге ответ',
            'учитывая значения'
        ]):
            continue
            
        # Начинаем сохранять с первого приветствия
        if not found_start and (
            line.startswith('Приветствую') or 
            line.startswith('Здравствуйте') or
            'готов' in line.lower() or
            'раскрыть карты' in line.lower()
        ):
            found_start = True
            
        if found_start:
            clean_lines.append(line)
    
    # Если не нашли нормальное начало, берем все
    if not found_start or len(clean_lines) < 3:
        clean_lines = [line.strip() for line in lines if line.strip()]
    
    text = '\n\n'.join(clean_lines)
    
    # 4. Удаляем оставшиеся технические фразы
    tech_patterns = [
        r'.*?карты, которые выпали.*?',
        r'.*?нужно строго.*?',
        r'.*?сначала переформулировать.*?',
        r'.*?потом перечислить.*?',
        r'.*?важно упомянуть.*?',
        r'.*?проверить, чтобы.*?',
        r'.*?также убедиться.*?',
        r'Пес$',
    ]
    
    for pattern in tech_patterns:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE | re.MULTILINE)
    
    # 5. Очищаем пробелы
    text = re.sub(r'\n\s*\n\s*\n', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = text.strip()
    
    # 6. Проверяем длину
    if len(text) < 100:
        logging.warning(f"⚠️ Ответ слишком короткий после очистки")
        return ""
    
    cleaned_length = len(text)
    logging.info(f"✅ Агрессивная очистка завершена: {original_length} -> {cleaned_length} символов")
    
    return text
    
    # 1. Удаляем HTML/XML теги
    text = re.sub(r'<[^>]*>', '', text)
    
    # 2. Удаляем markdown кодовые блоки
    text = re.sub(r'```[a-zA-Z]*\n?', '', text)
    text = re.sub(r'```', '', text)
    
    # 3. Удаляем технические фразы
    tech_patterns = [
        r'.*?пользователь спрашивает.*?',
        r'.*?нужно рассмотреть.*?',
        r'.*?анализируя карты.*?',
        r'.*?я вижу.*?',
        r'.*?мне кажется.*?',
        r'.*?считаю что.*?',
        r'Пес$',
    ]
    
    for pattern in tech_patterns:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE | re.MULTILINE)
    
    # 4. Удаляем лишние пробелы
    text = re.sub(r'\n\s*\n\s*\n', '\n\n', text)
    text = re.sub(r'\n\s*\n', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = text.strip()
    
    # 5. Проверяем длину (для подробных ответов)
    if len(text) < 100:
        logging.warning(f"⚠️ Ответ слишком короткий")
        return ""
    
    cleaned_length = len(text)
    logging.info(f"✅ Очистка завершена: {original_length} -> {cleaned_length} символов")
    
    return text
    
    # 1. Удаляем все HTML/XML теги включая <think>, <reflect>, <analyze> и другие
    text = re.sub(r'<[^>]*>', '', text)
    
    # 2. Удаляем markdown кодовые блоки
    text = re.sub(r'```[a-zA-Z]*\n?', '', text)
    text = re.sub(r'```', '', text)
    
    # 3. АГРЕССИВНАЯ ОЧИСТКА ТЕХНИЧЕСКИХ ФРАЗ
    # Удаляем фразы размышления и анализа
    tech_patterns = [
        r'.*?пользователь спрашивает.*?',
        r'.*?пользователь хочет.*?', 
        r'.*?нужно рассмотреть.*?',
        r'.*?анализируя карты.*?',
        r'.*?рассмотрим.*?',
        r'.*?проанализируем.*?',
        r'.*?интерпретация.*?',
        r'.*?я вижу.*?',
        r'.*?мне кажется.*?',
        r'.*?считаю что.*?',
        r'.*?думаю что.*?',
        r'.*?полагаю.*?',
        r'.*?можно сказать.*?',
        r'.*?стоит отметить.*?',
        r'.*?важно подчеркнуть.*?',
        r'.*?следует учесть.*?',
        r'.*?хорошо, пользователь.*?',
        r'.*?проверю.*?',
        r'.*?убедиться.*?',
        r'.*?завершить.*?',
        r'.*?предсказанием.*?',
        r'Пес$',  # Обрывки типа "Пес"
        r'^[А-Я][а-я]+$',  # Одинокие слова
    ]
    
    for pattern in tech_patterns:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE | re.MULTILINE)
    
    # 4. Удаляем конкретные имена карт
    card_names = [
        r'Туз Мечей', r'Туз Кубков', r'Туз Жезлов', r'Туз Пентаклей',
        r'Двойка.*?', r'Тройка.*?', r'Четверка.*?', r'Пятерка.*?',
        r'Шестерка.*?', r'Семерка.*?', r'Восьмерка.*?', r'Девятка.*?',
        r'Десятка.*?', r'Паж.*?', r'Рыцарь.*?', r'Королева.*?', r'Король.*?',
        r'Шут', r'Маг', r'Верховная Жрица', r'Императрица', r'Император',
        r'Иерофант', r'Влюбленные', r'Колесница', r'Сила', r'Отшельник',
        r'Колесо Фортуны', r'Справедливость', r'Повешенный', r'Смерть',
        r'Умеренность', r'Дьявол', r'Башня', r'Звезда', r'Луна', r'Солнце',
        r'Суд', r'Мир'
    ]
    
    for card_name in card_names:
        # Удаляем упоминания карт с "означает", "показывает" и т.д.
        text = re.sub(f'{card_name}.*?означает.*?', '', text, flags=re.IGNORECASE)
        text = re.sub(f'{card_name}.*?показывает.*?', '', text, flags=re.IGNORECASE)
        text = re.sub(f'{card_name}.*?говорит.*?', '', text, flags=re.IGNORECASE)
    
    # 5. Удаляем лишние пробелы и переносы
    text = re.sub(r'\n\s*\n\s*\n', '\n\n', text)  # Тройные переносы -> двойные
    text = re.sub(r'\n\s*\n', '\n\n', text)  # Очищаем пробелы между параграфами
    text = re.sub(r'[ \t]+', ' ', text)  # Многократные пробелы -> один
    text = text.strip()
    
    # 6. Проверяем структуру - должно быть 5 строк с эмодзи
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    valid_lines = []
    
    for line in lines:
        # Проверяем, что строка начинается с нужных эмодзи
        if any(line.startswith(emoji) for emoji in ['🔮', '🎴', '✨']):
            valid_lines.append(line)
        elif len(valid_lines) > 0:  # Добавляем только если уже есть валидные строки
            valid_lines.append(line)
    
    # Если получилось меньше 3 строк - возвращаем пустую строку
    if len(valid_lines) < 3:
        logging.warning(f"⚠️ Слишком мало валидных строк после очистки: {len(valid_lines)}")
        return ""
    
    # Собираем результат
    text = '\n'.join(valid_lines)
    
    # 7. Проверяем итоговую длину
    if len(text) < 50:  # Слишком короткий ответ после очистки
        logging.warning(f"⚠️ Ответ слишком короткий после очистки: '{text}'")
        return ""
    
    cleaned_length = len(text)
    logging.info(f"✅ Очистка завершена: {original_length} -> {cleaned_length} символов")
    
    return text

def format_tarot_response(text: str) -> str:
    """
    Форматирует ответ таролога для лучшей читаемости:
    - Добавляет правильные абзацы
    - Улучшает структуру и отступы
    - Делает текст более читабельным
    """
    if not text:
        return text
    
    # Очищаем лишние пробелы и переносы
    text = text.strip()
    
    # Разбиваем на строки
    lines = text.split('\n')
    formatted_lines = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        # Добавляем абзацы после определенных паттернов
        if (
            line.startswith('Приветствую!') or
            line.startswith('Итак, выпавшие карты:') or
            line.startswith('Судя по картам,') or
            line.startswith('В целом,')
        ):
            # Начальные блоки - с отступом после
            formatted_lines.append(line)
            formatted_lines.append('')  # Пустая строка для абзаца
        elif any(line.startswith(f'\"{card_start}') for card_start in [
            'Суд', 'Колесо Фортуны', 'Король', 'Королева', 'Рыцарь', 'Паж',
            'Туз', 'Двойка', 'Тройка', 'Четверка', 'Пятерка', 'Шестерка',
            'Семерка', 'Восьмерка', 'Девятка', 'Десятка', 'Шут', 'Маг',
            'Верховная Жрица', 'Императрица', 'Император', 'Иерофант',
            'Влюбленные', 'Колесница', 'Сила', 'Отшельник', 'Справедливость',
            'Повешенный', 'Смерть', 'Умеренность', 'Дьявол', 'Башня',
            'Звезда', 'Луна', 'Солнце', 'Мир'
        ]):
            # Объяснения карт - с отступом после
            formatted_lines.append(line)
            formatted_lines.append('')  # Пустая строка для абзаца
        else:
            # Обычные строки
            formatted_lines.append(line)
    
    # Убираем лишние пустые строки в конце
    while formatted_lines and not formatted_lines[-1]:
        formatted_lines.pop()
    
    # Соединяем обратно
    result = '\n'.join(formatted_lines)
    
    # Заменяем множественные пустые строки на одинарные
    result = re.sub(r'\n\n\n+', '\n\n', result)
    
    return result


def chunk_text(text: str, max_len: int = 4096) -> list:
    """Разбивка длинного ответа на части"""
    if len(text) <= max_len:
        return [text]
    
    parts = []
    while text:
        part = text[:max_len]
        text = text[max_len:]
        parts.append(part)
    return parts

# === Работа с данными пользователей ===
USER_DATA_FILE = "tarot_user_data.json"
DAILY_FREE_LIMIT = 3
ADMIN_ID = 360879411 # Замените на ваш ID администратора

# lock for thread-safe file ops
_user_data_lock = threading.Lock()

def load_user_data():
    if not os.path.exists(USER_DATA_FILE):
        return {}
    try:
        with _user_data_lock:
            with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        # Инициализация полей для всех пользователей
        for uid in data:
            user = data[uid]
            user.setdefault("referrals", [])

            user.setdefault("daily_count", 0)
            user.setdefault("last_reset", str(date.today()))
            user.setdefault("last_question", "")
            user.setdefault("last_cards", [])
        return data
    except Exception as e:
        logging.error(f"Ошибка загрузки user_data.json: {e}")
        return {}

def save_user_data(data: dict) -> None:
    with _user_data_lock:
        disk = {}
        if os.path.exists(USER_DATA_FILE):
            with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
                try:
                    disk = json.load(f)
                except Exception:
                    disk = {}

        merged = dict(disk)
        for uid, snapshot in (data or {}).items():
            prev = merged.get(uid, {})
            if isinstance(prev, dict) and isinstance(snapshot, dict):
                keep = ["subscriptions", "purchased_layouts"]
                for k in keep:
                    if k in prev and k not in snapshot:
                        snapshot[k] = prev[k]
                merged[uid] = {**prev, **snapshot}
            else:
                merged[uid] = snapshot

        with open(USER_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=4)

user_data = load_user_data()

# === Функция проверки доступа пользователя ===
# Упрощенная логика проверки доступа с интеграцией подписок
def can_user_read(user_id: int) -> tuple[bool, str]:
    if user_id == ADMIN_ID:
        return True, "👑 Админ: безлимит."
    
    uid = str(user_id)
    today = str(date.today())
    
    # Гарантированная инициализация пользователя
    user_data.setdefault(uid, {
        "daily_count": 0,
        "last_reset": today,
        "referrals": [],
        "last_question": "",
        "last_cards": [],
        "layout_limit": 0,
        "layouts_used": 0
    })
    
    u = user_data[uid]
    
    # Импортируем функции управления подписками
    from subscription_manager import check_subscription_status, consume_layout
    
    # Проверяем статус подписки
    subscription_status = check_subscription_status(uid)
    
    # Если есть активная подписка (недельная или месячная), то безлимит
    if subscription_status["has_active_subscription"] and subscription_status["subscription_type"] in ["week", "month"]:
        return True, "🔓 У вас активна подписка (безлимит)"
    
    # Если есть купленные разовые расклады, потребляем один
    if subscription_status["layouts_available"] > 0:
        if consume_layout(uid, user_data):
            return True, f"🎴 Использован купленный расклад. Осталось: {subscription_status['layouts_available'] - 1}"
    
    # Проверка дополнительных раскладов
    layout_limit = u.get("layout_limit", 0)
    layouts_used = u.get("layouts_used", 0)
    
    if layouts_used < layout_limit:
        # Есть доступные дополнительные расклады
        u["layouts_used"] = layouts_used + 1
        save_user_data(user_data)
        remaining = layout_limit - u["layouts_used"]
        return True, f"🃏 Использован дополнительный расклад. Осталось: {remaining}"

    # Сброс ежедневного счётчика
    if u["last_reset"] != today:
        u["daily_count"] = 0
        u["last_reset"] = today

    # Проверка бесплатного лимита
    if u["daily_count"] < DAILY_FREE_LIMIT:
        u["daily_count"] += 1
        save_user_data(user_data)
        remaining = DAILY_FREE_LIMIT - u["daily_count"]
        return True, f"✅ Осталось бесплатных раскладов: {remaining}"
    
    return False, "❌ Лимит исчерпан. Попробуйте завтра или получите больше раскладов."

# === Главное меню ===
def get_main_menu():
    keyboard = [
        [types.KeyboardButton(text="🎴 Задать вопрос")],
        [types.KeyboardButton(text="🎁 Больше раскладов")],
        [types.KeyboardButton(text="⚙️ Как это работает")],
        [types.KeyboardButton(text="🔗 Пригласить друзей")],
        [types.KeyboardButton(text="🆘 Поддержка")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

# === FSM ===
class TarotState(StatesGroup):
    """Strict one-shot flow: IDLE → WAITING_FOR_QUESTION → WAITING_FOR_CARDS → INTERPRETING → IDLE"""
    IDLE = State()  # нет активного процесса (по умолчанию)
    waiting_for_question = State()  # WAITING_FOR_QUESTION - ждём текст вопроса
    waiting_for_cards = State()  # WAITING_FOR_CARDS - ждём 3 карты из miniapp
    interpreting = State()  # INTERPRETING - запущена интерпретация (ожидание ответа ИИ)

# === Главное меню ===


# === Команды ===
# UNIVERSAL HANDLERS: мгновенный переход из любого состояния
@dp.message(Command("start"), StateFilter("*"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.set_state(TarotState.IDLE)
    await end_flow(message.from_user.id) if message.from_user else None
    
    if not message.from_user:
        await message.answer("Ошибка: не удалось получить информацию о пользователе.")
        return
        
    uid = str(message.from_user.id)
    from storage import load_user, save_user_merge, iso_now, DB_PATH
    
    # GATE: проверка "первый старт в истории БД" ПЕРЕД любыми записями
    existed_before = False
    try:
        if os.path.exists(DB_PATH):
            with open(DB_PATH, "r", encoding="utf-8") as f:
                raw_db = json.load(f)
            existed_before = uid in raw_db
    except Exception as e:
        logging.error(f"[referral-gate] ошибка чтения DB_PATH: {e}")
        existed_before = True  # безопасная сторона: если ошибка, считаем что существовал
    
    user = load_user(uid, write_back=True)
    
    user["username"] = message.from_user.username
    user["first_name"] = message.from_user.first_name
    user["telegram_id"] = message.from_user.id
    user["chat_id"] = message.chat.id
    
    if user.get("last_free_credit_at") is None:
        user["purchased_layouts"] = user.get("purchased_layouts", 0) + 1
        user["last_free_credit_at"] = iso_now()
    
    await save_user_merge(uid, user)

    command_args = message.text.split()[1:] if message.text and len(message.text.split()) > 1 else []
    
    # Обработка реферальных ссылок (формат: r{inviter_uid} или ref_{inviter_uid})
    referrer_id_str = None
    if command_args:
        payload = command_args[0]
        if payload.startswith("r") and payload[1:].isdigit():
            referrer_id_str = payload[1:]
        elif payload.startswith("ref_"):
            referrer_id_str = payload[4:]
    
    if referrer_id_str:
        # GATE: реферальная награда ТОЛЬКО для first-ever start
        if existed_before:
            logging.info(f"[referral-skip] uid={uid} reason=invitee_started_before")
        # Проверка: не сам себя пригласил
        elif referrer_id_str == uid:
            logging.info(f"[referral-skip] uid={uid} reason=self_referral")
        else:
            invitee = load_user(uid, write_back=True)
            inviter = load_user(referrer_id_str, write_back=True)
            
            # Проверка: пригласивший существует
            if not inviter.get("telegram_id"):
                logging.info(f"[referral-skip] uid={uid} reason=inviter_missing inviter_id={referrer_id_str}")
            # Проверка: invitee уже был приглашён (referrer_id уже установлен)
            elif invitee.get("referrer_id"):
                logging.info(f"[referral-skip] uid={uid} reason=invitee_already_invited existing_referrer={invitee.get('referrer_id')}")
            # Проверка идемпотентности: уже была обработана награда
            elif invitee.get("referral_bonus_processed"):
                logging.info(f"[referral-skip] uid={uid} reason=invitee_bonus_already_processed")
            else:
                # Проверка: invitee уже в списке рефералов inviter'а
                inv_refs = set(inviter.get("referrals", []))
                if uid in inv_refs:
                    logging.info(f"[referral-skip] uid={uid} reason=already_in_inviter_referrals inviter_id={referrer_id_str}")
                else:
                    # Всё ОК — начисляем награду
                    invitee["referrer_id"] = referrer_id_str
                    invitee["referral_bonus_processed"] = True
                    invitee["referral_activated_at"] = iso_now()
                    invitee["purchased_layouts"] = max(0, int(invitee.get("purchased_layouts", 0))) + 1
                    await save_user_merge(uid, invitee)
                    
                    inv_refs.add(uid)
                    inviter["referrals"] = list(inv_refs)
                    inviter["purchased_layouts"] = max(0, int(inviter.get("purchased_layouts", 0))) + 2
                    await save_user_merge(referrer_id_str, inviter)
                    
                    logging.info(f"[referral-success] invitee={uid} +1, inviter={referrer_id_str} +2")
                    
                    try:
                        await bot.send_message(
                            chat_id=int(uid),
                            text="🎁 Добро пожаловать! За приглашение от друга — +1 расклад в ваш баланс."
                        )
                    except Exception as e:
                        logging.error(f"[referral-notify] не удалось уведомить invitee {uid}: {e}")
                    
                    try:
                        await bot.send_message(
                            chat_id=int(referrer_id_str),
                            text="🎉 Ваш друг присоединился по вашей ссылке — +2 расклада начислено!"
                        )
                    except Exception as e:
                        logging.error(f"[referral-notify] не удалось уведомить inviter {referrer_id_str}: {e}")

    # Получаем имя пользователя, если доступно
    user_name = message.from_user.full_name if message.from_user.full_name else message.from_user.username if message.from_user.username else "Пользователь"
    
    await message.answer(
        f"🔮 Привет, <b>{user_name}</b>!\n\n"
        f"Добро пожаловать в <b>Карты говорят</b> — задавай вопрос, выбирай 3 карты и получай чёткую интерпретацию!\n\n"
        f"<b>Кнопки:</b>\n"
        f"• 🎴 <b>Задать вопрос</b> — Нажми, чтобы задать вопрос и получить расклад!\n"
        f"• 🎁 <b>Больше раскладов</b> — Тут можно оформить подписку или приобрести разовый расклад!\n"
        f"• ⚙️ <b>Как это работает</b> — краткая инструкция использования бота!\n"
        f"• 🔗 <b>Пригласить друзей</b> — Делись ссылкой и получай расклады!\n"
        f"• 🆘 <b>Поддержка</b> — Связаться со службой поддержки!",
        reply_markup=get_main_menu()
    )

# Улучшено сообщение профиля
@dp.message(Command("profile"), StateFilter("*"))
async def cmd_profile(message: types.Message, state: FSMContext):
    if not message.from_user:
        await message.reply("Ошибка: не удалось получить информацию о пользователе.")
        return

    await state.set_state(TarotState.IDLE)
    await end_flow(message.from_user.id)

    from storage import load_user_readonly, has_active_subscription, weekly_free_available, refresh_daily_window

    u = await refresh_daily_window(str(message.from_user.id))
    active, end_iso = has_active_subscription(u)

    display_name = message.from_user.full_name if message.from_user.full_name else (f"@{message.from_user.username}" if message.from_user.username else "Пользователь")

    if active and end_iso:
        try:
            from datetime import datetime
            end_dt = datetime.fromisoformat(end_iso.replace("Z","+00:00"))
            sub_text = f"активна до {end_dt.strftime('%d.%m.%Y %H:%M')}"
        except:
            sub_text = "активна"
    else:
        sub_text = "нет активной"

    limit_text = f'{u["daily_count"]}/40' if active else '—'

    queued = int(u.get("purchased_layouts", 0))
    available = queued + (0 if active else (1 if weekly_free_available(u) else 0))
    spreads_text = f"{queued} (в очереди)" if active else str(available)

    ref_count = len(u.get("referrals", []))

    text = (
        f"👤 Профиль {display_name}\n\n"
        f"💫 Подписка: {sub_text}\n"
        f"⏱️ Суточный лимит: {limit_text}\n"
        f"🎴 Расклады: {spreads_text}\n"
        f"👥 Рефералы: {ref_count}"
    )

    import logging
    logging.debug(f"[profile_readonly] uid={message.from_user.id}")
    await message.reply(text)


# === Support Handler ===
@dp.message(F.text == "🆘 Поддержка", StateFilter("*"))
async def support_handler(message: types.Message, state: FSMContext):
    await state.set_state(TarotState.IDLE)
    await end_flow(message.from_user.id) if message.from_user else None
    
    support_bot_username = os.getenv("SUPPORT_BOT_USERNAME", "TarotDailySpreadSupportBot")
    support_link = f"https://t.me/{support_bot_username}?start=help"
    
    await message.answer(
        "<b>🆘 Служба поддержки</b>\n\n"
        "Наша поддержка работает в отдельном боте.\n\n"
        "Нажмите кнопку ниже, чтобы открыть тикет или связаться с нами:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="📞 Открыть поддержку", url=support_link)]
        ])
    )

# === Вопрос → FSM ===
# STRICT ONE-SHOT: Запуск только через кнопку, только если процесса ещё нет
@dp.message(F.text == "🎴 Задать вопрос", StateFilter("*"))
async def ask_question(message: types.Message, state: FSMContext):
    if not message.from_user:
        await message.reply("Ошибка: не удалось получить информацию о пользователе.")
        return
    
    # === CHECK: блокируем запуск нового расклада, если процесс уже идёт ===
    current_state = await state.get_state()
    if current_state in (TarotState.waiting_for_question, TarotState.waiting_for_cards, TarotState.interpreting):
        await message.reply("У вас уже идёт расклад — дождитесь результата.")
        logging.info(f"[one-shot-block] uid={message.from_user.id} current_state={current_state}")
        return
    
    await state.clear()
    flow_id = await cancel_previous_flow(message.from_user.id, "ask_question")
    
    # === GATE: проверяем право на расклад ПЕРЕД показом miniapp ===
    from storage import load_user_readonly
    from subscription_manager import get_spread_entitlement
    
    uid = str(message.from_user.id)
    user = load_user_readonly(uid)
    ent = get_spread_entitlement(user)
    
    if not ent["allowed"]:
        logging.info(f"[gate-block] uid={uid} reason={ent['reason']}")
        
        # Формируем paywall текст
        paywall_text = (
            "🔮 <b>Похоже, у вас закончились расклады.</b>\n\n"
            "Выберите вариант ниже, чтобы продолжить:\n\n"
            "• <b>Разовый расклад</b> — 49 ₽\n"
            "• <b>Подписка на 30 дней</b> — 399 ₽ (до 40 раскладов в сутки)\n\n"
        )
        
        if ent["next_weekly_at"]:
            try:
                from datetime import datetime
                next_dt = datetime.fromisoformat(ent["next_weekly_at"].replace("Z", "+00:00"))
                paywall_text += f"<i>Бесплатный расклад появится: {next_dt.strftime('%d.%m.%Y')}</i>"
            except:
                paywall_text += "<i>Бесплатный расклад появится автоматически.</i>"
        else:
            paywall_text += "<i>Бесплатный расклад появится автоматически через неделю после первого использования.</i>"
        
        # Paywall клавиатура
        paywall_kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🎴 Купить 1 расклад — 49 ₽", callback_data="buy_single")],
            [types.InlineKeyboardButton(text="📆 Подписка 30 дней — 399 ₽", callback_data="buy_month")]
        ])
        
        await message.answer(paywall_text, reply_markup=paywall_kb, parse_mode="HTML")
        await end_flow(message.from_user.id)
        return
    
    # === Пользователь имеет право - продолжаем как раньше ===
    logging.info(f"[gate-pass] uid={uid} source={ent['source']}")
    
    await message.reply("Напиши свой вопрос 👇")
    await state.set_state(TarotState.waiting_for_question)
    await set_flow_timeout(message.from_user.id, state, 120)

# STRICT FSM: Принимаем текст вопроса ТОЛЬКО в состоянии waiting_for_question
@dp.message(TarotState.waiting_for_question)
async def handle_question(message: types.Message, state: FSMContext):
    # Проверяем, что у сообщения есть пользователь
    if not message.from_user:
        await message.reply("Ошибка: не удалось получить информацию о пользователе.")
        return
    
    # Проверяем, что поток все еще актуален
    uid = str(message.from_user.id)
    if uid in active_flows:
        current_flow_id = active_flows[uid].get('flow_id')
        if not is_valid_flow(message.from_user.id, current_flow_id):
            # Поток уже отменен, игнорируем сообщение
            return
    
    # Проверяем, является ли сообщение нажатием на кнопку из главного меню
    main_menu_buttons = [
        "🎴 Задать вопрос",
        "🎁 Больше раскладов",
        "⚙️ Как это работает",
        "🔗 Пригласить друзей"
    ]
    
    # Если текст сообщения совпадает с одной из кнопок главного меню,
    # завершаем состояние и позволяем обработчику кнопки работать как обычно
    if message.text and message.text in main_menu_buttons:
        await state.set_state(TarotState.IDLE)
        # Отменяем таймаут потока
        if uid in active_flows and 'timeout_task' in active_flows[uid]:
            active_flows[uid]['timeout_task'].cancel()
        # Завершаем текущий поток
        await end_flow(message.from_user.id)
        # Не обрабатываем сообщение как вопрос, позволяем обработчику кнопки работать
        return
    
    # Проверяем, является ли сообщение текстом
    if not message.text:
        await message.reply("Пожалуйста, введи текст вопроса.")
        # Оставляем состояние активным, чтобы пользователь мог попробовать снова
        return

    q = message.text.strip()
    if len(q) < 3:
        await message.reply("Вопрос слишком короткий. Попробуй сформулировать его подробнее.")
        # Оставляем состояние активным
        return

    # Все проверки пройдены, обрабатываем вопрос
    await state.update_data(question=q)
    if message.from_user is None:
        await message.reply("Ошибка: не удалось получить информацию о пользователе.")
        return
    uid = str(message.from_user.id)
    
    from storage import save_user_merge
    await save_user_merge(uid, {"last_question": q})

    # Предлагаем перейти к выбору карт
    # Используем URL из .env файла
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🎴 Выбрать 3 карты", web_app=types.WebAppInfo(url=WEB_APP_URL))]
    ])
    
    # Отправляем сообщение с кнопкой и СОХРАНЯЕМ его ID
    miniapp_message = await message.reply(
        "🔮 Теперь выбери 3 карты для расклада:\n\n"
        "✨ Доверься интуиции и выбери те карты, которые притягивают твой взгляд.\n\n"
        "🎴 Нажми кнопку ниже, чтобы открыть выбор карт 👇", 
        reply_markup=kb
    )
    logging.info(f"💬 [DEBUG] Отправлено сообщение с miniapp (ID: {miniapp_message.message_id})")
    
    # Сохраняем ID сообщения в FSM
    await state.update_data(miniapp_message_id=miniapp_message.message_id, chat_id=message.chat.id)
    logging.info(f"💾 [DEBUG] Сохранено в FSM: miniapp_message_id={miniapp_message.message_id}, chat_id={message.chat.id}")
    
    from storage import save_user_merge
    await save_user_merge(uid, {
        "miniapp_message_id": miniapp_message.message_id,
        "chat_id": message.chat.id
    })
    
    # === CRITICAL: переходим в waiting_for_cards ===
    await state.set_state(TarotState.waiting_for_cards)
    logging.info(f"[fsm-transition] uid={uid} waiting_for_question → waiting_for_cards")
    
    # Отменяем таймаут, так как вопрос получен
    if uid in active_flows and 'timeout_task' in active_flows[uid]:
        active_flows[uid]['timeout_task'].cancel()


# === НОВЫЕ ХЕНДЛЕРЫ ДЛЯ ДОПОЛНИТЕЛЬНЫХ КНОПОК ===

# --- "🎁 Больше раскладов" ---
@dp.message(F.text == "🎁 Больше раскладов", StateFilter("*"))
async def get_more_spreads(message: types.Message, state: FSMContext):
    # Очищаем любое активное состояние
    await state.set_state(TarotState.IDLE)
    await end_flow(message.from_user.id) if message.from_user else None
    
    if message.from_user is None:
        await message.answer("Ошибка: не удалось получить информацию о пользователе.")
        return
    uid = str(message.from_user.id)
    # Убедимся, что пользователь существует в данных
    user_data.setdefault(uid, {
        "daily_count": 0,
        "last_reset": str(date.today()),
        "referrals": [],
        "last_question": "",
        "last_cards": []
    })
    u = user_data[uid]

    # Формируем текст ответа
    info_text = (
        "🔮 <b>Хочешь больше раскладов?</b>\n\n"
        
        "🔓 <b>Способы получить дополнительные расклады:</b>\n\n"
        
        "📆 <b>Месячная подписка</b>\n"
        "Безлимитные расклады на 30 дней\n"
        "Цена: <b>399 ₽</b>\n\n"
        
        "🎴 <b>Разовый расклад</b>\n"
        "Один расклад по запросу\n"
        "Цена: <b>49 ₽</b>\n\n"
        
        "👥 <b>Пригласи друзей</b>\n"
        "Поделись своей реферальной ссылкой. За каждого друга, который начнёт пользоваться ботом по твоей ссылке, ты получишь <b>+1</b> бесплатный расклад.\n"
        "Нет ограничений на количество приглашений!\n\n"
        
        "<i>Выбери удобный способ ниже:</i>"
    )

    # Создаем инлайн-кнопки
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="📆 Месячная подписка — 399 ₽", callback_data="buy_month")],
        [types.InlineKeyboardButton(text="🎴 Разовый расклад — 49 ₽", callback_data="buy_single")],
        [types.InlineKeyboardButton(text="👥 Пригласить друзей", callback_data="show_referral_link")],
    ])

    await message.answer(info_text, reply_markup=keyboard, parse_mode="HTML")

# --- "🔗 Пригласить друзей" (Рефералка) ---
@dp.message(F.text == "🔗 Пригласить друзей", StateFilter("*"))
async def referral_info(message: types.Message, state: FSMContext):
    await state.set_state(TarotState.IDLE)
    await end_flow(message.from_user.id) if message.from_user else None
    
    if not message.from_user:
        await message.answer("Ошибка: не удалось получить информацию о пользователе.")
        return
    
    from storage import load_user_readonly
    import urllib.parse
    
    uid = str(message.from_user.id)
    u = load_user_readonly(uid)

    referrals_count = len(u.get("referrals", []))
    referral_link = f"https://t.me/TarotDailySpreadBot?start=r{uid}"
    
    share_text = "🔮 Попробуй бота для раскладов Таро! Получишь +1 расклад в подарок при старте."
    share_url = f"https://t.me/share/url?url={urllib.parse.quote(referral_link)}&text={urllib.parse.quote(share_text)}"

    referral_text = (
        "🔮 <b>Приглашай друзей и получай бонусы!</b>\n\n"
        "Дай другу +1 расклад за старт, а себе — +2 за каждого нового друга. Чем больше друзей — тем больше раскладов.\n\n"
        f"Твоя реферальная ссылка:\n"
        f"{referral_link}\n\n"
        f"Скопируй её и отправь друзьям!\n\n"
        f"👥 Приглашено друзей: <b>{referrals_count}</b>"
    )

    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="📤 Поделиться", url=share_url)]
    ])

    await message.answer(referral_text, reply_markup=keyboard, parse_mode="HTML")

# --- Callback для показа ссылки (если нужно) ---
# Этот хендлер обрабатывает нажатие на инлайн-кнопку "Пригласить друзей"
@dp.callback_query(F.data == "show_referral_link", StateFilter("*"))
async def process_referral_callback(callback_query: types.CallbackQuery, state: FSMContext):
    # Очищаем любое активное состояние
    await state.clear()
    if callback_query.from_user:
        await end_flow(callback_query.from_user.id)
    
    # Проверяем, что у callback_query есть пользователь
    if not callback_query.from_user:
        await callback_query.answer("Ошибка: не удалось получить информацию о пользователе.")
        return
    
    from storage import load_user_readonly
    import urllib.parse
        
    uid = str(callback_query.from_user.id)
    u = load_user_readonly(uid)
    referrals_count = len(u.get("referrals", []))
    referral_link = f"https://t.me/TarotDailySpreadBot?start=r{uid}"
    
    share_text = "🔮 Попробуй бота для раскладов Таро! Получишь +1 расклад в подарок при старте."
    share_url = f"https://t.me/share/url?url={urllib.parse.quote(referral_link)}&text={urllib.parse.quote(share_text)}"
    
    # Отправляем сообщение с ссылкой
    # Проверяем, что у callback_query есть сообщение
    if callback_query.message:
        referral_text = (
            "🔮 <b>Приглашай друзей и получай бонусы!</b>\n\n"
            "Дай другу +1 расклад за старт, а себе — +2 за каждого нового друга. Чем больше друзей — тем больше раскладов.\n\n"
            f"Твоя реферальная ссылка:\n"
            f"{referral_link}\n\n"
            f"Скопируй её и отправь друзьям!\n\n"
            f"👥 Приглашено друзей: <b>{referrals_count}</b>"
        )
        
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="📤 Поделиться", url=share_url)]
        ])
        
        await callback_query.message.answer(
            referral_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        await callback_query.answer("Ошибка: не удалось получить сообщение.")
    # Отвечаем на callback, чтобы убрать "часики" на кнопке
    await callback_query.answer()

# === Обработчики для покупки расскладов ===
# Обработчики для выбора пакета покупки
@dp.callback_query(F.data == "buy_week", StateFilter("*"))
async def handle_buy_week(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик для выбора недельного пакета - показ описания"""
    # Очищаем любое активное состояние
    await state.clear()
    if callback_query.from_user:
        await end_flow(callback_query.from_user.id)
    
    # Проверяем, что у callback_query есть пользователь
    if not callback_query.from_user:
        await callback_query.answer("Ошибка: не удалось получить информацию о пользователе.")
        return
    
    # Проверяем, что у callback_query есть сообщение
    if not callback_query.message:
        await callback_query.answer("Ошибка: не удалось получить сообщение.")
        return
    
    # Отправляем сообщение с описанием тарифа и кнопкой оплаты
    try:
        if isinstance(callback_query.message, types.Message):
            await callback_query.message.edit_text(
                "📅 <b>Недельный пакет</b>\n\n"
                "Безлимитные расклады на 7 дней\n\n"
                "Цена: <b>99 ₽</b>",
                parse_mode="HTML",
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text="Оплатить 99 ₽", callback_data="confirm_week_payment")],
                    [types.InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu")]
                ])
            )
        else:
            await callback_query.answer("Ошибка при отправке сообщения.")
    except Exception as e:
        await callback_query.answer("Ошибка при отправке сообщения.")
    await callback_query.answer()

@dp.callback_query(F.data == "confirm_week_payment", StateFilter("*"))
async def handle_confirm_week_payment(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик для подтверждения оплаты недельного пакета"""
    # Очищаем любое активное состояние
    await state.clear()
    if callback_query.from_user:
        await end_flow(callback_query.from_user.id)
    # Проверяем, что у callback_query есть пользователь
    if not callback_query.from_user:
        await callback_query.answer("Ошибка: не удалось получить информацию о пользователе.")
        return
        
    # Проверяем, что у callback_query есть сообщение
    if not callback_query.message:
        await callback_query.answer("Ошибка: не удалось получить сообщение.")
        return
        
    # Импортируем функцию обработки платежа
    from subscription_manager import process_payment_week, get_user_benefits_info
    
    # Имитация обработки платежа
    try:
        success = process_payment_week(str(callback_query.from_user.id))  # Преобразуем user_id в строку
    except Exception as e:
        success = False
    
    if success:
        # Получаем информацию о benefits пользователя
        try:
            benefits_info = get_user_benefits_info(str(callback_query.from_user.id))
        except Exception as e:
            benefits_info = "Не удалось получить информацию о статусе"
        
        # Отправляем сообщение об успешной покупке
        try:
            if isinstance(callback_query.message, types.Message):
                await callback_query.message.edit_text(
                    "📅 <b>Покупка недельного пакета</b>\n\n"
                    "Вы успешно приобрели пакет: <b>1 неделя</b>\n"
                    "Цена: <b>99 ₽</b>\n\n"
                    f"🎉 <b>Поздравляем!</b> Вам выдан безлимитный доступ к раскладам на 7 дней.\n\n"
                    f"<b>Ваш статус:</b>\n{benefits_info}",
                    parse_mode="HTML",
                    reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                        [types.InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu")]
                    ])
                )
            else:
                await callback_query.answer("Ошибка при отправке сообщения.")
        except Exception as e:
            await callback_query.answer("Ошибка при отправке сообщения.")
    else:
        # Отправляем сообщение об ошибке
        try:
            if isinstance(callback_query.message, types.Message):
                await callback_query.message.edit_text(
                    "📅 <b>Покупка недельного пакета</b>\n\n"
                    "❌ Возникла ошибка при обработке платежа. Пожалуйста, попробуйте позже.",
                    parse_mode="HTML",
                    reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                        [types.InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu")]
                    ])
                )
            else:
                await callback_query.answer("Ошибка при отправке сообщения.")
        except Exception as e:
            await callback_query.answer("Ошибка при отправке сообщения.")
    await callback_query.answer()

@dp.callback_query(F.data == "buy_month", StateFilter("*"))
async def handle_buy_month(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик для выбора месячного пакета - показ описания"""
    # Очищаем любое активное состояние
    await state.clear()
    if callback_query.from_user:
        await end_flow(callback_query.from_user.id)
    
    # Проверяем, что у callback_query есть пользователь
    if not callback_query.from_user:
        await callback_query.answer("Ошибка: не удалось получить информацию о пользователе.")
        return
    
    # Проверяем, что у callback_query есть сообщение
    if not callback_query.message:
        await callback_query.answer("Ошибка: не удалось получить сообщение.")
        return
    
    # Отправляем сообщение с описанием тарифа и кнопкой оплаты
    try:
        if isinstance(callback_query.message, types.Message):
            await callback_query.message.edit_text(
                "📆 <b>Месячный пакет*</b>\n\n"
                "Безлимитные расклады на 30 дней\n\n"
                "Цена: <b>399 ₽</b>\n\n"
                "<i>* — присутствует ограничение: не более 40 раскладов в течение суток.</i>",
                parse_mode="HTML",
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text="Оплатить 399 ₽", callback_data="confirm_month_payment")],
                    [types.InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu")]
                ])
            )
        else:
            await callback_query.answer("Ошибка при отправке сообщения.")
    except Exception as e:
        await callback_query.answer("Ошибка при отправке сообщения.")
    await callback_query.answer()

@dp.callback_query(F.data == "confirm_month_payment", StateFilter("*"))
async def handle_confirm_month_payment(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик для подтверждения оплаты месячного пакета"""
    # Очищаем любое активное состояние
    await state.clear()
    if callback_query.from_user:
        await end_flow(callback_query.from_user.id)
    # Проверяем, что у callback_query есть пользователь
    if not callback_query.from_user:
        await callback_query.answer("Ошибка: не удалось получить информацию о пользователе.")
        return
        
    # Проверяем, что у callback_query есть сообщение
    if not callback_query.message:
        await callback_query.answer("Ошибка: не удалось получить сообщение.")
        return
        
    # Получаем ID пользователя
    user_id = callback_query.from_user.id
    
    try:
        # Настраиваем YooKassa
        Configuration.configure(
            os.getenv("YOOKASSA_SHOP_ID"),
            os.getenv("YOOKASSA_SECRET_KEY")
        )
        
        # Создаем платеж через YooKassa
        payment = Payment.create({
            "amount": {
                "value": "399.00",
                "currency": "RUB"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": f"https://t.me/{(await bot.get_me()).username}"
            },
            "description": "Подписка 30 дней",
            "capture": True,
            "metadata": {
                "user_id": str(user_id),
                "product": "month",
                "payment_type": "month"
            }
        })
        
        # Проверяем, что платеж создан успешно
        if payment and payment.confirmation:
            confirmation_url = payment.confirmation.confirmation_url
            # Отправляем пользователю ссылку на оплату
            if isinstance(callback_query.message, types.Message):
                await callback_query.message.edit_text(
                    "📆 <b>Покупка месячного пакета</b>\n\n"
                    "Для завершения покупки перейдите по ссылке ниже и оплатите заказ.\n"
                    "После успешной оплаты вам будет автоматически активирована подписка.",
                    parse_mode="HTML",
                    reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                        [types.InlineKeyboardButton(text="Перейти к оплате", url=confirmation_url)],
                        [types.InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu")]
                    ])
                )
            else:
                await callback_query.answer("Ошибка при отправке сообщения.")
        else:
            raise Exception("Не удалось создать платеж")
            
    except Exception as e:
        logging.error(f"Ошибка при создании платежа: {e}")
        # Отправляем сообщение об ошибке
        try:
            if isinstance(callback_query.message, types.Message):
                await callback_query.message.edit_text(
                    "📆 <b>Покупка месячного пакета</b>\n\n"
                    "❌ Возникла ошибка при создании платежа. Пожалуйста, попробуйте позже.",
                    parse_mode="HTML",
                    reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                        [types.InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu")]
                    ])
                )
        except Exception as ex:
            logging.error(f"Ошибка при отправке сообщения об ошибке: {ex}")
        await callback_query.answer("Ошибка при создании платежа.")
    await callback_query.answer()

@dp.callback_query(F.data == "buy_single", StateFilter("*"))
async def handle_buy_single(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик для выбора одного рассклада - показ описания"""
    # Очищаем любое активное состояние
    await state.clear()
    if callback_query.from_user:
        await end_flow(callback_query.from_user.id)
    
    # Проверяем, что у callback_query есть пользователь
    if not callback_query.from_user:
        await callback_query.answer("Ошибка: не удалось получить информацию о пользователе.")
        return
    
    # Проверяем, что у callback_query есть сообщение
    if not callback_query.message:
        await callback_query.answer("Ошибка: не удалось получить сообщение.")
        return
    
    # Отправляем сообщение с описанием тарифа и кнопкой оплаты
    try:
        if isinstance(callback_query.message, types.Message):
            await callback_query.message.edit_text(
                "🎴 <b>Один расклад</b>\n\n"
                "Разовый расклад по запросу\n\n"
                "Цена: <b>49 ₽</b>",
                parse_mode="HTML",
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text="Оплатить 49 ₽", callback_data="confirm_single_payment")],
                    [types.InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu")]
                ])
            )
        else:
            await callback_query.answer("Ошибка при отправке сообщения.")
    except Exception as e:
        await callback_query.answer("Ошибка при отправке сообщения.")
    await callback_query.answer()

@dp.callback_query(F.data == "confirm_single_payment", StateFilter("*"))
async def handle_confirm_single_payment(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик для подтверждения оплаты одного рассклада"""
    # Очищаем любое активное состояние
    await state.clear()
    if callback_query.from_user:
        await end_flow(callback_query.from_user.id)
    # Проверяем, что у callback_query есть пользователь
    if not callback_query.from_user:
        await callback_query.answer("Ошибка: не удалось получить информацию о пользователе.")
        return
        
    # Проверяем, что у callback_query есть сообщение
    if not callback_query.message:
        await callback_query.answer("Ошибка: не удалось получить сообщение.")
        return
        
    # Получаем ID пользователя
    user_id = callback_query.from_user.id
    
    try:
        # Настраиваем YooKassa
        Configuration.configure(
            os.getenv("YOOKASSA_SHOP_ID"),
            os.getenv("YOOKASSA_SECRET_KEY")
        )
        
        # Создаем платеж через YooKassa
        payment = Payment.create({
            "amount": {
                "value": "49.00",
                "currency": "RUB"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": f"https://t.me/{(await bot.get_me()).username}"
            },
            "description": "Один разовый расклад",
            "capture": True,
            "metadata": {
                "user_id": str(user_id),
                "product": "single",
                "payment_type": "single"
            }
        })
        
        # Проверяем, что платеж создан успешно
        if payment and payment.confirmation:
            confirmation_url = payment.confirmation.confirmation_url
            # Отправляем пользователю ссылку на оплату
            if isinstance(callback_query.message, types.Message):
                await callback_query.message.edit_text(
                    "🎴 <b>Покупка одного расклада</b>\n\n"
                    "Для завершения покупки перейдите по ссылке ниже и оплатите заказ.\n"
                    "После успешной оплаты вам будет автоматически активирован расклад.",
                    parse_mode="HTML",
                    reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                        [types.InlineKeyboardButton(text="Перейти к оплате", url=confirmation_url)],
                        [types.InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu")]
                    ])
                )
            else:
                await callback_query.answer("Ошибка при отправке сообщения.")
        else:
            raise Exception("Не удалось создать платеж")
            
    except Exception as e:
        logging.error(f"Ошибка при создании платежа: {e}")
        # Отправляем сообщение об ошибке
        try:
            if isinstance(callback_query.message, types.Message):
                await callback_query.message.edit_text(
                    "🎴 <b>Покупка одного расклада</b>\n\n"
                    "❌ Возникла ошибка при создании платежа. Пожалуйста, попробуйте позже.",
                    parse_mode="HTML",
                    reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                        [types.InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu")]
                    ])
                )
        except Exception as ex:
            logging.error(f"Ошибка при отправке сообщения об ошибке: {ex}")
        await callback_query.answer("Ошибка при создании платежа.")
    await callback_query.answer()

@dp.callback_query(F.data == "main_menu", StateFilter("*"))
async def handle_main_menu(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик для возврата в главное меню"""
    # Очищаем любое активное состояние
    await state.set_state(TarotState.IDLE)
    if callback_query.from_user:
        await end_flow(callback_query.from_user.id)
    
    # Проверяем, что у callback_query есть сообщение
    if not callback_query.message:
        await callback_query.answer("Ошибка: не удалось получить сообщение.")
        return
    
    try:
        if isinstance(callback_query.message, types.Message):
            await callback_query.message.answer(
                "🔮 Главное меню",
                reply_markup=get_main_menu()
            )
            # Удаляем текущее сообщение с инлайн-кнопками
            try:
                await callback_query.message.delete()
            except:
                pass
        else:
            await callback_query.answer("Ошибка: не удалось отправить сообщение.")
    except Exception as e:
        await callback_query.answer("Ошибка: не удалось отправить сообщение.")
    await callback_query.answer()

# --- "⚙️ Как это работает" ---
@dp.message(F.text == "⚙️ Как это работает", StateFilter("*"))
async def how_to_use(message: types.Message, state: FSMContext):
    # Очищаем любое активное состояние
    await state.set_state(TarotState.IDLE)
    await end_flow(message.from_user.id) if message.from_user else None
    
    how_to_text = (
        "🔮 <b>Как пользоваться Таро-ботом:</b>\n\n"
        
        "<b>🎴 Расклады Таро:</b>\n"
        "1. Нажми <b>«🎴 Задать вопрос»</b> и введи свой вопрос\n"
        "2. Выбери 3 карты Таро в мини-приложении\n"
        "3. Получи глубокую интерпретацию расклада\n\n"
        
        "<i>🌟 Пусть мудрость Таро ведет тебя!</i>"
    )
    await message.answer(how_to_text, parse_mode="HTML")

# === Обработка данных из Mini App ===
# STRICT FSM: Принимаем web_app_data ТОЛЬКО в состоянии waiting_for_cards
@dp.message(F.web_app_data)
async def handle_web_app_data(message: types.Message, state: FSMContext):
    if not message.web_app_data:
        await message.reply("Ошибка: не удалось получить данные из web app.")
        return

    if not message.from_user:
        await message.reply("Ошибка: не удалось получить информацию о пользователе.")
        return
    
    # === CHECK: проверяем, что находимся в правильном состоянии ===
    current_state = await state.get_state()
    if current_state != TarotState.waiting_for_cards:
        await message.reply("Сначала нажмите '🎴 Задать вопрос' и следуйте шагам.")
        logging.warning(f"[fsm-violation] uid={message.from_user.id} web_app_data outside waiting_for_cards (state={current_state})")
        return

    try:
        data = json.loads(message.web_app_data.data)
    except Exception as e:
        await message.reply("Ошибка: не удалось разобрать данные из web app.")
        return

    if data.get("action") != "send_spread":
        return

    uid = str(message.from_user.id)
    
    # === GUARD: повторная проверка права на расклад ===
    from storage import load_user_readonly, save_user_merge, consume_spread
    from subscription_manager import get_spread_entitlement
    
    user = load_user_readonly(uid)
    ent = get_spread_entitlement(user)
    
    if not ent["allowed"]:
        logging.warning(f"[gate-block-webapp] uid={uid} reason={ent['reason']}")
        
        # Paywall текст
        paywall_text = (
            "🔮 <b>Похоже, у вас закончились расклады.</b>\n\n"
            "Выберите вариант ниже, чтобы продолжить:\n\n"
            "• <b>Разовый расклад</b> — 49 ₽\n"
            "• <b>Подписка на 30 дней</b> — 399 ₽ (до 40 раскладов в сутки)\n\n"
        )
        
        if ent["next_weekly_at"]:
            try:
                from datetime import datetime
                next_dt = datetime.fromisoformat(ent["next_weekly_at"].replace("Z", "+00:00"))
                paywall_text += f"<i>Бесплатный расклад появится: {next_dt.strftime('%d.%m.%Y')}</i>"
            except:
                paywall_text += "<i>Бесплатный расклад появится автоматически.</i>"
        else:
            paywall_text += "<i>Бесплатный расклад появится автоматически через неделю.</i>"
        
        paywall_kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🎴 Купить 1 расклад — 49 ₽", callback_data="buy_single")],
            [types.InlineKeyboardButton(text="📆 Подписка 30 дней — 399 ₽", callback_data="buy_month")]
        ])
        
        await message.answer(paywall_text, reply_markup=paywall_kb, parse_mode="HTML")
        # IMPORTANT: возвращаемся в IDLE после блокировки
        await state.set_state(TarotState.IDLE)
        await end_flow(message.from_user.id)
        return

    # === Право подтверждено - продолжаем обработку ===
    logging.info(f"[gate-pass-webapp] uid={uid} source={ent['source']}")

    patch = {}
    if "cards" in data:
        patch["last_cards"] = data["cards"][:3]
    if "question" in data:
        patch["last_question"] = data["question"]
    await save_user_merge(uid, patch)

    mode = await consume_spread(uid)
    if mode == "none":
        await message.answer("Раскладов нет. Оформите подписку или купите разовый расклад.")
        await state.set_state(TarotState.IDLE)
        await end_flow(message.from_user.id)
        return

    question = data.get("question", "Вопрос не задан")
    cards = data.get("cards", [])
    
    # === LLM GUARD: проверяем preconditions перед интерпретацией ===
    if not question or len(question) < 3:
        logging.warning(f"[LLM-GUARD] Interpretation blocked: missing/invalid question, uid={uid}")
        await message.answer("Сначала задайте вопрос через кнопку '🎴 Задать вопрос'.")
        await state.set_state(TarotState.IDLE)
        await end_flow(message.from_user.id)
        return
    
    if not cards or len(cards) != 3:
        logging.warning(f"[LLM-GUARD] Interpretation blocked: missing/invalid cards, uid={uid}")
        await message.answer("Выберите ровно 3 карты в мини-приложении.")
        await state.set_state(TarotState.IDLE)
        await end_flow(message.from_user.id)
        return
    
    # === FSM TRANSITION: переходим в состояние interpreting ===
    await state.set_state(TarotState.interpreting)
    logging.info(f"[fsm-transition] uid={uid} waiting_for_cards → interpreting")

    await message.reply("Данные из web app получены. Обработка продолжается...")

    logging.info(f"🎉 [WEB_APP_DATA] Обработчик web_app_data СРАБОТАЛ!")


    fsm_data = await state.get_data()
    miniapp_message_id = fsm_data.get("miniapp_message_id")
    chat_id = fsm_data.get("chat_id")

    if miniapp_message_id and chat_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=miniapp_message_id)
        except Exception as e:
            logging.warning(f"⚠️ [Ошибка] Не удалось удалить сообщение: {e}")

    cards_display = "\n".join([f"🎴 {i+1}. {card}" for i, card in enumerate(cards)])

    summary_message = (
        f"🔮 <b>Ваш расклад Таро</b>\n\n"
        f"❓ <b>Вопрос:</b>\n<i>{question}</i>\n\n"
        f"🎴 <b>Выбранные карты:</b>\n{cards_display}\n\n"
        f"✨ <i>Создаю мистическую интерпретацию...</i>"
    )

    summary_msg = await message.reply(summary_message, parse_mode="HTML")

    try:
        if message.bot and message.chat:
            await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    except Exception:
        pass

    answer = await ai_interpret_spread(question, cards)
    
    # === FINALIZATION: гарантированная очистка даже при ошибках отправки ===
    fsm_data = await state.get_data()
    miniapp_message_id = fsm_data.get("miniapp_message_id")
    chat_id_fsm = fsm_data.get("chat_id")
    
    try:
        tarot_msg = await message.reply(answer, parse_mode="HTML")
    finally:
        # Финализация ВСЕГДА выполняется, даже если отправка упала
        await finalize_spread(uid, chat_id=chat_id_fsm, miniapp_message_id=miniapp_message_id)
        logging.info(f"[fsm-transition] uid={uid} interpreting → IDLE (spread complete)")

# === ОБРАБОТЧИК НЕИЗВЕСТНЫХ КОМАНД ===
@dp.message()
async def handle_unknown_commands(message: types.Message):
    """Обработчик неизвестных команд и catch-all для защиты от free-text AI"""
    # Логируем неизвестную команду
    if message.text:
        logging.info(f"🔍 [UNKNOWN] Неизвестная команда/текст: {message.text[:50]}")
        if message.from_user:
            logging.info(f"[free-text-blocked] uid={message.from_user.id}")
    
    await message.reply("Пожалуйста, используйте меню ниже 🙂")

# === ИИ интерпретация ===
async def ai_interpret_spread(question: str, cards: list) -> str:
    """
    Создает расклад Таро с помощью OpenAI Chat Completions API.
    С автоматическим fallback на резервную модель при необходимости.
    """
    try:
        logging.info(f"🔮 Tarot interpretation for: {question[:50]}...")
        
        # Вызываем OpenAI Chat Completions API со структурированным JSON выходом
        result = call_tarot_model(
            question=question,
            cards_ru=cards,  # cards уже на русском
            spread_name="3-card spread",
            temperature=0.8,
            max_output_tokens=1100
        )
        
        logging.info(f"🔮 Result type: {type(result)}")
        logging.info(f"🔮 Result keys: {list(result.keys()) if isinstance(result, dict) else 'not a dict'}")
        logging.info(f"🔮 Full result: {result}")
        
        # Извлекаем структурированные поля
        summary = result.get("summary", "")
        interpretation = result.get("interpretation", "")
        disclaimer = result.get("disclaimer", "")
        usage = result.get("_usage", {})
        
        # Нормализуем интерпретацию к трем абзацам
        from llm_client import normalize_interpretation_three_paragraphs
        interpretation = normalize_interpretation_three_paragraphs(interpretation)
        
        logging.info(f"🔮 Summary: {summary[:100] if summary else 'EMPTY'}...")
        logging.info(f"🔮 Interpretation: {interpretation[:100] if interpretation else 'EMPTY'}...")
        logging.info(f"🔮 Disclaimer: {disclaimer[:100] if disclaimer else 'EMPTY'}...")
        
        # Логируем использование токенов
        if usage.get("total"):
            logging.info(f"📊 Tarot tokens: {usage['total']}")
        
        # Форматируем ответ в новом порядке: Интерпретация → Итог → Дисклеймер
        formatted = f"🔮 <b>Расклад Таро:</b>\n\n"
        
        if interpretation:
            formatted += f"<b>Интерпретация:</b>\n{interpretation}\n\n"
        
        if summary:
            formatted += f"<b>Итог:</b>\n{summary}\n\n"
        
        if disclaimer:
            formatted += f"<i>{disclaimer}</i>"
        
        logging.info(f"✅ Tarot response: {len(formatted)} chars")
        return formatted
        
    except Exception as e:
        logging.error(f"❌ Tarot error: {e}", exc_info=True)
        if "429" in str(e) or "rate limit" in str(e).lower():
            return "⚠️ Сервис ИИ временно перегружен. Попробуйте позже."
        elif "401" in str(e) or "403" in str(e) or "auth" in str(e).lower():
            return "⚠️ Ошибка аутентификации ИИ сервиса. Обратитесь к администратору."
        elif "500" in str(e) or "server" in str(e).lower():
            return "⚠️ Ошибка сервера ИИ. Попробуйте позже."
        else:
            return "⚠️ Ошибка при создании расклада. Попробуйте еще раз."

# === HTTP Webhook для получения данных от Node.js сервера ===
async def webhook_tarot_cards(request):
    """Webhook endpoint для получения данных о выбранных картах от Node.js сервера"""
    try:
        data = await request.json()
        logging.info(f"🎯 [WEBHOOK] Получены данные от Node.js: {data}")
        
        user_id = data.get('telegram_id')
        cards = data.get('last_cards', [])
        question = data.get('last_question', '')
        
        if not user_id or len(cards) != 3:
            logging.error(f"❌ [WEBHOOK] Некорректные данные: user_id={user_id}, cards={len(cards) if cards else 0}")
            return web.Response(status=400, text="Invalid data")
        
        # Сохраняем информацию о сообщении с кнопкой miniapp в данных пользователя
        uid = str(user_id)
        user_data.setdefault(uid, {
            "daily_count": 0,
            "premium": False,
            "premium_end_date": None,
            "last_reset": str(date.today()),
            "referrals": [],
            "last_question": "",
            "last_cards": [],
            "miniapp_message_id": None,
            "chat_id": None
        })
        
        # Обрабатываем данные как если бы они пришли от WebApp
        await process_tarot_selection(user_id, question, cards)
        
        return web.Response(status=200, text="OK")
        
    except Exception as e:
        logging.error(f"❌ [WEBHOOK] Ошибка обработки webhook: {e}", exc_info=True)
        return web.Response(status=500, text="Error")

async def internal_yookassa(request):
    """
    Внутренний эндпойнт для приёма данных от Node.js после успешного платежа YooKassa.
    Защищён секретом из .env: YOOHOOK_TOKEN
    """
    try:
        # Проверяем токен авторизации
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        expected_token = os.getenv('YOOHOOK_TOKEN', '')
        
        if not expected_token or token != expected_token:
            logging.warning(f"[internal_yookassa] Неверный токен авторизации")
            return web.Response(status=403, text="Forbidden")
        
        data = await request.json()
        logging.debug(f"[internal_yookassa] Получены данные: {data}")
        
        payment_id = data.get('payment_id')
        user_id = data.get('user_id')
        product = data.get('product')
        
        if not payment_id or not user_id or product not in ['single', 'month']:
            logging.error(f"[internal_yookassa] Некорректные данные")
            return web.Response(status=400, text="Invalid data")
        
        # Формируем данные в формате YooKassa webhook
        webhook_data = {
            'event': 'payment.succeeded',
            'object': {
                'id': payment_id,
                'status': 'succeeded',
                'metadata': {
                    'user_id': str(user_id),
                    'payment_type': product
                }
            }
        }
        
        # Вызываем обработчик
        from payment_handler import handle_yookassa_webhook
        success = await handle_yookassa_webhook(webhook_data, bot)
        
        if success:
            logging.info(f"[internal_yookassa] Платёж {payment_id} успешно обработан")
            return web.Response(status=200, text="OK")
        else:
            logging.error(f"[internal_yookassa] Ошибка обработки платежа {payment_id}")
            return web.Response(status=500, text="Error")
            
    except Exception as e:
        logging.error(f"[internal_yookassa] Ошибка: {e}", exc_info=True)
        return web.Response(status=500, text="Error")

async def webhook_yookassa_echo(request):
    """Test endpoint для проверки доставки webhook без начислений"""
    try:
        data = await request.json()
        logging.info(f"[ECHO] Получен webhook: {data}")
        return web.Response(status=200, text="OK")
    except Exception as e:
        logging.error(f"[ECHO] Ошибка: {e}")
        return web.Response(status=200, text="OK")

async def webhook_yookassa(request):
    """
    Webhook endpoint для получения уведомлений от YooKassa.
    Согласно документации YooKassa, мы должны:
    1. Ответить HTTP 200 для подтверждения получения
    2. Все остальное в теле/заголовках будет проигнорировано
    """
    try:
        # Логируем заголовки для диагностики
        headers = dict(request.headers)
        logging.info(f"📥 [YOOKASSA] Получен webhook, headers: {headers}")
        
        # Парсим JSON
        try:
            data = await request.json()
            logging.info(f"📝 [YOOKASSA] Получены данные: type={data.get('type')}, event={data.get('event')}")
        except Exception as json_err:
            logging.error(f"❌ [YOOKASSA] Ошибка парсинга JSON: {json_err}")
            # Все равно возвращаем 200, чтобы YooKassa не пыталась переотправлять
            return web.Response(status=200, text="OK")
        
        # Обрабатываем webhook
        from payment_handler import handle_yookassa_webhook
        bot_instance = request.app["bot"]
        success = await handle_yookassa_webhook(data, bot_instance)
        
        if not success:
            logging.error(f"❌ [YOOKASSA] Ошибка обработки")
        else:
            logging.info(f"✅ [YOOKASSA] Успешно обработано")
        
        # ВСЕГДА возвращаем 200 для подтверждения получения
        return web.Response(status=200, text="OK")
        
    except Exception as e:
        logging.error(f"❌ [YOOKASSA] Exception: {e}", exc_info=True)
        # Даже при ошибке возвращаем 200
        return web.Response(status=200, text="OK")

async def process_tarot_selection(user_id: int, question: str, cards: list):
    """Обрабатывает выбор карт Таро и отправляет расклад пользователю"""
    uid = str(user_id)
    miniapp_message_id = None
    chat_id = None
    
    try:
        logging.info(f"🎴 [PROCESS] Обрабатываем выбор карт для пользователя {user_id}")
        logging.info(f"❓ [PROCESS] Вопрос: '{question}'")
        logging.info(f"🃏 [PROCESS] Карты: {cards}")
        
        from storage import save_user_merge, DB_PATH
        await save_user_merge(uid, {
            "last_question": question,
            "last_cards": cards[:3]
        })
        
        logging.info(f"💾 [PROCESS] Данные сохранены в tarot_user_data.json")

        # Получаем miniapp_message_id из FSM storage (предпочтительно)
        try:
            key = StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id)
            fsm_data = await storage.get_data(key)
            miniapp_message_id = fsm_data.get("miniapp_message_id")
            chat_id = fsm_data.get("chat_id") or user_id
            logging.debug(f"[PROCESS] FSM data: miniapp_message_id={miniapp_message_id}, chat_id={chat_id}")
        except Exception as e:
            logging.debug(f"[PROCESS] Не удалось получить FSM data: {e}")
            # Fallback: читаем raw JSON
            try:
                if os.path.exists(DB_PATH):
                    with open(DB_PATH, "r", encoding="utf-8") as f:
                        raw_db = json.load(f)
                    rec = raw_db.get(uid, {})
                    miniapp_message_id = rec.get("miniapp_message_id")
                    chat_id = rec.get("chat_id") or user_id
                    logging.debug(f"[PROCESS] Raw JSON: miniapp_message_id={miniapp_message_id}, chat_id={chat_id}")
            except Exception as e2:
                logging.debug(f"[PROCESS] Не удалось прочитать raw JSON: {e2}")

        # Показываем сообщение с вопросом и выбранными картами
        cards_display = "\n".join([f"🎴 {i+1}. {card}" for i, card in enumerate(cards)])
        
        summary_message = (
            f"🔮 <b>Ваш расклад Таро</b>\n\n"
            f"❓ <b>Вопрос:</b>\n<i>{question}</i>\n\n"
            f"🎴 <b>Выбранные карты:</b>\n{cards_display}\n\n"
            f"✨ <i>Создаю мистическую интерпретацию...</i>"
        )
        
        logging.info(f"💬 [PROCESS] Отправляем сводку пользователю {user_id}...")
        await bot.send_message(chat_id=user_id, text=summary_message, parse_mode="HTML")
        
        # Отправляем запрос к ИИ
        logging.info(f"🤖 [PROCESS] Отправляем запрос к ИИ...")
        
        try:
            await bot.send_chat_action(chat_id=user_id, action="typing")
        except Exception:
            pass
            
        answer = await ai_interpret_spread(question, cards)
        logging.info(f"💫 [PROCESS] Получен ответ: {len(answer)} символов")
        
        # Отправляем расклад
        await bot.send_message(chat_id=user_id, text=answer, parse_mode="HTML")
        logging.info(f"✅ [PROCESS] Расклад отправлен пользователю {user_id}")
        
        # Отмечаем расклад как использованный и лисываем кредит
        from subscription_manager import mark_reading_consumed
        mark_reading_consumed(user_id)

    except Exception as e:
        logging.error(f"❌ [PROCESS] Ошибка в process_tarot_selection: {e}", exc_info=True)
        try:
            await bot.send_message(chat_id=user_id, text="❌ Ошибка при создании расклада. Попробуйте еще раз.")
        except:
            pass
    finally:
        # FINALIZATION: гарантированная очистка FSM и active_flows
        await finalize_spread(user_id, chat_id=chat_id, miniapp_message_id=miniapp_message_id)

# === Еженедельное напоминание ===
async def weekly_reminder_task():
    """Еженедельное напоминание всем пользователям по четвергам в 15:00 МСК"""
    moscow_tz = pytz.timezone('Europe/Moscow')
    
    mystical_messages = [
        "🔮 Звёзды шепчут о том, что настало время заглянуть в будущее... Карты Таро готовы раскрыть тайны, которые ждут вас на этой неделе. Какой вопрос терзает ваше сердце?",
        "✨ Мистический четверг напоминает: Вселенная готова поделиться своими секретами. Три карты Таро могут стать ключом к пониманию вашего пути. Осмелитесь ли вы заглянуть за завесу?",
        "🌙 Лунная энергия этой недели особенно сильна... Карты Таро пульсируют древней мудростью, готовой открыться вам. Что волнует ваш дух сегодня?",
        "🎴 Архетипы Таро зовут вас в магическое путешествие познания... Позвольте картам стать проводниками в лабиринте жизненных решений. Какая тайна ждёт разгадки?",
        "⭐ Космические силы сошлись в идеальной гармонии для откровений... Три священные карты готовы поведать о том, что судьба приготовила именно для вас. Доверьтесь интуиции!",
        "🔯 Древняя мудрость Таро пробуждается в этот особенный день... Карты жаждут поделиться видениями о вашем грядущем. Какой вопрос зреет в глубинах вашей души?",
        "🌟 Энергетические потоки Вселенной указывают на важные откровения... Позвольте картам Таро стать мостом между вашими сомнениями и ясностью. Время для магии!"
    ]
    
    while True:
        try:
            # Получаем текущее время в Москве
            now_moscow = datetime.now(moscow_tz)
            
            # Вычисляем следующий четверг в 15:00
            days_until_thursday = (3 - now_moscow.weekday()) % 7  # 3 = четверг (понедельник = 0)
            if days_until_thursday == 0 and now_moscow.hour >= 15:
                # Если сегодня четверг и уже больше 15:00, ждём следующий четверг
                days_until_thursday = 7
                
            next_thursday = now_moscow.replace(hour=15, minute=0, second=0, microsecond=0) + timedelta(days=days_until_thursday)
            
            # Вычисляем время ожидания
            wait_seconds = (next_thursday - now_moscow).total_seconds()
            
            logging.info(f"📅 Следующее напоминание: {next_thursday.strftime('%Y-%m-%d %H:%M')} МСК (через {wait_seconds/3600:.1f} часов)")
            
            # Ждём до времени отправки
            await asyncio.sleep(wait_seconds)
            
            # Выбираем случайное мистическое сообщение
            import random
            message = random.choice(mystical_messages)
            
            # Получаем всех пользователей
            all_users = list(user_data.keys())
            sent_count = 0
            
            logging.info(f"📨 Отправляем еженедельное напоминание {len(all_users)} пользователям...")
            
            # Отправляем всем пользователям
            for user_id_str in all_users:
                try:
                    user_id = int(user_id_str)
                    
                    # Добавляем кнопку для быстрого начала
                    kb = types.InlineKeyboardMarkup(inline_keyboard=[
                        [types.InlineKeyboardButton(text="🎴 Задать вопрос картам", callback_data="start_tarot_reading")]
                    ])
                    
                    await bot.send_message(
                        chat_id=user_id, 
                        text=message,
                        reply_markup=kb
                    )
                    sent_count += 1
                    
                    # Небольшая пауза между отправками, чтобы не спамить
                    await asyncio.sleep(0.1)
                    
                except Exception as e:
                    logging.warning(f"⚠️ Не удалось отправить напоминание пользователю {user_id_str}: {e}")
                    continue
            
            logging.info(f"✅ Еженедельное напоминание отправлено {sent_count} пользователям")
            
        except Exception as e:
            logging.error(f"❌ Ошибка в еженедельном напоминании: {e}")
            # В случае ошибки ждём час и пробуем снова
            await asyncio.sleep(3600)

# Обработчик кнопки из еженедельного напоминания
@dp.callback_query(F.data == "start_tarot_reading", StateFilter("*"))
async def process_start_tarot_reading(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработка нажатия кнопки из еженедельного напоминания"""
    # Очищаем любое активное состояние
    await state.set_state(TarotState.IDLE)
    if callback_query.from_user:
        await end_flow(callback_query.from_user.id)
    # Проверяем, что callback_query.message существует и является Message, а не InaccessibleMessage
    if callback_query.message and not isinstance(callback_query.message, types.InaccessibleMessage):
        try:
            await callback_query.message.edit_text(
                "🔮 Отлично! Теперь напишите свой вопрос, и я помогу вам выбрать карты для расклада."
            )
        except Exception:
            await callback_query.answer("Ошибка при отправке сообщения.")
    await callback_query.answer()
    
    # Отправляем пользователю меню для задавания вопроса
    # Проверяем, что callback_query.message существует и является Message, а не InaccessibleMessage
    if callback_query.message and not isinstance(callback_query.message, types.InaccessibleMessage):
        try:
            await callback_query.message.answer(
                "❓ Сформулируйте свой вопрос и нажмите кнопку ниже:",
                reply_markup=get_main_menu()
            )
        except Exception:
            pass

async def health_check(request):
    """Health check endpoint для проверки работоспособности сервиса"""
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat()
    return web.json_response({"ok": True, "ts": ts})

async def routes_list(request):
    """Diagnostic: список зарегистрированных роутов"""
    routes = []
    for route in request.app.router.routes():
        routes.append({"method": route.method, "path": str(route.resource)})
    return web.json_response({"routes": routes, "app_id": id(request.app)})

# === Запуск HTTP сервера для webhook ===
async def init_webhook_server():
    """Инициализирует HTTP сервер для webhook"""
    app = web.Application()
    app["bot"] = bot
    app.router.add_get('/health', health_check)
    app.router.add_get('/routes', routes_list)
    app.router.add_post('/webhook/tarot-cards', webhook_tarot_cards)
    app.router.add_post('/webhook/yookassa', webhook_yookassa)
    app.router.add_post('/webhook/yookassa/', webhook_yookassa)
    app.router.add_post('/webhook/yookassa/_echo', webhook_yookassa_echo)
    app.router.add_post('/internal/yookassa', internal_yookassa)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 5000)
    await site.start()
    
    logging.info("🌐 Webhook сервер запущен на 0.0.0.0:5000")
    logging.info("📡 Зарегистрированы роуты: GET /health, POST /webhook/yookassa")
    return runner

# === Запуск бота ===
async def main():
    logging.info("🚀 Бот 'Карты говорят!' запущен")
    
    # Запускаем webhook сервер
    webhook_runner = await init_webhook_server()
    
    # Запускаем задачу еженедельного напоминания
    weekly_task = asyncio.create_task(weekly_reminder_task())
    logging.info("📅 Задача еженедельного напоминания запущена")
    
    try:
        # Запускаем бота
        await dp.start_polling(bot)
    finally:
        # Завершаем webhook сервер и задачу напоминания
        weekly_task.cancel()
        await webhook_runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())

