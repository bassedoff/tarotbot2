# payment_handler.py
"""
Модуль для обработки уведомлений о платежах от ЮКасса с идемпотентностью
"""

import logging
import json
import os
from datetime import datetime, timedelta, timezone

PROCESSED_EVENTS_FILE = os.getenv("YOOKASSA_EVENTS_PATH", "./yookassa_events.json")

def _load_processed_events() -> dict:
    """Загружает обработанные payment_id из файла"""
    if not os.path.exists(PROCESSED_EVENTS_FILE):
        return {}
    try:
        with open(PROCESSED_EVENTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except:
        return {}

def _save_processed_events(events: dict):
    """Сохраняет обработанные payment_id в файл"""
    try:
        with open(PROCESSED_EVENTS_FILE, "w", encoding="utf-8") as f:
            json.dump(events, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logging.error(f"Ошибка сохранения processed_events: {e}")

processed_event_ids = _load_processed_events()

async def handle_yookassa_webhook(data: dict, bot=None) -> bool:
    """
    Обработка webhook от YooKassa.
    Начисляет только по payment.succeeded, идемпотентно.
    """
    try:
        # DEBUG: запись в webhook.log
        log_dir = "logs"
        os.makedirs(log_dir, exist_ok=True)
        webhook_log_path = os.path.join(log_dir, "webhook.log")
        ts = datetime.now(timezone.utc).isoformat()
        
        # Логируем полные данные webhook
        with open(webhook_log_path, "a", encoding="utf-8") as wf:
            raw_str = json.dumps(data, ensure_ascii=False, indent=2)
            wf.write(f"\n{'='*80}\n")
            wf.write(f"{ts} | НОВЫЙ WEBHOOK:\n")
            wf.write(f"{raw_str}\n")
        
        logging.info(f"[YooKassa] Webhook: event={data.get('event')}, type={data.get('type')}")
        
        event_type = data.get('event')
        if event_type not in ['payment.succeeded', 'notification']:
            # Для событий, которые не требуют обработки, все равно возвращаем True (200 OK)
            # как того требует YooKassa
            with open(webhook_log_path, "a", encoding="utf-8") as wf:
                wf.write(f"{ts} | SKIP: event={event_type} (не payment.succeeded)\n")
            logging.info(f"[YooKassa] Пропуск события: {event_type}")
            return True  # Возвращаем True, чтобы YooKassa получила 200 OK

        payment_object = data.get('object') or data
        payment_id = payment_object.get('id')
        status = payment_object.get('status')

        if status != 'succeeded':
            return False

        # Идемпотентность
        if payment_id in processed_event_ids:
            logging.info(f"[YooKassa] Платеж {payment_id} уже обработан")
            return True

        metadata = payment_object.get('metadata', {})
        user_id = metadata.get('user_id')
        product = metadata.get('payment_type')

        if not user_id or not product:
            logging.error(f"[YooKassa] Отсутствуют metadata в платеже {payment_id}")
            with open(webhook_log_path, "a", encoding="utf-8") as wf:
                wf.write(f"{ts} | SKIP: no metadata user_id={user_id}, product={product}, payment_id={payment_id}\n")
            return True  # Возвращаем True, чтобы YooKassa получила 200 OK

        logging.debug(f"[YooKassa] Обработка платежа {payment_id}: user_id={user_id}, product={product}")

        from storage import load_user, save_user_merge, iso_now, today_str

        uid = str(user_id)

        if product == "single":
            # Разовый расклад
            u = load_user(uid)
            old_pl = u.get("purchased_layouts", 0)
            patch = {"purchased_layouts": old_pl + 1}
            await save_user_merge(uid, patch)
            logging.info(f"[YooKassa] Добавлен 1 разовый расклад для {uid}")
            
            # Отправляем уведомление
            if bot:
                try:
                    import asyncio
                    await asyncio.sleep(0.5)
                    new_u = load_user(uid)
                    available = new_u.get("purchased_layouts", 0)
                    text = (
                        "🎉 <b>Поздравляем!</b>\n"
                        "Вы приобрели 1 расклад.\n"
                        f"Доступно: <b>{available}</b>"
                    )
                    await bot.send_message(chat_id=int(user_id), text=text, parse_mode="HTML")
                    logging.debug(f"[YooKassa] Отправлено уведомление пользователю {user_id}")
                except Exception as e:
                    logging.error(f"[YooKassa] Ошибка отправки уведомления: {e}")

        elif product == "month":
            # Месячная подписка
            start = datetime.now(timezone.utc)
            end = start + timedelta(days=30)
            u = load_user(uid)
            subs = u.get("subscriptions", [])
            subs.append({"type": "month", "start_date": start.isoformat(), "end_date": end.isoformat()})
            patch = {
                "subscriptions": subs,
                "daily_count": 0,
                "last_reset": today_str()
            }
            await save_user_merge(uid, patch)
            logging.info(f"[YooKassa] Добавлена месячная подписка для {uid}")
            
            # Отправляем уведомление
            if bot:
                try:
                    import asyncio
                    await asyncio.sleep(0.5)
                    end_str = end.strftime("%d.%m.%Y %H:%M")
                    text = (
                        "🎉 <b>Поздравляем!</b>\n"
                        "Подписка активирована до: "
                        f"<b>{end_str}</b>"
                    )
                    await bot.send_message(chat_id=int(user_id), text=text, parse_mode="HTML")
                    logging.debug(f"[YooKassa] Отправлено уведомление пользователю {user_id}")
                except Exception as e:
                    logging.error(f"[YooKassa] Ошибка отправки уведомления: {e}")

        # Отмечаем как обработанный
        processed_event_ids[payment_id] = True
        _save_processed_events(processed_event_ids)

        return True

    except Exception as e:
        logging.error(f"[YooKassa] Ошибка обработки webhook: {e}", exc_info=True)
        try:
            log_dir = "logs"
            webhook_log_path = os.path.join(log_dir, "webhook.log")
            ts = datetime.now(timezone.utc).isoformat()
            with open(webhook_log_path, "a", encoding="utf-8") as wf:
                wf.write(f"{ts} | EXCEPTION: {str(e)}\n")
        except:
            pass
        # ВСЕГДА возвращаем True, чтобы YooKassa получила 200 OK
        return True
