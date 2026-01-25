#!/bin/bash
# healthcheck.sh - Проверка работоспособности сервисов Tarot Bot
# Использование: ./scripts/healthcheck.sh [--url URL]

# URL по умолчанию (измените на ваш домен в production)
HEALTH_URL="${HEALTH_URL:-http://5.129.196.58:5000/health}"

# Разбор аргументов
while [[ $# -gt 0 ]]; do
    case $1 in
        --url)
            HEALTH_URL="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

echo "=========================================="
echo "🏥 Проверка здоровья Tarot Bot"
echo "=========================================="
echo ""
echo "🌐 Проверка: $HEALTH_URL"
echo ""

# Проверка наличия curl
if ! command -v curl &> /dev/null; then
    echo "❌ curl не установлен"
    exit 1
fi

# Выполнение проверки
HTTP_CODE=$(curl -s -o /tmp/health_response.txt -w "%{http_code}" "$HEALTH_URL" 2>/dev/null)
RESPONSE=$(cat /tmp/health_response.txt 2>/dev/null)

echo "HTTP Статус: $HTTP_CODE"
echo ""

if [ "$HTTP_CODE" = "200" ]; then
    echo "✅ Проверка пройдна"
    echo ""
    echo "Ответ:"
    echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
    EXIT_CODE=0
else
    echo "❌ Проверка НЕ ПРОЙДЕНА"
    echo ""
    echo "Ответ:"
    echo "$RESPONSE"
    EXIT_CODE=1
fi

# Очистка
rm -f /tmp/health_response.txt

echo ""
echo "=========================================="

exit $EXIT_CODE
