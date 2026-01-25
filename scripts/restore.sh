#!/bin/bash
# restore.sh - Восстановление данных Tarot Bot из резервной копии
# Использование: ./scripts/restore.sh <файл_бэкапа>
# Пример: ./scripts/restore.sh /var/backups/tarotbot/backup_2026-01-25_12-00-00.tar.gz

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_DIR="/var/lib/tarotbot"
BACKUP_DIR="/var/backups/tarotbot"
LOCAL_BACKUP_DIR="$PROJECT_ROOT/backups"

echo "=========================================="
echo "♻️  Восстановление Tarot Bot"
echo "=========================================="
echo ""

# Проверка, указан ли файл бэкапа
if [ -z "$1" ]; then
    echo "❌ Ошибка: Не указан файл резервной копии"
    echo ""
    echo "Использование: $0 <файл_бэкапа>"
    echo ""
    echo "Доступные бэкапы:"
    
    if [ -d "$BACKUP_DIR" ]; then
        echo ""
        echo "Production бэкапы ($BACKUP_DIR):"
        ls -lh "$BACKUP_DIR"/backup_*.tar.gz 2>/dev/null | tail -10 || echo "  Бэкапы не найдены"
    fi
    
    if [ -d "$LOCAL_BACKUP_DIR" ]; then
        echo ""
        echo "Локальные бэкапы ($LOCAL_BACKUP_DIR):"
        ls -lh "$LOCAL_BACKUP_DIR"/backup_*.tar.gz 2>/dev/null | tail -10 || echo "  Бэкапы не найдены"
    fi
    
    echo ""
    exit 1
fi

BACKUP_FILE="$1"

# Проверка существования файла бэкапа
if [ ! -f "$BACKUP_FILE" ]; then
    echo "❌ Ошибка: Файл бэкапа не найден: $BACKUP_FILE"
    exit 1
fi

BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "📦 Файл бэкапа: $BACKUP_FILE"
echo "📊 Размер: $BACKUP_SIZE"
echo ""

# Определение целевого каталога
if [ -d "$DATA_DIR" ]; then
    TARGET_DIR="$DATA_DIR"
    echo "📂 Цель: $TARGET_DIR (production)"
else
    TARGET_DIR="$PROJECT_ROOT"
    echo "📂 Цель: $TARGET_DIR (разработка)"
fi

echo ""
echo "⚠️  ВНИМАНИЕ: Это действие перезапишет текущие файлы данных!"
echo ""
read -p "Вы уверены, что хотите продолжить? (yes/no): " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    echo "❌ Восстановление отменено"
    exit 0
fi

# Создание страховочного бэкапа текущего состояния
echo ""
echo "💾 Создание страховочного бэкапа текущего состояния..."
"$SCRIPT_DIR/backup.sh"

# Остановка сервисов, если они запущены
echo ""
echo "🛑 Остановка сервисов..."
if command -v systemctl &> /dev/null && systemctl list-unit-files | grep -q "tarotbot.service"; then
    sudo systemctl stop tarotbot || true
    sudo systemctl stop tarot-support || true
    echo "✓ Сервисы остановлены"
else
    echo "⏩ Сервисы Systemd не найдены"
fi

# Распаковка бэкапа
echo ""
echo "📦 Восстановление из архива..."
if [ -d "$DATA_DIR" ]; then
    # Восстановление в production
    sudo tar -xzf "$BACKUP_FILE" -C "$TARGET_DIR" || tar -xzf "$BACKUP_FILE" -C "$TARGET_DIR"
else
    # Восстановление при разработке
    tar -xzf "$BACKUP_FILE" -C "$TARGET_DIR"
fi

echo "✅ Файлы восстановлены"

# Перезапуск сервисов
echo ""
echo "🔄 Перезапуск сервисов..."
if command -v systemctl &> /dev/null && systemctl list-unit-files | grep -q "tarotbot.service"; then
    sudo systemctl start tarotbot
    sudo systemctl start tarot-support
    echo "✓ Сервисы запущены"
    sleep 2
    "$SCRIPT_DIR/status.sh"
else
    echo "⏩ Сервисы Systemd не найдены. Запустите их вручную, если требуется."
fi

echo ""
echo "=========================================="
echo "✅ Восстановление успешно завершено!"
echo "=========================================="
