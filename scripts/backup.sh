#!/bin/bash
# backup.sh - Создание резервной копии данных Tarot Bot
# Использование: ./scripts/backup.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_DIR="/var/lib/tarotbot"
BACKUP_DIR="/var/backups/tarotbot"
LOCAL_BACKUP_DIR="$PROJECT_ROOT/backups"
TIMESTAMP=$(date +%Y-%m-%d_%H-%M-%S)

echo "=========================================="
echo "💾 Резервное копирование Tarot Bot"
echo "=========================================="
echo ""

# Определение источника данных
if [ -d "$DATA_DIR" ]; then
    SOURCE_DIR="$DATA_DIR"
    TARGET_DIR="$BACKUP_DIR"
    echo "📂 Источник: $SOURCE_DIR (production)"
    
    # Создание каталога бэкапа, если он не существует
    sudo mkdir -p "$TARGET_DIR" || mkdir -p "$TARGET_DIR"
else
    SOURCE_DIR="$PROJECT_ROOT"
    TARGET_DIR="$LOCAL_BACKUP_DIR"
    echo "📂 Источник: $SOURCE_DIR (разработка)"
    
    # Создание локального каталога бэкапа
    mkdir -p "$TARGET_DIR"
fi

BACKUP_FILE="$TARGET_DIR/backup_$TIMESTAMP.tar.gz"

echo "📦 Создание архива..."

# Создание tar-архива файлов данных
if [ -d "$DATA_DIR" ]; then
    # Бэкап в production
    sudo tar -czf "$BACKUP_FILE" \
        -C "$DATA_DIR" \
        tarot_user_data.json \
        support_tickets.json \
        yookassa_events.json \
        2>/dev/null || \
    tar -czf "$BACKUP_FILE" \
        -C "$DATA_DIR" \
        tarot_user_data.json \
        support_tickets.json \
        yookassa_events.json \
        2>/dev/null || echo "⚠️  Некоторые файлы могут отсутствовать"
else
    # Бэкап при разработке
    tar -czf "$BACKUP_FILE" \
        -C "$PROJECT_ROOT" \
        --exclude='venv' \
        --exclude='node_modules' \
        --exclude='__pycache__' \
        --exclude='.git' \
        tarot_user_data.json \
        support_tickets.json \
        yookassa_events.json \
        2>/dev/null || echo "⚠️  Некоторые файлы могут отсутствовать"
fi

BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)

echo "✅ Бэкап создан: $BACKUP_FILE"
echo "📊 Размер: $BACKUP_SIZE"
echo ""

# Очистка старых бэкапов (хранить последние 30)
echo "🧹 Очистка старых бэкапов (хранение последних 30)..."
if [ -d "$TARGET_DIR" ]; then
    ls -t "$TARGET_DIR"/backup_*.tar.gz 2>/dev/null | tail -n +31 | xargs -r rm -f
    BACKUP_COUNT=$(ls "$TARGET_DIR"/backup_*.tar.gz 2>/dev/null | wc -l)
    echo "✓ Всего бэкапов: $BACKUP_COUNT"
fi

echo ""
echo "=========================================="
echo "✅ Резервное копирование успешно завершено!"
echo "=========================================="
