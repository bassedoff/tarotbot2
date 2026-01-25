# subscription_manager.py
"""
Модуль для управления подписками и раскладами пользователей после оплаты
"""

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional

# Пути к файлам данных
USER_DATA_FILE = "tarot_user_data.json"

# Период для бесплатного кредита (в днях)
FREE_PERIOD_DAYS = 14

# Create a lock for thread-safe file operations
_USER_LOCK = threading.Lock()

def _migrate_user_record(u: dict) -> dict:
    """
    Миграция старых полей в новую модель данных.
    Преобразует layout_limit, layouts_used, purchased_layouts, free_layouts в purchased_layouts.
    """
    if "last_free_credit_at" not in u:
        u["last_free_credit_at"] = datetime.now(timezone.utc).isoformat()
    
    if "subscriptions" not in u:
        u["subscriptions"] = []
    
    if "referrals" not in u:
        u["referrals"] = []
    
    if "referral_bonus_processed" not in u:
        u["referral_bonus_processed"] = False
    
    if "referrer_id" not in u:
        u["referrer_id"] = None
    
    if "first_seen_at" not in u:
        u["first_seen_at"] = datetime.now(timezone.utc).isoformat()
    
    if "referral_activated_at" not in u:
        u["referral_activated_at"] = ""
    
    u.setdefault("daily_count", 0)
    u.setdefault("last_reset", str(datetime.now().date().isoformat()))
    u.setdefault("last_question", "")
    u.setdefault("last_cards", [])
    u.setdefault("purchased_layouts", 0)
    u.setdefault("free_layouts", 0)
    
    return u

def migrate_user_record_v2(u: dict) -> dict:
    """
    Миграция данных пользователя к версии 2 (каноническая схема)
    """
    out = {}
    
    out["telegram_id"] = int(u.get("telegram_id") or u.get("chat_id") or 0)
    out["username"] = u.get("username") or ""
    out["first_name"] = u.get("first_name") or ""
    
    out["first_seen_at"] = u.get("first_seen_at") or datetime.now(timezone.utc).isoformat()
    out["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    out["last_question"] = u.get("last_question") or ""
    out["last_cards"] = u.get("last_cards") or []
    
    out["subscriptions"] = u.get("subscriptions") or []
    
    pl = int(u.get("purchased_layouts", 0))
    out["purchased_layouts"] = pl
    
    out["last_free_credit_at"] = u.get("last_free_credit_at") or ""
    
    out["daily_count"] = int(u.get("daily_count", u.get("daily_spreads_count", 0)) or 0)
    out["last_reset"] = u.get("last_reset") or u.get("spreads_reset_date") or datetime.now(timezone.utc).date().isoformat()
    
    out["referrer_id"] = u.get("referrer_id", None)
    out["referrals"] = u.get("referrals") or []
    out["referral_bonus_processed"] = u.get("referral_bonus_processed", False)
    out["referral_activated_at"] = u.get("referral_activated_at") or ""
    
    return out

def _ensure_defaults(u: dict) -> dict:
    """Устанавливает дефолтные значения для полей пользователя"""
    return _migrate_user_record(u)

def utc_now_iso() -> str:
    """Возвращает текущую дату/время в формате ISO"""
    return datetime.now(timezone.utc).isoformat()

def today_str() -> str:
    """Возвращает текущую дату в формате YYYY-MM-DD"""
    return datetime.now(timezone.utc).date().isoformat()

def load_user_data() -> Dict[str, Any]:
    """Загрузка данных пользователей из файла с миграцией к v2"""
    try:
        with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # Создаем бэкап перед миграцией
        import shutil
        shutil.copy2(USER_DATA_FILE, USER_DATA_FILE.replace('.json', '.backup.json'))
        
        # Мигрируем все записи к v2
        migrated = {}
        changed = False
        for uid in data:
            old_record = data[uid]
            new_record = migrate_user_record_v2(old_record)
            migrated[uid] = new_record
            
            # Проверяем, были ли изменения
            if new_record != old_record:
                changed = True
        
        # Сохраняем мигрированные данные, если были изменения
        if changed:
            with open(USER_DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(migrated, f, ensure_ascii=False, indent=4)
        
        return migrated
    except FileNotFoundError:
        return {}
    except Exception as e:
        logging.error(f"Ошибка загрузки {USER_DATA_FILE}: {e}")
        return {}

def save_user_data_merge(all_mem_data: dict) -> None:
    """
    Сохраняет users с merge из файла, чтобы не терять подписки/покупки,
    добавленные webhook'ом.
    """
    with _USER_LOCK:
        try:
            disk = {}
            if os.path.exists(USER_DATA_FILE):
                with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
                    try:
                        disk = json.load(f)
                    except Exception:
                        disk = {}

            merged = dict(disk)
            for uid, snap in (all_mem_data or {}).items():
                prev = merged.get(uid, {})
                if isinstance(prev, dict) and isinstance(snap, dict):
                    # Никогда не теряем эти поля
                    for k in ("subscriptions", "purchased_layouts"):
                        if k in prev and k not in snap:
                            snap[k] = prev[k]
                    merged[uid] = {**prev, **snap}
                else:
                    merged[uid] = snap

            with open(USER_DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logging.error(f"Ошибка сохранения {USER_DATA_FILE} (merge): {e}")

def save_user(uid: str, patch: dict) -> None:
    """Атомарное сохранение данных пользователя (thread-safe merge)"""
    save_user_data_merge({uid: patch})

def save_user_data(data: Dict[str, Any]) -> None:
    """Алиас для совместимости со старым кодом"""
    save_user_data_merge(data)

def load_user_from_disk(user_id: int | str) -> dict:
    """
    Возвращает свежие данные пользователя из файла и одновременно
    синхронизирует их в оперативную структуру user_data.
    """
    uid = str(user_id)
    disk_user = {}
    with _USER_LOCK:
        if os.path.exists(USER_DATA_FILE):
            try:
                with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
                    disk = json.load(f)
                    disk_user = disk.get(uid, {})
            except Exception:
                disk_user = {}
    return migrate_user_record_v2(disk_user)

def load_user(uid: str) -> dict:
    """Загрузка данных пользователя из файла с миграцией к v2"""
    with _USER_LOCK:
        if os.path.exists(USER_DATA_FILE):
            try:
                with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if uid in data:
                        return migrate_user_record_v2(data[uid])
            except Exception:
                pass
    return {}

def get_or_create_user(user_id: int | str) -> dict:
    """
    1) Загружает user_data из tarot_user_data.json.
    2) Если пользователя нет — создаёт новую запись по умолчанию:
       - purchased_layouts = 0
       - subscriptions = []
       - last_free_credit_at = None
       - referrer_id = None
       - referrals = []
       - referral_bonus_processed = False
       - first_seen_at = now (UTC ISO)
    3) Гарантирует наличие всех нужных ключей.
    4) Возвращает dict пользователя (без записи на диск).
    """
    uid = str(user_id)
    user = {}
    
    with _USER_LOCK:
        if os.path.exists(USER_DATA_FILE):
            try:
                with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
                    all_data = json.load(f)
                    user = all_data.get(uid, {})
            except Exception:
                user = {}
    
    if not user:
        current_time = datetime.now(timezone.utc).isoformat()
        user = {
            "telegram_id": int(user_id) if isinstance(user_id, (int, str)) and str(user_id).isdigit() else None,
            "username": None,
            "first_name": None,
            "purchased_layouts": 0,
            "subscriptions": [],
            "last_free_credit_at": None,
            "referrer_id": None,
            "referrals": [],
            "referral_bonus_processed": False,
            "referral_activated_at": "",
            "first_seen_at": current_time,
            "last_question": "",
            "last_cards": [],
            "miniapp_message_id": 0,
            "chat_id": int(user_id) if isinstance(user_id, (int, str)) and str(user_id).isdigit() else None,
        }
    
    return _ensure_defaults(user)

def initialize_user_data(user_id: str) -> Dict[str, Any]:
    """Инициализация данных пользователя"""
    current_time = datetime.now().isoformat()
    today = datetime.now().date().isoformat()
    
    return {
        "daily_count": 0,
        "last_reset": today,
        "referrals": [],
        "last_question": "",
        "last_cards": [],
        "layout_limit": 0,
        "layouts_used": 0,
        # Поля для управления подписками
        "subscriptions": [],  # Список активных подписок
        "purchased_layouts": 0,  # Количество купленных разовых раскладов
        "free_layouts": 3,  # Количество бесплатных раскладов
        "last_activity": current_time,
    }

def add_subscription(user_id: str, subscription_type: str, duration_days: int) -> bool:
    """
    Добавление подписки пользователю
    subscription_type: "week", "month", "single"
    duration_days: длительность подписки в днях
    """
    try:
        logging.info(f"➕ [SUBSCRIPTION] Добавление подписки пользователю {user_id}: тип={subscription_type}, длительность={duration_days} дней")
        
        user_data = load_user_data()
        uid = str(user_id)
        
        logging.info(f"➕ [SUBSCRIPTION] Загружены данные пользователя {uid}")
        
        if uid not in user_data:
            user_data[uid] = initialize_user_data(uid)
            logging.info(f"➕ [SUBSCRIPTION] Созданы новые данные для пользователя {uid}")
        
        user = user_data[uid]
        
        # Создаем новую подписку
        start_time = datetime.now(timezone.utc)
        end_time = start_time + timedelta(days=duration_days)
        
        subscription = {
            "type": subscription_type,
            "start_date": start_time.isoformat(),
            "end_date": end_time.isoformat(),
            "is_active": True
        }
        
        logging.info(f"➕ [SUBSCRIPTION] Создана подписка: {subscription}")
        
        # Добавляем подписку в список
        if "subscriptions" not in user:
            user["subscriptions"] = []
        
        user["subscriptions"].append(subscription)
        logging.info(f"➕ [SUBSCRIPTION] Подписка добавлена в список подписок пользователя {uid}")
        
        # Для недельной и месячной подписки даем безлимит
        # Для разового расклада увеличиваем счетчик купленных раскладов
        if subscription_type in ["week", "month"]:
            # Уже добавили подписку, которая даст безлимит в can_user_access_reading
            logging.info(f"➕ [SUBSCRIPTION] Установлен безлимит для подписки типа {subscription_type}")
        elif subscription_type == "single":
            user["purchased_layouts"] = user.get("purchased_layouts", 0) + 1
            logging.info(f"➕ [SUBSCRIPTION] Добавлен один купленный расклад для пользователя {uid}")
        
        save_user_data_merge(user_data)
        logging.info(f"✅ [SUBSCRIPTION] Данные пользователя {uid} успешно сохранены")
        
        return True
        
    except Exception as e:
        logging.error(f"❌ [SUBSCRIPTION] Ошибка при добавлении подписки пользователю {user_id}: {e}", exc_info=True)
        return False

def add_purchased_layouts(user_id: str, count: int = 1) -> bool:
    """
    Добавление купленных разовых раскладов пользователю
    """
    try:
        logging.info(f"➕ [LAYOUTS] Добавление {count} купленных раскладов пользователю {user_id}")
        
        user = get_or_create_user(user_id)
        uid = str(user_id)
        
        old_count = user.get("purchased_layouts", 0)
        user["purchased_layouts"] = old_count + count
        user["updated_at"] = utc_now_iso()
        
        logging.info(f"➕ [LAYOUTS] Обновлено количество купленных раскладов для пользователя {uid}: {old_count} -> {user['purchased_layouts']}")
        
        save_user_data_merge({uid: user})
        logging.info(f"✅ [LAYOUTS] Данные пользователя {uid} успешно сохранены")
        
        return True
        
    except Exception as e:
        logging.error(f"❌ [LAYOUTS] Ошибка при добавлении купленных раскладов пользователю {user_id}: {e}", exc_info=True)
        return False

def has_active_month_subscription(user: dict) -> tuple[bool, str | None]:
    """
    Проверяет наличие активной месячной подписки
    Возвращает (активна ли подписка, дата окончания)
    """
    subs = user.get("subscriptions", [])
    now = datetime.now(timezone.utc)
    active = False
    last_end = None
    for s in subs:
        if s.get("type") != "month":
            continue
        end_iso = s.get("end_date")
        if end_iso:
            last_end = end_iso
            try:
                if now <= datetime.fromisoformat(end_iso):
                    active = True
            except Exception:
                pass
    return active, last_end

def has_active_subscription(user: dict) -> tuple[bool, str | None]:
    """
    Проверяет наличие активной подписки любого типа
    Возвращает (активна ли подписка, дата окончания)
    """
    subs = user.get("subscriptions", [])
    now = datetime.now(timezone.utc)
    active = False
    last_end = None
    for s in subs:
        end_iso = s.get("end_date")
        if end_iso:
            last_end = end_iso
            try:
                if now <= datetime.fromisoformat(end_iso):
                    active = True
                    break
            except Exception:
                pass
    return active, last_end

def refresh_daily_window(uid: str) -> dict:
    """
    Сброс суточного лимита, если дата сброса не совпадает с сегодняшней
    """
    user = load_user(uid)
    if user.get("last_reset") != today_str():
        user["daily_count"] = 0
        user["last_reset"] = today_str()
        user["updated_at"] = utc_now_iso()
        save_user(uid, user)
    return user

def increment_daily_if_subscribed(uid: str) -> None:
    """
    Увеличивает суточный счетчик, если есть активная подписка
    """
    user = refresh_daily_window(uid)
    active, _ = has_active_subscription(user)
    if active:
        user["daily_count"] = user.get("daily_count", 0) + 1
        user["updated_at"] = utc_now_iso()
        save_user(uid, user)

def weekly_free_available(user: dict) -> bool:
    """
    Проверяет, доступен ли бесплатный расклад по недельному лимиту
    """
    last_free_at = user.get("last_free_credit_at")
    if not last_free_at:
        return True
    
    try:
        last = datetime.fromisoformat(last_free_at)
        now = datetime.now(timezone.utc)
        elapsed_days = (now - last).days
        return elapsed_days >= 7
    except Exception:
        return True

def consume_spread(uid: str) -> str:
    """
    Потребление одного расклада
    Возвращает тип использованного доступа: "sub", "paid", "weekly", "none"
    """
    # Обновляем суточное окно
    user = refresh_daily_window(uid)
    
    # Проверяем активную подписку
    active, end_iso = has_active_subscription(user)
    
    # Если подписка активна и лимит не превышен
    if active and user.get("daily_count", 0) < 40:
        increment_daily_if_subscribed(uid)
        return "sub"
    
    # Если есть купленные расклады
    if user.get("purchased_layouts", 0) > 0:
        user["purchased_layouts"] -= 1
        user["updated_at"] = utc_now_iso()
        save_user(uid, user)
        return "paid"
    
    # Если доступен недельный бесплатный расклад
    if weekly_free_available(user):
        user["last_free_credit_at"] = utc_now_iso()
        user["updated_at"] = utc_now_iso()
        save_user(uid, user)
        return "weekly"
    
    # Нет доступов
    return "none"

def check_subscription_status(user_id: str) -> Dict[str, Any]:
    """
    Проверка статуса подписки пользователя
    Возвращает словарь с информацией о подписке
    """
    try:
        logging.info(f"🔍 [SUBSCRIPTION] Проверка статуса подписки для пользователя {user_id}")
        
        user_data = load_user_data()
        uid = str(user_id)
        
        logging.info(f"🔍 [SUBSCRIPTION] Загружены данные пользователя {uid}")
        
        if uid not in user_data:
            logging.info(f"🔍 [SUBSCRIPTION] Пользователь {uid} не найден в данных")
            return {
                "has_active_subscription": False,
                "subscription_type": None,
                "subscription_end_date": None,
                "purchased_layouts": 0
            }
        
        user = user_data[uid]
        current_time = datetime.now(timezone.utc)
        
        logging.info(f"🔍 [SUBSCRIPTION] Текущее время: {current_time.isoformat()}")
        
        active_subscription = None
        purchased_layouts = user.get("purchased_layouts", 0)
        
        logging.info(f"🔍 [SUBSCRIPTION] Количество purchased_layouts: {purchased_layouts}")
        
        subscriptions = user.get("subscriptions", [])
        logging.info(f"🔍 [SUBSCRIPTION] Найдено подписок: {len(subscriptions)}")
        
        for i, subscription in enumerate(subscriptions):
            is_active = subscription.get("is_active", False)
            logging.info(f"🔍 [SUBSCRIPTION] Подписка {i}: is_active={is_active}")
            
            if is_active:
                end_date_str = subscription["end_date"]
                end_date = datetime.fromisoformat(end_date_str)
                logging.info(f"🔍 [SUBSCRIPTION] Дата окончания подписки {i}: {end_date.isoformat()}")
                
                if current_time < end_date:
                    active_subscription = subscription
                    logging.info(f"🔍 [SUBSCRIPTION] Найдена активная подписка: {active_subscription}")
                else:
                    # Подписка истекла, деактивируем её
                    subscription["is_active"] = False
                    logging.info(f"🔍 [SUBSCRIPTION] Подписка {i} истекла и деактивирована")
        
        # Сохраняем изменения, если были деактивированы подписки
        if any(not sub["is_active"] for sub in subscriptions if sub.get("is_active", False)):
            save_user_data_merge(user_data)
            logging.info(f"✅ [SUBSCRIPTION] Обновлены данные пользователя {uid} после деактивации истекших подписок")
        
        result = {
            "has_active_subscription": active_subscription is not None,
            "subscription_type": active_subscription["type"] if active_subscription else None,
            "subscription_end_date": active_subscription["end_date"] if active_subscription else None,
            "purchased_layouts": purchased_layouts
        }
        
        logging.info(f"🔍 [SUBSCRIPTION] Результат проверки для пользователя {uid}: {result}")
        return result
        
    except Exception as e:
        logging.error(f"❌ [SUBSCRIPTION] Ошибка при проверке статуса подписки для пользователя {user_id}: {e}", exc_info=True)
        return {
            "has_active_subscription": False,
            "subscription_type": None,
            "subscription_end_date": None,
            "purchased_layouts": 0
        }

def try_consume_token(user: dict, user_id: int) -> str | None:
    """
    Приоритетное потребление токенов:
    1) Если активная месячная подписка — не списываем, возвращаем "subscription"
    2) Иначе пробуем purchased_layouts
    3) Иначе пробуем free_layouts
    4) Иначе None
    """
    active, _ = has_active_month_subscription(user)
    if active:
        return "subscription"  # ничего не списывать

    if user.get("purchased_layouts", 0) > 0:
        user["purchased_layouts"] -= 1
        user["updated_at"] = utc_now_iso()
        # Сохраняем только этого пользователя
        save_user_data_merge({str(user_id): user})
        return "purchased"

    if user.get("free_layouts", 0) > 0:
        user["free_layouts"] -= 1
        user["updated_at"] = utc_now_iso()
        save_user_data_merge({str(user_id): user})
        return "free"

    return None

def consume_layout(user_id: str, user_data: Dict[str, Any]) -> bool:
    """
    Потребление одного расклада (для обратной совместимости)
    Возвращает True, если расклад успешно потреблен
    """
    uid = str(user_id)
    
    # Используем новую функцию consume_spread
    result = consume_spread(uid)
    return result != "none"

def get_user_benefits_info(user_id: str) -> str:
    """
    Получение информации о benefits пользователя для отображения
    """
    # Загружаем свежие данные пользователя
    user = load_user(str(user_id))
    status = check_subscription_status(user_id)
    
    if status["has_active_subscription"]:
        end_date = datetime.fromisoformat(status["subscription_end_date"])
        end_date_str = end_date.strftime("%d.%m.%Y %H:%M")
        
        if status["subscription_type"] == "week":
            return f"У вас активна недельная подписка (безлимит) до {end_date_str}"
        elif status["subscription_type"] == "month":
            return f"У вас активна месячная подписка (безлимит) до {end_date_str}"
    
    if user.get("purchased_layouts", 0) > 0:
        layouts = user["purchased_layouts"]
        spread_word = "расклад" if layouts == 1 else ("раскада" if layouts in [2, 3, 4] else "раскладов")
        return f"У вас есть {layouts} доступных {spread_word}"
    
    return "⚠️ Сейчас раскладов нет.\n\n🎴 Купи разовый расклад — <b>49 ₽</b>\nили\n📆 Оформи подписку на <b>30 дней</b> и получай безлимит*.\n\n<i>Мы сообщим, когда появится новый бесплатный расклад.</i>"

def maybe_grant_periodic_free_credit(user: dict) -> dict:
    """
    Если прошло >= 14 дней с момента last_free_credit_at,
    начисляет бесплатные кредиты и сдвигает last_free_credit_at.
    Возвращает обновлённого пользователя (без записи на диск).
    """
    last_free_at = user.get("last_free_credit_at")
    
    if last_free_at is None:
        # Это покрывается стартовой логикой, не трогаем здесь
        return user
    
    try:
        last = datetime.fromisoformat(last_free_at)
        now = datetime.now(timezone.utc)
        elapsed_days = (now - last).days
        periods = elapsed_days // FREE_PERIOD_DAYS
        
        if periods >= 1:
            user["purchased_layouts"] = user.get("purchased_layouts", 0) + periods
            # Сдвигаем last_free_credit_at на количество целых периодов
            user["last_free_credit_at"] = (last + timedelta(days=FREE_PERIOD_DAYS * periods)).isoformat()
            logging.info(f"💰 Начисленo {periods} бесплатный(х) кредит(ов) пользователю. Новый баланс: {user['purchased_layouts']}")
    except Exception as e:
        logging.error(f"Ошибка при начислении периодического кредита: {e}")
    
    return user

def can_start_reading(user_id: int | str) -> tuple[bool, str]:
    """
    1) Загружает/инициализирует пользователя (get_or_create_user).
    2) Выдает стартовый кредит, если это фирст кол на странице.
    3) Вызывает maybe_grant_periodic_free_credit(user).
    4) Обновляет/проверяет подписки (временная заглушка).
    5) Логика доступа:
        - если user["purchased_layouts"] > 0:
              return True, f"🎴 У вас есть {user['purchased_layouts']} доступных расклад(ов)"
        - иначе:
              return False, "❌ У вас нет доступных раскладов..."
    6) Проверяет дневной лимит для месячной подписки (40 раскладов/сутки)
    7) Сохраняет изменения пользователя на диск.
    """
    user = get_or_create_user(user_id)
    uid = str(user_id)
    
    # Если это первый раз (стартовой кредит не выдан), выдаем его
    if user.get("last_free_credit_at") is None:
        user["purchased_layouts"] = user.get("purchased_layouts", 0) + 1
        user["last_free_credit_at"] = datetime.now(timezone.utc).isoformat()
        user["updated_at"] = utc_now_iso()
    
    # Начисляем периодические бесплатные кредиты
    user = maybe_grant_periodic_free_credit(user)
    
    # Проверяем статус подписки
    status = check_subscription_status(uid)
    
    # Проверяем дневной лимит для месячной подписки
    if status["has_active_subscription"] and status["subscription_type"] == "month":
        # Обновляем суточное окно
        user = refresh_daily_window(uid)
        
        # Проверяем лимит (40 раскладов в день)
        if user["daily_count"] >= 40:
            save_user_data_merge({uid: user})
            return False, "⏱ Дневной лимит исрасходован.\n\nЗавтра лимит обновится автоматически."
        
        # Увеличиваем счетчик
        user["daily_count"] += 1
        user["updated_at"] = utc_now_iso()
        save_user_data_merge({uid: user})
        remaining = 40 - user["daily_count"]
        logging.info(f"📊 [RATE_LIMIT] Пользователь {uid}: {user['daily_count']}/40 раскладов сегодня")
        return True, f"🔓 У вас активна месячная подписка (осталось {remaining} раскладов сегодня)"
    
    # Сохраняем изменения
    save_user_data_merge({uid: user})
    
    # Формируем ответ
    if user.get("purchased_layouts", 0) > 0:
        layouts = user["purchased_layouts"]
        spread_word = "расклад" if layouts == 1 else ("раскада" if layouts in [2, 3, 4] else "раскладов")
        return True, f"🎴 У вас есть {layouts} доступный(х) {spread_word}"
    
    return False, "⚠️ Сейчас раскладов нет.\n\n🎴 Купи разовый расклад — <b>49 ₽</b>\nили\n📆 Оформи подписку на <b>30 дней</b> и получай безлимит*.\n\n<i>Мы сообщим, когда появится новый бесплатный расклад.</i>"

# === Функции для имитации обработки платежей ===

def process_payment_week(user_id: str) -> bool:
    """
    Имитация обработки платежа за недельную подписку
    В реальной реализации здесь будет интеграция с ЮKassa
    """
    try:
        logging.info(f"📅 [SUBSCRIPTION] Начало обработки недельной подписки для пользователя {user_id}")
        
        # Добавляем недельную подписку (7 дней)
        success = add_subscription(user_id, "week", 7)
        
        if success:
            logging.info(f"✅ [SUBSCRIPTION] Пользователю {user_id} успешно добавлена недельная подписка")
            return True
        else:
            logging.error(f"❌ [SUBSCRIPTION] Ошибка при добавлении недельной подписки пользователю {user_id}")
            return False
    except Exception as e:
        logging.error(f"❌ [SUBSCRIPTION] Ошибка при обработке платежа за недельную подписку для пользователя {user_id}: {e}", exc_info=True)
        return False

def process_payment_month(user_id: str) -> bool:
    """
    Имитация обработки платежа за месячную подписку
    В реальной реализации здесь будет интеграция с ЮKassa
    """
    try:
        logging.info(f"📅 [SUBSCRIPTION] Начало обработки месячной подписки для пользователя {user_id}")
        
        # Добавляем месячную подписку (30 дней)
        success = add_subscription(user_id, "month", 30)
        
        if success:
            # Сбрасываем суточный лимит
            uid = str(user_id)
            user = load_user(uid)
            user["daily_count"] = 0
            user["last_reset"] = today_str()
            user["updated_at"] = utc_now_iso()
            save_user(uid, user)
            
            logging.info(f"✅ [SUBSCRIPTION] Пользователю {user_id} успешно добавлена месячная подписка")
            return True
        else:
            logging.error(f"❌ [SUBSCRIPTION] Ошибка при добавлении месячной подписки пользователю {user_id}")
            return False
    except Exception as e:
        logging.error(f"❌ [SUBSCRIPTION] Ошибка при обработке платежа за месячную подписку для пользователя {user_id}: {e}", exc_info=True)
        return False

def process_payment_single(user_id: str) -> bool:
    """
    Имитация обработки платежа за один расклад
    В реальной реализации здесь будет интеграция с ЮKassa
    """
    try:
        logging.info(f"🎴 [SUBSCRIPTION] Начало обработки разового расклада для пользователя {user_id}")
        
        # Добавляем один купленный расклад
        success = add_purchased_layouts(user_id, 1)
        
        if success:
            logging.info(f"✅ [SUBSCRIPTION] Пользователю {user_id} успешно добавлен один купленный расклад")
            return True
        else:
            logging.error(f"❌ [SUBSCRIPTION] Ошибка при добавлении купленного расклада пользователю {user_id}")
            return False
    except Exception as e:
        logging.error(f"❌ [SUBSCRIPTION] Ошибка при обработке платежа за один расклад для пользователя {user_id}: {e}", exc_info=True)
        return False

def get_spread_entitlement(user: dict, now_utc=None) -> dict:
    """
    Read-only проверка права пользователя на новый расклад.
    Возвращает:
      {
        "allowed": bool,
        "source": "subscription" | "purchased" | "weekly" | None,
        "daily_remaining": int | None,
        "next_weekly_at": iso | None,
        "reason": str
      }
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    
    # Обновляем суточное окно (read-only)
    today = now_utc.date().isoformat()
    if user.get("last_reset") != today:
        user["daily_count"] = 0
        user["last_reset"] = today
    
    # 1) Проверка активной подписки
    active_sub, end_iso = has_active_subscription(user)
    if active_sub:
        daily_count = user.get("daily_count", 0)
        if daily_count < 40:
            return {
                "allowed": True,
                "source": "subscription",
                "daily_remaining": 40 - daily_count,
                "next_weekly_at": None,
                "reason": "subscription_ok"
            }
        else:
            return {
                "allowed": False,
                "source": None,
                "daily_remaining": 0,
                "next_weekly_at": None,
                "reason": "daily_limit_exceeded"
            }
    
    # 2) Проверка купленных раскладов
    purchased = int(user.get("purchased_layouts", 0))
    if purchased > 0:
        return {
            "allowed": True,
            "source": "purchased",
            "daily_remaining": None,
            "next_weekly_at": None,
            "reason": "purchased_ok"
        }
    
    # 3) Проверка weekly free (только если нет активной подписки)
    if weekly_free_available(user):
        return {
            "allowed": True,
            "source": "weekly",
            "daily_remaining": None,
            "next_weekly_at": None,
            "reason": "weekly_free_ok"
        }
    
    # 4) Нет прав - вычисляем когда будет следующий weekly free
    last_weekly = user.get("last_free_credit_at")
    next_weekly = None
    if last_weekly:
        try:
            last_dt = datetime.fromisoformat(last_weekly.replace("Z", "+00:00"))
            next_dt = last_dt + timedelta(days=7)
            next_weekly = next_dt.isoformat()
        except:
            pass
    
    return {
        "allowed": False,
        "source": None,
        "daily_remaining": None,
        "next_weekly_at": next_weekly,
        "reason": "no_entitlement"
    }

def mark_reading_consumed(user_id: int | str) -> None:
    """
    Вызывается один раз, когда расклад успешно завершен:
      - миниапп прислала карты,
      - ИИ сгенерировал текст,
      - бот успешно отправил сообщение с интерпретацией.

    Логика:
      - используем consume_spread для правильного списания
    """
    try:
        uid = str(user_id)
        result = consume_spread(uid)
        
        if result == "sub":
            logging.info(f"🔓 [READING] Не списываем - активна подписка для пользователя {user_id}")
        elif result == "paid":
            logging.info(f"🔼 [READING] Списан один купленный расклад для пользователя {user_id}")
        elif result == "weekly":
            logging.info(f"🆓 [READING] Использован недельный бесплатный расклад для пользователя {user_id}")
        else:
            logging.warning(f"⚠️ [READING] Нет доступных раскладов для пользователя {user_id}")
            
    except Exception as e:
        logging.error(f"❌ [READING] Ошибка при отмечании расклада как использованного для пользователя {user_id}: {e}", exc_info=True)