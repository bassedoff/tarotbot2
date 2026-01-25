# storage.py
import json, os, tempfile, asyncio, logging
from datetime import datetime, timezone, date
from typing import Tuple

DB_PATH = os.getenv("DATA_PATH", "./tarot_user_data.json")
SAFE_STORAGE_WRITE = os.getenv("SAFE_STORAGE_WRITE", "0")
DEBUG_MODE = os.getenv("DEBUG", "0") == "1"

logger = logging.getLogger(__name__)
if DEBUG_MODE:
    logger.setLevel(logging.DEBUG)

_user_locks: dict[str, asyncio.Lock] = {}

def _lock(uid: str) -> asyncio.Lock:
    if uid not in _user_locks:
        _user_locks[uid] = asyncio.Lock()
    return _user_locks[uid]

def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def today_str() -> str:
    return date.today().isoformat()

def _atomic_write(path: str, data: dict):
    tmp_fd, tmp_path = tempfile.mkstemp(prefix="tarot_", suffix=".json", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        os.replace(tmp_path, path)
    finally:
        try: os.remove(tmp_path)
        except: pass

def _load_raw() -> dict:
    if not os.path.exists(DB_PATH): return {}
    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except: return {}

def _save_raw(db: dict):
    _atomic_write(DB_PATH, db)

def _iso_or(val: str, default: str) -> str:
    if not val: return default
    try:
        datetime.fromisoformat(val.replace("Z","+00:00"))
        return val
    except: return default

def _date_or_today(val: str) -> str:
    try:
        return datetime.fromisoformat(val).date().isoformat()
    except:
        try:
            return datetime.fromisoformat(val.replace("Z","+00:00")).date().isoformat()
        except: return today_str()

def migrate_user_record_v2(u: dict) -> dict:
    out = {
        "telegram_id": int(u.get("telegram_id") or u.get("chat_id") or 0),
        "username": u.get("username") or "",
        "first_name": u.get("first_name") or "",
        "first_seen_at": _iso_or(u.get("first_seen_at"), iso_now()),
        "updated_at": iso_now(),
        "last_question": u.get("last_question") or "",
        "last_cards": u.get("last_cards") or [],
        "subscriptions": u.get("subscriptions") or [],
        "purchased_layouts": int(max(u.get("purchased_layouts", 0), u.get("credits", 0))),
        "last_free_credit_at": _iso_or(u.get("last_free_credit_at"), ""),
        "daily_count": int(u.get("daily_count") or u.get("daily_spreads_count") or 0),
        "last_reset": _date_or_today(u.get("last_reset") or u.get("spreads_reset_date") or today_str()),
        "referrer_id": u.get("referrer_id", None),
        "referrals": u.get("referrals") or [],
        "referral_bonus_processed": u.get("referral_bonus_processed", False),
        "referral_activated_at": _iso_or(u.get("referral_activated_at"), ""),
    }
    return out

def load_user(uid: int | str, write_back: bool = False) -> dict:
    """Загружает данные пользователя, при необходимости нормализует.
    Если write_back=True, сохраняет нормализованную версию на диск.
    Если write_back=False (по умолчанию), изменения остаются только в памяти.
    """
    uid = str(uid)
    db = _load_raw()
    cur = db.get(uid, {})
    cur_m = migrate_user_record_v2(cur)
    
    if "credits" in cur_m:
        y = int(cur_m.get("purchased_layouts", 0))
        x = int(cur_m.get("credits", 0))
        cur_m["purchased_layouts"] = max(0, y + x)
        cur_m.pop("credits", None)
        if write_back:
            db[uid] = cur_m
            _save_raw(db)
        logger.debug(f"[migration] uid={uid}, credits={x} -> purchased_layouts, total={cur_m['purchased_layouts']}")
    elif write_back and (cur != cur_m):
        db[uid] = cur_m
        _save_raw(db)
    return cur_m

def load_user_readonly(uid: int | str) -> dict:
    """Удобная обёртка: загружает пользователя БЕЗ записи на диск."""
    return load_user(uid, write_back=False)

async def save_user_merge(uid: int | str, patch: dict):
    """Безопасная запись полей пользователя (мердж только указанных ключей).
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
        cur = migrate_user_record_v2(db.get(uid, {}))
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
        _atomic_write(DB_PATH, db)
        logger.debug(f"[save_user_merge] uid={uid}, keys={list(patch.keys())}")

def has_active_subscription(user: dict) -> Tuple[bool, str | None]:
    now = datetime.now(timezone.utc)
    for s in user.get("subscriptions", []):
        if s.get("type") == "month":
            end = s.get("end_date")
            try:
                if end and now <= datetime.fromisoformat(end.replace("Z","+00:00")):
                    return True, end
            except: pass
    return False, None

async def refresh_daily_window(uid: int | str) -> dict:
    u = load_user_readonly(uid)
    if u.get("last_reset") != today_str():
        await save_user_merge(uid, {"daily_count": 0, "last_reset": today_str()})
        u = load_user_readonly(uid)
    return u

def weekly_free_available(user: dict) -> bool:
    last = user.get("last_free_credit_at") or ""
    if not last: return True
    try:
        last_dt = datetime.fromisoformat(last.replace("Z","+00:00"))
    except:
        return True
    delta = datetime.now(timezone.utc) - last_dt
    return delta.days >= 7

async def consume_spread(uid: int | str) -> str:
    u = await refresh_daily_window(uid)
    active, _ = has_active_subscription(u)
    if active and u.get("daily_count", 0) < 40:
        await save_user_merge(uid, {"daily_count": u.get("daily_count", 0) + 1})
        return "sub"
    if u.get("purchased_layouts", 0) > 0:
        await save_user_merge(uid, {"purchased_layouts": u["purchased_layouts"] - 1})
        return "paid"
    if weekly_free_available(u):
        await save_user_merge(uid, {"last_free_credit_at": iso_now()})
        return "weekly"
    return "none"
