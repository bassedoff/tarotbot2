# storage_adapter.py
import json
import os
import tempfile
import asyncio
import logging
from datetime import datetime, timezone, timedelta, date
from typing import Tuple, Optional
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DATA_PATH", "./tarot_user_data.json")
SAFE_STORAGE_WRITE = os.getenv("SAFE_STORAGE_WRITE", "0")
DEBUG_MODE = os.getenv("DEBUG", "0") == "1"

logger = logging.getLogger(__name__)
if DEBUG_MODE:
    logger.setLevel(logging.DEBUG)

_user_locks: dict[str, asyncio.Lock] = {}

def _lock(uid: str) -> asyncio.Lock:
    """Возвращает per-user lock для атомарных операций"""
    if uid not in _user_locks:
        _user_locks[uid] = asyncio.Lock()
    return _user_locks[uid]

def iso_now() -> str:
    """Возвращает текущее время в ISO формате"""
    return datetime.now(timezone.utc).isoformat()

def today_str() -> str:
    """Возвращает текущую дату в формате YYYY-MM-DD"""
    return date.today().isoformat()

def _atomic_write(path: str, data: dict):
    """Атомарная запись в файл через временный файл"""
    tmp_fd, tmp_path = tempfile.mkstemp(prefix="tarot_", suffix=".json", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        os.replace(tmp_path, path)
    finally:
        try:
            os.remove(tmp_path)
        except:
            pass

def _load_raw() -> dict:
    """Загружает весь JSON файл"""
    if not os.path.exists(DB_PATH):
        return {}
    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        logging.error(f"Ошибка чтения {DB_PATH}")
        return {}

def _save_raw(db: dict):
    """Сохраняет весь JSON файл атомарно"""
    _atomic_write(DB_PATH, db)

def load_user(uid: int | str, write_back: bool = False) -> dict:
    """
    Загружает данные пользователя из файла.
    Возвращает словарь с дефолтными значениями, если пользователь не найден.
    Если write_back=True, записывает дефолты на диск.
    Если write_back=False (по умолчанию), только в памяти.
    """
    uid = str(uid)
    db = _load_raw()
    user = db.get(uid, {})
    
    user.setdefault("telegram_id", int(uid) if uid.isdigit() else 0)
    user.setdefault("username", "")
    user.setdefault("first_name", "")
    user.setdefault("first_seen_at", iso_now())
    user.setdefault("updated_at", iso_now())
    user.setdefault("last_question", "")
    user.setdefault("last_cards", [])
    user.setdefault("subscriptions", [])
    user.setdefault("purchased_layouts", 0)
    user.setdefault("last_free_credit_at", "")
    user.setdefault("daily_count", 0)
    user.setdefault("last_reset", today_str())
    user.setdefault("referrer_id", None)
    user.setdefault("referrals", [])
    user.setdefault("referral_bonus_processed", False)
    user.setdefault("referral_activated_at", "")
    
    if "credits" in user:
        y = int(user.get("purchased_layouts", 0))
        x = int(user.get("credits", 0))
        user["purchased_layouts"] = max(0, y + x)
        user.pop("credits", None)
        if write_back:
            db[uid] = user
            _save_raw(db)
        logger.debug(f"[migration] uid={uid}, credits={x} -> purchased_layouts, total={user['purchased_layouts']}")
    elif write_back and (db.get(uid, {}) != user):
        db[uid] = user
        _save_raw(db)
    
    return user

def load_user_readonly(uid: int | str) -> dict:
    """Удобная обёртка: загружает пользователя БЕЗ записи на диск."""
    return load_user(uid, write_back=False)

async def save_user_merge(uid: int | str, patch: dict):
    """
    Мягкое слияние: обновляет только ключи из patch, остальное не трогаем.
    Write-through для совместимости: если меняем purchased_layouts, зеркалим в credits.
    Если SAFE_STORAGE_WRITE != "1", только логируем.
    """
    uid = str(uid)
    if SAFE_STORAGE_WRITE != "1":
        logger.debug(f"[save_user_merge] SAFE_STORAGE_WRITE=0, не пишем, uid={uid}, keys={list(patch.keys())}")
        return
    
    if "credits" in patch:
        patch.pop("credits")
        logger.debug(f"[save_user_merge] фильтр credits в patch, uid={uid}")
    
    async with _lock(uid):
        db = _load_raw()
        cur = db.get(uid, {})
        
        if "credits" in cur:
            y = int(cur.get("purchased_layouts", 0))
            x = int(cur.get("credits", 0))
            cur["purchased_layouts"] = max(0, y + x)
            cur.pop("credits", None)
            logger.debug(f"[save_user_merge] миграция credits={x}, uid={uid}")
        
        cur.pop("credits", None)
        cur.update(patch)
        cur["updated_at"] = iso_now()
        
        if "purchased_layouts" in patch:
            cur["purchased_layouts"] = max(cur.get("purchased_layouts", 0), patch["purchased_layouts"])
        
        db[uid] = cur
        _save_raw(db)
        
        logger.debug(f"[save_user_merge] uid={uid}, keys={list(patch.keys())}")

def has_active_month(user: dict) -> Tuple[bool, Optional[str]]:
    """
    Проверяет наличие активной месячной подписки.
    Возвращает (активна ли подписка, дата окончания в ISO формате).
    """
    now = datetime.now(timezone.utc)
    for s in user.get("subscriptions", []):
        if s.get("type") == "month":
            end = s.get("end_date")
            try:
                if end and now <= datetime.fromisoformat(end.replace("Z", "+00:00")):
                    return True, end
            except:
                pass
    return False, None
