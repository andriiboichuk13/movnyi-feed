"""Сервер «Мовного фіду» (FastAPI).

Запуск:  .venv\\Scripts\\python -m uvicorn server:app --port 8000
Перевірка в браузері: http://localhost:8000/health  і  http://localhost:8000/docs

Ендпоінти (формати з ТЗ):
  GET  /alternatives?videoId=…&mode=default|kids
  POST /classify   {videoIds: [...]}
  POST /wish       {installId, videoId, videoTitle, level, userText}
  POST /events     {installId, type, level, ts}
  POST /actions    {installId, items: [{action, videoId}]}
"""

import csv
import datetime as dt
import hmac
import logging
import os
import re
import threading
import time
from contextlib import asynccontextmanager
from typing import List, Literal, Optional

import numpy as np
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pgvector.psycopg import register_vector
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field, field_validator

import config as C
import wishes_pipeline
from lang import source_language
from youtube import YouTube, YouTubeError, available_in_ukraine, is_short, parse_duration

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "alternatives_v2.csv")
UK_DUBS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uk_dubs.txt")

SCHEMA = [
    # Мова будь-якого відео, яке відкривали користувачі (кеш, щоб не витрачати квоту).
    """create table if not exists source_videos (
        video_id text primary key,
        channel_id text,
        title text,
        description text,
        tags text[],
        lang text not null,
        confidence real not null,
        lang_source text,
        made_for_kids boolean,
        fetched_at timestamptz not null default now()
    )""",
    """create table if not exists alternatives_cache (
        video_id text not null,
        mode text not null,
        response jsonb not null,
        created_at timestamptz not null default now(),
        primary key (video_id, mode)
    )""",
    # Службові значення, наприклад версія алгоритму, з якою зібрано кеш.
    "create table if not exists meta (key text primary key, value text)",
    """create table if not exists search_budget (
        day date primary key,
        calls integer not null default 0
    )""",
    # Таблиці з ТЗ для «Хочу українське» і подій.
    """create table if not exists topics (
        id serial primary key,
        name text not null,
        parent_id integer references topics(id),
        description text
    )""",
    """create table if not exists wishes (
        id bigserial primary key,
        created_at timestamptz not null default now(),
        install_id text not null,
        video_id text not null,
        video_title text,
        user_text text,
        level_reached integer,
        topic_id integer references topics(id),
        subtopic_id integer references topics(id),
        topic_confidence real,
        processed_at timestamptz
    )""",
    "create index if not exists wishes_install_idx on wishes (install_id, created_at)",
    """create table if not exists events (
        id bigserial primary key,
        install_id text not null,
        type text not null,
        level integer,
        count integer,
        day date,
        ts timestamptz not null default now()
    )""",
    """create table if not exists video_actions (
        action_id bigserial primary key,
        created_at timestamptz not null default now(),
        install_id text not null,
        action text not null check (action in ('suggested', 'hid')),
        video_id text not null
    )""",
    "create index if not exists video_actions_install_idx on video_actions (install_id, created_at desc)",
    "create index if not exists video_actions_action_idx on video_actions (action, created_at desc)",
]

state = {}
model_lock = threading.Lock()


def configure_connection(conn):
    register_vector(conn)
    # За замовчуванням індекс віддає лише ~40 найближчих; нам треба INDEX_PREFETCH.
    conn.execute(f"set hnsw.ef_search = {max(40, C.INDEX_PREFETCH)}")


class Timer:
    """Скільки мілісекунд зайняв кожен етап відповіді. Пишеться в лог сервера."""

    def __init__(self):
        self.start = self.last = time.perf_counter()
        self.parts = {}

    def mark(self, name):
        now = time.perf_counter()
        self.parts[name] = self.parts.get(name, 0) + round((now - self.last) * 1000)
        self.last = now

    def total(self):
        return round((time.perf_counter() - self.start) * 1000)

    def line(self):
        return f"total={self.total()}ms " + " ".join(f"{k}={v}ms" for k, v in self.parts.items())


# ---------------------------------------------------------------- Запуск


@asynccontextmanager
async def lifespan(app: FastAPI):
    first = C.connect_db()  # створює розширення vector, якщо його ще немає
    for stmt in SCHEMA:
        first.execute(stmt)
    wishes_pipeline.ensure_wishes_columns(first)
    first.execute(f"alter table if exists videos add column if not exists title_embedding vector({C.EMBED_DIM})")
    # Індекс векторів для швидкого пошуку найближчих відео. Будується один раз (на 13 тис. відео
    # це до хвилини); якщо вже є, команда нічого не робить.
    if first.execute("select to_regclass('videos')").fetchone()[0]:
        first.execute("create index if not exists videos_embedding_hnsw on videos using hnsw (embedding vector_cosine_ops)")
    # Змінився алгоритм → старі готові відповіді й визначені мови більше не годяться.
    row = first.execute("select value from meta where key = 'algo_version'").fetchone()
    if not row or row[0] != str(C.ALGO_VERSION):
        first.execute("truncate alternatives_cache, source_videos")
        first.execute(
            """insert into meta (key, value) values ('algo_version', %s)
               on conflict (key) do update set value = excluded.value""",
            (str(C.ALGO_VERSION),),
        )
        print(f"Версія алгоритму {C.ALGO_VERSION}: кеш відповідей і мов очищено.")
    # Дані з YouTube API не тримаємо довше 30 днів.
    first.execute(f"delete from source_videos where fetched_at < now() - interval '{C.RETENTION_DAYS} days'")
    first.execute(f"delete from alternatives_cache where created_at < now() - interval '{C.RETENTION_DAYS} days'")
    first.close()

    state["pool"] = ConnectionPool(
        C.env("DATABASE_URL"),
        min_size=1,  # тримаємо мінімум одне з'єднання, щоб не витрачати час на нові
        max_size=5,
        max_lifetime=300,
        check=ConnectionPool.check_connection,
        kwargs={"autocommit": True, "connect_timeout": 10},
        configure=configure_connection,
        open=True,
    )
    yt_key = (os.getenv("YOUTUBE_API_KEY") or "").strip()
    state["yt"] = YouTube(yt_key) if yt_key else None
    if not state["yt"]:
        logging.warning("YOUTUBE_API_KEY не задано — /alternatives і /classify працюватимуть лише з кешу або повернуть 503.")
    try:
        state["model"] = C.load_model()
        wishes_pipeline.set_embed_model(state["model"])
    except Exception:
        logging.exception("Не вдалося завантажити модель ембедінгів — векторний пошук вимкнено.")
        state["model"] = None
    state["uk_dubs"] = load_uk_dubs()
    print("Сервер готовий: http://localhost:8000/docs")
    yield
    state["pool"].close()


app = FastAPI(title="Мовний фід API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # сайт /try і розширення; секретів через API не віддаємо
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def db():
    return state["pool"].connection()


def embed_one(text: str):
    with model_lock:
        return C.embed(state["model"], [text])[0]


def embed_many(texts):
    with model_lock:
        return C.embed(state["model"], texts)


def load_uk_dubs():
    """Ручний список англомовних відео з українським дубляжем (по одному videoId на рядок)."""
    if not os.path.exists(UK_DUBS_PATH):
        return set()
    with open(UK_DUBS_PATH, encoding="utf-8") as f:
        return {line.strip() for line in f if re.fullmatch(r"[\w-]{11}", line.strip())}


VIDEO_ID = re.compile(r"^[\w-]{11}$")


# ---------------------------------------------------------------- Мова відео


def catalog_channels(conn, channel_ids):
    rows = conn.execute("select channel_id from channels where channel_id = any(%s)", (list(channel_ids),)).fetchall()
    return {r[0] for r in rows}


def get_sources(video_ids):
    """Метадані й мова для списку відео: з кешу або через videos.list (1 одиниця на 50 відео)."""
    result = {}
    with db() as conn:
        rows = conn.execute(
            f"""select video_id, channel_id, title, description, tags, lang, confidence, lang_source, made_for_kids
                from source_videos
                where video_id = any(%s) and fetched_at > now() - interval '{C.SOURCE_CACHE_DAYS} days'""",
            (list(video_ids),),
        ).fetchall()
        for r in rows:
            result[r[0]] = dict(
                video_id=r[0], channel_id=r[1], title=r[2], description=r[3], tags=r[4] or [],
                lang=r[5], confidence=r[6], lang_source=r[7], made_for_kids=r[8],
            )

        missing = [v for v in video_ids if v not in result]
        if not missing:
            return result
        if not state["yt"]:
            raise YouTubeError("YOUTUBE_API_KEY не налаштовано")

        items = []
        for i in range(0, len(missing), 50):
            items += state["yt"].videos(missing[i : i + 50])
        in_catalog = catalog_channels(conn, {it["snippet"].get("channelId") for it in items})
        # Відео, яке вже є в нашій базі українських відео, точно українське.
        catalog_videos = {
            r[0] for r in conn.execute(
                "select video_id from videos where video_id = any(%s)", ([it["id"] for it in items],)
            ).fetchall()
        }

        to_save = []
        for it in items:
            sn = it["snippet"]
            lang, conf, src = source_language(sn, sn.get("channelId") in in_catalog)
            if it["id"] in catalog_videos:
                lang, conf, src = "uk", 0.95, "catalog-video"
            info = dict(
                video_id=it["id"], channel_id=sn.get("channelId"), title=sn.get("title", ""),
                description=(sn.get("description") or "")[:2000], tags=sn.get("tags") or [],
                lang=lang, confidence=float(conf), lang_source=src,
                made_for_kids=bool(it.get("status", {}).get("madeForKids")),
            )
            result[it["id"]] = info
            to_save.append(info)

        if to_save:
            with conn.cursor() as cur:
                cur.executemany(
                    """insert into source_videos (video_id, channel_id, title, description, tags, lang,
                                                  confidence, lang_source, made_for_kids, fetched_at)
                       values (%(video_id)s, %(channel_id)s, %(title)s, %(description)s, %(tags)s, %(lang)s,
                               %(confidence)s, %(lang_source)s, %(made_for_kids)s, now())
                       on conflict (video_id) do update set
                         channel_id = excluded.channel_id, title = excluded.title,
                         description = excluded.description, tags = excluded.tags, lang = excluded.lang,
                         confidence = excluded.confidence, lang_source = excluded.lang_source,
                         made_for_kids = excluded.made_for_kids, fetched_at = now()""",
                    to_save,
                )
    return result


# ---------------------------------------------------------------- Ранжування

# Російські й українські написання брендів, щоб «айфон» збігався з «iPhone».
BRAND_ALIASES = {
    "айфон": "iphone", "айфона": "iphone", "айфоне": "iphone", "айпад": "ipad",
    "макбук": "macbook", "самсунг": "samsung", "сяоми": "xiaomi", "сяомі": "xiaomi",
    "ксиоми": "xiaomi", "плейстейшн": "playstation", "нвидиа": "nvidia", "нвідіа": "nvidia",
    "эпл": "apple", "епл": "apple", "гугл": "google", "андроид": "android", "андроїд": "android",
    "виндовс": "windows", "віндовс": "windows", "майнкрафт": "minecraft",
}
LATIN_STOP = {"the", "and", "for", "with", "of", "in", "on", "to", "a", "vs", "my", "is", "it", "ua", "ru"}


def keywords(text: str):
    """Числа й латинські назви (iphone, 16, rtx, 5090), за якими варто підсилити збіг."""
    text = (text or "").lower()
    words = set(re.findall(r"[a-z][a-z0-9+]{1,}|\d{2,}", text)) - LATIN_STOP
    for w in re.findall(r"[\u0400-\u04FF]+", text):
        if w in BRAND_ALIASES:
            words.add(BRAND_ALIASES[w])
    return words


def keyword_bonus(query_words, title: str, tags) -> float:
    if not query_words:
        return 0.0
    cand = keywords(title + " " + " ".join(tags or []))
    common = query_words & cand
    bonus = sum(0.01 if w.isdigit() else 0.005 for w in common)
    return min(C.KEYWORD_BONUS_CAP, bonus)


def combined(title_sim: float, full_sim: float) -> float:
    return C.TITLE_WEIGHT * title_sim + (1 - C.TITLE_WEIGHT) * full_sim


def index_candidates(conn, qvec, tvec, exclude_id: str, exclude_channel, kids: bool, limit: int = 60):
    """Кандидати з бази. Схожість рахуємо двічі: назва з назвою і назва+опис з назвою+описом.

    Відео з того самого каналу, що й вихідне, не пропонуємо: їх і так видно в рекомендаціях YouTube.

    Звичайний режим: індекс векторів швидко дає INDEX_PREFETCH найближчих відео за назвою+описом,
    і вже серед них рахуємо остаточну схожість із врахуванням назви.
    Дитячий режим: точний перебір лише серед дитячих відео. Їх небагато, а індекс після
    фільтра «лише дитячі» загубив би більшість кандидатів.
    """
    w = C.TITLE_WEIGHT
    where = f"""v.lang = 'uk' and v.video_id <> %(vid)s
              and (v.duration_s > {C.SHORTS_MAX_SECONDS} or (c.kids and v.duration_s > {C.KIDS_SHORTS_MAX_SECONDS}))
              and v.channel_id is distinct from %(ch)s"""
    score = f"""({w} * (coalesce(v.title_embedding, v.embedding) <=> %(t)s) + {1 - w} * (v.embedding <=> %(q)s))"""
    params = dict(t=tvec, q=qvec, vid=exclude_id, ch=exclude_channel, limit=limit, prefetch=C.INDEX_PREFETCH)

    if kids:
        sql = f"""select v.video_id, v.title, v.tags, c.title, v.channel_id,
                         1 - (coalesce(v.title_embedding, v.embedding) <=> %(t)s) as title_sim,
                         1 - (v.embedding <=> %(q)s) as full_sim
                  from videos v join channels c using (channel_id)
                  where {where} and (v.made_for_kids or c.kids)
                  order by {score} + 0
                  limit %(limit)s"""
    else:
        sql = f"""with near as (
                      select video_id from videos
                      order by embedding <=> %(q)s
                      limit %(prefetch)s
                  )
                  select v.video_id, v.title, v.tags, c.title, v.channel_id,
                         1 - (coalesce(v.title_embedding, v.embedding) <=> %(t)s) as title_sim,
                         1 - (v.embedding <=> %(q)s) as full_sim
                  from near join videos v using (video_id) join channels c using (channel_id)
                  where {where}
                  order by {score}
                  limit %(limit)s"""
    rows = conn.execute(sql, params).fetchall()
    return [
        dict(video_id=r[0], title=r[1], tags=r[2], channel=r[3], channel_id=r[4],
             title_sim=float(r[5]), full_sim=float(r[6]), score=combined(float(r[5]), float(r[6])))
        for r in rows
    ]


def pick(cands, min_score: float, max_score: float = 2.0):
    """Найкращі кандидати в діапазоні схожості, не більше MAX_PER_CHANNEL з каналу."""
    chosen, per_channel = [], {}
    for c in sorted(cands, key=lambda x: -x["rank"]):
        if not (min_score <= c["score"] < max_score):
            continue
        if per_channel.get(c["channel_id"], 0) >= C.MAX_PER_CHANNEL:
            continue
        per_channel[c["channel_id"]] = per_channel.get(c["channel_id"], 0) + 1
        chosen.append(c)
        if len(chosen) == C.MAX_ITEMS:
            break
    return chosen


def item(c, lang="uk"):
    return {
        "videoId": c["video_id"],
        "title": c["title"],
        "channel": c["channel"],
        "lang": lang,
        "hasUkDub": c["video_id"] in state["uk_dubs"],
        "score": round(float(c["score"]), 3),
    }


# ---------------------------------------------------------------- Пошук через YouTube


def take_search_budget(conn) -> bool:
    if not C.ENABLE_SEARCH:
        return False
    calls = conn.execute(
        """insert into search_budget (day, calls) values (current_date, 1)
           on conflict (day) do update set calls = search_budget.calls + 1
           returning calls"""
    ).fetchone()[0]
    return calls <= C.SEARCH_DAILY_LIMIT


def youtube_search(conn, query_text: str, qvec, tvec, lang: str, kids: bool, exclude_id: str, exclude_channel=None):
    """search.list (100 одиниць) + videos.list (1 одиниця), лише якщо лишився денний бюджет."""
    if not take_search_budget(conn):
        return []
    params = dict(part="id", q=query_text[:100], type="video", maxResults=8,
                  relevanceLanguage=lang, regionCode="UA", safeSearch="strict" if kids else "moderate")
    try:
        found = state["yt"].get("search", **params).get("items", [])
        ids = [f["id"]["videoId"] for f in found if f.get("id", {}).get("videoId") and f["id"]["videoId"] != exclude_id]
        videos = state["yt"].videos(ids)
    except YouTubeError as e:
        print("search.list:", e)
        return []

    good = []
    for v in videos:
        sn, cd = v["snippet"], v["contentDetails"]
        if sn.get("liveBroadcastContent", "none") != "none" or is_short(parse_duration(cd.get("duration", ""))):
            continue
        if not available_in_ukraine(cd) or sn.get("channelId") == exclude_channel:
            continue
        if kids and not v.get("status", {}).get("madeForKids"):
            continue
        v_lang, v_conf, _ = source_language(sn)
        # Для англійської достатньо латиниці без кирилиці (0.7): у багатьох англомовних
        # відео автор не заповнює defaultAudioLanguage.
        min_conf = 0.7 if lang == "en" else C.LANG_THRESHOLD
        if v_lang != lang or v_conf < min_conf:
            continue
        good.append(v)
    if not good:
        return []

    texts = [C.embedding_text(v["snippet"]["title"], v["snippet"].get("tags"), v["snippet"].get("description")) for v in good]
    titles = [C.title_text(v["snippet"]["title"]) for v in good]
    vecs = embed_many(texts + titles)
    full_vecs, title_vecs = vecs[: len(good)], vecs[len(good):]
    out = []
    for v, fv, tv in zip(good, full_vecs, title_vecs):
        # Вектори нормовані, тож скалярний добуток і є косинусною схожістю.
        title_sim, full_sim = float(np.dot(tvec, tv)), float(np.dot(qvec, fv))
        score = combined(title_sim, full_sim)
        out.append(dict(video_id=v["id"], title=v["snippet"]["title"], tags=v["snippet"].get("tags"),
                        channel=v["snippet"].get("channelTitle", ""), channel_id=v["snippet"].get("channelId"),
                        title_sim=title_sim, full_sim=full_sim, score=score, rank=score))
    return out


# ---------------------------------------------------------------- Лог для підбору порогів


def log_answer(video_id, mode, src, response, stats):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    new = not os.path.exists(LOG_PATH)
    with open(LOG_PATH, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "video_id", "mode", "source_title", "source_lang", "source_conf", "level",
                        "best_score", "median_score", "margin", "alt1", "score1", "alt2", "score2", "alt3", "score3"])
        r3 = lambda x: round(x, 3) if x is not None else ""
        row = [dt.datetime.now().isoformat(timespec="seconds"), video_id, mode, src.get("title", ""),
               src.get("lang"), round(src.get("confidence", 0), 2), response["level"],
               r3(stats.get("best")), r3(stats.get("median")), r3(stats.get("margin"))]
        for it in response["items"][:3]:
            row += [it["title"], it["score"]]
        w.writerow(row)


# ---------------------------------------------------------------- Ендпоінти


@app.get("/health")
def health():
    with db() as conn:
        has_videos = conn.execute("select to_regclass('public.videos') is not null").fetchone()[0]
        has_channels = conn.execute("select to_regclass('public.channels') is not null").fetchone()[0]
        videos = conn.execute("select count(*) from videos").fetchone()[0] if has_videos else 0
        channels = conn.execute("select count(*) from channels").fetchone()[0] if has_channels else 0
        has_actions = conn.execute("select exists (select 1 from video_actions)").fetchone()[0]
        searches = conn.execute("select coalesce(max(calls), 0) from search_budget where day = current_date").fetchone()[0]
    return {"ok": True, "videos": videos, "channels": channels, "hasVideoActions": has_actions,
            "searchesToday": searches, "searchDailyLimit": C.SEARCH_DAILY_LIMIT}


@app.get("/alternatives")
def alternatives(
    videoId: str = Query(...),
    mode: str = Query("default", pattern="^(default|kids)$"),
    fresh: bool = False,
    debug: bool = False,
):
    """debug=true: свіжа відповідь без кешу й без пошуку YouTube (квота не витрачається),
    плюс 10 найкращих кандидатів з бази з усіма цифрами схожості. Для підбору порогів."""
    if not VIDEO_ID.match(videoId):
        raise HTTPException(400, "Некоректний videoId")
    kids = mode == "kids"
    fresh = fresh or debug
    timer = Timer()

    if not fresh:
        with db() as conn:
            cached = conn.execute(
                f"""select response from alternatives_cache
                    where video_id = %s and mode = %s and created_at > now() - interval '{C.CACHE_HOURS} hours'""",
                (videoId, mode),
            ).fetchone()
        if cached:
            print(f"[alt] {videoId} {mode} з кешу {timer.line()}", flush=True)
            return cached[0]
        timer.mark("cache")

    try:
        src = get_sources([videoId]).get(videoId)
    except YouTubeError as e:
        raise HTTPException(503, f"YouTube API: {e}")

    if not src:
        return {"source": {"lang": "unknown", "confidence": 0}, "level": 5, "items": []}

    timer.mark("source")
    source = {"lang": src["lang"], "confidence": round(float(src["confidence"]), 2)}
    stats, cands = {}, []

    if src["lang"] == "uk" and src["confidence"] >= C.LANG_THRESHOLD:
        # Відео вже українське: альтернативи не потрібні.
        response = {"source": source, "level": 0, "items": []}
    else:
        if not state["model"]:
            raise HTTPException(503, "Модель ембедінгів не завантажено")
        qvec, tvec = embed_many([C.embedding_text(src["title"], src["tags"], src["description"]),
                                 C.title_text(src["title"])])
        qwords = keywords(src["title"] + " " + " ".join(src["tags"]))
        timer.mark("embed")

        with db() as conn:
            cands = index_candidates(conn, qvec, tvec, videoId, src.get("channel_id"), kids)
            timer.mark("index")
            for c in cands:
                c["rank"] = c["score"] + keyword_bonus(qwords, c["title"], c["tags"])
            scores = sorted((c["score"] for c in cands), reverse=True)
            if scores:
                median = scores[len(scores) // 2]
                stats = {"best": scores[0], "median": median, "margin": scores[0] - median}
            # Найкраще відео ледь виділяється над фоном → жодне не підходить по-справжньому.
            weak = C.MIN_MARGIN > 0 and stats and stats["margin"] < C.MIN_MARGIN
            usable = [] if weak else cands

            level, items = 5, []
            median = stats.get("median", 0.0)
            same = pick([c for c in usable
                         if c["score"] >= C.SAME_TOPIC_STRONG or c["score"] - median >= C.SAME_TOPIC_MIN_MARGIN],
                        C.SAME_TOPIC_THRESHOLD)
            if same:  # 1. українське на ту саму тему з нашої бази
                # Решту місць у панелі заповнюємо відео «близької теми», щоб не показувати одне-єдине.
                level, items = 1, [item(c) for c in pick(usable, C.CLOSE_TOPIC_THRESHOLD)]
            else:
                found = [] if debug else youtube_search(
                    conn, src["title"], qvec, tvec, "uk", kids, videoId, src.get("channel_id"))
                timer.mark("search_uk")
                for c in found:
                    c["rank"] = c["score"] + keyword_bonus(qwords, c["title"], c["tags"])
                found_good = pick(found, C.CLOSE_TOPIC_THRESHOLD)
                # Усе від порогу «близької теми» і вище: сюди потрапляють і відео з високою схожістю,
                # які не пройшли додаткову перевірку рівня 1 (відрив від фону).
                close = pick(usable, C.CLOSE_TOPIC_THRESHOLD)
                if found_good:  # 2. українське через пошук YouTube
                    level, items = 2, [item(c) for c in found_good]
                elif close:  # 3. українське на близьку тему
                    level, items = 3, [item(c) for c in close]
                elif not debug:  # 4. англомовне на ту саму тему
                    en = youtube_search(conn, src["title"], qvec, tvec, "en", kids, videoId, src.get("channel_id"))
                    timer.mark("search_en")
                    for c in en:
                        c["rank"] = c["score"] + (0.05 if c["video_id"] in state["uk_dubs"] else 0)
                    en_good = pick(en, C.CLOSE_TOPIC_THRESHOLD)
                    if en_good:
                        level, items = 4, [item(c, "en") for c in en_good]
            response = {"source": source, "level": level, "items": items}

    if debug:
        r3 = lambda x: round(x, 3)
        response = dict(response)
        response["debug"] = {
            "timingsMs": dict(timer.parts, total=timer.total()),
            "sourceTitle": src["title"],
            "langSource": src.get("lang_source"),
            "queryText": C.embedding_text(src["title"], src["tags"], src["description"]),
            "thresholds": {"same": C.SAME_TOPIC_THRESHOLD, "sameMinMargin": C.SAME_TOPIC_MIN_MARGIN,
                           "sameStrong": C.SAME_TOPIC_STRONG,
                           "close": C.CLOSE_TOPIC_THRESHOLD,
                           "minMargin": C.MIN_MARGIN, "titleWeight": C.TITLE_WEIGHT},
            "stats": {k: r3(v) for k, v in stats.items()},
            "top": [
                {"title": c["title"], "channel": c["channel"], "score": r3(c["score"]),
                 "titleSim": r3(c["title_sim"]), "fullSim": r3(c["full_sim"]), "rank": r3(c["rank"])}
                for c in sorted(cands, key=lambda x: -x["rank"])[:10]
            ],
        }
        return response

    with db() as conn:
        conn.execute(
            """insert into alternatives_cache (video_id, mode, response, created_at) values (%s, %s, %s, now())
               on conflict (video_id, mode) do update set response = excluded.response, created_at = now()""",
            (videoId, mode, Jsonb(response)),
        )
    timer.mark("save")
    log_answer(videoId, mode, src, response, stats)
    print(f"[alt] {videoId} {mode} рівень {response['level']} {timer.line()}", flush=True)
    return response


class ClassifyBody(BaseModel):
    videoIds: List[str] = Field(..., max_length=50)


@app.post("/classify")
def classify(body: ClassifyBody):
    ids = [v for v in dict.fromkeys(body.videoIds) if VIDEO_ID.match(v)]
    try:
        sources = get_sources(ids)
    except YouTubeError as e:
        raise HTTPException(503, f"YouTube API: {e}")
    results = {}
    for v in ids:
        s = sources.get(v)
        results[v] = (
            {"lang": s["lang"], "confidence": round(float(s["confidence"]), 2), "source": s["lang_source"]}
            if s else {"lang": "unknown", "confidence": 0, "source": "not_found"}
        )
    return {"results": results}


class WishBody(BaseModel):
    installId: str = Field(..., min_length=8, max_length=64)
    videoId: str
    videoTitle: Optional[str] = Field(None, max_length=300)
    level: Optional[int] = None
    userText: Optional[str] = Field(None, max_length=C.WISH_TEXT_MAX)


@app.post("/wish")
def wish(body: WishBody):
    if not VIDEO_ID.match(body.videoId):
        raise HTTPException(400, "Некоректний videoId")
    with db() as conn:
        used = conn.execute(
            "select count(*) from wishes where install_id = %s and created_at > now() - interval '1 day'",
            (body.installId,),
        ).fetchone()[0]
        if used >= C.WISH_DAILY_LIMIT:
            return JSONResponse({"ok": False, "reason": "limit"}, status_code=429)
        conn.execute(
            """insert into wishes (install_id, video_id, video_title, user_text, level_reached)
               values (%s, %s, %s, %s, %s)""",
            (body.installId, body.videoId, body.videoTitle, (body.userText or "").strip() or None, body.level),
        )
    return {"ok": True}


EVENT_TYPES = {"panel_shown", "alt_clicked", "wish_sent", "kids_hidden_daily"}


class EventBody(BaseModel):
    installId: str = Field(..., min_length=8, max_length=64)
    type: str
    level: Optional[int] = None
    count: Optional[int] = Field(None, ge=0, le=100000)
    date: Optional[dt.date] = None
    ts: Optional[str] = None


@app.post("/events")
def events(body: EventBody):
    if body.type not in EVENT_TYPES:
        raise HTTPException(400, "Невідомий тип події")
    with db() as conn:
        conn.execute(
            "insert into events (install_id, type, level, count, day) values (%s, %s, %s, %s, %s)",
            (body.installId, body.type, body.level, body.count, body.date),
        )
    return {"ok": True}


class ActionItem(BaseModel):
    action: Literal["suggested", "hid"]
    videoId: str

    @field_validator("videoId")
    @classmethod
    def check_video_id(cls, v: str) -> str:
        if not VIDEO_ID.match(v):
            raise ValueError("Некоректний videoId")
        return v


class ActionsBody(BaseModel):
    installId: str = Field(..., min_length=8, max_length=64)
    items: List[ActionItem] = Field(..., min_length=1, max_length=100)


@app.post("/actions")
def actions(body: ActionsBody):
    rows = [(body.installId, item.action, item.videoId) for item in body.items]
    with db() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.executemany(
                    """insert into video_actions (install_id, action, video_id)
                       values (%s, %s, %s)""",
                    rows,
                )
    return {"ok": True, "inserted": len(rows)}


CREATORS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "creators")
if os.path.isdir(CREATORS_DIR):
    app.mount("/creators/assets", StaticFiles(directory=CREATORS_DIR), name="creators-assets")


@app.get("/", include_in_schema=False)
def root_redirect():
    return RedirectResponse(url="/creators")


@app.get("/creators", include_in_schema=False)
def creators_dashboard():
    index_path = os.path.join(CREATORS_DIR, "index.html")
    if not os.path.isfile(index_path):
        raise HTTPException(404, "Веб-інтерфейс для авторів не знайдено")
    return FileResponse(index_path)


@app.get("/api/creators/overview")
def creators_overview(
    days: Optional[int] = Query(None, ge=1, le=3650),
    topic_id: Optional[int] = Query(None, ge=1),
    level: Optional[int] = Query(None, ge=0, le=5),
    q: Optional[str] = Query(None, max_length=120),
):
    """Агрегована аналітика побажань із таблиці `wishes` для українських авторів."""
    with db() as conn:
        return wishes_pipeline.build_creators_analytics(
            conn,
            days=days,
            topic_id=topic_id,
            level=level,
            query=q,
        )


class ProcessWishesBody(BaseModel):
    force: bool = False


@app.post("/api/creators/process")
def creators_process(
    body: Optional[ProcessWishesBody] = None,
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    """Запускає класифікацію тем та оновлення метаданих для записів у `wishes`."""
    expected = (os.getenv("ADMIN_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(503, "ADMIN_TOKEN не налаштовано на сервері")
    if not x_admin_token or not hmac.compare_digest(x_admin_token, expected):
        raise HTTPException(403, "Недійсний токен адміністратора")

    force = bool(body.force) if body else False
    with db() as conn:
        updated = wishes_pipeline.process_unprocessed_wishes(conn, force=force)
        overview = wishes_pipeline.build_creators_analytics(conn)
    return {"ok": True, "processed": updated, "overview": overview}
