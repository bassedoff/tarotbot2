# Руководство по ежедневному администрированию

Практическое руководство по администрированию и обслуживанию Tarot Bot в рабочей среде.

## Содержание

1. [Краткая справка](#краткая-справка)
2. [Общие задачи](#общие-задачи)
3. [Управление данными](#управление-данными)
4. [Мониторинг](#мониторинг)
5. [Устранение неполадок](#устранение-неполадок)
6. [Админ-интерфейс (CLI)](#админ-интерфейс-cli)

---

## Краткая справка

### Основные команды

```bash
# Управление сервисами
./scripts/status.sh          # Проверить статус сервисов
./scripts/logs.sh            # Просмотр логов (в реальном времени)
./scripts/deploy.sh          # Развертывание обновлений
./scripts/healthcheck.sh     # Проверка работоспособности

# Управление данными
./scripts/backup.sh          # Создать резервную копию
./scripts/restore.sh <файл>  # Восстановить из копии

# Админ-интерфейс (CLI)
python admin_cli.py stats    # Показать статистику
python admin_cli.py user <id> # Показать детали пользователя
```

### Расположение файлов

```
/opt/tarotbot/              → Код (приложение)
/var/lib/tarotbot/          → Данные (постоянные JSON-файлы)
/etc/tarotbot/              → Конфигурация (.env файлы)
/var/backups/tarotbot/      → Резервные копии
/var/log/nginx/             → Логи Nginx
```

---

## Общие задачи

### Развертывание обновлений кода

```bash
# Подключитесь к серверу по SSH
ssh tarotbot@yourserver.com

# Переключитесь на пользователя tarotbot (если еще не сделали этого)
sudo su - tarotbot

# Перейдите в каталог проекта
cd /opt/tarotbot

# Запустите скрипт развертывания (стягивает код, ставит зависимости, перезапускает сервисы)
./scripts/deploy.sh
```

**Что делает скрипт:**
1. Стягивает последний код из git
2. Создает автоматическую резервную копию
3. Устанавливает зависимости Python
4. Устанавливает зависимости Node.js (если нужно)
5. Перезапускает оба сервиса
6. Показывает статус

### Перезапуск сервисов

```bash
# Перезапустить оба сервиса
sudo systemctl restart tarotbot
sudo systemctl restart tarot-support

# Или по отдельности
sudo systemctl restart tarotbot         # Только основной бот
sudo systemctl restart tarot-support   # Только бот поддержки

# Проверить успешность перезапуска
./scripts/status.sh
```

### Просмотр логов

```bash
# Просмотр в реальном времени (tail -f)
./scripts/logs.sh

# Последние 100 строк
./scripts/logs.sh --lines 100

# Только основной бот
sudo journalctl -u tarotbot -f

# Только бот поддержки
sudo journalctl -u tarot-support -f

# Поиск ошибок
sudo journalctl -u tarotbot -u tarot-support | grep -i error
```

### Проверка статуса сервисов

```bash
# Быстрая проверка
./scripts/status.sh

# Или напрямую через systemctl
sudo systemctl status tarotbot
sudo systemctl status tarot-support

# Проверить, включен ли автозапуск
sudo systemctl is-enabled tarotbot
sudo systemctl is-enabled tarot-support
```

---

## Управление данными

### Понимание файлов данных

Бот использует JSON-файлы для постоянного хранения:

| Файл | Расположение | Назначение |
|------|--------------|------------|
| `tarot_user_data.json` | `/var/lib/tarotbot/` | Данные пользователей, подписки, расклады |
| `support_tickets.json` | `/var/lib/tarotbot/` | Тикеты поддержки и сообщения |
| `yookassa_events.json` | `/var/lib/tarotbot/` | Дедупликация событий оплаты |

**ВАЖНО**: Никогда не редактируйте эти файлы напрямую, пока запущены сервисы!

### Безопасное редактирование через VSCode SSH

1. **Подключитесь к серверу через VSCode Remote-SSH:**
   - Установите расширение "Remote - SSH" в VSCode
   - Нажмите F1 → "Remote-SSH: Connect to Host"
   - Введите: `tarotbot@yourserver.com`

2. **Откройте папку с данными:**
   - File → Open Folder
   - Перейдите в `/var/lib/tarotbot/`

3. **Редактируйте JSON-файлы:**
   - Сначала остановите сервисы: `sudo systemctl stop tarotbot tarot-support`
   - Отредактируйте файлы в VSCode
   - Проверьте синтаксис JSON (VSCode подсветит ошибки)
   - Сохраните файлы
   - Запустите сервисы: `sudo systemctl start tarotbot tarot-support`

### Резервное копирование

```bash
# Создать копию вручную
./scripts/backup.sh

# Копии хранятся в:
# - На сервере: /var/backups/tarotbot/
# - Локально (dev): ./backups/

# Список последних копий
ls -lht /var/backups/tarotbot/ | head -10
```

**График автоматического копирования:**
- Копии создаются автоматически при каждом запуске `deploy.sh`
- Старые копии хранятся 30 дней (сохраняются 30 последних)
- Путь: `/var/backups/tarotbot/backup_YYYY-MM-DD_HH-MM-SS.tar.gz`

### Восстановление данных

```bash
# Список доступных копий
./scripts/restore.sh

# Восстановление из конкретного файла
./scripts/restore.sh /var/backups/tarotbot/backup_2026-01-25_12-00-00.tar.gz
```

**Предупреждение**: Восстановление выполнит следующее:
1. Создаст страховочную копию текущего состояния
2. Остановит оба сервиса
3. Перезапишет текущие файлы данных
4. Запустит сервисы

### Экспорт данных для анализа

```bash
# Скопировать данные на локальный компьютер (из локального терминала)
scp tarotbot@yourserver.com:/var/lib/tarotbot/tarot_user_data.json ./local_backup.json

# Или используйте встроенную функцию VSCode:
# Правой кнопкой на файл → Download
```

---

## Мониторинг

### Проверка работоспособности (Health Checks)

```bash
# Запустить проверку
./scripts/healthcheck.sh

# Или указать URL
./scripts/healthcheck.sh --url https://yourdomain.com/health

# Ожидаемый ответ:
# HTTP Status: 200
# {
#   "status": "ok",
#   "timestamp": "2026-01-25T12:00:00Z"
# }
```

### Проверка места на диске

```bash
# Размер папки с данными
du -sh /var/lib/tarotbot/

# Размер папки с бэкапами
du -sh /var/backups/tarotbot/

# Доступное место на диске
df -h /var/lib/tarotbot/
```

### Мониторинг ресурсов

```bash
# Проверить использование памяти
free -h

# Память, используемая сервисом
systemctl status tarotbot | grep Memory
systemctl status tarot-support | grep Memory

# Использование процессора
top -p $(pgrep -f "python.*main.py"),$(pgrep -f "python.*support.py")
```

---

## Устранение неполадок

### Бот не отвечает

**1. Проверьте, запущены ли сервисы:**
```bash
./scripts/status.sh
```

**2. Проверьте логи на наличие ошибок:**
```bash
./scripts/logs.sh --lines 50 | grep -i error
```

**3. Перезапустите сервисы:**
```bash
sudo systemctl restart tarotbot tarot-support
```

**4. Проверьте эндпоинт здоровья:**
```bash
./scripts/healthcheck.sh
```

### Проблемы с вебхуками

**1. Проверьте работу Nginx:**
```bash
sudo systemctl status nginx
```

**2. Проверьте логи Nginx:**
```bash
sudo tail -f /var/log/nginx/tarotbot_error.log
```

**3. Протестируйте эндпоинт вебхука:**
```bash
curl -X POST https://yourdomain.com/webhook/yookassa \
  -H "Content-Type: application/json" \
  -d '{"event":"payment.succeeded","object":{"id":"test"}}'
```

**4. Проверьте логи бота на предмет обработки вебхуков:**
```bash
sudo journalctl -u tarotbot | grep -i yookassa
```

### Данные не сохраняются

**1. Проверьте настройку SAFE_STORAGE_WRITE:**
```bash
grep SAFE_STORAGE_WRITE /etc/tarotbot/main.env
# Должно быть: SAFE_STORAGE_WRITE=1
```

**2. Проверьте права доступа к файлам:**
```bash
ls -la /var/lib/tarotbot/
# Файлы должны принадлежать пользователю tarotbot:tarotbot
```

**3. Проверьте место на диске:**
```bash
df -h /var/lib/tarotbot/
```

**4. Проверьте на наличие блокировок файлов:**
```bash
lsof /var/lib/tarotbot/*.json
```

### Сервис постоянно падает

**1. Проверьте последние логи:**
```bash
sudo journalctl -u tarotbot -u tarot-support -n 100 --no-pager
```

**2. Проверьте окружение Python:**
```bash
sudo su - tarotbot
source /opt/tarotbot/venv/bin/activate
python -c "import aiogram; print(aiogram.__version__)"
```

**3. Проверьте переменные окружения:**
```bash
# Просмотр окружения (без секретов)
sudo systemctl show tarotbot --property=Environment
```

**4. Запустите бота вручную для отладки:**
```bash
sudo su - tarotbot
cd /opt/tarotbot
source venv/bin/activate
python main.py
# Нажмите Ctrl+C для остановки
```

---

## Админ-интерфейс (CLI)

Админ-интерфейс (`admin_cli.py`) обеспечивает безопасный доступ для чтения данных.

### Просмотр деталей пользователя

```bash
python admin_cli.py user 123456789
```

Пример вывода:
```
============================================================
👤 User Details: 123456789
============================================================

Telegram ID:       123456789
Username:          @username
First Name:        John
First Seen:        2026-01-20 10:30:45 UTC
Last Updated:      2026-01-25 12:15:30 UTC

📊 Usage:
  Daily Count:     2
  Last Reset:      2026-01-25
  Purchased Layouts: 5

💫 Subscriptions:
  1. Type: month, Start: 2026-01-20, End: 2026-02-20

👥 Referrals:
  Referred By:     None
  Referral Count:  3
  Referral IDs:    987654321, 456789123, 789456123
```

### Просмотр статистики

```bash
python admin_cli.py stats
```

Пример вывода:
```
============================================================
📊 Tarot Bot Statistics
============================================================

👥 Users:
  Total Users:           245
  Users with Subscriptions: 42
  Users with Referrals:  87

🎴 Layouts:
  Total Purchased:       523
  Average per User:      2.13

🔗 Referrals:
  Total Referrals:       156
  Average per User:      0.64
```

### Список пользователей

```bash
# Показать 20 последних пользователей
python admin_cli.py list-users

# Показать 50 пользователей
python admin_cli.py list-users --limit 50
```

### Добавление раскладов (запись)

```bash
# Добавить 5 раскладов пользователю
python admin_cli.py add-layouts 123456789 5

# Требуется подтверждение:
# Type 'yes' to confirm: yes
```

### Просмотр событий YooKassa

```bash
# Показать последние 10 платежей
python admin_cli.py yookassa-events

# Показать последние 50
python admin_cli.py yookassa-events --limit 50
```

### Просмотр тикетов поддержки

```bash
# Показать все тикеты
python admin_cli.py support-tickets

# Фильтр по статусу
python admin_cli.py support-tickets --status open
python admin_cli.py support-tickets --status new
```

---

## Лучшие практики

### Ежедневные действия

1. Проверка статуса сервисов: `./scripts/status.sh`
2. Проверка логов на ошибки: `sudo journalctl -u tarotbot -u tarot-support --since today | grep -i error`
3. Проверка свободного места: `df -h`

### Еженедельные действия

1. Просмотр статистики: `python admin_cli.py stats`
2. Проверка наличия автоматических бэкапов: `ls -lh /var/backups/tarotbot/`
3. Проверка работоспособности: `./scripts/healthcheck.sh`

### Ежемесячные действия

1. Анализ тенденций роста пользователей
2. Очистка старых логов: `sudo journalctl --vacuum-time=30d`
3. Обновление зависимостей (при необходимости)
4. Проверка обновлений безопасности ОС

### Перед обновлениями кода

1. Создание бэкапа вручную: `./scripts/backup.sh`
2. Ознакомление со списком изменений
3. Тестирование в dev-окружении (если возможно)
4. Развертывание в часы низкой активности
5. Мониторинг логов после развертывания

---

## Контакты для экстренной связи

- **Разработчик**: [Ваш контакт]
- **Хостинг-провайдер**: [Поддержка провайдера]
- **Регистратор домена**: [Поддержка регистратора]

---

## Дополнительные ресурсы

- [README_DEPLOY.md](README_DEPLOY.md) - Полное руководство по развертыванию
- [Telegram Bot API](https://core.telegram.org/bots/api) - Официальная документация
- [YooKassa Docs](https://yookassa.ru/developers) - Интеграция платежей

---

## Примечания

### Переменные окружения

Никогда не сохраняйте файлы `.env` в git и не делитесь ими публично. Если учетные данные скомпрометированы:
1. Перегенерируйте токены ботов в BotFather
2. Перегенерируйте ключи YooKassa в панели управления
3. Обновите `/etc/tarotbot/*.env`
4. Перезапустите сервисы

### Целостность данных

- Всегда делайте бэкап перед ручным редактированием данных
- Никогда не редактируйте JSON, пока запущены сервисы
- Проверяйте синтаксис JSON перед сохранением
- Используйте админ-интерфейс (CLI) для безопасных операций

### Производительность

Ожидаемое использование ресурсов:
- **Память**: 100-300 МБ на каждый сервис
- **Процессор**: <5% в простое, 10-30% под нагрузкой
- **Диск**: Минимум (запись в JSON происходит нечасто)

Если показатели превышают эти диапазоны, изучите логи на предмет проблем.
