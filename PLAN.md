# VideoFeed: Домашний видео-стриминг сервис

## Context

Нужен локальный self-hosted сервис для просмотра коллекции юмористических видео (~283 файла, ~1.8GB, форматы mp4/mkv/avi/mov) в браузере с красивым плеером. Позже — библиотека фильмов/сериалов. Кастомная сборка с нуля, без Docker, только локальная сеть.

---

## Tech Stack

| Компонент | Технология | Почему |
|-----------|-----------|--------|
| Backend | **FastAPI** (Python) | Знакомый Python, async файловый I/O, удобная интеграция с FFmpeg через subprocess |
| Frontend | **React + Vite** (TypeScript) | Быстрая разработка, большая экосистема |
| Плеер | **Video.js 8** + `@videojs/http-streaming` | Лучшая поддержка HLS, плагин качества, расширяемый |
| CSS | **Tailwind CSS** | Быстрый тёмный UI, responsive grid |
| БД | **SQLite** + SQLAlchemy + Alembic | Zero config, один файл, достаточно для тысяч видео |
| Поиск | SQLite **FTS5** | Встроенный, быстрый для локального использования |
| Транскодирование | **FFmpeg** → HLS | Мультикачество, адаптивный битрейт |

---

## Структура проекта

```
video_feed/
├── videos/                      # Исходные файлы (уже есть, READ-ONLY)
├── media/
│   ├── hls/{video_uuid}/        # HLS сегменты
│   │   ├── master.m3u8
│   │   ├── 1080p/ 720p/ 480p/
│   └── thumbnails/              # Автогенерированные превью
├── data/
│   └── videofeed.db             # SQLite база
├── backend/
│   ├── main.py                  # FastAPI entry point
│   ├── config.py                # Настройки (пути, FFmpeg)
│   ├── database.py              # SQLAlchemy engine + session
│   ├── models.py                # ORM модели
│   ├── schemas.py               # Pydantic схемы
│   ├── routers/
│   │   ├── videos.py            # CRUD + листинг
│   │   ├── streaming.py         # HLS + raw стриминг
│   │   ├── transcode.py         # Управление транскодированием
│   │   └── progress.py          # Прогресс просмотра
│   ├── services/
│   │   ├── transcoder.py        # FFmpeg HLS пайплайн
│   │   ├── scanner.py           # Сканирование videos/
│   │   ├── metadata.py          # FFprobe метаданные
│   │   └── thumbnail.py         # Генерация превью
│   └── requirements.txt
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   └── src/
│       ├── App.tsx
│       ├── api/                 # API клиент
│       ├── components/
│       │   ├── VideoGrid.tsx    # Сетка видео (YouTube-стиль)
│       │   ├── VideoCard.tsx    # Карточка (превью + длительность + название)
│       │   ├── VideoPlayer.tsx  # Video.js обёртка с HLS
│       │   ├── SearchBar.tsx
│       │   ├── TagFilter.tsx
│       │   └── Layout.tsx       # Шапка + сайдбар + контент
│       ├── pages/
│       │   ├── HomePage.tsx     # Сетка всех видео
│       │   ├── WatchPage.tsx    # Плеер + инфо
│       │   └── AdminPage.tsx    # Сканирование + транскодирование
│       └── hooks/
│           └── useWatchProgress.ts
└── run.py                       # Единый лаунчер
```

---

## Схема БД

**videos**: id (uuid), title, description, original_filename (unique), original_path, duration, width, height, file_size, codec, transcode_status (pending/processing/completed/failed), transcode_progress, hls_path, thumbnail_path, tags (comma-separated), category, is_vertical, created_at, added_at

**watch_progress**: id, video_id (FK), position (seconds), duration, completed (bool), updated_at

---

## API эндпоинты

**Видео**: `GET /api/videos` (список + фильтры), `GET /api/videos/{id}`, `PATCH /api/videos/{id}`, `GET /api/videos/search?q=`, `GET /api/videos/tags`, `GET /api/videos/categories`

**Стриминг**: `GET /api/stream/{id}/master.m3u8`, `GET /api/stream/{id}/{quality}/stream.m3u8`, `GET /api/stream/{id}/{quality}/{segment}`, `GET /api/stream/{id}/thumbnail`, `GET /api/stream/{id}/raw` (фоллбэк с Range headers)

**Транскодирование**: `POST /api/transcode/scan`, `POST /api/transcode/{id}`, `POST /api/transcode/all`, `GET /api/transcode/queue`

**Прогресс**: `GET /api/progress/{id}`, `PUT /api/progress/{id}`, `GET /api/progress/continue-watching`

---

## Транскодирование

- **Сканирование**: обход `videos/`, FFprobe для метаданных, создание записей в БД
- **Превью**: FFmpeg вырезает кадр на 25% длительности → `thumbnails/{uuid}.jpg`
- **HLS**: FFmpeg конвертирует в сегменты по 4 сек, качество зависит от исходника:
  - >= 1080p → 1080p (5Mbps) + 720p (2.5Mbps) + 480p (1Mbps)
  - >= 720p → 720p + 480p
  - < 720p → только оригинальное разрешение
- **Очередь**: `asyncio.Queue` с одним воркером (без Celery/Redis)
- **Фоллбэк**: до завершения транскодирования — прямой стриминг оригинала через `/raw`

---

## Ключевые решения по UI

- Тёмная тема (YouTube-стиль)
- Responsive grid с карточками (превью + длительность + прогресс-бар)
- Video.js с кастомным скином, выбор качества, скорость воспроизведения
- Вертикальные видео: адаптивный контейнер вместо чёрных полос
- Сохранение прогресса каждые 5 сек, "Продолжить просмотр" на главной
- Поиск по FTS5, фильтры по тегам/категориям/длительности

---

## Порядок реализации

### Фаза 1: Минимальный рабочий плеер
1. Backend foundation: `config.py`, `database.py`, `models.py`, `schemas.py`
2. `services/metadata.py` — FFprobe интеграция
3. `services/scanner.py` — сканирование `videos/`, заполнение БД
4. `routers/videos.py` — GET список и детали
5. `routers/streaming.py` — `/raw` стриминг с Range headers
6. `main.py` — сборка FastAPI приложения
7. Frontend scaffold: Vite + React + Tailwind
8. `VideoGrid`, `VideoCard`, `HomePage` — сетка с названиями
9. `VideoPlayer`, `WatchPage` — Video.js играет raw файлы

**Результат**: можно смотреть все 283 видео в красивой сетке.

### Фаза 2: Превью и транскодирование
1. `services/thumbnail.py` — генерация превью
2. Обновить `VideoCard` — реальные превью
3. `services/transcoder.py` — HLS пайплайн
4. `routers/transcode.py` — управление очередью
5. `AdminPage` — кнопки сканирования и транскодирования
6. Обновить `VideoPlayer` — HLS когда доступен, raw как фоллбэк

### Фаза 3: Полировка
1. Прогресс просмотра (save/resume)
2. Поиск через FTS5
3. Теги и категории
4. "Продолжить просмотр" на главной
5. Кастомная тёмная тема Video.js
6. Автоочистка названий из имён файлов
7. Оверлей длительности на превью
8. Горячие клавиши (пробел, стрелки, F)

### Фаза 4: Будущее (библиотека фильмов)
1. Модели Movie/Series с сезонами/эпизодами
2. Папочная организация
3. Метаданные из TMDB/OMDB
4. Профили пользователей

---

## Верификация

1. `pip install -r backend/requirements.txt` + `npm install` в frontend/
2. `python run.py` — запуск обоих серверов
3. Открыть `http://localhost:3000` — должна отобразиться сетка видео
4. Кликнуть на видео — должен запуститься плеер (raw стриминг)
5. Admin → Scan Library → видео появляются с превью
6. Admin → Transcode All → видео конвертируются в HLS
7. После транскодирования — плеер переключается на HLS с выбором качества
8. Проверить поиск, фильтры, сохранение прогресса
