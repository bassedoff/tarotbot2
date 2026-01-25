#!/bin/bash
# logs.sh - Просмотр логов сервисов Tarot Bot
# Использование: ./scripts/logs.sh [--lines N]

LINES=50

# Разбор аргументов
while [[ $# -gt 0 ]]; do
    case $1 in
        --lines|-n)
            LINES="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

echo "=========================================="
echo "📋 Логи Tarot Bot (последние $LINES строк)"
echo "=========================================="
echo ""
echo "Нажмите Ctrl+C для выхода"
echo ""

if command -v journalctl &> /dev/null; then
    if systemctl list-unit-files | grep -q "tarotbot.service"; then
        # Следовать за логами в журнале systemd
        journalctl -u tarotbot -u tarot-support -f -n "$LINES"
    else
        echo "⚠️  Сервисы Systemd не найдены. Показ логов из файлов..."
        tail -n "$LINES" -f logs/*.log 2>/dev/null || echo "Файлы логов в каталоге logs/ не найдены"
    fi
else
    echo "⚠️  journalctl недоступен. Показ логов из файлов..."
    tail -n "$LINES" -f logs/*.log 2>/dev/null || echo "Файлы логов в каталоге logs/ не найдены"
fi
