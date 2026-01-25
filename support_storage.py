# support_storage.py
import os
import json
import asyncio
import tempfile
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

logger = logging.getLogger(__name__)

TICKETS_PATH = os.getenv("SUPPORT_TICKETS_PATH", "./support_tickets.json")

_ticket_locks: Dict[str, asyncio.Lock] = {}
_global_lock = asyncio.Lock()

def _ticket_lock(ticket_id: str) -> asyncio.Lock:
    """Возвращает per-ticket lock"""
    if ticket_id not in _ticket_locks:
        _ticket_locks[ticket_id] = asyncio.Lock()
    return _ticket_locks[ticket_id]

def _atomic_write(path: str, data: dict):
    """Атомарная запись в файл через временный файл"""
    dir_path = os.path.dirname(path) or "."
    tmp_fd, tmp_path = tempfile.mkstemp(prefix="support_", suffix=".json", dir=dir_path)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    finally:
        try:
            os.remove(tmp_path)
        except:
            pass

def _load_raw() -> dict:
    """Загружает базу тикетов"""
    if not os.path.exists(TICKETS_PATH):
        return {"tickets": {}, "last_id": 0}
    try:
        with open(TICKETS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка загрузки {TICKETS_PATH}: {e}")
        return {"tickets": {}, "last_id": 0}

def _save_raw(db: dict):
    """Сохраняет базу тикетов"""
    _atomic_write(TICKETS_PATH, db)

def _generate_ticket_id(last_id: int) -> str:
    """Генерирует ID тикета вида T-2026-01-0001"""
    now = datetime.utcnow()
    return f"T-{now.year}-{now.month:02d}-{last_id + 1:04d}"

async def create_ticket(
    user_snapshot: dict,
    category: str,
    subject: str,
    text: str,
    files: Optional[List[str]] = None
) -> dict:
    """Создаёт новый тикет"""
    from support_models import ticket_schema
    
    async with _global_lock:
        db = _load_raw()
        last_id = db.get("last_id", 0)
        new_id = last_id + 1
        ticket_id = _generate_ticket_id(last_id)
        
        ticket = ticket_schema(
            ticket_id=ticket_id,
            user_id=user_snapshot.get("telegram_id", 0),
            chat_id=user_snapshot.get("chat_id", user_snapshot.get("telegram_id", 0)),
            username=user_snapshot.get("username", ""),
            first_name=user_snapshot.get("first_name", ""),
            category=category,
            subject=subject,
            initial_text=text,
            files=files
        )
        
        db["tickets"][ticket_id] = ticket
        db["last_id"] = new_id
        _save_raw(db)
        
        logger.info(f"Создан тикет {ticket_id} для пользователя {user_snapshot.get('telegram_id')}")
        return ticket

async def get_ticket(ticket_id: str) -> Optional[dict]:
    """Получает тикет по ID с миграцией старых данных"""
    db = _load_raw()
    ticket = db.get("tickets", {}).get(ticket_id)
    
    if ticket:
        # Migrate old attachment format
        ticket = _migrate_ticket_attachments(ticket)
    
    return ticket

def _migrate_ticket_attachments(ticket: dict) -> dict:
    """Мигрирует старый формат вложений в новый"""
    from support_models import normalize_attachments
    
    # Migrate each message
    if "messages" in ticket:
        for msg in ticket["messages"]:
            if "files" in msg:
                msg["files"] = normalize_attachments(msg["files"])
    
    # Ensure new fields exist
    if "user_write_enabled" not in ticket:
        ticket["user_write_enabled"] = ticket.get("status") != "new"
    
    if "allow_one_followup" not in ticket:
        ticket["allow_one_followup"] = False
    
    return ticket

async def update_ticket(ticket_id: str, patch: dict):
    """Обновляет тикет (merge patch)"""
    async with _ticket_lock(ticket_id):
        db = _load_raw()
        if ticket_id not in db.get("tickets", {}):
            logger.warning(f"Тикет {ticket_id} не найден для обновления")
            return
        
        current = db["tickets"][ticket_id]
        current.update(patch)
        current["updated_at"] = datetime.utcnow().isoformat() + "Z"
        
        _save_raw(db)
        logger.info(f"Обновлён тикет {ticket_id}: {list(patch.keys())}")

async def append_message(ticket_id: str, message_dict: dict):
    """Добавляет сообщение в тикет"""
    async with _ticket_lock(ticket_id):
        db = _load_raw()
        if ticket_id not in db.get("tickets", {}):
            logger.warning(f"Тикет {ticket_id} не найден для добавления сообщения")
            return
        
        ticket = db["tickets"][ticket_id]
        if "messages" not in ticket:
            ticket["messages"] = []
        
        ticket["messages"].append(message_dict)
        ticket["updated_at"] = datetime.utcnow().isoformat() + "Z"
        
        _save_raw(db)
        logger.info(f"Добавлено сообщение в тикет {ticket_id} от {message_dict.get('from')}")

async def list_tickets(
    status: Optional[str] = None,
    status_in: Optional[List[str]] = None,
    assignee_id: Optional[int] = None,
    user_id: Optional[int] = None,
    limit: int = 100,
    include_closed: bool = True
) -> List[dict]:
    """Список тикетов с фильтрацией"""
    db = _load_raw()
    tickets = list(db.get("tickets", {}).values())
    
    # Migrate attachments on read
    tickets = [_migrate_ticket_attachments(t) for t in tickets]
    
    # Фильтры
    if status:
        tickets = [t for t in tickets if t.get("status") == status]
    
    if status_in:
        tickets = [t for t in tickets if t.get("status") in status_in]
    
    if assignee_id is not None:
        if assignee_id == 0:  # Special case: unassigned
            tickets = [t for t in tickets if t.get("assignee_id") is None]
        else:
            tickets = [t for t in tickets if t.get("assignee_id") == assignee_id]
    
    if user_id:
        tickets = [t for t in tickets if t.get("user_id") == user_id]
    
    if not include_closed:
        tickets = [t for t in tickets if t.get("status") not in ["closed", "spam"]]
    
    # Сортировка по дате создания (новые первыми)
    tickets.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    
    return tickets[:limit]

async def count_by_status() -> dict:
    """Подсчёт тикетов по статусам"""
    db = _load_raw()
    tickets = list(db.get("tickets", {}).values())
    
    counts = {}
    for t in tickets:
        status = t.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    
    return counts
