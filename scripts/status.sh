#!/bin/bash
# status.sh - Проверка статуса сервисов Tarot Bot
# Использование: ./scripts/status.sh

echo "=========================================="
echo "📊 Статус сервисов Tarot Bot"
echo "=========================================="
echo ""

if command -v systemctl &> /dev/null; then
    if systemctl list-unit-files | grep -q "tarotbot.service"; then
        echo "🤖 Основной бот (tarotbot):"
        systemctl status tarotbot --no-pager -l || true
        echo ""
        echo "🆘 Бот поддержки (tarot-support):"
        systemctl status tarot-support --no-pager -l || true
    else
        echo "⚠️  Сервисы Systemd не установлены"
        echo ""
        echo "Проверка запущенных процессов..."
        if pgrep -f "python.*main.py" > /dev/null; then
            echo "✓ Основной бот запущен (PID: $(pgrep -f 'python.*main.py'))"
        else
            echo "✗ Основной бот НЕ запущен"
        fi
        
        if pgrep -f "python.*support.py" > /dev/null; then
            echo "✓ Бот поддержки запущен (PID: $(pgrep -f 'python.*support.py'))"
        else
            echo "✗ Бот поддержки НЕ запущен"
        fi
    fi
else
    echo "⚠️  systemctl недоступен"
    echo ""
    echo "Проверка запущенных процессов..."
    if pgrep -f "python.*main.py" > /dev/null; then
        echo "✓ Основной бот запущен (PID: $(pgrep -f 'python.*main.py'))"
    else
        echo "✗ Основной бот НЕ запущен"
    fi
    
    if pgrep -f "python.*support.py" > /dev/null; then
        echo "✓ Бот поддержки запущен (PID: $(pgrep -f 'python.*support.py'))"
    else
        echo "✗ Бот поддержки НЕ запущен"
    fi
fi

echo ""
echo "=========================================="
