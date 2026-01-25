#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт для массовой рассылки сообщений всем пользователям Tarot бота
Использование: python broadcast_message.py "Ваше сообщение"
"""

import os
import json
import asyncio
import sys
from dotenv import load_dotenv
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# Загрузка переменных окружения
load_dotenv()

API_TOKEN = os.getenv("BOT_TOKEN")
USER_DATA_FILE = "tarot_user_data.json"

if not API_TOKEN:
    print("❌ Ошибка: BOT_TOKEN не найден в .env файле")
    sys.exit(1)

def load_user_data():
    """Загружает данные пользователей из JSON файла"""
    if not os.path.exists(USER_DATA_FILE):
        print(f"❌ Файл {USER_DATA_FILE} не найден")
        return {}
    
    try:
        with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Ошибка чтения {USER_DATA_FILE}: {e}")
        return {}

async def broadcast_message(message_text: str):
    """Отправляет сообщение всем пользователям"""
    # Инициализация бота
    bot = Bot(
        token=API_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    
    # Загрузка пользователей
    user_data = load_user_data()
    if not user_data:
        print("❌ Нет данных пользователей")
        return
    
    total_users = len(user_data)
    print(f"📊 Найдено пользователей: {total_users}")
    print(f"📝 Сообщение: {message_text}")
    print("")
    
    # Подтверждение
    confirm = input(f"❓ Отправить сообщение {total_users} пользователям? (y/N): ")
    if confirm.lower() not in ['y', 'yes', 'да', 'д']:
        print("❌ Рассылка отменена")
        return
    
    print("🚀 Начинаем рассылку...")
    print("")
    
    sent_count = 0
    failed_count = 0
    
    for user_id_str in user_data.keys():
        try:
            user_id = int(user_id_str)
            await bot.send_message(chat_id=user_id, text=message_text)
            print(f"✅ Отправлено пользователю {user_id}")
            sent_count += 1
            
            # Небольшая пауза, чтобы не превысить лимиты Telegram
            await asyncio.sleep(0.1)
            
        except Exception as e:
            print(f"❌ Ошибка отправки пользователю {user_id_str}: {e}")
            failed_count += 1
    
    print("")
    print(f"📊 Итоги рассылки:")
    print(f"✅ Успешно отправлено: {sent_count}")
    print(f"❌ Ошибок: {failed_count}")
    print(f"📱 Общий охват: {sent_count}/{total_users} ({sent_count/total_users*100:.1f}%)")
    
    await bot.session.close()

def main():
    """Главная функция"""
    if len(sys.argv) < 2:
        print("❌ Использование: python broadcast_message.py \"Ваше сообщение\"")
        print("")
        print("Примеры:")
        print('python broadcast_message.py "🔮 Важное объявление для всех пользователей!"')
        print('python broadcast_message.py "⚡ Обновление бота завершено. Попробуйте новые функции!"')
        sys.exit(1)
    
    message_text = " ".join(sys.argv[1:])  # Объединяем все аргументы в одно сообщение
    
    # Запуск асинхронной функции
    asyncio.run(broadcast_message(message_text))

if __name__ == "__main__":
    main()