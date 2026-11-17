"""Спільні налаштування для скриптів і сервера.

Модель, префікс і нормалізація тексту мають бути однакові скрізь (наповнення бази,
пошук, сервер), інакше вектори не можна порівнювати між собою.
"""

import os
import re
import sys

from dotenv import load_dotenv

load_dotenv()

# Щоб кирилиця нормально друкувалася в консолі Windows.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ---------------------------------------------------------------- Модель
MODEL_NAME = "intfloat/multilingual-e5-small"
EMBED_DIM = 384
# Для e5 симетричне порівняння «відео ↔ відео»: префікс "query: " з обох боків.
EMBED_PREFIX = "query: "

# ---------------------------------------------------------------- Каталог
MAX_VIDEOS_PER_CHANNEL = 100  # скільки останніх відео брати з кожного каналу
SHORTS_MAX_SECONDS = 180  # усе, що не довше 3 хвилин, вважаємо Shorts
# Дитячі пісні й мультики часто тривають 2–3 хвилини, тож для каналів з kids = yes
# Shorts вважаємо лише відео до хвилини.
KIDS_SHORTS_MAX_SECONDS = 60
DESCRIPTION_STORE_CHARS = 500  # скільки опису зберігати в базі (місце в Supabase обмежене)
RETENTION_DAYS = 30  # правила YouTube API: оновлювати або видаляти дані щонайменше раз на 30 днів

# ---------------------------------------------------------------- Рекомендації
# Версія алгоритму. Коли змінюємо, як рахується схожість, збільшуємо число:
# сервер при старті сам очистить старі кеші відповідей і мов.
ALGO_VERSION = 5

# Схожість = TITLE_WEIGHT × схожість назв + решта × схожість назви з описом.
# Назва найкраще описує тему; опис додає контекст, але часто засмічений.
TITLE_WEIGHT = 0.5

# Пороги схожості (косинусна, модель e5-small). Стартові значення з перших тестів:
# коли відповідь у базі є, найкращий результат 0.865–0.875; коли немає, 0.825–0.84.
SAME_TOPIC_THRESHOLD = 0.86  # рівень 1: та сама тема
CLOSE_TOPIC_THRESHOLD = 0.84  # рівень 3: близька тема
# Для рівня 1 відео має ще й виділятися над «фоном» (медіаною схожості кандидатів).
# Справжній збіг теми відривається від фону на 0.03+, випадковий (через спільне слово
# на кшталт «ШЕДЕВР») — менше. Стартове значення з перших прикладів, уточнюємо далі.
SAME_TOPIC_MIN_MARGIN = 0.03
# Дуже висока схожість — рівень 1 без перевірки відриву. Інакше в темах, де в базі багато
# влучних відео (десятки оглядів iPhone), висока медіана «з'їла» б відрив навіть в ідеального збігу.
SAME_TOPIC_STRONG = 0.875
MAX_ITEMS = 3  # скільки альтернатив віддавати
# Скільки найближчих відео брати з індексу векторів, перш ніж рахувати остаточну схожість
# (із врахуванням назви). Більше — точніше, але повільніше.
INDEX_PREFETCH = 100
MAX_PER_CHANNEL = 2  # не більше стількох відео з одного каналу
KEYWORD_BONUS_CAP = 0.03  # максимальний бонус за збіг чисел і назв моделей
# Наскільки найкраще відео має виділятися над «фоном» (медіаною 60 кандидатів).
# 0 = перевірку вимкнено. Підбираємо за /alternatives?debug=true.
MIN_MARGIN = 0.0

LANG_THRESHOLD = 0.8  # нижче цієї впевненості мова = unknown (як у розширенні)
CACHE_HOURS = 24  # скільки тримати готову відповідь /alternatives
SOURCE_CACHE_DAYS = 7  # скільки тримати визначену мову вихідного відео

# search.list коштує 100 одиниць квоти з 10 000 на день, тобто максимум 100 пошуків
# на весь проєкт. Лишаємо запас для наповнення бази й /classify.
ENABLE_SEARCH = True
SEARCH_DAILY_LIMIT = 80

WISH_DAILY_LIMIT = 20
WISH_TEXT_MAX = 280


def env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        sys.exit(f"У файлі .env немає змінної {name}. Додайте її і запустіть ще раз.")
    return value


def connect_db():
    """Одне підключення до Postgres (Supabase) з підтримкою векторів."""
    import psycopg
    from pgvector.psycopg import register_vector

    conn = psycopg.connect(env("DATABASE_URL"), autocommit=True)
    conn.execute("create extension if not exists vector")
    register_vector(conn)
    return conn


# ---------------------------------------------------------------- Текст і вектори

_CAPS_WORD = re.compile(r"\b[^\W\d_]{4,}\b")


def normalize_caps(text: str) -> str:
    """«НОУТБУК ПО ЦЕНЕ АВТО» → «ноутбук ПО цене авто».

    Модель розрізняє великі й малі літери, і заголовки КАПСОМ вона розуміє гірше.
    Слова з 4+ літер, написані повністю великими, переводимо в малі. Короткі абревіатури
    (RTX, AMD, USB, ЗСУ) лишаємо як є.
    """
    return _CAPS_WORD.sub(lambda m: m.group(0).lower() if m.group(0).isupper() else m.group(0), text or "")


# Рядки опису, які описують не тему відео, а рекламу, соцмережі й прохання підписатися.
_PROMO = re.compile(
    r"промокод|promo|знижк|скидк|купон|coupon|спонсор|sponsor|реклам|партнер|affiliate|"
    r"підпис|подпис|subscribe|telegram|телеграм|instagram|інстаграм|инстаграм|tiktok|тікток|тикток|"
    r"patreon|патреон|донат|donat|monobank|монобанк|discord|діскорд|дискорд|facebook|twitter|"
    r"лайк|like|колокольчик|дзвіночок|мерч|merch|доставк",
    re.IGNORECASE,
)
_URL = re.compile(r"https?://|www\.|t\.me/|@\w{3,}")
# Таймкоди глав: «00:00 Страх неопределенности», «1:02:03 - Висновки».
_CHAPTER = re.compile(r"^\(?\d{1,2}:\d{2}(?::\d{2})?\)?\s*[-–—:|]?\s*(.+)$")
_LETTER = re.compile(r"[^\W\d_]")


def clean_description(description: str, limit: int = 400) -> str:
    """Лише змістовна частина опису: без посилань, реклами й соцмереж.

    Назви глав (рядки з таймкодами) зберігаємо: вони найкраще описують тему.
    """
    kept = []
    for line in (description or "").splitlines():
        line = line.strip()
        if not line:
            continue
        chapter = _CHAPTER.match(line)
        if chapter:
            kept.append(chapter.group(1))
            continue
        if _URL.search(line) or _PROMO.search(line):
            continue
        if len(_LETTER.findall(line)) < 3:  # рядки з самих емодзі чи символів
            continue
        kept.append(line.replace("#", " "))
    return "\n".join(kept)[:limit]


def embedding_text(title: str, tags, description: str) -> str:
    tags = [t for t in (tags or []) if t][:10]
    return "\n".join(part for part in [title or "", " ".join(tags), clean_description(description)] if part)


def title_text(title: str) -> str:
    return title or ""


# Скільки потоків процесора дати PyTorch. У контейнері (Railway) PyTorch бачить усі ядра
# фізичного сервера і запускає десятки потоків на кілька реально доступних ядер, через що
# обчислення сповільнюються в рази. Тому кількість потоків обмежуємо явно.
TORCH_THREADS = int(os.getenv("TORCH_THREADS", "2"))


def load_model():
    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(TORCH_THREADS)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass  # уже налаштовано раніше
    print(f"Завантажую модель {MODEL_NAME} (потоків: {TORCH_THREADS})…")
    return SentenceTransformer(MODEL_NAME, device="cpu")


def embed(model, texts):
    return model.encode(
        [EMBED_PREFIX + normalize_caps(t) for t in texts],
        batch_size=32,
        normalize_embeddings=True,
        show_progress_bar=len(texts) > 64,
    )
