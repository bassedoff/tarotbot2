# llm_client.py
"""
OpenAI Responses API client for Tarot interpretations.
Replaces OpenRouter with official OpenAI SDK.
"""

import os
import json
import logging
import re
from typing import Dict, Any, Optional
from openai import OpenAI, AsyncOpenAI
from httpx import Timeout

# === Configuration ===
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-thinking")  # Default GPT-5 model name
OPENAI_FALLBACK_MODEL = os.getenv("OPENAI_FALLBACK_MODEL", "gpt-4o")  # Fallback model
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")


def normalize_interpretation_three_paragraphs(text: str) -> str:
    """Normalize interpretation to exactly three paragraphs separated by empty lines."""
    # Разбить по пустым строкам (двойные переводы или \n\n)
    parts = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    if len(parts) == 3:
        return "\n\n".join(parts)
    # Попытка: разбить по картам-ключам (если в начале строк есть названия)
    # Иначе — безопасно порезать на 3 примерно равные части
    raw = text.strip()
    if not raw:
        return text
    # fallback: делим по предложениям
    sents = re.split(r"(?<=[.!?])\s+", raw)
    if len(sents) >= 3:
        third = max(1, len(sents)//3)
        p1 = " ".join(sents[:third])
        p2 = " ".join(sents[third:2*third])
        p3 = " ".join(sents[2*third:])
        return "\n\n".join([p1.strip(), p2.strip(), p3.strip()])
    return text

if not OPENAI_API_KEY:
    OPENAI_API_KEY = "sk-test-key-for-testing"  # Using test key for local testing
    logging.warning("OPENAI_API_KEY not configured. Using test key for local testing.")

# Initialize sync and async clients
client = OpenAI(
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_BASE_URL,
    timeout=Timeout(30.0)
)

aclient = AsyncOpenAI(
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_BASE_URL,
    timeout=Timeout(30.0)
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def _choose_model(pref: Optional[str] = None) -> str:
    """Select model: preferred, default OPENAI_MODEL, or fallback."""
    return pref or OPENAI_MODEL or OPENAI_FALLBACK_MODEL


TAROT_JSON_SCHEMA_V2 = {
    "type": "object",
    "additionalProperties": False,
    "required": ["interpretation", "summary", "disclaimer"],
    "properties": {
        "interpretation": {
            "type": "string",
            "description": "Три абзаца — по одной карте в каждом. Каждый абзац начинается с названия карты. Абзацы разделены одной пустой строкой."
        },
        "summary": {
            "type": "string",
            "description": "Синтез на 3–4 предложения. В интерфейсе показывается после «Интерпретации»."
        },
        "disclaimer": {
            "type": "string",
            "description": "Короткое напоминание о природе Таро (1–2 предложения)."
        }
    }
}


SYSTEM_PROMPT_ANALYTIC_V2 = (
    "Ты — опытный таролог и бережный собеседник. Отвечай по-русски.\n\n"
    "ЦЕЛЬ: дать глубокую, логичную и эмпатичную интерпретацию расклада строго в привязке к ВОПРОСУ пользователя и к ВЫПАВШИМ КАРТАМ.\n\n"
    "СТИЛЬ И ТОН:\n"
    "• Пиши живо, но без пафоса; уважительно, без фатализма и категоричных предсказаний.\n"
    "• Формулируй аккуратно: «скорее всего», «похоже», «видится так, что…».\n"
    "• Аналитика важнее общих фраз: карта → тезис → что это значит именно для этого вопроса/ситуации.\n\n"
    "СТРУКТУРА ВЫВОДА (строго в JSON по схеме):\n"
    "1) \"interpretation\" — три отдельные абзаца (по одной карте в каждом). Каждый абзац начинается с названия карты, затем разбор смысла карты в контексте вопроса пользователя. Абзацы разделяй одной пустой строкой.\n"
    "2) \"summary\" — общий синтез (3–4 предложения). В приложении этот блок показывается ПОСЛЕ «Интерпретации».\n"
    "3) \"disclaimer\" — короткое напоминание о природе Таро (1–2 предложения).\n\n"
    "ТРЕБОВАНИЯ:\n"
    "• Используй только валидный JSON (без комментариев и префиксов), строго с полями interpretation, summary, disclaimer.\n"
    "• Ссылайся на конфликтующие/поддерживающие смыслы карт и как они складываются в общую картину.\n"
    "• Не давай прямых «советов»/«рекомендаций» — этот раздел исключён."
)


def build_user_prompt_v2(question: str, cards_ru: list[str], spread_name: str) -> list[dict]:
    cards_joined = ", ".join(cards_ru)  # пример: "Рыцарь Кубков, Восьмёрка Жезлов, Семёрка Мечей"
    user_text = (
        "Контекст:\n"
        f"• Вопрос пользователя: \"{question}\"\n"
        f"• Расклад: {spread_name}\n"
        f"• Выпавшие карты (слева направо): {cards_joined}\n\n"
        "Задача:\n"
        "Сформируй JSON со следующими полями:\n"
        "- \"interpretation\": три абзаца (по одной карте в абзаце), каждый абзац начинается с названия карты и раскрывает её значение строго к вопросу пользователя. Абзацы разделены одной пустой строкой.\n"
        "- \"summary\": общий вывод (3–4 предложения), синтез карт и вопроса.\n"
        "- \"disclaimer\": короткий дисклеймер о природе Таро.\n\n"
        "Важно:\n"
        "• Только валидный JSON, без кода и без пояснений вне JSON."
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT_ANALYTIC_V2},
        {"role": "user", "content": user_text},
    ]


def call_tarot_model(
    question: str,
    cards_ru: list[str],
    spread_name: str = "3-card spread",
    temperature: float = 0.8,
    max_output_tokens: int = 1100,
    prefer_model: Optional[str] = None
) -> Dict[str, Any]:
    """
    Call tarot interpretation model via OpenAI Chat Completions API with JSON mode.
    Returns structured JSON response with fallback on error.
    """
    model = _choose_model(prefer_model)
    
    try:
        logger.info(f"🤖 Calling OpenAI Chat Completions API with model: {model}")
        
        resp = client.chat.completions.create(
            model=model,
            messages=build_user_prompt_v2(question, cards_ru, spread_name),
            response_format={"type": "json_object"},
            temperature=temperature,
            max_tokens=max_output_tokens,
        )
        
        logger.info(f"✅ Response received from {model}")
        
    except Exception as e:
        logger.warning(f"⚠️ Primary model {model} failed: {e}")
        logger.info(f"🔄 Falling back to {OPENAI_FALLBACK_MODEL}")
        
        try:
            resp = client.chat.completions.create(
                model=OPENAI_FALLBACK_MODEL,
                messages=build_user_prompt_v2(question, cards_ru, spread_name),
                response_format={"type": "json_object"},
                temperature=temperature,
                max_tokens=max_output_tokens,
            )
            logger.info(f"✅ Fallback response received from {OPENAI_FALLBACK_MODEL}")
        except Exception as fallback_error:
            logger.error(f"❌ Both primary and fallback models failed: {fallback_error}")
            raise

    # Parse structured output
    try:
        content = resp.choices[0].message.content if resp.choices else ""
        data = json.loads(content)
        logger.info("✅ Successfully parsed JSON response")
    except (json.JSONDecodeError, IndexError, AttributeError) as json_error:
        logger.warning(f"⚠️ Failed to parse JSON: {json_error}")
        content = resp.choices[0].message.content if resp.choices else ""
        data = {
            "summary": "",
            "interpretation": content,
            "disclaimer": ""
        }

    # Extract usage info
    usage = resp.usage if hasattr(resp, "usage") else None
    tokens = {
        "input": usage.prompt_tokens if usage else None,
        "output": usage.completion_tokens if usage else None,
        "total": usage.total_tokens if usage else None,
    }
    data["_usage"] = tokens
    
    logger.info(f"📊 Token usage: {tokens}")
    return data


async def acall_tarot_model(
    question: str,
    cards_ru: list[str],
    spread_name: str = "3-card spread",
    temperature: float = 0.8,
    max_output_tokens: int = 1100,
    prefer_model: Optional[str] = None
) -> Dict[str, Any]:
    """
    Async version of call_tarot_model.
    Currently uses sync client wrapped in async context.
    TODO: Replace with full async implementation using AsyncOpenAI client.
    """
    # For now, wrap sync call in async context
    # Could be replaced with full async implementation if needed
    return call_tarot_model(
        question=question,
        cards_ru=cards_ru,
        spread_name=spread_name,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        prefer_model=prefer_model
    )


# === Simple chat completions (for general AI chat) ===
def call_general_chat(
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 500,
    prefer_model: Optional[str] = None
) -> str:
    """
    Simple chat completions call for general AI chat.
    Falls back to standard chat.completions if needed.
    """
    model = _choose_model(prefer_model)
    
    try:
        logger.info(f"🤖 Chat completions call with model: {model}")
        
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        
        answer = resp.choices[0].message.content.strip() if resp.choices else ""
        logger.info(f"✅ Chat response received: {len(answer)} chars")
        return answer
        
    except Exception as e:
        logger.warning(f"⚠️ Primary model {model} failed: {e}")
        logger.info(f"🔄 Falling back to {OPENAI_FALLBACK_MODEL}")
        
        try:
            resp = client.chat.completions.create(
                model=OPENAI_FALLBACK_MODEL,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            
            answer = resp.choices[0].message.content.strip() if resp.choices else ""
            logger.info(f"✅ Fallback chat response received: {len(answer)} chars")
            return answer
            
        except Exception as fallback_error:
            logger.error(f"❌ Both models failed: {fallback_error}")
            raise


async def acall_general_chat(
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 500,
    prefer_model: Optional[str] = None
) -> str:
    """Async version of general chat call."""
    return call_general_chat(
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        prefer_model=prefer_model
    )
