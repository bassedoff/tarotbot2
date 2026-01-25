# support_models.py
from typing import Optional, List, Dict, Any
from datetime import datetime

def ticket_schema(
    ticket_id: str,
    user_id: int,
    chat_id: int,
    username: str,
    first_name: str,
    category: str,
    subject: str,
    initial_text: str,
    files: Optional[List] = None
) -> Dict[str, Any]:
    """Возвращает схему нового тикета"""
    now = datetime.utcnow().isoformat() + "Z"
    
    # Normalize files to dict format
    normalized_files = normalize_attachments(files or [])
    
    return {
        "ticket_id": ticket_id,
        "user_id": user_id,
        "chat_id": chat_id,
        "username": username,
        "first_name": first_name,
        "category": category,
        "status": "new",
        "priority": "normal",
        "subject": subject,
        "messages": [
            {
                "from": "user",
                "text": initial_text,
                "ts": now,
                "files": normalized_files
            }
        ],
        "assignee_id": None,
        "tags": [],
        "created_at": now,
        "updated_at": now,
        "links": {
            "yookassa_payment_id": None,
            "subscription_snapshot": None
        },
        "user_write_enabled": False,  # User can't write until operator replies
        "allow_one_followup": False
    }

def message_schema(from_: str, text: str, files: Optional[List] = None) -> Dict[str, Any]:
    """Возвращает схему сообщения"""
    now = datetime.utcnow().isoformat() + "Z"
    
    # Normalize files to dict format
    normalized_files = normalize_attachments(files or [])
    
    return {
        "from": from_,
        "text": text,
        "ts": now,
        "files": normalized_files
    }

def normalize_attachments(files: List) -> List[Dict[str, Any]]:
    """Нормализует вложения: конвертирует старые строковые file_id в dict-формат"""
    normalized = []
    for f in files:
        if isinstance(f, dict):
            # Already normalized
            if "kind" in f and "file_id" in f:
                normalized.append(f)
            else:
                # Old dict format without kind, assume photo
                normalized.append({
                    "kind": "photo",
                    "file_id": f.get("file_id", ""),
                    "mime_type": f.get("mime_type"),
                    "file_name": f.get("file_name")
                })
        elif isinstance(f, str):
            # Old string format (just file_id)
            normalized.append({
                "kind": "photo",  # Assume photo for old data
                "file_id": f,
                "mime_type": None,
                "file_name": None
            })
    return normalized

# Категории
CATEGORIES = {
    "payments": "Платежи",
    "subscription": "Подписка",
    "tech": "Технические проблемы",
    "abuse": "Жалобы",
    "other": "Другое"
}

# Статусы
STATUSES = {
    "new": "Новый",
    "open": "Открыт",
    "awaiting_user": "Ожидает пользователя",
    "resolved": "Решён",
    "closed": "Закрыт",
    "spam": "Спам"
}

# Приоритеты
PRIORITIES = {
    "low": "Низкий",
    "normal": "Нормальный",
    "high": "Высокий"
}
