#!/bin/bash
# deploy.sh - Скрипт автоматического развертывания для Tarot Bot
# Использование: ./scripts/deploy.sh [--no-restart]

set -e  # Остановить выполнение при ошибке

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_DIR="/var/lib/tarotbot"
VENV_DIR="$PROJECT_ROOT/venv"

echo "=========================================="
echo "🚀 Развертывание Tarot Bot"
echo "=========================================="
echo ""

# Проверка, запущен ли скрипт от имени пользователя tarotbot (в production)
if [ "$(whoami)" = "tarotbot" ]; then
    echo "✓ Запущено от имени пользователя tarotbot"
else
    echo "⚠️  Внимание: Запущено не от пользователя tarotbot (текущий: $(whoami))"
    echo "   Это нормально для разработки, но в production должен использоваться пользователь tarotbot"
fi

# Шаг 1: Git pull
echo ""
echo "📥 Шаг 1/5: Получение последнего кода..."
cd "$PROJECT_ROOT"
git fetch origin
git pull origin main || git pull origin master || echo "⚠️  Git pull не удался или это не git-репозиторий"

# Шаг 2: Бэкап данных (если в production)
if [ -d "$DATA_DIR" ]; then
    echo ""
    echo "💾 Шаг 2/5: Создание резервной копии..."
    "$SCRIPT_DIR/backup.sh"
else
    echo ""
    echo "⏩ Шаг 2/5: Пропуск бэкапа (каталог данных $DATA_DIR не найден)"
fi

# Шаг 3: Установка зависимостей Python
echo ""
echo "📦 Шаг 3/5: Установка зависимостей Python..."
if [ -d "$VENV_DIR" ]; then
    source "$VENV_DIR/bin/activate"
    pip install -q --upgrade pip
    pip install -q -r "$PROJECT_ROOT/requirements.txt"
    echo "✓ Зависимости Python установлены"
else
    echo "⚠️  Виртуальное окружение не найдено в $VENV_DIR"
    echo "   Создание нового venv..."
    python3 -m venv "$VENV_DIR"
    source "$VENV_DIR/bin/activate"
    pip install -q --upgrade pip
    pip install -q -r "$PROJECT_ROOT/requirements.txt"
    echo "✓ Виртуальное окружение создано, зависимости установлены"
fi

# Шаг 4: Установка зависимостей Node (если package.json существует и npm доступен)
echo ""
echo "📦 Шаг 4/5: Проверка зависимостей Node.js..."
if [ -f "$PROJECT_ROOT/package.json" ] && command -v npm &> /dev/null; then
    echo "   Установка зависимостей Node..."
    cd "$PROJECT_ROOT"
    npm ci --quiet || npm install --quiet
    echo "✓ Зависимости Node.js установлены"
else
    echo "⏩ Пропуск зависимостей Node.js (package.json не найден или npm недоступен)"
fi

# Шаг 5: Перезапуск сервисов (если не указан флаг --no-restart)
NO_RESTART=false
if [ "$1" = "--no-restart" ]; then
    NO_RESTART=true
fi

echo ""
echo "🔄 Шаг 5/5: Перезапуск сервисов..."
if [ "$NO_RESTART" = true ]; then
    echo "⏩ Пропуск перезапуска (флаг --no-restart)"
elif command -v systemctl &> /dev/null; then
    # Проверка существования сервисов
    if systemctl list-unit-files | grep -q "tarotbot.service"; then
        sudo systemctl restart tarotbot
        sudo systemctl restart tarot-support
        echo "✓ Сервисы перезапущены"
        sleep 2
        "$SCRIPT_DIR/status.sh"
    else
        echo "⚠️  Сервисы Systemd не найдены. Пропуск перезапуска."
        echo "   Запустите сервисы вручную: python main.py & python support.py"
    fi
else
    echo "⚠️  systemctl недоступен. Пропуск перезапуска."
    echo "   Запустите сервисы вручную: python main.py & python support.py"
fi

echo ""
echo "=========================================="
echo "✅ Развертывание успешно завершено!"
echo "=========================================="
echo ""
echo "Следующие шаги:"
echo "  - Проверить логи: ./scripts/logs.sh"
echo "  - Проверить статус: ./scripts/status.sh"
echo "  - Проверка здоровья: ./scripts/healthcheck.sh"
