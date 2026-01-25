# Руководство по развертыванию в рабочей среде (Production)

Полное пошаговое руководство по развертыванию Tarot Bot на VPS-сервере.

## Содержание

1. [Предварительные требования](#предварительные-требования)
2. [Настройка сервера](#настройка-сервера)
3. [Установка](#установка)
4. [Конфигурация](#конфигурация)
5. [Настройка сервисов](#настройка-сервисов)
6. [Nginx и SSL](#nginx-и-ssl)
7. [Вебхук YooKassa](#yookassa-webhook)
8. [Проверка](#проверка)
9. [Устранение неполадок](#устранение-неполадок)

---

## Предварительные требования

### Требования к серверу

- **ОС**: Ubuntu 20.04 LTS или новее (на базе Debian)
- **ОЗУ**: Минимум 512 МБ, рекомендуется 1 ГБ
- **Диск**: 5 ГБ свободного места
- **Процессор**: 1 ядро (рекомендуется 2+)
- **Сеть**: Публичный IP-адрес с открытыми портами 80, 443

### Необходимое ПО

- Python 3.8+
- Git
- Nginx
- Certbot (для SSL-сертификатов)
- systemd

### Доступ

- SSH-доступ с правами root или sudo
- Доменное имя, указывающее на IP-адрес сервера

---

## Настройка сервера

### 1. Создание выделенного пользователя

```bash
# Создать пользователя tarotbot
sudo useradd -m -s /bin/bash tarotbot

# Добавить в необходимые группы
sudo usermod -aG www-data tarotbot
```

### 2. Создание структуры каталогов

```bash
# Каталог приложения
sudo mkdir -p /opt/tarotbot
sudo chown tarotbot:tarotbot /opt/tarotbot

# Каталог данных (постоянные данные)
sudo mkdir -p /var/lib/tarotbot
sudo chown tarotbot:tarotbot /var/lib/tarotbot
sudo chmod 750 /var/lib/tarotbot

# Каталог резервных копий
sudo mkdir -p /var/backups/tarotbot
sudo chown tarotbot:tarotbot /var/backups/tarotbot

# Каталог конфигурации
sudo mkdir -p /etc/tarotbot
sudo chown root:tarotbot /etc/tarotbot
sudo chmod 750 /etc/tarotbot

# Каталог логов (опционально, по умолчанию используется журнал systemd)
sudo mkdir -p /var/log/tarotbot
sudo chown tarotbot:tarotbot /var/log/tarotbot
```

---

## Установка

### 1. Клонирование репозитория

```bash
# Переключиться на пользователя tarotbot
sudo su - tarotbot

# Клонировать репозиторий
cd /opt
git clone <URL_ВАШЕГО_РЕПОЗИТОРИЯ> tarotbot
cd tarotbot
```

### 2. Настройка окружения Python

```bash
# Создать виртуальное окружение
python3 -m venv venv

# Активировать venv
source venv/bin/activate

# Обновить pip
pip install --upgrade pip

# Установить зависимости
pip install -r requirements.txt
```

### 3. Настройка Node.js (если мини-приложение использует npm)

```bash
# Установить зависимости Node.js (если требуется)
npm ci
```

---

## Конфигурация

### 1. Создание файлов окружения

Создайте два отдельных файла окружения для основного бота и бота поддержки:

#### Окружение основного бота (`/etc/tarotbot/main.env`)

```bash
sudo nano /etc/tarotbot/main.env
```

Содержимое (скопируйте из `.env.example` и подставьте реальные значения):

```bash
# === ТОКЕНЫ ТЕЛЕГРАМ-БОТА ===
BOT_TOKEN=ваш_реальный_токен_основного_бота
SUPPORT_BOT_USERNAME=ИмяПользователяБотаПоддержки
MAIN_BOT_USERNAME=ИмяПользователяОсновногоБота

# === ВЕБ-ПРИЛОЖЕНИЕ ===
WEB_APP_URL=https://yourdomain.com/miniapp.html

# === КОНФИГУРАЦИЯ OPENAI ===
OPENAI_API_KEY=ваш_реальный_ключ_api
OPENAI_MODEL=gpt-4o
OPENAI_FALLBACK_MODEL=gpt-4o
OPENAI_BASE_URL=https://api.openai.com/v1
DISABLE_FREE_TEXT_AI=1

# === ОПЛАТА YOOKASSA ===
YOOKASSA_SHOP_ID=ваш_id_магазина
YOOKASSA_SECRET_KEY=ваш_секретный_ключ
YOOHOOK_TOKEN=ваш_случайный_защищенный_токен

# === ПУТИ К ДАННЫМ (production) ===
DATA_PATH=/var/lib/tarotbot/tarot_user_data.json
YOOKASSA_EVENTS_PATH=/var/lib/tarotbot/yookassa_events.json

# === НАСТРОЙКИ ХРАНИЛИЩА ===
SAFE_STORAGE_WRITE=1
DEBUG=0
```

#### Окружение бота поддержки (`/etc/tarotbot/support.env`)

```bash
sudo nano /etc/tarotbot/support.env
```

Содержимое:

```bash
# === ТОКЕНЫ ТЕЛЕГРАМ-БОТА ===
SUPPORT_BOT_TOKEN=ваш_реальный_токен_бота_поддержки
SUPPORT_BOT_USERNAME=ИмяПользователяБотаПоддержки
MAIN_BOT_USERNAME=ИмяПользователяОсновногоБота

# === КОНФИГУРАЦИЯ ПОДДЕРЖКИ ===
SUPPORT_OPERATORS_CHAT_ID=id_чата_операторов
SUPPORT_OPERATOR_IDS=id_оператора_1,id_оператора_2
ADMINS=id_администратора_в_телеграм

# === ПУТИ К ДАННЫМ (production) ===
SUPPORT_TICKETS_PATH=/var/lib/tarotbot/support_tickets.json

# === НАСТРОЙКИ БОТА ПОДДЕРЖКИ ===
SUPPORT_FAQ_PATH=/opt/tarotbot/support/faq.yaml
SUPPORT_HOURS=10:00-20:00 MSK
SUPPORT_RATE_LIMIT_USER=3
SUPPORT_AUTOCLOSE_HOURS=72
```

### 2. Настройка прав доступа

```bash
# Защитить файлы окружения
sudo chmod 640 /etc/tarotbot/main.env
sudo chmod 640 /etc/tarotbot/support.env
sudo chown root:tarotbot /etc/tarotbot/main.env
sudo chown root:tarotbot /etc/tarotbot/support.env
```

### 3. Инициализация файлов данных

```bash
# Создать пустые файлы данных, если они не существуют
sudo -u tarotbot touch /var/lib/tarotbot/tarot_user_data.json
sudo -u tarotbot touch /var/lib/tarotbot/support_tickets.json
sudo -u tarotbot touch /var/lib/tarotbot/yookassa_events.json

# Инициализировать пустым JSON
echo '{}' | sudo -u tarotbot tee /var/lib/tarotbot/tarot_user_data.json
echo '{"tickets": {}, "last_id": 0}' | sudo -u tarotbot tee /var/lib/tarotbot/support_tickets.json
echo '{}' | sudo -u tarotbot tee /var/lib/tarotbot/yookassa_events.json
```

---

## Настройка сервисов

### 1. Установка сервисов Systemd

```bash
# Скопировать файлы сервисов
sudo cp /opt/tarotbot/deploy/systemd/tarotbot.service /etc/systemd/system/
sudo cp /opt/tarotbot/deploy/systemd/tarot-support.service /etc/systemd/system/

# Перезагрузить конфигурацию systemd
sudo systemctl daemon-reload

# Включить сервисы (автозапуск при загрузке)
sudo systemctl enable tarotbot
sudo systemctl enable tarot-support

# Запустить сервисы
sudo systemctl start tarotbot
sudo systemctl start tarot-support
```

### 2. Проверка сервисов

```bash
# Проверить статус
sudo systemctl status tarotbot
sudo systemctl status tarot-support

# Или использовать вспомогательный скрипт
/opt/tarotbot/scripts/status.sh
```

### 3. Просмотр логов

```bash
# С помощью journalctl
sudo journalctl -u tarotbot -u tarot-support -f

# Или использовать вспомогательный скрипт
/opt/tarotbot/scripts/logs.sh
```

---

## Nginx и SSL

### 1. Установка Nginx

```bash
sudo apt update
sudo apt install -y nginx
```

### 2. Конфигурация Nginx

```bash
# Скопировать конфигурацию nginx
sudo cp /opt/tarotbot/deploy/nginx/tarotbot.nginx.conf /etc/nginx/sites-available/tarotbot

# Отредактировать конфигурацию — замените yourdomain.com на ваш реальный домен
sudo nano /etc/nginx/sites-available/tarotbot
```

Обновите эти строки:
- `server_name yourdomain.com www.yourdomain.com;`
- Пути к SSL-сертификатам (после получения сертификатов)

```bash
# Включить сайт
sudo ln -s /etc/nginx/sites-available/tarotbot /etc/nginx/sites-enabled/

# Проверить конфигурацию
sudo nginx -t

# Перезагрузить nginx
sudo systemctl reload nginx
```

### 3. Получение SSL-сертификатов

```bash
# Установить certbot
sudo apt install -y certbot python3-certbot-nginx

# Получить сертификаты (интерактивно)
sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com

# Certbot автоматически настроит nginx для работы по HTTPS
```

### 4. Настройка автопродления

```bash
# Проверить продление
sudo certbot renew --dry-run

# Задание cron для продления создается certbot автоматически
```

---

## Вебхук YooKassa

### 1. Получение URL вебхука

Ваш URL вебхука будет иметь вид:
```
https://yourdomain.com/webhook/yookassa
```

### 2. Настройка в личном кабинете YooKassa

1. Войдите в личный кабинет YooKassa: https://yookassa.ru/
2. Перейдите в **Настройки** → **HTTP-уведомления**
3. Добавьте URL вебхука: `https://yourdomain.com/webhook/yookassa`
4. Выберите события: `payment.succeeded`
5. Сохраните конфигурацию

### 3. Тестирование вебхука

```bash
# Проверить эндпоинт здоровья
curl https://yourdomain.com/health

# Или использовать скрипт healthcheck
/opt/tarotbot/scripts/healthcheck.sh --url https://yourdomain.com/health
```

---

## Проверка

### 1. Проверка здоровья сервиса

```bash
# Запустить проверку статуса
/opt/tarotbot/scripts/status.sh

# Запустить проверку работоспособности
/opt/tarotbot/scripts/healthcheck.sh --url https://yourdomain.com/health
```

### 2. Тестирование бота

- Откройте Telegram и отправьте `/start` вашему боту
- Попробуйте задать вопрос
- Протестируйте процесс оплаты (используйте тестовый режим YooKassa)
- Протестируйте бота поддержки

### 3. Мониторинг логов

```bash
# Наблюдать за логами в реальном времени
/opt/tarotbot/scripts/logs.sh

# Или напрямую через systemd
sudo journalctl -u tarotbot -u tarot-support -f
```

---

## Устранение неполадок

### Сервис не запускается

```bash
# Проверить статус сервиса
sudo systemctl status tarotbot

# Проверить логи
sudo journalctl -u tarotbot -n 50

# Частые проблемы:
# - Проверьте файлы окружения: /etc/tarotbot/*.env
# - Проверьте права доступа к файлам: /var/lib/tarotbot/
# - Проверьте виртуальное окружение Python: /opt/tarotbot/venv
```

### Бот не отвечает

```bash
# Проверить, запущен ли сервис
sudo systemctl status tarotbot

# Проверить сетевое соединение
curl https://api.telegram.org

# Проверить токен бота
# В файле /etc/tarotbot/main.env
```

### Вебхук не работает

```bash
# Проверить эндпоинт здоровья
curl https://yourdomain.com/health

# Проверить логи nginx
sudo tail -f /var/log/nginx/tarotbot_error.log

# Проверить логи бота на ошибки вебхука
sudo journalctl -u tarotbot | grep webhook
```

### Данные не сохраняются

```bash
# Проверить права доступа к каталогу данных
ls -la /var/lib/tarotbot/

# Убедиться, что SAFE_STORAGE_WRITE установлено в 1
grep SAFE_STORAGE_WRITE /etc/tarotbot/main.env

# Проверить, записываются ли файлы
sudo -u tarotbot ls -la /var/lib/tarotbot/
```

---

## После развертывания

### Регулярное обслуживание

1. **Мониторинг логов** ежедневно
2. **Резервное копирование данных** еженедельно (автоматически через cron, см. README_ADMIN.md)
3. **Обновление кода** при выходе новых функций
4. **Проверка места на диске** ежемесячно
5. **Обзор безопасности** ежеквартально

### Процедура обновления

```bash
# Использовать скрипт развертывания
sudo su - tarotbot
cd /opt/tarotbot
./scripts/deploy.sh
```

Это выполнит:
- Стягивание последнего кода
- Создание автоматической резервной копии
- Установку зависимостей
- Перезапуск сервисов

---

## Следующие шаги

- Прочтите [README_ADMIN.md](README_ADMIN.md) для руководства по ежедневному администрированию
- Настройте мониторинг и оповещения
- Настройте автоматизированное резервное копирование
- Изучите способы усиления безопасности

---

## Поддержка

При возникновении проблем или вопросов:
- Проверьте логи: `/opt/tarotbot/scripts/logs.sh`
- Проверьте статус: `/opt/tarotbot/scripts/status.sh`
- Контакт: [Ваш контакт для связи]
