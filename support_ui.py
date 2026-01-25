# support_ui.py
from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import Optional

def render_ticket_card(ticket: dict) -> str:
    """Рендерит карточку тикета для операторов"""
    ticket_id = ticket.get("ticket_id", "???")
    username = ticket.get("username", "unknown")
    first_name = ticket.get("first_name", "User")
    category = ticket.get("category", "other")
    status = ticket.get("status", "new")
    subject = ticket.get("subject", "")
    created_at = ticket.get("created_at", "")
    
    # Первое сообщение
    messages = ticket.get("messages", [])
    first_msg = messages[0] if messages else {}
    text = first_msg.get("text", "")
    
    card = f"""<b>🎫 Тикет {ticket_id}</b>

👤 <b>Пользователь:</b> {first_name} (@{username})
📂 <b>Категория:</b> {category}
🔖 <b>Тема:</b> {subject}
📊 <b>Статус:</b> {status}
🕒 <b>Создан:</b> {created_at[:16]}

💬 <b>Сообщение:</b>
{text[:500]}{"..." if len(text) > 500 else ""}
"""
    return card

def get_ticket_keyboard(ticket_id: str, operator_id: Optional[int] = None) -> types.InlineKeyboardMarkup:
    """Клавиатура для управления тикетом"""
    builder = InlineKeyboardBuilder()
    
    builder.button(text="✅ Взять", callback_data=f"sup:take:{ticket_id}")
    builder.button(text="💬 Ответить", callback_data=f"sup:reply:{ticket_id}")
    builder.adjust(2)
    
    builder.button(text="⏸️ Ожидание", callback_data=f"sup:await:{ticket_id}")
    builder.button(text="✅ Решён", callback_data=f"sup:resolve:{ticket_id}")
    builder.adjust(2)
    
    builder.button(text="📋 История", callback_data=f"sup:history:{ticket_id}")
    builder.button(text="📎 Вложения", callback_data=f"sup:attachments:{ticket_id}")
    builder.adjust(2)
    
    builder.button(text="👤 Профиль", callback_data=f"sup:profile:{ticket_id}")
    builder.button(text="🚫 Закрыть", callback_data=f"sup:close:{ticket_id}")
    builder.adjust(2)
    
    return builder.as_markup()

def get_reply_keyboard(ticket_id: str) -> types.InlineKeyboardMarkup:
    """Клавиатура для ответа"""
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data=f"sup:cancel_reply:{ticket_id}")
    return builder.as_markup()

def get_category_keyboard() -> types.InlineKeyboardMarkup:
    """Клавиатура выбора категории"""
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Платежи", callback_data="sup_cat:payments")
    builder.button(text="⭐ Подписка", callback_data="sup_cat:subscription")
    builder.button(text="🔧 Техническая", callback_data="sup_cat:tech")
    builder.button(text="⚠️ Жалоба", callback_data="sup_cat:abuse")
    builder.button(text="❓ Другое", callback_data="sup_cat:other")
    builder.adjust(2)
    return builder.as_markup()

def get_confirm_keyboard() -> types.InlineKeyboardMarkup:
    """Клавиатура подтверждения создания тикета"""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Отправить", callback_data="sup_confirm:yes")
    builder.button(text="❌ Отмена", callback_data="sup_confirm:no")
    builder.adjust(2)
    return builder.as_markup()

def render_user_profile(user: dict) -> str:
    """Рендерит профиль пользователя для операторов"""
    telegram_id = user.get("telegram_id", 0)
    username = user.get("username", "unknown")
    first_name = user.get("first_name", "User")
    first_seen = user.get("first_seen_at", "")[:16]
    
    # Подписки
    subscriptions = user.get("subscriptions", [])
    sub_text = "Нет" if not subscriptions else f"Есть ({len(subscriptions)})"
    
    # Расклады
    purchased = user.get("purchased_layouts", 0)
    
    # Рефералы
    referrals = user.get("referrals", [])
    ref_count = len(referrals)
    
    # Последний вопрос
    last_q = user.get("last_question", "")
    if last_q:
        last_q = last_q[:100] + ("..." if len(last_q) > 100 else "")
    else:
        last_q = "—"
    
    profile = f"""<b>👤 Профиль пользователя</b>

🆔 ID: <code>{telegram_id}</code>
👤 Имя: {first_name} (@{username})
📅 Первый визит: {first_seen}

💳 <b>Платёжная информация:</b>
• Подписка: {sub_text}
• Куплено раскладов: {purchased}

👥 <b>Рефералы:</b> {ref_count}

🔮 <b>Последний вопрос:</b>
{last_q}
"""
    return profile

def get_admin_panel_keyboard() -> types.InlineKeyboardMarkup:
    """Клавиатура фильтров для админ-панели"""
    builder = InlineKeyboardBuilder()
    
    builder.button(text="🆕 Новые", callback_data="filter:new")
    builder.button(text="🟢 Открытые", callback_data="filter:open")
    builder.adjust(2)
    
    builder.button(text="🟡 Ожидает юзера", callback_data="filter:awaiting_user")
    builder.button(text="✅ Решенные", callback_data="filter:resolved")
    builder.adjust(2)
    
    builder.button(text="🗒 Неназначенные", callback_data="filter:unassigned")
    builder.button(text="📊 Статистика", callback_data="admin:stats")
    builder.adjust(2)
    
    return builder.as_markup()

def render_ticket_history(ticket: dict) -> str:
    """Рендерит историю тикета"""
    ticket_id = ticket.get("ticket_id", "???")
    messages = ticket.get("messages", [])
    
    history = f"<b>📋 История тикета {ticket_id}</b>\n\n"
    
    for i, msg in enumerate(messages[-10:], 1):  # Last 10 messages
        from_role = msg.get("from", "unknown")
        text = msg.get("text", "")
        ts = msg.get("ts", "")[:16]
        files = msg.get("files", [])
        
        role_emoji = {
            "user": "👤",
            "agent": "👨\u200d💼",
            "system": "⚙️"
        }.get(from_role, "❓")
        
        history += f"{role_emoji} <b>{from_role.upper()}</b> ({ts}):\n"
        history += f"{text[:300]}{'...' if len(text) > 300 else ''}\n"
        
        if files:
            history += f"📎 Вложений: {len(files)}\n"
        
        history += "\n"
    
    if len(messages) > 10:
        history += f"... и ещё {len(messages) - 10} сообщений\n"
    
    return history
