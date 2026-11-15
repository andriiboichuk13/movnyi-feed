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
MAX_VIDEOS_PER_CHANNEL = 50  # скільки останніх відео брати з кожного каналу
SHORTS_MAX_SECONDS = 180  # усе, що не довше 3 хвилин, вважаємо Shorts
RETENTION_DAYS = 30  # правила YouTube API: оновлювати або видаляти дані щонайменше раз на 30 днів

# ---------------------------------------------------------------- Рекомендації
# Пороги схожості (косинусна, модель e5-small). Стартові значення з перших тестів:
# коли відповідь у базі є, найкращий результат 0.865–0.875; коли немає, 0.825–0.84.
SAME_TOPIC_THRESHOLD = 0.86  # рівень 1: та сама тема
CLOSE_TOPIC_THRESHOLD = 0.84  # рівень 3: близька тема
MAX_ITEMS = 3  # скільки альтернатив віддавати
MAX_PER_CHANNEL = 2  # не більше стількох відео з одного каналу
KEYWORD_BONUS_CAP = 0.03  # максимальний бонус за збіг чисел і назв моделей

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


def embedding_text(title: str, tags, description: str) -> str:
    return "\n".join([title or "", " ".join((tags or [])[:15]), (description or "")[:500]])


def load_model():
    from sentence_transformers import SentenceTransformer

    print(f"Завантажую модель {MODEL_NAME}…")
    return SentenceTransformer(MODEL_NAME)


def embed(model, texts):
    return model.encode(
        [EMBED_PREFIX + normalize_caps(t) for t in texts],
        batch_size=32,
        normalize_embeddings=True,
        show_progress_bar=len(texts) > 64,
    )
