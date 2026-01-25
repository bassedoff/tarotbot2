// server.js
const express = require('express');
const cors = require('cors');
const fs = require('fs');
const fsp = require('fs/promises');
const path = require('path');

const app = express();
const PORT = process.env.PORT || 3000;

// === Хранилище ===
const DATA_DIR = path.resolve(__dirname);
const DATA_FILE = path.join(DATA_DIR, 'tarot_user_data.json');

function ensureStorage() {
    if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
    if (!fs.existsSync(DATA_FILE)) fs.writeFileSync(DATA_FILE, '{}', 'utf-8');
}

function readUserData() {
    try {
        ensureStorage();
        const raw = fs.readFileSync(DATA_FILE, 'utf-8').trim();
        if (!raw) return {};
        const parsed = JSON.parse(raw);
        return typeof parsed === 'object' && parsed !== null ? parsed : {};
    } catch (e) {
        console.error('❌ Ошибка чтения tarot_user_data.json:', e.message);
        fs.writeFileSync(DATA_FILE, '{}', 'utf-8');
        return {};
    }
}

async function writeUserData(data) {
    try {
        ensureStorage();
        const tmp = DATA_FILE + '.tmp';
        const content = JSON.stringify(data, null, 2);
        await fsp.writeFile(tmp, content, 'utf-8');
        await fsp.rename(tmp, DATA_FILE);
        console.log(`✅ Данные сохранены. Пользователей: ${Object.keys(data).length}`);
    } catch (e) {
        console.error('❌ Ошибка записи tarot_user_data.json:', e.message);
    }
}

// === Middleware ===
// Добавляем middleware для отдачи статических файлов из папки images
app.use('/images', express.static(path.join(__dirname, 'images')));

app.use(cors({
    origin: (origin, cb) => {
        const allowed = [
            'https://t.me',
            'https://web.telegram.org',
            'http://5.129.196.58',
            'http://5.129.196.58:3000',
            'http://5.129.196.58:8000',
            'http://localhost:3000',
            'http://127.0.0.1:3000'
        ];
        console.log(`🌐 Проверка CORS для origin: ${origin}`);
        if (!origin || allowed.some(url => origin.startsWith(url))) {
            console.log('✅ CORS разрешён');
            cb(null, true);
        } else {
            console.log('❌ CORS заблокирован:', origin);
            cb(new Error('Not allowed by CORS'));
        }
    },
    credentials: true,
    optionsSuccessStatus: 204
}));
app.use(express.json({ limit: '256kb' }));

// === API ===

// --- ЗАПРЕЩЕНО: Запись в JSON из Node.js ---
app.post('/save_user_data', async (req, res) => {
    console.log('🔥 [LOG] Пришёл POST /save_user_data - проксирование в Python');
    console.log('📥 Тело запроса:', req.body);

    const {
        telegram_id,
        username,
        first_name,
        last_question,
        last_cards,
    } = req.body || {};

    if (!telegram_id) {
        console.log('❌ Нет telegram_id');
        return res.status(400).json({ error: 'telegram_id required' });
    }

    // Проксируем запрос в Python бота
    if (Array.isArray(last_cards) && last_cards.length === 3) {
        console.log('🎯 [WEBHOOK] Отправляем данные на Python бота...');
        try {
            const webhookUrl = 'http://localhost:5000/webhook/tarot-cards';
            const webhookData = {
                telegram_id: parseInt(telegram_id),
                last_question: last_question || 'Вопрос не задан',
                last_cards: last_cards,
                username: username,
                first_name: first_name
            };
            
            console.log('📤 [WEBHOOK] Отправляем webhook:', webhookData);
            
            const response = await fetch(webhookUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(webhookData)
            });
            
            if (response.ok) {
                console.log('✅ [WEBHOOK] Webhook успешно отправлен');
                return res.json({ success: true });
            } else {
                console.error('❌ [WEBHOOK] Ошибка webhook:', response.status, await response.text());
                return res.status(500).json({ error: 'Webhook failed' });
            }
        } catch (error) {
            console.error('❌ [WEBHOOK] Ошибка отправки webhook:', error.message);
            return res.status(500).json({ error: 'Webhook failed' });
        }
    }
    
    res.json({ success: true });
});

// --- Получение данных пользователя ---
app.get('/get_user_data', (req, res) => {
    const { telegram_id } = req.query;
    if (!telegram_id) return res.status(400).json({ error: 'telegram_id required' });

    const userData = readUserData();
    const user = userData[telegram_id] || null;
    console.log(`📤 /get_user_data(${telegram_id}):`, user ? 'найден' : 'не найден');
    res.json(user);
});

// --- Получение последних раскладов (для отладки) ---
app.get('/get_recent_readings', (req, res) => {
    const userData = readUserData();
    const readings = Object.values(userData)
        .filter(u => u.last_cards && u.last_cards.length > 0)
        .sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at))
        .slice(0, 5)
        .map(({ telegram_id, username, first_name, last_question, last_cards, updated_at }) => ({
            telegram_id,
            username,
            first_name,
            last_question,
            last_cards,
            updated_at
        }));
    res.json(readings);
});

// --- Отладка: просмотр всего файла ---
app.get('/debug', (req, res) => {
    res.type('json').send(readUserData());
});

// --- Главная страница сервера ---
app.get('/', (req, res) => {
    const userData = readUserData();
    res.send(`<h1>✅ Tarot MiniApp Server</h1>
        <p>Сервер запущен. Пользователей: ${Object.keys(userData).length}</p>
        <ul>
            <li><a href="/get_recent_readings">/get_recent_readings</a></li>
            <li><a href="/debug">/debug</a></li>
        </ul>
        <p><strong>Файл:</strong> ${DATA_FILE}</p>`);
});

// --- Запуск сервера ---
app.listen(PORT, '0.0.0.0', () => {
    ensureStorage();
    console.log(`✅ Сервер запущен на порту ${PORT}`);
    console.log(`🏠 Домашняя страница: http://localhost:${PORT}`);
    console.log(`📁 Файл данных: ${DATA_FILE}`);

    // Проверяем, что сервер действительно работает
    setTimeout(() => {
        console.log('✅ Сервер работает стабильно');
    }, 1000);
});

// --- Обработка ошибок ---
process.on('uncaughtException', (err) => {
    console.error('❌ Непойманная ошибка:', err);
    process.exit(1);
});

process.on('unhandledRejection', (reason, promise) => {
    console.error('❌ Необработанное отклонение промиса:', reason);
    process.exit(1);
});