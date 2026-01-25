#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Support Bot Module for Tarot Cards Telegram Bot
Implements FAQ, triage chatbot, and ticket escalation system with Forum Topics
"""

import os
import json
import logging
import yaml
import asyncio
import threading
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

import pytz

# === Load environment variables ===
load_dotenv()

# === Configuration from .env ===
SUPPORT_BOT_TOKEN = os.getenv("SUPPORT_BOT_TOKEN")
# SUPPORT_HUB_ID больше не используется
ADMINS = os.getenv("ADMINS", "").split(",") if os.getenv("ADMINS") else []
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SUPPORT_FAQ_PATH = os.getenv("SUPPORT_FAQ_PATH", "support/faq.yaml")
SUPPORT_HOURS = os.getenv("SUPPORT_HOURS", "10:00-20:00 MSK")
SUPPORT_RATE_LIMIT_USER = int(os.getenv("SUPPORT_RATE_LIMIT_USER", "3"))
SUPPORT_AUTOCLOSE_HOURS = int(os.getenv("SUPPORT_AUTOCLOSE_HOURS", "72"))

# Convert ADMINS to integers
ADMINS = [int(admin.strip()) for admin in ADMINS if admin.strip().isdigit()]

# === Logging setup ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler("support/support_bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# === Global constants ===
MOSCOW_TZ = pytz.timezone('Europe/Moscow')
TICKETS_DB_FILE = "support/support_tickets.json"
FAQ_CACHE = {}
LAST_FAQ_LOAD = None

# === Bot initialization ===
if not SUPPORT_BOT_TOKEN:
    raise ValueError("SUPPORT_BOT_TOKEN is not set in .env")

bot = Bot(
    token=SUPPORT_BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

# === FSM States ===
class AskStates(StatesGroup):
    waiting_for_question = State()

class TicketStates(StatesGroup):
    viewing_ticket = State()

class AdminStates(StatesGroup):
    waiting_for_reply = State()
    viewing_ticket = State()

# === Data Structures ===
@dataclass
class MessageRecord:
    t: str  # "user" or "agent"
    ts: str  # timestamp
    text: str
    file_id: Optional[str] = None

@dataclass
class Ticket:
    id: int
    user_id: int
    username: str
    status: str  # "open", "pending_user", "closed"
    topic_id: Optional[int]
    created_at: str
    updated_at: str
    messages: List[MessageRecord]
    category: str  # "billing", "limits", "miniapp", "profile", "other"
    autoclose_at: str
    csat: Optional[str] = None  # "up" or "down"

# === Thread-safe ticket storage ===
tickets_lock = threading.Lock()

# === Admin Notification System ===
async def notify_admins_new_ticket(ticket: Ticket) -> None:
    """Notify admins about new ticket"""
    try:
        message = (
            f"📬 Новый тикет №{ticket.id}\n"
            f"Пользователь: @{ticket.username} ({ticket.user_id})\n"
            f"Категория: {ticket.category}\n"
            f"Вопрос: {ticket.messages[0].text[:100]}{'...' if len(ticket.messages[0].text) > 100 else ''}"
        )
        
        for admin_id in ADMINS:
            try:
                await bot.send_message(
                    admin_id,
                    message,
                    reply_markup=get_admin_ticket_keyboard(ticket.id)
                )
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")
    except Exception as e:
        logger.error(f"Error notifying admins: {e}")

def get_admin_ticket_keyboard(ticket_id: int) -> types.InlineKeyboardMarkup:
    """Admin keyboard for ticket actions"""
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 Ответить", callback_data=f"admin:reply:{ticket_id}")
    builder.button(text="🔒 Закрыть", callback_data=f"admin:close:{ticket_id}")
    builder.button(text="📄 История", callback_data=f"admin:history:{ticket_id}")
    builder.adjust(2, 1)
    return builder.as_markup()

# === Utility Functions ===
def load_db() -> Dict[str, Any]:
    """Load tickets database from JSON file"""
    try:
        with open(TICKETS_DB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Convert message records to proper objects
            for ticket_id, ticket_data in data.get("tickets", {}).items():
                messages = []
                for msg in ticket_data.get("messages", []):
                    messages.append(MessageRecord(**msg))
                ticket_data["messages"] = messages
            return data
    except FileNotFoundError:
        # Return default structure
        return {
            "last_id": 0,
            "tickets": {},
            "map_user_open_ticket": {}
        }
    except Exception as e:
        logger.error(f"Error loading tickets DB: {e}")
        return {
            "last_id": 0,
            "tickets": {},
            "map_user_open_ticket": {}
        }

def save_db(data: Dict[str, Any]) -> None:
    """Save tickets database to JSON file"""
    with tickets_lock:
        try:
            # Convert MessageRecord objects to dicts for JSON serialization
            tickets_copy = {}
            for ticket_id, ticket_data in data.get("tickets", {}).items():
                ticket_dict = ticket_data.copy()
                messages_dicts = []
                for msg in ticket_dict.get("messages", []):
                    if isinstance(msg, MessageRecord):
                        messages_dicts.append({
                            "t": msg.t,
                            "ts": msg.ts,
                            "text": msg.text,
                            "file_id": msg.file_id
                        })
                    else:
                        messages_dicts.append(msg)
                ticket_dict["messages"] = messages_dicts
                tickets_copy[ticket_id] = ticket_dict
            
            data_to_save = data.copy()
            data_to_save["tickets"] = tickets_copy
            
            with open(TICKETS_DB_FILE, "w", encoding="utf-8") as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Error saving tickets DB: {e}")

def next_ticket_id() -> int:
    """Generate next ticket ID"""
    with tickets_lock:
        db = load_db()
        db["last_id"] += 1
        ticket_id = db["last_id"]
        save_db(db)
        return ticket_id

def get_open_ticket(user_id: int) -> Optional[Ticket]:
    """Get open ticket for user"""
    db = load_db()
    user_id_str = str(user_id)
    ticket_id_str = db.get("map_user_open_ticket", {}).get(user_id_str)
    
    if ticket_id_str and ticket_id_str in db.get("tickets", {}):
        ticket_data = db["tickets"][ticket_id_str]
        # Convert to Ticket object
        messages = [MessageRecord(**msg) if isinstance(msg, dict) else msg 
                   for msg in ticket_data.get("messages", [])]
        return Ticket(
            id=ticket_data["id"],
            user_id=ticket_data["user_id"],
            username=ticket_data["username"],
            status=ticket_data["status"],
            topic_id=ticket_data["topic_id"],
            created_at=ticket_data["created_at"],
            updated_at=ticket_data["updated_at"],
            messages=messages,
            category=ticket_data["category"],
            autoclose_at=ticket_data["autoclose_at"],
            csat=ticket_data.get("csat")
        )
    return None

def create_ticket(user_id: int, username: str, category: str, first_message: str) -> Ticket:
    """Create a new ticket"""
    with tickets_lock:
        ticket_id = next_ticket_id()
        now = datetime.now(MOSCOW_TZ)
        autoclose_at = now + timedelta(hours=SUPPORT_AUTOCLOSE_HOURS)
        
        ticket = Ticket(
            id=ticket_id,
            user_id=user_id,
            username=username or f"user_{user_id}",
            status="open",
            topic_id=None,  # Не используется
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
            messages=[MessageRecord(t="user", ts=now.isoformat(), text=first_message)],
            category=category,
            autoclose_at=autoclose_at.isoformat()
        )
        
        # Save to database
        db = load_db()
        db["tickets"][str(ticket_id)] = {
            "id": ticket.id,
            "user_id": ticket.user_id,
            "username": ticket.username,
            "status": ticket.status,
            "topic_id": ticket.topic_id,
            "created_at": ticket.created_at,
            "updated_at": ticket.updated_at,
            "messages": [{"t": msg.t, "ts": msg.ts, "text": msg.text, "file_id": msg.file_id} 
                        for msg in ticket.messages],
            "category": ticket.category,
            "autoclose_at": ticket.autoclose_at,
            "csat": ticket.csat
        }
        db["map_user_open_ticket"][str(user_id)] = str(ticket_id)
        save_db(db)
        
        # Уведомляем админов о новом тикете
        asyncio.create_task(notify_admins_new_ticket(ticket))
        
        return ticket

def append_message(ticket_id: int, role: str, text: str, file_id: Optional[str] = None) -> None:
    """Add a message to a ticket"""
    with tickets_lock:
        db = load_db()
        ticket_id_str = str(ticket_id)
        
        if ticket_id_str in db.get("tickets", {}):
            now = datetime.now(MOSCOW_TZ)
            message = MessageRecord(t=role, ts=now.isoformat(), text=text, file_id=file_id)
            
            # Convert existing messages if they're dicts
            messages = []
            for msg in db["tickets"][ticket_id_str].get("messages", []):
                if isinstance(msg, dict):
                    messages.append(MessageRecord(**msg))
                else:
                    messages.append(msg)
            
            messages.append(message)
            db["tickets"][ticket_id_str]["messages"] = [
                {"t": msg.t, "ts": msg.ts, "text": msg.text, "file_id": msg.file_id} 
                for msg in messages
            ]
            db["tickets"][ticket_id_str]["updated_at"] = now.isoformat()
            
            # Update autoclose time
            autoclose_at = now + timedelta(hours=SUPPORT_AUTOCLOSE_HOURS)
            db["tickets"][ticket_id_str]["autoclose_at"] = autoclose_at.isoformat()
            
            save_db(db)

def close_ticket(ticket_id: int, reason: str = "") -> None:
    """Close a ticket"""
    with tickets_lock:
        db = load_db()
        ticket_id_str = str(ticket_id)
        
        if ticket_id_str in db.get("tickets", {}):
            now = datetime.now(MOSCOW_TZ)
            db["tickets"][ticket_id_str]["status"] = "closed"
            db["tickets"][ticket_id_str]["updated_at"] = now.isoformat()
            
            # Remove from open ticket map
            user_id = db["tickets"][ticket_id_str]["user_id"]
            if str(user_id) in db.get("map_user_open_ticket", {}):
                del db["map_user_open_ticket"][str(user_id)]
            
            save_db(db)

def touch_ticket(ticket_id: int) -> None:
    """Update ticket's updated_at and autoclose_at timestamps"""
    with tickets_lock:
        db = load_db()
        ticket_id_str = str(ticket_id)
        
        if ticket_id_str in db.get("tickets", {}):
            now = datetime.now(MOSCOW_TZ)
            autoclose_at = now + timedelta(hours=SUPPORT_AUTOCLOSE_HOURS)
            
            db["tickets"][ticket_id_str]["updated_at"] = now.isoformat()
            db["tickets"][ticket_id_str]["autoclose_at"] = autoclose_at.isoformat()
            
            save_db(db)

# === FAQ Functions ===
def load_faq() -> Dict[str, Any]:
    """Load FAQ from YAML/JSON file"""
    global FAQ_CACHE, LAST_FAQ_LOAD
    
    try:
        # Check if file has been modified
        if os.path.exists(SUPPORT_FAQ_PATH):
            file_modified = os.path.getmtime(SUPPORT_FAQ_PATH)
            if LAST_FAQ_LOAD and file_modified <= LAST_FAQ_LOAD:
                return FAQ_CACHE
        
        # Load the FAQ file
        if SUPPORT_FAQ_PATH.endswith('.yaml') or SUPPORT_FAQ_PATH.endswith('.yml'):
            with open(SUPPORT_FAQ_PATH, 'r', encoding='utf-8') as f:
                FAQ_CACHE = yaml.safe_load(f) or {}
        elif SUPPORT_FAQ_PATH.endswith('.json'):
            with open(SUPPORT_FAQ_PATH, 'r', encoding='utf-8') as f:
                FAQ_CACHE = json.load(f)
        else:
            # Default to YAML
            with open(SUPPORT_FAQ_PATH, 'r', encoding='utf-8') as f:
                FAQ_CACHE = yaml.safe_load(f) or {}
        
        LAST_FAQ_LOAD = datetime.now().timestamp()
        logger.info(f"FAQ loaded from {SUPPORT_FAQ_PATH}")
        return FAQ_CACHE
    except Exception as e:
        logger.error(f"Error loading FAQ: {e}")
        return {}

def search_faq(query: str) -> List[Dict[str, Any]]:
    """Simple full-text search in FAQ"""
    faq = load_faq()
    results = []
    
    query_lower = query.lower()
    
    # Search in categories and questions
    for category in faq.get("categories", []):
        category_title = category.get("title", "").lower()
        category_desc = category.get("description", "").lower()
        
        # Search in questions
        for question in category.get("questions", []):
            q_title = question.get("title", "").lower()
            q_body = question.get("body", "").lower()
            
            # Calculate relevance score
            score = 0
            if query_lower in q_title:
                score += 3
            if query_lower in q_body:
                score += 2
            if query_lower in category_title:
                score += 1
            if query_lower in category_desc:
                score += 1
                
            if score > 0:
                results.append({
                    "category": category.get("title", ""),
                    "question": question.get("title", ""),
                    "answer": question.get("body", ""),
                    "score": score
                })
    
    # Sort by relevance and return top 3
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:3]

# === Time Functions ===
def is_within_support_hours() -> bool:
    """Check if current time is within support hours"""
    try:
        # Parse SUPPORT_HOURS format "10:00-20:00 MSK"
        hours_part = SUPPORT_HOURS.split(" ")[0]  # Get "10:00-20:00"
        start_time, end_time = hours_part.split("-")
        
        start_hour, start_minute = map(int, start_time.split(":"))
        end_hour, end_minute = map(int, end_time.split(":"))
        
        now_msk = datetime.now(MOSCOW_TZ)
        current_hour, current_minute = now_msk.hour, now_msk.minute
        
        start_total_minutes = start_hour * 60 + start_minute
        end_total_minutes = end_hour * 60 + end_minute
        current_total_minutes = current_hour * 60 + current_minute
        
        return start_total_minutes <= current_total_minutes <= end_total_minutes
    except Exception as e:
        logger.error(f"Error parsing support hours: {e}")
        return True  # Default to inside hours if parsing fails

def get_support_hours_text() -> str:
    """Get formatted support hours text"""
    return f"Мы отвечаем: {SUPPORT_HOURS}.\nВне часов сообщения фиксируются — ответим в ближайшее время."

# === Rate Limiting ===
user_last_message_time = {}  # user_id -> timestamp
user_rate_limit_lock = threading.Lock()

def is_rate_limited(user_id: int) -> bool:
    """Check if user is rate limited"""
    with user_rate_limit_lock:
        now = datetime.now().timestamp()
        last_time = user_last_message_time.get(user_id, 0)
        if now - last_time < SUPPORT_RATE_LIMIT_USER:
            return True
        user_last_message_time[user_id] = now
        return False

# === Auto-close Tickets ===
async def autoclose_tickets_task() -> None:
    """Background task to auto-close inactive tickets"""
    while True:
        try:
            await asyncio.sleep(15 * 60)  # 15 minutes
            
            with tickets_lock:
                db = load_db()
                now = datetime.now(MOSCOW_TZ)
                
                closed_tickets = []
                for ticket_id_str, ticket_data in db.get("tickets", {}).items():
                    if ticket_data.get("status") == "open":
                        try:
                            autoclose_at = datetime.fromisoformat(ticket_data.get("autoclose_at"))
                            if now >= autoclose_at:
                                # Close the ticket
                                ticket_data["status"] = "closed"
                                closed_tickets.append(int(ticket_id_str))
                                
                                # Remove from open ticket map
                                user_id = ticket_data["user_id"]
                                if str(user_id) in db.get("map_user_open_ticket", {}):
                                    del db["map_user_open_ticket"][str(user_id)]
                                
                                # Notify user
                                try:
                                    await bot.send_message(
                                        user_id,
                                        f"✅ Обращение №{ticket_id_str} закрыто (автоматически по истечении времени неактивности). Всё получилось? 👍/👎",
                                        reply_markup=get_csat_keyboard(int(ticket_id_str))
                                    )
                                except Exception as e:
                                    logger.warning(f"Could not notify user about auto-closed ticket {ticket_id_str}: {e}")
                        except Exception as e:
                            logger.error(f"Error processing ticket {ticket_id_str} for autoclose: {e}")
                
                if closed_tickets:
                    save_db(db)
                    logger.info(f"Auto-closed tickets: {closed_tickets}")
                    
        except Exception as e:
            logger.error(f"Error in autoclose task: {e}")
            await asyncio.sleep(60)  # Wait before retrying

# === Keyboard Builders ===
def get_main_menu() -> types.ReplyKeyboardMarkup:
    """Main menu keyboard"""
    kb = [
        [types.KeyboardButton(text="🔎 FAQ"), types.KeyboardButton(text="💬 Спросить")],
        [types.KeyboardButton(text="📂 Мои тикеты"), types.KeyboardButton(text="🕒 Время работы")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_csat_keyboard(ticket_id: int) -> types.InlineKeyboardMarkup:
    """CSAT (Customer Satisfaction) keyboard"""
    builder = InlineKeyboardBuilder()
    builder.button(text="👍 Да", callback_data=f"ticket:csat:up:{ticket_id}")
    builder.button(text="👎 Нет", callback_data=f"ticket:csat:down:{ticket_id}")
    builder.adjust(2)
    return builder.as_markup()

def get_ticket_action_keyboard(ticket_id: int) -> types.InlineKeyboardMarkup:
    """Ticket action keyboard"""
    builder = InlineKeyboardBuilder()
    builder.button(text="✉️ Открыть диалог", callback_data=f"ticket:open:{ticket_id}")
    builder.button(text="🔒 Закрыть тикет", callback_data=f"ticket:close:{ticket_id}")
    builder.adjust(2)
    return builder.as_markup()

def get_faq_categories_keyboard() -> types.InlineKeyboardMarkup:
    """FAQ categories keyboard"""
    faq = load_faq()
    builder = InlineKeyboardBuilder()
    
    for i, category in enumerate(faq.get("categories", [])[:10]):  # Limit to 10 categories
        builder.button(text=category.get("title", f"Category {i+1}"), 
                      callback_data=f"faq:category:{i}")
    
    builder.button(text="🔍 Поиск по вопросам", callback_data="faq:search")
    builder.button(text="🧑💻 Связаться с оператором", callback_data="faq:operator")
    builder.adjust(1)
    return builder.as_markup()

def get_faq_questions_keyboard(category_index: int) -> types.InlineKeyboardMarkup:
    """FAQ questions keyboard for a category"""
    faq = load_faq()
    builder = InlineKeyboardBuilder()
    
    if 0 <= category_index < len(faq.get("categories", [])):
        category = faq["categories"][category_index]
        for i, question in enumerate(category.get("questions", [])[:10]):  # Limit to 10 questions
            builder.button(text=question.get("title", f"Question {i+1}")[:30] + "...", 
                          callback_data=f"faq:question:{category_index}:{i}")
    
    builder.button(text="⬅️ Назад", callback_data="faq:back")
    builder.adjust(1)
    return builder.as_markup()

def get_faq_answer_keyboard() -> types.InlineKeyboardMarkup:
    """FAQ answer feedback keyboard"""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Помогло", callback_data="faq:helped")
    builder.button(text="🧑💻 Нужна помощь", callback_data="faq:need_help")
    builder.adjust(2)
    return builder.as_markup()

# === Command Handlers ===
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Handle /start command"""
    welcome_text = (
        "Привет! Я помогу с оплатой, подпиской и мини-приложением.\n"
        "Начни с 🔎 FAQ или нажми 💬 Спросить — опиши проблему (можно приложить скрин)."
    )
    await message.answer(welcome_text, reply_markup=get_main_menu())

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Handle /help command"""
    await cmd_start(message)  # Same as start

@dp.message(Command("hours"))
async def cmd_hours(message: types.Message):
    """Handle /hours command"""
    await message.answer(get_support_hours_text())

@dp.message(Command("faq"))
async def cmd_faq(message: types.Message):
    """Handle /faq command"""
    faq = load_faq()
    if not faq.get("categories"):
        await message.answer("База знаний пока пуста. Попробуйте позже.")
        return
    
    text = "Выберите категорию вопросов:"
    await message.answer(text, reply_markup=get_faq_categories_keyboard())

@dp.message(Command("ask"))
async def cmd_ask(message: types.Message, state: FSMContext):
    """Handle /ask command"""
    await message.answer("Опишите вашу проблему или задайте вопрос. Можно приложить скриншот.")
    await state.set_state(AskStates.waiting_for_question)

@dp.message(Command("tickets"))
async def cmd_tickets(message: types.Message):
    """Handle /tickets command"""
    user_id = message.from_user.id
    db = load_db()
    
    # Find user's tickets
    user_tickets = []
    for ticket_id, ticket_data in db.get("tickets", {}).items():
        if ticket_data.get("user_id") == user_id:
            user_tickets.append((int(ticket_id), ticket_data))
    
    # Sort by creation date (newest first)
    user_tickets.sort(key=lambda x: x[1].get("created_at", ""), reverse=True)
    
    if not user_tickets:
        await message.answer("У вас пока нет обращений.")
        return
    
    # Show last 5 tickets
    text = "Ваши последние обращения:\n\n"
    for ticket_id, ticket_data in user_tickets[:5]:
        status = ticket_data.get("status", "unknown")
        status_emoji = {
            "open": "🟢",
            "pending_user": "🟡",
            "closed": "🔴"
        }.get(status, "⚪")
        
        created_at = ticket_data.get("created_at", "")
        try:
            dt = datetime.fromisoformat(created_at)
            formatted_date = dt.strftime("%d.%m.%Y %H:%M")
        except:
            formatted_date = created_at[:16] if created_at else ""
        
        text += f"{status_emoji} №{ticket_id} ({status}) от {formatted_date}\n"
    
    await message.answer(text, reply_markup=get_main_menu())

def get_back_to_ticket_keyboard(ticket_id: int) -> types.InlineKeyboardMarkup:
    """Keyboard to go back to ticket view"""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data=f"admin:view:{ticket_id}")
    return builder.as_markup()

@dp.message(Command("admin_panel"))
async def cmd_admin_panel(message: types.Message):
    """Admin panel showing open tickets"""
    user_id = message.from_user.id
    if user_id not in ADMINS:
        await message.answer("У вас нет доступа к админ-панели.")
        return
    
    db = load_db()
    open_tickets = []
    
    for ticket_id, ticket_data in db.get("tickets", {}).items():
        if ticket_data.get("status") == "open":
            open_tickets.append((int(ticket_id), ticket_data))
    
    # Sort by creation date (newest first)
    open_tickets.sort(key=lambda x: x[1].get("created_at", ""), reverse=True)
    
    if not open_tickets:
        await message.answer("Нет открытых тикетов.")
        return
    
    text = "📬 Открытые тикеты:\n\n"
    for ticket_id, ticket_data in open_tickets[:10]:  # Show only first 10
        username = ticket_data.get("username", "Unknown")
        category = ticket_data.get("category", "other")
        created_at = ticket_data.get("created_at", "")
        try:
            dt = datetime.fromisoformat(created_at)
            formatted_date = dt.strftime("%d.%m.%Y %H:%M")
        except:
            formatted_date = created_at[:16] if created_at else ""
        
        text += f"№{ticket_id} · @{username} · {category}\n"
        text += f"  {formatted_date}\n"
        text += f"  {ticket_data['messages'][0]['text'][:50]}...\n\n"
    
    await message.answer(text, reply_markup=get_admin_list_keyboard(open_tickets))

def get_admin_list_keyboard(tickets) -> types.InlineKeyboardMarkup:
    """Keyboard for admin ticket list"""
    builder = InlineKeyboardBuilder()
    for ticket_id, _ in tickets[:10]:  # Max 10 buttons
        builder.button(text=f"#{ticket_id}", callback_data=f"admin:view:{ticket_id}")
    builder.button(text="🔄 Обновить", callback_data="admin:refresh")
    builder.adjust(3)  # 3 buttons per row
    return builder.as_markup()

@dp.message(Command("support_stats"))
async def cmd_support_stats(message: types.Message):
    """Handle /support_stats command (admin only)"""
    user_id = message.from_user.id
    if user_id not in ADMINS:
        await message.answer("У вас нет доступа к этой команде.")
        return
    
    db = load_db()
    now = datetime.now(MOSCOW_TZ)
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)
    
    # Stats for 24h and 7d
    tickets_24h = 0
    tickets_7d = 0
    closed_24h = 0
    closed_7d = 0
    first_response_times = []
    
    for ticket_data in db.get("tickets", {}).values():
        try:
            created_at = datetime.fromisoformat(ticket_data.get("created_at"))
            if created_at >= day_ago:
                tickets_24h += 1
            if created_at >= week_ago:
                tickets_7d += 1
                
            if ticket_data.get("status") == "closed":
                closed_at = datetime.fromisoformat(ticket_data.get("updated_at"))
                if closed_at >= day_ago:
                    closed_24h += 1
                if closed_at >= week_ago:
                    closed_7d += 1
                    
                # Calculate first response time if possible
                messages = ticket_data.get("messages", [])
                if len(messages) >= 2:  # User message and then agent response
                    user_msg_time = datetime.fromisoformat(messages[0].get("ts"))
                    agent_msg_time = datetime.fromisoformat(messages[1].get("ts"))
                    first_response_times.append((agent_msg_time - user_msg_time).total_seconds())
        except Exception as e:
            logger.error(f"Error processing ticket for stats: {e}")
    
    avg_first_response = sum(first_response_times) / len(first_response_times) if first_response_times else 0
    
    text = (
        "<b>Статистика поддержки:</b>\n\n"
        f"<b>За 24 часа:</b>\n"
        f"  • Входящих: {tickets_24h}\n"
        f"  • Закрыто: {closed_24h}\n\n"
        f"<b>За 7 дней:</b>\n"
        f"  • Входящих: {tickets_7d}\n"
        f"  • Закрыто: {closed_7d}\n"
        f"  • Среднее время первого ответа: {avg_first_response/60:.1f} мин\n"
    )
    
    await message.answer(text)

# === Message Handlers ===
@dp.message(StateFilter(AskStates.waiting_for_question))
async def handle_question(message: types.Message, state: FSMContext):
    """Handle user question in ask flow"""
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name or f"user_{user_id}"
    
    # Check rate limit
    if is_rate_limited(user_id):
        await message.answer("Пожалуйста, не пишите слишком часто. Подождите немного перед следующим сообщением.")
        return
    
    question_text = message.text or message.caption or "Вопрос от пользователя"
    
    # Search FAQ first
    faq_results = search_faq(question_text)
    
    if faq_results:
        # Show top FAQ result
        top_result = faq_results[0]
        answer_text = top_result["answer"]
        
        # Truncate if too long
        if len(answer_text) > 1000:
            answer_text = answer_text[:1000] + "..."
        
        response_text = f"<b>Возможно, вам поможет:</b>\n\n{answer_text}"
        await message.answer(response_text, reply_markup=get_faq_answer_keyboard())
    else:
        # If no FAQ match, escalate to ticket
        ticket = create_ticket(user_id, username, "other", question_text)
        await message.answer(f"✅ Создали обращение №{ticket.id}. Ответ придёт сюда.")
        
        # Clear state
        await state.clear()

@dp.message(F.text == "🔎 FAQ")
async def btn_faq(message: types.Message):
    """Handle FAQ button"""
    await cmd_faq(message)

@dp.message(F.text == "💬 Спросить")
async def btn_ask(message: types.Message, state: FSMContext):
    """Handle Ask button"""
    await cmd_ask(message, state)

@dp.message(F.text == "📂 Мои тикеты")
async def btn_tickets(message: types.Message):
    """Handle Tickets button"""
    await cmd_tickets(message)

@dp.message(F.text == "🕒 Время работы")
async def btn_hours(message: types.Message):
    """Handle Hours button"""
    await cmd_hours(message)

# === Callback Query Handlers ===
@dp.callback_query(F.data == "faq:helped")
async def cb_faq_helped(callback: types.CallbackQuery):
    """User indicated FAQ helped"""
    await callback.answer("Рады, что смогли помочь!", show_alert=True)
    await callback.message.delete()

@dp.callback_query(F.data == "faq:need_help")
async def cb_faq_need_help(callback: types.CallbackQuery, state: FSMContext):
    """User needs operator help after FAQ"""
    await callback.message.delete()
    await callback.message.answer("Опишите подробнее вашу проблему, и мы передадим её оператору.")
    await state.set_state(AskStates.waiting_for_question)

@dp.callback_query(F.data == "faq:operator")
async def cb_faq_operator(callback: types.CallbackQuery, state: FSMContext):
    """User wants to contact operator directly"""
    await callback.message.delete()
    await callback.message.answer("Опишите вашу проблему, и мы передадим её оператору.")
    await state.set_state(AskStates.waiting_for_question)

@dp.callback_query(F.data.startswith("ticket:close:"))
async def cb_close_ticket(callback: types.CallbackQuery):
    """Close ticket"""
    try:
        ticket_id = int(callback.data.split(":")[2])
        close_ticket(ticket_id)
        
        # Update forum topic if exists
        db = load_db()
        ticket_data = db.get("tickets", {}).get(str(ticket_id), {})
        topic_id = ticket_data.get("topic_id")
        
        if topic_id and SUPPORT_HUB_ID:
            try:
                await bot.edit_forum_topic(
                    chat_id=int(SUPPORT_HUB_ID),
                    message_thread_id=topic_id,
                    name=f"Ticket #{ticket_id} · CLOSED"
                )
            except Exception as e:
                logger.error(f"Error renaming forum topic: {e}")
        
        # Notify user
        await callback.message.edit_text(f"✅ Обращение №{ticket_id} закрыто. Всё получилось?")
        await callback.message.edit_reply_markup(reply_markup=get_csat_keyboard(ticket_id))
        await callback.answer()
    except Exception as e:
        logger.error(f"Error closing ticket: {e}")
        await callback.answer("Ошибка при закрытии тикета", show_alert=True)

@dp.callback_query(F.data.startswith("ticket:csat:"))
async def cb_ticket_csat(callback: types.CallbackQuery):
    """Handle CSAT feedback"""
    try:
        parts = callback.data.split(":")
        rating = parts[2]  # "up" or "down"
        ticket_id = int(parts[3])
        
        # Update ticket with CSAT
        with tickets_lock:
            db = load_db()
            if str(ticket_id) in db.get("tickets", {}):
                db["tickets"][str(ticket_id)]["csat"] = rating
                save_db(db)
        
        feedback_text = "Спасибо за ваш отзыв!" if rating == "up" else "Жаль, что не всё получилось. Мы постараемся улучшить сервис."
        await callback.message.edit_text(feedback_text)
        await callback.answer()
    except Exception as e:
        logger.error(f"Error handling CSAT: {e}")
        await callback.answer("Ошибка при обработке отзыва", show_alert=True)

# === Admin Callback Handlers ===
@dp.callback_query(F.data.startswith("admin:"))
async def admin_callback_handler(callback: types.CallbackQuery, state: FSMContext):
    """Handle all admin callbacks"""
    user_id = callback.from_user.id
    if user_id not in ADMINS:
        await callback.answer("У вас нет доступа.", show_alert=True)
        return
    
    try:
        action_parts = callback.data.split(":")
        action = action_parts[1]
        
        if action == "reply":
            ticket_id = int(action_parts[2])
            await state.update_data(replying_ticket_id=ticket_id)
            await state.set_state(AdminStates.waiting_for_reply)
            await callback.message.edit_text(f"Введите ответ для тикета №{ticket_id}:")
            
        elif action == "close":
            ticket_id = int(action_parts[2])
            close_ticket(ticket_id, "Закрыто администратором")
            await callback.message.edit_text(f"✅ Тикет №{ticket_id} закрыт. Всё получилось?", reply_markup=get_csat_keyboard(ticket_id))
            
        elif action == "history":
            ticket_id = int(action_parts[2])
            db = load_db()
            ticket_data = db.get("tickets", {}).get(str(ticket_id))
            
            if not ticket_data:
                await callback.answer("Тикет не найден.", show_alert=True)
                return
            
            history_text = f"📜 История тикета №{ticket_id}:\n\n"
            for msg in ticket_data.get("messages", []):
                role = "👤 Пользователь" if msg["t"] == "user" else "👨‍💼 Админ"
                timestamp = msg["ts"][:16] if msg["ts"] else ""
                history_text += f"{role} ({timestamp}):\n{msg['text']}\n\n"
            
            await callback.message.edit_text(history_text, reply_markup=get_back_to_ticket_keyboard(ticket_id))
            
        elif action == "view":
            ticket_id = int(action_parts[2])
            db = load_db()
            ticket_data = db.get("tickets", {}).get(str(ticket_id))
            
            if not ticket_data:
                await callback.answer("Тикет не найден.", show_alert=True)
                return
            
            username = ticket_data.get("username", "Unknown")
            category = ticket_data.get("category", "other")
            status = ticket_data.get("status", "unknown")
            created_at = ticket_data.get("created_at", "")
            
            try:
                dt = datetime.fromisoformat(created_at)
                formatted_date = dt.strftime("%d.%m.%Y %H:%M")
            except:
                formatted_date = created_at[:16] if created_at else ""
            
            info_text = (
                f"🎫 Тикет №{ticket_id}\n"
                f"Пользователь: @{username}\n"
                f"Категория: {category}\n"
                f"Статус: {status}\n"
                f"Создан: {formatted_date}\n\n"
                f"Вопрос: {ticket_data['messages'][0]['text']}"
            )
            
            await callback.message.edit_text(
                info_text, 
                reply_markup=get_admin_ticket_keyboard(ticket_id)
            )
            
        elif action == "refresh":
            # Refresh the admin panel
            db = load_db()
            open_tickets = []
            
            for ticket_id, ticket_data in db.get("tickets", {}).items():
                if ticket_data.get("status") == "open":
                    open_tickets.append((int(ticket_id), ticket_data))
            
            open_tickets.sort(key=lambda x: x[1].get("created_at", ""), reverse=True)
            
            if not open_tickets:
                await callback.message.edit_text("Нет открытых тикетов.")
                return
            
            text = "📬 Открытые тикеты:\n\n"
            for ticket_id, ticket_data in open_tickets[:10]:
                username = ticket_data.get("username", "Unknown")
                category = ticket_data.get("category", "other")
                created_at = ticket_data.get("created_at", "")
                try:
                    dt = datetime.fromisoformat(created_at)
                    formatted_date = dt.strftime("%d.%m.%Y %H:%M")
                except:
                    formatted_date = created_at[:16] if created_at else ""
                
                text += f"№{ticket_id} · @{username} · {category}\n"
                text += f"  {formatted_date}\n"
                text += f"  {ticket_data['messages'][0]['text'][:50]}...\n\n"
            
            await callback.message.edit_text(text, reply_markup=get_admin_list_keyboard(open_tickets))
            
    except Exception as e:
        logger.error(f"Error in admin callback handler: {e}")
        await callback.answer("Ошибка обработки запроса", show_alert=True)

# === Admin Reply Handler ===
@dp.message(AdminStates.waiting_for_reply)
async def admin_reply_handler(message: types.Message, state: FSMContext):
    """Handle admin reply to ticket"""
    user_id = message.from_user.id
    if user_id not in ADMINS:
        await message.answer("У вас нет доступа.")
        return
    
    try:
        # Get ticket ID from state
        state_data = await state.get_data()
        ticket_id = state_data.get("replying_ticket_id")
        
        if not ticket_id:
            await message.answer("Ошибка: не найден ID тикета.")
            await state.clear()
            return
        
        # Add admin message to ticket
        append_message(ticket_id, "agent", message.text)
        
        # Get user ID from ticket
        db = load_db()
        ticket_data = db.get("tickets", {}).get(str(ticket_id))
        
        if not ticket_data:
            await message.answer("Ошибка: тикет не найден.")
            await state.clear()
            return
        
        user_id = ticket_data.get("user_id")
        
        # Send message to user
        try:
            await bot.send_message(user_id, f"👨‍💼 Ответ от поддержки:\n\n{message.text}")
            await message.answer("✅ Ответ отправлен пользователю.")
        except Exception as e:
            logger.error(f"Failed to send message to user {user_id}: {e}")
            await message.answer("⚠️ Не удалось отправить сообщение пользователю.")
        
        # Clear state and show ticket info
        await state.clear()
        
        # Show ticket info
        username = ticket_data.get("username", "Unknown")
        category = ticket_data.get("category", "other")
        status = ticket_data.get("status", "unknown")
        created_at = ticket_data.get("created_at", "")
        
        try:
            dt = datetime.fromisoformat(created_at)
            formatted_date = dt.strftime("%d.%m.%Y %H:%M")
        except:
            formatted_date = created_at[:16] if created_at else ""
        
        info_text = (
            f"🎫 Тикет №{ticket_id}\n"
            f"Пользователь: @{username}\n"
            f"Категория: {category}\n"
            f"Статус: {status}\n"
            f"Создан: {formatted_date}\n\n"
            f"Вопрос: {ticket_data['messages'][0]['text']}"
        )
        
        await message.answer(info_text, reply_markup=get_admin_ticket_keyboard(ticket_id))
        
    except Exception as e:
        logger.error(f"Error in admin reply handler: {e}")
        await message.answer("Ошибка при отправке ответа.")
        await state.clear()

# === Support Hub Bridge ===
# Этот функционал больше не используется, так как мы напрямую общаемся с пользователями
# @dp.message(lambda message: message.chat.id == int(SUPPORT_HUB_ID) if SUPPORT_HUB_ID and SUPPORT_HUB_ID.isdigit() else False)
# async def agent_reply_in_topic(message: types.Message):
#     """Handle agent replies in support hub topics"""
#     if not message.message_thread_id:
#         return  # Not in a topic
#     
#     # Find ticket by topic_id
#     db = load_db()
#     ticket_id = None
#     for tid, ticket_data in db.get("tickets", {}).items():
#         if ticket_data.get("topic_id") == message.message_thread_id:
#             ticket_id = int(tid)
#             break
#     
#     if not ticket_id:
#         return
#     
#     # Forward message to user
#     user_id = db["tickets"][str(ticket_id)]["user_id"]
#     
#     try:
#         if message.photo:
#             await bot.send_photo(
#                 user_id, 
#                 message.photo[-1].file_id, 
#                 caption=message.caption
#             )
#         elif message.document:
#             await bot.send_document(
#                 user_id, 
#                 message.document.file_id, 
#                 caption=message.caption
#             )
#         elif message.text:
#             await bot.send_message(user_id, message.text)
#         
#         # Add to ticket history
#         message_text = message.text or message.caption or "Файл/медиа"
#         append_message(ticket_id, "agent", message_text)
#     except Exception as e:
#         logger.error(f"Error forwarding agent message to user: {e}")

# === Main Function ===
async def main():
    """Main function to run the support bot"""
    logger.info("Starting Support Bot...")
    
    # Start autoclose task
    asyncio.create_task(autoclose_tickets_task())
    
    # Start polling
    await dp.start_polling(bot)

if __name__ == "__main__":
    # Wrapper: delegate to canonical root support.py
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from support import main as support_main
    
    logger.info("Starting support bot via support/support.py wrapper...")
    asyncio.run(support_main())