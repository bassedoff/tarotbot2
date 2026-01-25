#!/usr/bin/env python3
# check_user_data.py
"""
Скрипт для проверки данных пользователя в tarot_user_data.json
"""

import sys
import os
import json
from datetime import datetime

# Добавляем путь к проекту
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from subscription_manager import check_subscription_status

def check_user_data(user_id):
    """Проверка данных пользователя"""
    print(f"🔍 Проверка данных пользователя {user_id}")
    
    # Проверяем файл tarot_user_data.json
    data_file = "tarot_user_data.json"
    if not os.path.exists(data_file):
        print(f"❌ Файл {data_file} не найден")
        return
    
    try:
        with open(data_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"❌ Ошибка чтения файла {data_file}: {e}")
        return
    
    uid = str(user_id)
    if uid not in data:
        print(f"❌ Пользователь {uid} не найден в данных")
        return
    
    user_data = data[uid]
    print(f"✅ Пользователь {uid} найден")
    print(f"📊 Данные пользователя:")
    
    # Выводим основные поля
    fields_to_show = [
        "daily_count", "last_reset", "referrals", "layout_limit", 
        "layouts_used", "purchased_layouts", "subscriptions"
    ]
    
    for field in fields_to_show:
        if field in user_data:
            value = user_data[field]
            if field == "subscriptions" and isinstance(value, list):
                print(f"   {field}: {len(value)} подписок")
                for i, sub in enumerate(value):
                    print(f"     Подписка {i+1}: {sub}")
            else:
                print(f"   {field}: {value}")
    
    # Проверяем статус подписки через функцию
    print(f"\n🔍 Проверка статуса подписки через check_subscription_status:")
    subscription_status = check_subscription_status(uid)
    print(f"   Результат: {subscription_status}")

def main():
    """Основная функция"""
    if len(sys.argv) > 1:
        user_id = sys.argv[1]
    else:
        user_id = "360879418"  # ID пользователя по умолчанию
    
    print(f"📋 Проверка данных для пользователя: {user_id}\n")
    check_user_data(user_id)

if __name__ == "__main__":
    main()