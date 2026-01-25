#!/usr/bin/env python3
"""
admin_cli.py - Инструмент командной строки для администратора Tarot Bot
Безопасный просмотр и управление данными JSON без изменения логики работы бота.

Использование:
    python admin_cli.py user <telegram_id>              # Показать детали пользователя
    python admin_cli.py stats                            # Показать общую статистику
    python admin_cli.py list-users [--limit N]           # Список всех пользователей
    python admin_cli.py add-layouts <telegram_id> <N>    # Добавить купленные расклады пользователю
    python admin_cli.py yookassa-events [--limit N]      # Показать последние события YooKassa
    python admin_cli.py support-tickets [--status STATUS] # Показать тикеты поддержки
"""

import sys
import json
import os
from datetime import datetime, timezone
from typing import Optional

# Импорт модулей хранилища (использование существующей логики)
try:
    from storage import load_user_readonly, DB_PATH
    from support_storage import TICKETS_PATH, list_tickets as list_support_tickets
    from payment_handler import PROCESSED_EVENTS_FILE
except ImportError as e:
    print(f"❌ Ошибка импорта модулей хранилища: {e}")
    print("Убедитесь, что admin_cli.py находится в том же каталоге, что и storage.py")
    sys.exit(1)


def format_datetime(iso_str: str) -> str:
    """Форматирование строки ISO datetime для отображения"""
    if not iso_str:
        return "Н/Д"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except:
        return iso_str


def show_user(telegram_id: str):
    """Показать подробную информацию о пользователе"""
    print(f"\n{'='*60}")
    print(f"👤 Детали пользователя: {telegram_id}")
    print(f"{'='*60}\n")
    
    try:
        user = load_user_readonly(telegram_id)
        
        if not user.get("telegram_id"):
            print(f"❌ Пользователь не найден: {telegram_id}")
            return
        
        print(f"Telegram ID:       {user.get('telegram_id')}")
        print(f"Username:          @{user.get('username', 'Н/Д')}")
        print(f"Имя:               {user.get('first_name', 'Н/Д')}")
        print(f"Впервые замечен:   {format_datetime(user.get('first_seen_at', ''))}")
        print(f"Последнее обновл.: {format_datetime(user.get('updated_at', ''))}")
        print()
        print(f"📊 Использование:")
        print(f"  Счетчик за день: {user.get('daily_count', 0)}")
        print(f"  Последний сброс: {user.get('last_reset', 'Н/Д')}")
        print(f"  Куплено раскл.:  {user.get('purchased_layouts', 0)}")
        print()
        
        # Подписки
        subs = user.get("subscriptions", [])
        if subs:
            print(f"💫 Подписки:")
            for i, sub in enumerate(subs, 1):
                sub_type = sub.get("type", "unknown")
                start = format_datetime(sub.get("start_date", ""))
                end = format_datetime(sub.get("end_date", ""))
                print(f"  {i}. Тип: {sub_type}, Начало: {start}, Конец: {end}")
        else:
            print(f"💫 Подписки: Нет")
        print()
        
        # Рефералы
        referrals = user.get("referrals", [])
        referrer_id = user.get("referrer_id")
        print(f"👥 Рефералы:")
        print(f"  Приглашен кем:   {referrer_id if referrer_id else 'Нет'}")
        print(f"  Кол-во реферал.: {len(referrals)}")
        if referrals:
            print(f"  ID рефералов:    {', '.join(map(str, referrals[:5]))}")
            if len(referrals) > 5:
                print(f"                   ... и еще {len(referrals) - 5}")
        print()
        
        # Последнее взаимодействие
        last_q = user.get("last_question", "")
        if last_q:
            print(f"💬 Последний вопр.: {last_q[:80]}...")
        
    except Exception as e:
        print(f"❌ Ошибка загрузки пользователя: {e}")


def show_stats():
    """Показать общую статистику"""
    print(f"\n{'='*60}")
    print(f"📊 Статистика Tarot Bot")
    print(f"{'='*60}\n")
    
    try:
        # Загрузка всех пользователей
        if not os.path.exists(DB_PATH):
            print("❌ Файл базы данных не найден")
            return
        
        with open(DB_PATH, "r", encoding="utf-8") as f:
            db = json.load(f)
        
        total_users = len(db)
        users_with_subs = sum(1 for u in db.values() if u.get("subscriptions"))
        total_purchased = sum(u.get("purchased_layouts", 0) for u in db.values())
        users_with_referrals = sum(1 for u in db.values() if u.get("referrals"))
        total_referrals = sum(len(u.get("referrals", [])) for u in db.values())
        
        print(f"👥 Пользователи:")
        print(f"  Всего пользователей:    {total_users}")
        print(f"  Польз. с подпиской:     {users_with_subs}")
        print(f"  Польз. с рефералами:    {users_with_referrals}")
        print()
        print(f"🎴 Расклады:")
        print(f"  Всего куплено:          {total_purchased}")
        print(f"  В среднем на польз.:    {total_purchased / total_users if total_users > 0 else 0:.2f}")
        print()
        print(f"🔗 Рефералы:")
        print(f"  Всего рефералов:        {total_referrals}")
        print(f"  В среднем на польз.:    {total_referrals / total_users if total_users > 0 else 0:.2f}")
        print()
        
    except Exception as e:
        print(f"❌ Ошибка загрузки статистики: {e}")


def list_users(limit: int = 20):
    """Список всех пользователей"""
    print(f"\n{'='*60}")
    print(f"👥 Список пользователей (лимит: {limit})")
    print(f"{'='*60}\n")
    
    try:
        if not os.path.exists(DB_PATH):
            print("❌ Файл базы данных не найден")
            return
        
        with open(DB_PATH, "r", encoding="utf-8") as f:
            db = json.load(f)
        
        # Сортировка по дате обновления
        users = sorted(db.items(), key=lambda x: x[1].get("updated_at", ""), reverse=True)
        
        for i, (uid, user) in enumerate(users[:limit], 1):
            username = user.get("username", "Н/Д")
            first_name = user.get("first_name", "Н/Д")
            purchased = user.get("purchased_layouts", 0)
            has_sub = "✓" if user.get("subscriptions") else "✗"
            print(f"{i:3}. {uid:15} | @{username:15} | {first_name:20} | Раскл: {purchased:3} | Подп: {has_sub}")
        
        if len(users) > limit:
            print(f"\n... и еще {len(users) - limit} пользователей")
        
    except Exception as e:
        print(f"❌ Ошибка вывода списка пользователей: {e}")


def add_layouts(telegram_id: str, count: int):
    """Добавить купленные расклады пользователю (требуется подтверждение)"""
    print(f"\n{'='*60}")
    print(f"⚠️  Операция: Добавление раскладов")
    print(f"{'='*60}\n")
    
    print(f"ID пользователя: {telegram_id}")
    print(f"Добавить:        +{count} раскладов")
    print()
    
    confirm = input("Введите 'yes' для подтверждения: ")
    if confirm.lower() != 'yes':
        print("❌ Операция отменена")
        return
    
    try:
        from storage import load_user, save_user_merge
        import asyncio
        
        async def do_add():
            user = load_user(telegram_id)
            if not user.get("telegram_id"):
                print(f"❌ Пользователь не найден: {telegram_id}")
                return
            
            old_count = user.get("purchased_layouts", 0)
            new_count = old_count + count
            
            await save_user_merge(telegram_id, {"purchased_layouts": new_count})
            
            print(f"✅ Расклады обновлены: {old_count} → {new_count}")
        
        asyncio.run(do_add())
        
    except Exception as e:
        print(f"❌ Ошибка при добавлении раскладов: {e}")


def show_yookassa_events(limit: int = 10):
    """Показать последние события YooKassa"""
    print(f"\n{'='*60}")
    print(f"💳 События YooKassa (лимит: {limit})")
    print(f"{'='*60}\n")
    
    try:
        if not os.path.exists(PROCESSED_EVENTS_FILE):
            print("❌ Файл событий YooKassa не найден")
            return
        
        with open(PROCESSED_EVENTS_FILE, "r", encoding="utf-8") as f:
            events = json.load(f)
        
        if not events:
            print("Событий не найдено")
            return
        
        sorted_events = sorted(events.items(), reverse=True)[:limit]
        
        for i, (payment_id, processed) in enumerate(sorted_events, 1):
            status = "✓ Обработано" if processed else "✗ Не обработано"
            print(f"{i:3}. {payment_id:40} | {status}")
        
    except Exception as e:
        print(f"❌ Ошибка загрузки событий YooKassa: {e}")


async def show_support_tickets(status: Optional[str] = None):
    """Показать тикеты поддержки"""
    print(f"\n{'='*60}")
    print(f"🎫 Тикеты поддержки{f' (статус: {status})' if status else ''}")
    print(f"{'='*60}\n")
    
    try:
        if status:
            tickets = await list_support_tickets(status=status, limit=50)
        else:
            tickets = await list_support_tickets(limit=50)
        
        if not tickets:
            print("Тикетов не найдено")
            return
        
        for i, ticket in enumerate(tickets, 1):
            ticket_id = ticket.get("ticket_id", "Н/Д")
            t_status = ticket.get("status", "Н/Д")
            username = ticket.get("username", "Н/Д")
            subject = ticket.get("subject", "Н/Д")[:40]
            created = format_datetime(ticket.get("created_at", ""))
            
            status_emoji = {
                "new": "🆕",
                "open": "🟢",
                "awaiting_user": "🟡",
                "resolved": "✅",
                "closed": "🔒",
                "spam": "🚫"
            }.get(t_status, "⚪")
            
            print(f"{i:3}. {status_emoji} {ticket_id:20} | @{username:15} | {subject:40} | {created}")
        
    except Exception as e:
        print(f"❌ Ошибка загрузки тикетов поддержки: {e}")


def main():
    """Главная точка входа CLI"""
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    command = sys.argv[1].lower()
    
    if command == "user":
        if len(sys.argv) < 3:
            print("Использование: admin_cli.py user <telegram_id>")
            sys.exit(1)
        show_user(sys.argv[2])
    
    elif command == "stats":
        show_stats()
    
    elif command == "list-users":
        limit = 20
        if "--limit" in sys.argv:
            try:
                limit = int(sys.argv[sys.argv.index("--limit") + 1])
            except:
                pass
        list_users(limit)
    
    elif command == "add-layouts":
        if len(sys.argv) < 4:
            print("Использование: admin_cli.py add-layouts <telegram_id> <count>")
            sys.exit(1)
        try:
            telegram_id = sys.argv[2]
            count = int(sys.argv[3])
            add_layouts(telegram_id, count)
        except ValueError:
            print("❌ Кол-во должно быть целым числом")
            sys.exit(1)
    
    elif command == "yookassa-events":
        limit = 10
        if "--limit" in sys.argv:
            try:
                limit = int(sys.argv[sys.argv.index("--limit") + 1])
            except:
                pass
        show_yookassa_events(limit)
    
    elif command == "support-tickets":
        status = None
        if "--status" in sys.argv:
            try:
                status = sys.argv[sys.argv.index("--status") + 1]
            except:
                pass
        import asyncio
        asyncio.run(show_support_tickets(status))
    
    else:
        print(f"❌ Неизвестная команда: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
