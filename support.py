# support.py — полная реализация поддержки для пользователей и операторов
import os
import logging
import asyncio
import html
from datetime import datetime, timedelta
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

import support_storage as storage
import support_ui as ui
from support_models import message_schema

load_dotenv()

SUPPORT_BOT_TOKEN = os.getenv("SUPPORT_BOT_TOKEN")
SUPPORT_OPERATORS_CHAT_ID = int(os.getenv("SUPPORT_OPERATORS_CHAT_ID", "0"))
SUPPORT_OPERATOR_IDS = [int(x.strip()) for x in os.getenv("SUPPORT_OPERATOR_IDS", "").split(",") if x.strip().isdigit()]
MAIN_BOT_USERNAME = os.getenv("MAIN_BOT_USERNAME", "TarotDailySpreadBot")
SUPPORT_BOT_USERNAME = os.getenv("SUPPORT_BOT_USERNAME", "support_bot")

if not SUPPORT_BOT_TOKEN:
    raise ValueError("SUPPORT_BOT_TOKEN не задан в .env")

if not SUPPORT_OPERATORS_CHAT_ID:
    raise ValueError("SUPPORT_OPERATORS_CHAT_ID не задан в .env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler("logs/support.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

bot = Bot(token=SUPPORT_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# === FSM States ===
class UserStates(StatesGroup):
    waiting_category = State()
    waiting_subject = State()
    waiting_description = State()
    waiting_files = State()
    confirm = State()
    in_ticket = State()

class OperatorStates(StatesGroup):
    waiting_reply = State()

# === Media group collector ===
media_groups = {}  # media_group_id -> {"files": [], "user_id": int, "ticket_id": str, "timeout": asyncio.Task}

async def collect_media_group(media_group_id: str, file_info: dict, user_id: int, ticket_id: str):
    """Собирает медиа группу в течение 2 секунд и отправляет одним альбомом"""
    if media_group_id not in media_groups:
        media_groups[media_group_id] = {
            "files": [],
            "user_id": user_id,
            "ticket_id": ticket_id,
            "timeout": None
        }
    
    media_groups[media_group_id]["files"].append(file_info)
    
    # Отменяем старый таймаут
    if media_groups[media_group_id]["timeout"]:
        media_groups[media_group_id]["timeout"].cancel()
    
    # Создаём новый таймаут
    async def send_group():
        await asyncio.sleep(2)
        group_data = media_groups.pop(media_group_id, None)
        if not group_data:
            return
        
        files = group_data["files"]
        ticket_id = group_data["ticket_id"]
        
        # Сохраняем в тикет
        msg = message_schema("user", "", files)
        await storage.append_message(ticket_id, msg)
        
        # Отправляем оператору
        ticket = await storage.get_ticket(ticket_id)
        if ticket:
            await send_files_to_operator(ticket, files)
    
    media_groups[media_group_id]["timeout"] = asyncio.create_task(send_group())

async def send_files_to_operator(ticket: dict, files: list):
    """Отправляет файлы в операторский чат (без скачивания)"""
    try:
        ticket_id = ticket["ticket_id"]
        username = ticket.get("username", "")
        caption = f"#Файлы от @{username} (тикет {ticket_id})"
        
        # Если больше 1 файла и все фото - отправляем альбомом
        if len(files) > 1 and all(f["kind"] == "photo" for f in files):
            from aiogram.types import InputMediaPhoto
            media = []
            for i, f in enumerate(files[:10]):  # max 10 в группе
                if i == 0:
                    media.append(InputMediaPhoto(media=f["file_id"], caption=caption))
                else:
                    media.append(InputMediaPhoto(media=f["file_id"]))
            
            await bot.send_media_group(
                chat_id=SUPPORT_OPERATORS_CHAT_ID,
                media=media,
                reply_to_message_id=ticket.get("operator_thread_msg_id")
            )
        else:
            # Отправляем поштучно
            for f in files:
                if f["kind"] == "photo":
                    await bot.send_photo(
                        chat_id=SUPPORT_OPERATORS_CHAT_ID,
                        photo=f["file_id"],
                        caption=caption,
                        reply_to_message_id=ticket.get("operator_thread_msg_id")
                    )
                elif f["kind"] == "document":
                    await bot.send_document(
                        chat_id=SUPPORT_OPERATORS_CHAT_ID,
                        document=f["file_id"],
                        caption=caption,
                        reply_to_message_id=ticket.get("operator_thread_msg_id")
                    )
    except Exception as e:
        logger.error(f"Ошибка отправки файлов оператору: {e}")

# === Rate limiting ===
user_last_ticket = {}
THROTTLE_SECONDS = 300

def is_throttled(user_id: int) -> bool:
    now = datetime.utcnow().timestamp()
    last = user_last_ticket.get(user_id, 0)
    return (now - last) < THROTTLE_SECONDS

def update_throttle(user_id: int):
    user_last_ticket[user_id] = datetime.utcnow().timestamp()

# === Role detection ===
def is_operator(user_id: int, chat_id: int) -> bool:
    return chat_id == SUPPORT_OPERATORS_CHAT_ID or user_id in SUPPORT_OPERATOR_IDS

def is_private_user(chat_type: str, user_id: int) -> bool:
    return chat_type == "private" and user_id not in SUPPORT_OPERATOR_IDS

# === Notify operators ===
async def notify_operators(ticket: dict):
    try:
        card_text = ui.render_ticket_card(ticket)
        keyboard = ui.get_ticket_keyboard(ticket["ticket_id"])
        
        msg = await bot.send_message(
            chat_id=SUPPORT_OPERATORS_CHAT_ID,
            text=card_text,
            reply_markup=keyboard
        )
        
        # Сохраняем ID сообщения для потенциального редактирования
        await storage.update_ticket(ticket["ticket_id"], {"operator_thread_msg_id": msg.message_id})
        
        logger.info(f"Уведомлён чат операторов о тикете {ticket['ticket_id']}")
    except Exception as e:
        logger.error(f"Ошибка уведомления операторов: {e}")

# === Update operator card ===
async def update_operator_card(ticket_id: str):
    try:
        ticket = await storage.get_ticket(ticket_id)
        if not ticket or not ticket.get("operator_thread_msg_id"):
            return
        
        card_text = ui.render_ticket_card(ticket)
        keyboard = ui.get_ticket_keyboard(ticket_id)
        
        await bot.edit_message_text(
            chat_id=SUPPORT_OPERATORS_CHAT_ID,
            message_id=ticket["operator_thread_msg_id"],
            text=card_text,
            reply_markup=keyboard
        )
    except Exception as e:
        logger.debug(f"Не удалось обновить карточку тикета {ticket_id}: {e}")

# === USER FLOWS ===

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    
    # Проверяем payload
    args = message.text.split(maxsplit=1)
    payload = args[1] if len(args) > 1 else None
    
    if payload == "help" or not payload:
        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🎫 Открыть тикет", callback_data="user:new_ticket")],
            [types.InlineKeyboardButton(text="📂 Мои тикеты", callback_data="user:my_tickets")]
        ])
        
        # Показываем reply-keyboard
        reply_kb = types.ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text="🎫 Новый тикет")],
                [types.KeyboardButton(text="❓ FAQ"), types.KeyboardButton(text="📂 Мои тикеты")]
            ],
            resize_keyboard=True
        )
        
        await message.answer(
            "<b>🆘 Служба поддержки</b>\n\n"
            "Добро пожаловать! Здесь вы можете:\n\n"
            "• Открыть тикет по вашему вопросу\n"
            "• Просмотреть историю обращений\n"
            "• Получить помощь от нашей команды",
            reply_markup=reply_kb
        )

@dp.callback_query(F.data == "user:new_ticket")
async def user_new_ticket(callback: types.CallbackQuery, state: FSMContext):
    # Throttle check
    if is_throttled(callback.from_user.id):
        await callback.answer(
            "⏳ Вы недавно создавали тикет. Пожалуйста, подождите немного.",
            show_alert=True
        )
        return
    
    await callback.message.edit_text(
        "<b>📂 Категория обращения</b>\n\n"
        "Выберите категорию вашего вопроса:",
        reply_markup=ui.get_category_keyboard()
    )
    await state.set_state(UserStates.waiting_category)
    await callback.answer()

@dp.callback_query(F.data.startswith("sup_cat:"))
async def user_category_selected(callback: types.CallbackQuery, state: FSMContext):
    category = callback.data.split(":")[1]
    await state.update_data(category=category)
    
    await callback.message.edit_text(
        "<b>📝 Тема обращения</b>\n\n"
        "Кратко опишите тему (1-2 предложения):"
    )
    await state.set_state(UserStates.waiting_subject)
    await callback.answer()

@dp.message(UserStates.waiting_subject)
async def user_subject_received(message: types.Message, state: FSMContext):
    subject = message.text.strip()
    
    if len(subject) < 3:
        await message.answer("⚠️ Тема слишком короткая. Опишите тему подробнее.")
        return
    
    await state.update_data(subject=subject)
    await message.answer(
        "<b>✍️ Описание проблемы</b>\n\n"
        "Подробно опишите вашу проблему:"
    )
    await state.set_state(UserStates.waiting_description)

@dp.message(UserStates.waiting_description)
async def user_description_received(message: types.Message, state: FSMContext):
    description = message.text.strip()
    
    if len(description) < 10:
        await message.answer("⚠️ Описание слишком короткое. Опишите проблему подробнее.")
        return
    
    await state.update_data(description=description)
    
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="➡️ Пропустить", callback_data="user:skip_files")]
    ])
    
    await message.answer(
        "<b>📎 Вложения (опционально)</b>\n\n"
        "Прикрепите скриншоты или файлы (или нажмите Пропустить):",
        reply_markup=kb
    )
    await state.set_state(UserStates.waiting_files)

@dp.message(UserStates.waiting_files, F.photo | F.document)
async def user_files_received(message: types.Message, state: FSMContext):
    data = await state.get_data()
    files = data.get("files", [])
    
    # Normalize to dict format
    if message.photo:
        files.append({
            "kind": "photo",
            "file_id": message.photo[-1].file_id,
            "mime_type": "image/jpeg",
            "file_name": None
        })
    elif message.document:
        files.append({
            "kind": "document",
            "file_id": message.document.file_id,
            "mime_type": message.document.mime_type,
            "file_name": message.document.file_name
        })
    
    await state.update_data(files=files)
    
    category = data.get("category", "other")
    subject = data.get("subject", "")
    description = data.get("description", "")
    
    confirm_text = f"""<b>✅ Подтверждение</b>

📂 <b>Категория:</b> {category}
🔖 <b>Тема:</b> {subject}
💬 <b>Описание:</b>
{description[:200]}{'...' if len(description) > 200 else ''}

📎 <b>Файлов:</b> {len(files)}

Всё верно?"""
    
    await message.answer(confirm_text, reply_markup=ui.get_confirm_keyboard())
    await state.set_state(UserStates.confirm)

@dp.callback_query(F.data == "user:skip_files")
async def user_skip_files(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    category = data.get("category", "other")
    subject = data.get("subject", "")
    description = data.get("description", "")
    
    confirm_text = f"""<b>✅ Подтверждение</b>

📂 <b>Категория:</b> {category}
🔖 <b>Тема:</b> {subject}
💬 <b>Описание:</b>
{description[:200]}{'...' if len(description) > 200 else ''}

Всё верно?"""
    
    await callback.message.edit_text(confirm_text, reply_markup=ui.get_confirm_keyboard())
    await state.set_state(UserStates.confirm)
    await callback.answer()

@dp.callback_query(F.data == "sup_confirm:yes")
async def user_confirm_yes(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    
    # Создаём user snapshot без импорта main storage
    user_snapshot = {
        "telegram_id": callback.from_user.id,
        "chat_id": callback.message.chat.id,
        "username": callback.from_user.username or "",
        "first_name": callback.from_user.first_name or ""
    }
    
    ticket = await storage.create_ticket(
        user_snapshot=user_snapshot,
        category=data.get("category", "other"),
        subject=data.get("subject", ""),
        text=data.get("description", ""),
        files=data.get("files")
    )
    
    update_throttle(callback.from_user.id)
    await notify_operators(ticket)
    
    await callback.message.edit_text(
        f"✅ <b>Тикет создан!</b>\n\n"
        f"Номер: <code>{ticket['ticket_id']}</code>\n\n"
        f"Мы получили ваше обращение. Ответ придёт сюда.\n\n"
        f"Вы можете продолжить писать в этом чате — ваши сообщения будут добавлены к тикету."
    )
    
    await state.clear()
    await state.set_state(UserStates.in_ticket)
    await state.update_data(ticket_id=ticket["ticket_id"])
    await callback.answer()
    
    logger.info(f"Тикет {ticket['ticket_id']} создан пользователем {callback.from_user.id}")

@dp.callback_query(F.data == "sup_confirm:no")
async def user_confirm_no(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("❌ Создание тикета отменено.")
    await state.clear()
    await callback.answer()

@dp.callback_query(F.data == "user:my_tickets")
async def user_my_tickets(callback: types.CallbackQuery):
    tickets = await storage.list_tickets(user_id=callback.from_user.id, limit=10)
    
    if not tickets:
        await callback.message.edit_text("У вас пока нет тикетов.")
        return
    
    text = "<b>📂 Ваши тикеты:</b>\n\n"
    for t in tickets:
        status_emoji = {"new": "🆕", "open": "🟢", "awaiting_user": "🟡", "resolved": "✅", "closed": "🔒", "spam": "🚫"}.get(t["status"], "⚪")
        text += f"{status_emoji} <code>{t['ticket_id']}</code> — {t['subject'][:30]}\n"
    
    await callback.message.edit_text(text)
    await callback.answer()

@dp.message(F.text == "🎫 Новый тикет")
async def btn_new_ticket(message: types.Message, state: FSMContext):
    """Кнопка reply-keyboard для создания тикета"""
    if is_throttled(message.from_user.id):
        await message.answer("⏳ Вы недавно создавали тикет. Пожалуйста, подождите немного.")
        return
    
    await message.answer(
        "<b>📂 Категория обращения</b>\n\nВыберите категорию вашего вопроса:",
        reply_markup=ui.get_category_keyboard()
    )
    await state.set_state(UserStates.waiting_category)

@dp.message(F.text == "❓ FAQ")
async def btn_faq(message: types.Message):
    """Кнопка FAQ"""
    await message.answer("Появится совсем скоро!")

@dp.message(F.text == "📂 Мои тикеты")
async def btn_my_tickets(message: types.Message):
    """Кнопка списка тикетов"""
    tickets = await storage.list_tickets(user_id=message.from_user.id, limit=10)
    
    if not tickets:
        await message.answer("У вас пока нет тикетов.")
        return
    
    text = "<b>📂 Ваши тикеты:</b>\n\n"
    for t in tickets:
        status_emoji = {"new": "🆕", "open": "🟢", "awaiting_user": "🟡", "resolved": "✅", "closed": "🔒", "spam": "🚫"}.get(t["status"], "⚪")
        text += f"{status_emoji} <code>{t['ticket_id']}</code> — {t['subject'][:30]}\n"
    
    await message.answer(text)

# Обработка сообщений пользователя в тикете
@dp.message(UserStates.in_ticket)
async def user_message_in_ticket(message: types.Message, state: FSMContext):
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    
    if not ticket_id:
        return
    
    ticket = await storage.get_ticket(ticket_id)
    if not ticket or ticket["status"] in ["closed", "spam"]:
        await message.answer("❌ Тикет закрыт. Создайте новый: /start")
        await state.clear()
        return
    
    # Check if user is allowed to write
    user_write_enabled = ticket.get("user_write_enabled", False)
    allow_followup = ticket.get("allow_one_followup", False)
    
    if not user_write_enabled and not allow_followup:
        logger.warning(f"User {message.from_user.id} blocked from writing to ticket {ticket_id}")
        await message.answer(
            "⏳ Ваше сообщение уже получено оператором. Пожалуйста, дождитесь ответа. Спасибо за терпение!"
        )
        return
    
    # Добавляем сообщение
    files = []
    if message.photo:
        files.append({
            "kind": "photo",
            "file_id": message.photo[-1].file_id,
            "mime_type": "image/jpeg",
            "file_name": None
        })
    elif message.document:
        files.append({
            "kind": "document",
            "file_id": message.document.file_id,
            "mime_type": message.document.mime_type,
            "file_name": message.document.file_name
        })
    
    msg = message_schema("user", message.text or "", files)
    await storage.append_message(ticket_id, msg)
    
    # Block further messages
    if allow_followup:
        await storage.update_ticket(ticket_id, {
            "allow_one_followup": False,
            "user_write_enabled": False
        })
        logger.info(f"User {message.from_user.id} used followup in ticket {ticket_id}")
    else:
        await storage.update_ticket(ticket_id, {"user_write_enabled": False})
    
    # Переводим из awaiting_user в open
    if ticket["status"] == "awaiting_user":
        await storage.update_ticket(ticket_id, {"status": "open"})
    
    # Уведомляем операторов
    try:
        text = f"💬 <b>Тикет {ticket_id}</b>\n\nНовое сообщение от пользователя"
        if message.text:
            text += f":\n{html.escape(message.text[:200])}"
        
        await bot.send_message(
            chat_id=SUPPORT_OPERATORS_CHAT_ID,
            text=text,
            reply_to_message_id=ticket.get("operator_thread_msg_id")
        )
        
        # Отправляем файлы если есть
        if files:
            await send_files_to_operator(ticket, files)
    except Exception as e:
        logger.error(f"Failed to notify operator about user message: {e}")
    
    await message.answer("✅ Сообщение добавлено к тикету. Ожидайте ответа оператора.")
    logger.info(f"User {message.from_user.id} replied in ticket {ticket_id}")

# Обработка фото/документов вне тикета
@dp.message(F.photo | F.document, ~StateFilter(UserStates.in_ticket), ~StateFilter(UserStates.waiting_files))
async def handle_media_outside_ticket(message: types.Message):
    await message.answer("Чтобы продолжить, дождитесь ответа оператора или создайте новый тикет.")

# === OPERATOR FLOWS ===

@dp.message(Command("new"))
async def cmd_new(message: types.Message):
    if not is_operator(message.from_user.id, message.chat.id):
        # Для пользователя - алиас для создания тикета
        await message.answer(
            "<b>📂 Категория обращения</b>\n\nВыберите категорию вашего вопроса:",
            reply_markup=ui.get_category_keyboard()
        )
        return
    
    tickets = await storage.list_tickets(status="new", limit=10)
    if not tickets:
        await message.answer("Нет новых тикетов.")
        return
    
    text = "<b>🆕 Новые тикеты:</b>\n\n"
    for t in tickets:
        text += f"• <code>{t['ticket_id']}</code> — {t['first_name']} (@{t['username']})\n"
    
    await message.answer(text)

@dp.message(Command("queue"))
async def cmd_queue(message: types.Message):
    if not is_operator(message.from_user.id, message.chat.id):
        return
    
    tickets = await storage.list_tickets(assignee_id=message.from_user.id, limit=20)
    
    if not tickets:
        await message.answer("У вас нет назначенных тикетов.")
        return
    
    text = "<b>📋 Ваши тикеты:</b>\n\n"
    for t in tickets:
        status_emoji = {"open": "🟢", "awaiting_user": "🟡", "resolved": "✅"}.get(t["status"], "⚪")
        text += f"{status_emoji} <code>{t['ticket_id']}</code> — {t['first_name']}\n"
    
    await message.answer(text)

@dp.message(Command("ticket"))
async def cmd_ticket(message: types.Message):
    if not is_operator(message.from_user.id, message.chat.id):
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /ticket T-2026-01-0001")
        return
    
    ticket_id = args[1].strip()
    ticket = await storage.get_ticket(ticket_id)
    
    if not ticket:
        await message.answer(f"Тикет {ticket_id} не найден.")
        return
    
    card_text = ui.render_ticket_card(ticket)
    keyboard = ui.get_ticket_keyboard(ticket_id)
    await message.answer(card_text, reply_markup=keyboard)

@dp.message(Command("take"))
async def cmd_take(message: types.Message):
    if not is_operator(message.from_user.id, message.chat.id):
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /take T-2026-01-0001")
        return
    
    ticket_id = args[1].strip()
    await storage.update_ticket(ticket_id, {"assignee_id": message.from_user.id, "status": "open"})
    await message.answer(f"✅ Тикет {ticket_id} назначен на вас.")
    await update_operator_card(ticket_id)
    logger.info(f"Тикет {ticket_id} взят оператором {message.from_user.id}")

@dp.message(Command("reply"))
async def cmd_reply(message: types.Message):
    if not is_operator(message.from_user.id, message.chat.id):
        return
    
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer("Использование: /reply T-2026-01-0001 текст ответа")
        return
    
    ticket_id = args[1].strip()
    reply_text = args[2]
    
    if not reply_text.strip():
        await message.answer("⚠️ Текст ответа не может быть пустым")
        return
    
    ticket = await storage.get_ticket(ticket_id)
    if not ticket:
        await message.answer(f"Тикет {ticket_id} не найден.")
        return
    
    msg = message_schema("agent", reply_text)
    await storage.append_message(ticket_id, msg)
    
    # Enable user to write (one followup)
    await storage.update_ticket(ticket_id, {
        "user_write_enabled": True,
        "status": "awaiting_user"
    })
    
    # Кнопки для пользователя
    feedback_kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="✅ Проблема решена!", callback_data=f"ticket:{ticket_id}:solved")],
        [types.InlineKeyboardButton(text="♻️ Проблема осталась!", callback_data=f"ticket:{ticket_id}:not_solved")]
    ])
    
    # Отправляем пользователю (use chat_id, escape HTML, handle Forbidden)
    target_chat_id = ticket.get("chat_id") or ticket.get("user_id")
    safe_reply_text = html.escape(reply_text)
    
    try:
        await bot.send_message(
            chat_id=target_chat_id,
            text=f"💬 <b>Поддержка:</b>\n\n{safe_reply_text}",
            reply_markup=feedback_kb,
            parse_mode=ParseMode.HTML
        )
        logger.info(f"Operator {message.from_user.id} replied to ticket {ticket_id}")
    except Exception as e:
        error_msg = str(e)
        if "Forbidden" in error_msg or "blocked" in error_msg.lower():
            deep_link = f"https://t.me/{SUPPORT_BOT_USERNAME}?start=help"
            await message.answer(
                f"⚠️ <b>Пользователь не открыл чат с ботом поддержки.</b>\n\n"
                f"Попросите пользователя перейти по ссылке:\n{deep_link}\n\n"
                f"Ticket: <code>{ticket_id}</code>",
                disable_web_page_preview=True
            )
        else:
            await message.answer(f"⚠️ Не удалось отправить пользователю: {error_msg}")
        logger.error(f"Failed to send operator reply to user (ticket {ticket_id}): {e}")
        return
    
    await message.answer(f"✅ Ответ отправлен пользователю.")
    await update_operator_card(ticket_id)

@dp.message(Command("resolve"))
async def cmd_resolve(message: types.Message):
    if not is_operator(message.from_user.id, message.chat.id):
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /resolve T-2026-01-0001")
        return
    
    ticket_id = args[1].strip()
    await storage.update_ticket(ticket_id, {"status": "resolved"})
    await message.answer(f"✅ Тикет {ticket_id} помечен как решённый.")
    await update_operator_card(ticket_id)
    logger.info(f"Тикет {ticket_id} решён оператором {message.from_user.id}")

@dp.message(Command("close"))
async def cmd_close(message: types.Message):
    if not is_operator(message.from_user.id, message.chat.id):
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /close T-2026-01-0001")
        return
    
    ticket_id = args[1].strip()
    await storage.update_ticket(ticket_id, {"status": "closed"})
    await message.answer(f"🔒 Тикет {ticket_id} закрыт.")
    await update_operator_card(ticket_id)
    logger.info(f"Тикет {ticket_id} закрыт оператором {message.from_user.id}")

@dp.message(Command("note"))
async def cmd_note(message: types.Message):
    if not is_operator(message.from_user.id, message.chat.id):
        return
    
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer("Использование: /note T-2026-01-0001 текст заметки")
        return
    
    ticket_id = args[1].strip()
    note_text = args[2]
    
    msg = message_schema("system", f"Заметка оператора: {note_text}")
    await storage.append_message(ticket_id, msg)
    
    await message.answer(f"✅ Заметка добавлена к тикету {ticket_id}.")
    logger.info(f"Заметка добавлена к тикету {ticket_id} оператором {message.from_user.id}")

@dp.message(Command("search"))
async def cmd_search(message: types.Message):
    if not is_operator(message.from_user.id, message.chat.id):
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /search текст запроса")
        return
    
    query = args[1].lower()
    all_tickets = await storage.list_tickets(limit=1000)
    
    found = []
    for t in all_tickets:
        if (query in t.get("ticket_id", "").lower() or
            query in t.get("username", "").lower() or
            query in t.get("subject", "").lower() or
            any(query in msg.get("text", "").lower() for msg in t.get("messages", []))):
            found.append(t)
    
    if not found:
        await message.answer(f"По запросу '{query}' ничего не найдено.")
        return
    
    text = f"<b>🔍 Найдено тикетов: {len(found)}</b>\n\n"
    for t in found[:10]:
        text += f"• <code>{t['ticket_id']}</code> — {t['first_name']} (@{t['username']})\n"
    
    if len(found) > 10:
        text += f"\n... и ещё {len(found) - 10}"
    
    await message.answer(text)

# === Callback handlers ===

@dp.callback_query(F.data.startswith("sup:take:"))
async def cb_take(callback: types.CallbackQuery):
    if not is_operator(callback.from_user.id, callback.message.chat.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    ticket_id = callback.data.split(":")[2]
    await storage.update_ticket(ticket_id, {"assignee_id": callback.from_user.id, "status": "open"})
    await callback.answer("✅ Тикет назначен на вас")
    await update_operator_card(ticket_id)
    logger.info(f"Тикет {ticket_id} взят оператором {callback.from_user.id}")

@dp.callback_query(F.data.startswith("sup:reply:"))
async def cb_reply(callback: types.CallbackQuery, state: FSMContext):
    if not is_operator(callback.from_user.id, callback.message.chat.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    ticket_id = callback.data.split(":")[2]
    logger.info(f"[TEST] Operator {callback.from_user.id} clicked reply button for ticket {ticket_id}")
    
    await state.update_data(replying_to=ticket_id)
    await state.set_state(OperatorStates.waiting_reply)
    
    await callback.message.answer(
        f"💬 Введите ваш ответ для тикета {ticket_id}:",
        reply_markup=ui.get_reply_keyboard(ticket_id)
    )
    await callback.answer()
    logger.info(f"[TEST] State set to waiting_reply for ticket {ticket_id}")

@dp.message(OperatorStates.waiting_reply)
async def handle_reply(message: types.Message, state: FSMContext):
    logger.info(f"[TEST] Received message in waiting_reply state from operator {message.from_user.id}")
    
    data = await state.get_data()
    ticket_id = data.get("replying_to")
    
    logger.info(f"[TEST] Retrieved ticket_id: {ticket_id}")
    
    if not ticket_id:
        logger.error("[TEST] Ticket ID not found in state data")
        await message.answer("❌ Ошибка: ID тикета не найден")
        await state.clear()
        return
    
    if not message.text or not message.text.strip():
        logger.warning(f"[TEST] Empty or non-text message received from operator {message.from_user.id}")
        await message.answer("⚠️ Отправьте текстовое сообщение. Стикеры/фото не поддерживаются в режиме ответа.")
        return
    
    logger.info(f"[TEST] Fetching ticket {ticket_id} from storage")
    ticket = await storage.get_ticket(ticket_id)
    if not ticket:
        logger.error(f"[TEST] Ticket {ticket_id} not found in storage")
        await message.answer(f"❌ Тикет {ticket_id} не найден.")
        await state.clear()
        return
    
    logger.info(f"[TEST] Creating message schema for ticket {ticket_id}")
    msg = message_schema("agent", message.text)
    await storage.append_message(ticket_id, msg)
    
    # Enable user to write
    await storage.update_ticket(ticket_id, {
        "user_write_enabled": True,
        "status": "awaiting_user"
    })
    
    # Кнопки для пользователя
    feedback_kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="✅ Проблема решена!", callback_data=f"ticket:{ticket_id}:solved")],
        [types.InlineKeyboardButton(text="♻️ Проблема осталась!", callback_data=f"ticket:{ticket_id}:not_solved")]
    ])
    
    target_chat_id = ticket.get("chat_id") or ticket.get("user_id")
    safe_reply_text = html.escape(message.text)
    
    logger.info(f"[TEST] Sending message to user. Target chat_id: {target_chat_id}, ticket_id: {ticket_id}")
    
    try:
        sent_message = await bot.send_message(
            chat_id=target_chat_id,
            text=f"💬 <b>Поддержка:</b>\n\n{safe_reply_text}",
            reply_markup=feedback_kb,
            parse_mode=ParseMode.HTML
        )
        logger.info(f"[TEST] SUCCESS: Message sent to user {target_chat_id}. Message ID: {sent_message.message_id}")
        logger.info(f"Operator {message.from_user.id} replied to ticket {ticket_id}")
    except Exception as e:
        error_msg = str(e)
        logger.error(f"[TEST] FAILED to send message to user {target_chat_id}. Error: {error_msg}")
        if "Forbidden" in error_msg or "blocked" in error_msg.lower():
            deep_link = f"https://t.me/{SUPPORT_BOT_USERNAME}?start=help"
            await message.answer(
                f"⚠️ <b>Пользователь не открыл чат с ботом поддержки.</b>\n\n"
                f"Попросите пользователя перейти по ссылке:\n{deep_link}\n\n"
                f"Ticket: <code>{ticket_id}</code>",
                disable_web_page_preview=True
            )
        else:
            await message.answer(f"⚠️ Не удалось отправить пользователю: {error_msg}")
        logger.error(f"Failed to send operator reply to user (ticket {ticket_id}): {e}")
        await state.clear()
        return
    
    await message.answer("✅ Ответ отправлен пользователю.")
    await state.clear()
    await update_operator_card(ticket_id)
    logger.info(f"[TEST] Reply process completed for ticket {ticket_id}")

@dp.callback_query(F.data.startswith("sup:await:"))
async def cb_await(callback: types.CallbackQuery):
    if not is_operator(callback.from_user.id, callback.message.chat.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    ticket_id = callback.data.split(":")[2]
    await storage.update_ticket(ticket_id, {"status": "awaiting_user"})
    await callback.answer("⏸️ Статус изменён на 'Ожидание пользователя'")
    await update_operator_card(ticket_id)
    logger.info(f"Тикет {ticket_id} переведён в ожидание")

@dp.callback_query(F.data.startswith("sup:resolve:"))
async def cb_resolve(callback: types.CallbackQuery):
    if not is_operator(callback.from_user.id, callback.message.chat.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    ticket_id = callback.data.split(":")[2]
    await storage.update_ticket(ticket_id, {"status": "resolved"})
    await callback.answer("✅ Тикет помечен как решённый")
    await update_operator_card(ticket_id)
    logger.info(f"Тикет {ticket_id} решён")

@dp.callback_query(F.data.startswith("sup:close:"))
async def cb_close(callback: types.CallbackQuery):
    if not is_operator(callback.from_user.id, callback.message.chat.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    ticket_id = callback.data.split(":")[2]
    await storage.update_ticket(ticket_id, {"status": "closed"})
    await callback.answer("🔒 Тикет закрыт")
    await update_operator_card(ticket_id)
    logger.info(f"Тикет {ticket_id} закрыт")

@dp.callback_query(F.data.startswith("sup:history:"))
async def cb_history(callback: types.CallbackQuery):
    if not is_operator(callback.from_user.id, callback.message.chat.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    ticket_id = callback.data.split(":")[2]
    ticket = await storage.get_ticket(ticket_id)
    
    if not ticket:
        await callback.answer("❌ Тикет не найден", show_alert=True)
        return
    
    history_text = ui.render_ticket_history(ticket)
    
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"ticket:open:{ticket_id}")]
    ])
    
    await callback.message.answer(history_text, reply_markup=kb)
    await callback.answer()
    logger.info(f"History viewed for ticket {ticket_id}")

@dp.callback_query(F.data.startswith("sup:attachments:"))
async def cb_attachments(callback: types.CallbackQuery):
    if not is_operator(callback.from_user.id, callback.message.chat.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    ticket_id = callback.data.split(":")[2]
    ticket = await storage.get_ticket(ticket_id)
    
    if not ticket:
        await callback.answer("❌ Тикет не найден", show_alert=True)
        return
    
    # Collect all attachments from all messages
    all_files = []
    messages = ticket.get("messages", [])
    
    for msg in messages:
        files = msg.get("files", [])
        all_files.extend(files)
    
    if not all_files:
        await callback.answer("❌ В этом тикете нет вложений", show_alert=True)
        return
    
    await callback.answer(f"📎 Отправляю {len(all_files)} вложений...")
    
    # Send attachments
    try:
        await send_files_to_operator(ticket, all_files)
        logger.info(f"Attachments resent for ticket {ticket_id}")
    except Exception as e:
        logger.error(f"Failed to resend attachments: {e}")
        await callback.message.answer("❌ Ошибка при отправке вложений")

@dp.message(Command("admin_panel"))
async def cmd_admin_panel(message: types.Message):
    """Админ-панель с фильтрами"""
    if not is_operator(message.from_user.id, message.chat.id):
        return
    
    counts = await storage.count_by_status()
    
    text = "<b>📊 Админ-панель</b>\n\n"
    text += f"🆕 Новые: {counts.get('new', 0)}\n"
    text += f"🟢 Открытые: {counts.get('open', 0)}\n"
    text += f"🟡 Ожидает юзера: {counts.get('awaiting_user', 0)}\n"
    text += f"✅ Решенные: {counts.get('resolved', 0)}\n"
    text += f"🔒 Закрытые: {counts.get('closed', 0)}\n"
    
    await message.answer(text, reply_markup=ui.get_admin_panel_keyboard())

@dp.callback_query(F.data.startswith("filter:"))
async def cb_filter(callback: types.CallbackQuery):
    if not is_operator(callback.from_user.id, callback.message.chat.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    filter_status = callback.data.split(":")[1]
    
    if filter_status == "unassigned":
        tickets = await storage.list_tickets(assignee_id=0, include_closed=False, limit=20)
        title = "🗒 Неназначенные тикеты"
    else:
        tickets = await storage.list_tickets(status=filter_status, limit=20)
        status_names = {
            "new": "🆕 Новые",
            "open": "🟢 Открытые",
            "awaiting_user": "🟡 Ожидает юзера",
            "resolved": "✅ Решенные"
        }
        title = status_names.get(filter_status, filter_status)
    
    if not tickets:
        await callback.answer("❌ Тикетов не найдено", show_alert=True)
        return
    
    text = f"<b>{title}</b>\n\n"
    
    builder = InlineKeyboardBuilder()
    for t in tickets[:10]:
        status_emoji = {"new": "🆕", "open": "🟢", "awaiting_user": "🟡", "resolved": "✅"}.get(t["status"], "⚪")
        text += f"{status_emoji} <code>{t['ticket_id']}</code> — {t.get('first_name', 'User')} — {t.get('subject', '')[:30]}\n"
        builder.button(text=f"📖 {t['ticket_id']}", callback_data=f"ticket:open:{t['ticket_id']}")
    
    builder.adjust(2)
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data.startswith("sup:profile:"))
async def cb_profile(callback: types.CallbackQuery):
    if not is_operator(callback.from_user.id, callback.message.chat.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    ticket_id = callback.data.split(":")[2]
    ticket = await storage.get_ticket(ticket_id)
    
    if not ticket:
        await callback.answer("❌ Тикет не найден", show_alert=True)
        return
    
    # Read-only профиль пользователя
    try:
        import sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from storage import load_user_readonly
        user = load_user_readonly(ticket["user_id"])
        profile_text = ui.render_user_profile(user)
    except Exception as e:
        profile_text = f"❌ Не удалось загрузить профиль: {e}"
    
    await callback.message.answer(profile_text)
    await callback.answer()

@dp.callback_query(F.data.startswith("sup:cancel_reply:"))
async def cb_cancel_reply(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.answer("❌ Ответ отменён")

# === Обработчики кнопок "Решена / Осталась" ===

@dp.callback_query(F.data.startswith("ticket:") & F.data.contains(":solved"))
async def cb_ticket_solved(callback: types.CallbackQuery):
    """Пользователь подтвердил решение проблемы"""
    try:
        parts = callback.data.split(":")
        ticket_id = parts[1]
        
        ticket = await storage.get_ticket(ticket_id)
        if not ticket:
            await callback.answer("❌ Тикет не найден", show_alert=True)
            return
        
        # Обновляем статус
        await storage.update_ticket(ticket_id, {"status": "resolved"})
        
        # Добавляем системную запись
        sys_msg = message_schema("system", "Пользователь подтвердил решение")
        await storage.append_message(ticket_id, sys_msg)
        
        # Отвечаем пользователю
        main_bot_link = f"https://t.me/{MAIN_BOT_USERNAME}"
        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🔮 Перейти к основному боту", url=main_bot_link)]
        ])
        
        await callback.message.edit_text(
            "Спасибо! Рады помочь 🌟",
            reply_markup=kb
        )
        
        # Уведомляем операторов
        try:
            await bot.send_message(
                chat_id=SUPPORT_OPERATORS_CHAT_ID,
                text=f"✅ Тикет {ticket_id}: пользователь подтвердил решение",
                reply_to_message_id=ticket.get("operator_thread_msg_id")
            )
        except:
            pass
        
        await update_operator_card(ticket_id)
        await callback.answer("✅ Спасибо за отзыв!")
        logger.info(f"Тикет {ticket_id} помечен как решённый пользователем")
        
    except Exception as e:
        logger.error(f"Ошибка обработки solved callback: {e}")
        await callback.answer("❌ Ошибка", show_alert=True)

@dp.callback_query(F.data.startswith("ticket:") & F.data.contains(":not_solved"))
async def cb_ticket_not_solved(callback: types.CallbackQuery, state: FSMContext):
    """Пользователь указал, что проблема осталась"""
    try:
        parts = callback.data.split(":")
        ticket_id = parts[1]
        
        ticket = await storage.get_ticket(ticket_id)
        if not ticket:
            await callback.answer("❌ Тикет не найден", show_alert=True)
            return
        
        # Allow ONE followup message
        await storage.update_ticket(ticket_id, {
            "allow_one_followup": True,
            "user_write_enabled": True,
            "status": "open"
        })
        
        # Добавляем системную запись
        sys_msg = message_schema("system", "Пользователь указал, что проблема осталась")
        await storage.append_message(ticket_id, sys_msg)
        
        # Устанавливаем состояние для пользователя
        await state.set_state(UserStates.in_ticket)
        await state.update_data(ticket_id=ticket_id)
        
        # Отвечаем пользователю
        await callback.message.edit_text(
            "Опишите, что осталось нерешённым <b>одним сообщением</b>, пожалуйста."
        )
        
        # Уведомляем операторов
        try:
            await bot.send_message(
                chat_id=SUPPORT_OPERATORS_CHAT_ID,
                text=f"♻️ Тикет {ticket_id}: пользователь указал, что проблема осталась. Ждём дополнительное сообщение.",
                reply_to_message_id=ticket.get("operator_thread_msg_id")
            )
        except:
            pass
        
        await update_operator_card(ticket_id)
        await callback.answer("💬 Опишите проблему")
        logger.info(f"Ticket {ticket_id} reopened by user with followup mode")
        
    except Exception as e:
        logger.error(f"Error in not_solved callback: {e}")
        await callback.answer("❌ Ошибка", show_alert=True)

@dp.message(Command("tickets"))
async def cmd_tickets(message: types.Message):
    """Список тикетов с пагинацией для оператора"""
    if not is_operator(message.from_user.id, message.chat.id):
        # Для пользователя - показываем его тикеты
        tickets = await storage.list_tickets(user_id=message.from_user.id, limit=10)
        if not tickets:
            await message.answer("У вас пока нет тикетов.")
            return
        
        text = "<b>📂 Ваши тикеты:</b>\n\n"
        for t in tickets:
            status_emoji = {"new": "🆕", "open": "🟢", "awaiting_user": "🟡", "resolved": "✅", "closed": "🔒", "spam": "🚫"}.get(t["status"], "⚪")
            text += f"{status_emoji} <code>{t['ticket_id']}</code> — {t['subject'][:30]}\n"
        await message.answer(text)
        return
    
    # Для оператора - показываем все с пагинацией
    await show_tickets_page(message, page=1)

@dp.message(Command("admin_panel"))
async def cmd_admin_panel_alias(message: types.Message):
    """Алиас для /tickets (для совместимости с документацией)"""
    await cmd_tickets(message)

async def show_tickets_page(message: types.Message, page: int = 1):
    """Показывает страницу тикетов с пагинацией"""
    PAGE_SIZE = 10
    all_tickets = await storage.list_tickets(limit=1000)
    
    total = len(all_tickets)
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    
    if page < 1:
        page = 1
    if page > total_pages and total_pages > 0:
        page = total_pages
    
    start_idx = (page - 1) * PAGE_SIZE
    end_idx = start_idx + PAGE_SIZE
    page_tickets = all_tickets[start_idx:end_idx]
    
    if not page_tickets:
        await message.answer("Нет тикетов.")
        return
    
    text = f"<b>📋 Тикеты (страница {page}/{total_pages}):</b>\n\n"
    
    builder = InlineKeyboardBuilder()
    for t in page_tickets:
        status_emoji = {"new": "🆕", "open": "🟢", "awaiting_user": "🟡", "resolved": "✅", "closed": "🔒", "spam": "🚫"}.get(t["status"], "⚪")
        short_desc = t.get("subject", "")[:40]
        text += f"{status_emoji} <code>{t['ticket_id']}</code> — {t.get('first_name', 'User')} — {short_desc}\n"
        builder.button(text=f"📖 {t['ticket_id']}", callback_data=f"ticket:open:{t['ticket_id']}")
    
    builder.adjust(2)
    
    # Кнопки навигации
    nav_buttons = []
    if page > 1:
        nav_buttons.append(types.InlineKeyboardButton(text="⟨ Назад", callback_data=f"tickets:page:{page-1}"))
    if page < total_pages:
        nav_buttons.append(types.InlineKeyboardButton(text="Вперёд ⟩", callback_data=f"tickets:page:{page+1}"))
    
    if nav_buttons:
        builder.row(*nav_buttons)
    
    await message.answer(text, reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("tickets:page:"))
async def cb_tickets_page(callback: types.CallbackQuery):
    """Обработчик пагинации тикетов"""
    if not is_operator(callback.from_user.id, callback.message.chat.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    page = int(callback.data.split(":")[2])
    await callback.message.delete()
    await show_tickets_page(callback.message, page)
    await callback.answer()

@dp.callback_query(F.data.startswith("ticket:open:"))
async def cb_ticket_open(callback: types.CallbackQuery):
    """Открыть карточку тикета"""
    if not is_operator(callback.from_user.id, callback.message.chat.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    ticket_id = callback.data.split(":")[2]
    ticket = await storage.get_ticket(ticket_id)
    
    if not ticket:
        await callback.answer("❌ Тикет не найден", show_alert=True)
        return
    
    card_text = ui.render_ticket_card(ticket)
    keyboard = ui.get_ticket_keyboard(ticket_id)
    
    await callback.message.answer(card_text, reply_markup=keyboard)
    await callback.answer()

# === Main ===
async def main():
    # Устанавливаем команды для приватных чатов
    await bot.set_my_commands(
        commands=[
            types.BotCommand(command="start", description="Начать работу"),
            types.BotCommand(command="new", description="Новый тикет"),
            types.BotCommand(command="tickets", description="Мои тикеты"),
            types.BotCommand(command="help", description="Помощь")
        ],
        scope=types.BotCommandScopeAllPrivateChats()
    )
    
    # Устанавливаем команды для операторского чата
    await bot.set_my_commands(
        commands=[
            types.BotCommand(command="tickets", description="Все тикеты"),
            types.BotCommand(command="admin_panel", description="Панель администратора"),
            types.BotCommand(command="new", description="Новые тикеты"),
            types.BotCommand(command="queue", description="Моя очередь"),
            types.BotCommand(command="ticket", description="Открыть тикет"),
            types.BotCommand(command="take", description="Взять тикет"),
            types.BotCommand(command="reply", description="Ответить"),
            types.BotCommand(command="resolve", description="Решено"),
            types.BotCommand(command="close", description="Закрыть"),
            types.BotCommand(command="search", description="Поиск")
        ],
        scope=types.BotCommandScopeChat(chat_id=SUPPORT_OPERATORS_CHAT_ID)
    )
    
    logger.info("Запуск Support Bot...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
