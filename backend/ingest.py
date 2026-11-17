"""Наповнення бази українських відео.

Що робить:
1. Читає таблицю каналів (Google Sheets за посиланням або локальний CSV).
2. Бере лише канали зі status = approved.
3. Для кожного каналу отримує останні відео через YouTube Data API.
4. Відкидає Shorts (усе до 3 хвилин), трансляції, відео недоступні в Україні, відео до uk_since
   і відео, які виявилися не українською.
5. Рахує вектори моделлю e5-small і записує все в Postgres (Supabase).
6. Видаляє дані старші за 30 днів (правила YouTube API).

Запуск:  .venv\\Scripts\\python ingest.py
Квота: приблизно 3 одиниці на канал (з 10 000 на день).
"""

import csv
import datetime as dt
import io
import re
import sys
import urllib.parse

import requests

import hashlib

from config import (
    ALGO_VERSION,
    DESCRIPTION_STORE_CHARS,
    EMBED_DIM,
    EMBED_PREFIX,
    KIDS_SHORTS_MAX_SECONDS,
    MODEL_NAME,
    SHORTS_MAX_SECONDS,
    MAX_VIDEOS_PER_CHANNEL,
    RETENTION_DAYS,
    connect_db,
    embed,
    embedding_text,
    env,
    load_model,
    title_text,
)
from lang import video_language
from youtube import YouTube, YouTubeError, available_in_ukraine, is_short, parse_duration, published_at


# ---------------------------------------------------------------- Таблиця


def sheet_csv_url(link: str) -> str:
    """Звичайне посилання на Google Sheets → посилання на CSV-експорт того самого аркуша."""
    m = re.search(r"/spreadsheets/d/([\w-]+)", link)
    if not m or "export" in link or "output=csv" in link:
        return link
    url = f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv"
    gid = re.search(r"[#&?]gid=(\d+)", link)
    if gid:
        url += f"&gid={gid.group(1)}"
    return url


def load_sheet(source: str):
    if source.lower().startswith("http"):
        r = requests.get(sheet_csv_url(source), timeout=30)
        ctype = r.headers.get("content-type", "")
        if r.status_code != 200 or "csv" not in ctype:
            sys.exit(
                "Не вдалося прочитати таблицю за посиланням.\n"
                "Перевірте в Google Sheets: «Поділитися» → «Загальний доступ» → "
                "«Усі, хто має посилання» → «Читач»."
            )
        r.encoding = "utf-8"
        text = r.text
    else:
        with open(source, encoding="utf-8-sig") as f:
            text = f.read()

    rows = []
    for raw in csv.DictReader(io.StringIO(text)):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        if row.get("channel_url"):
            rows.append(row)
    return rows


def parse_date(value: str):
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    print(f"  ⚠ Не розібрав дату «{value}» в uk_since. Пишіть у форматі 2024-03-01.")
    return None


def is_yes(value: str) -> bool:
    return (value or "").strip().lower() in ("yes", "так", "y", "true", "1", "+")


# ---------------------------------------------------------------- Канали й відео


def parse_channel_ref(link: str):
    """Посилання на канал → (тип, значення) для channels.list, або None."""
    link = urllib.parse.unquote(link.strip())
    if link.startswith("UC") and len(link) == 24:
        return "id", link
    if link.startswith("@"):
        return "handle", link

    path = urllib.parse.urlparse(link if "://" in link else "https://" + link).path
    parts = [p for p in path.split("/") if p]
    if not parts:
        return None
    if parts[0] == "channel" and len(parts) > 1:
        return "id", parts[1]
    if parts[0].startswith("@"):
        return "handle", parts[0]
    if parts[0] == "user" and len(parts) > 1:
        return "username", parts[1]
    return None  # /c/назва та інші старі формати API не вміє знаходити дешево


def resolve_channel(yt: YouTube, ref):
    kind, value = ref
    params = {"part": "snippet,contentDetails"}
    params[{"id": "id", "handle": "forHandle", "username": "forUsername"}[kind]] = value
    items = yt.get("channels", **params).get("items") or []
    if not items:
        return None
    c = items[0]
    return {
        "id": c["id"],
        "title": c["snippet"]["title"],
        "uploads": c["contentDetails"]["relatedPlaylists"]["uploads"],
    }


def fetch_channel_videos(yt: YouTube, channel: dict, uk_since, kids: bool = False):
    """Повертає (відібрані відео, статистика відсіву).

    Бере до MAX_VIDEOS_PER_CHANNEL останніх відео: playlistItems.list віддає по 50 за сторінку,
    тож 200 відео коштують 4 + 4 одиниці квоти (сторінки + метадані).
    """
    stats = {"total": 0, "shorts": 0, "live": 0, "blocked": 0, "before_uk_since": 0, "not_uk": 0}
    shorts_limit = KIDS_SHORTS_MAX_SECONDS if kids else SHORTS_MAX_SECONDS

    ids, token = [], None
    while len(ids) < MAX_VIDEOS_PER_CHANNEL:
        params = dict(part="contentDetails", playlistId=channel["uploads"], maxResults=50)
        if token:
            params["pageToken"] = token
        try:
            pl = yt.get("playlistItems", **params)
        except YouTubeError as e:
            if "playlistNotFound" in str(e):
                break  # канал без відео
            raise
        ids += [it["contentDetails"]["videoId"] for it in pl.get("items", [])]
        token = pl.get("nextPageToken")
        if not token:
            break
    ids = ids[:MAX_VIDEOS_PER_CHANNEL]
    if not ids:
        return [], stats

    items = []
    for i in range(0, len(ids), 50):
        items += yt.videos(ids[i : i + 50])

    kept = []
    for v in items:
        stats["total"] += 1
        sn, cd, st = v["snippet"], v["contentDetails"], v.get("status", {})
        title, description = sn.get("title", ""), sn.get("description", "")
        seconds = parse_duration(cd.get("duration", ""))
        published = published_at(sn)

        if sn.get("liveBroadcastContent", "none") != "none":
            stats["live"] += 1
            continue
        if is_short(seconds, shorts_limit):
            stats["shorts"] += 1
            continue
        if not available_in_ukraine(cd):
            stats["blocked"] += 1
            continue
        if uk_since and published.date() < uk_since:
            stats["before_uk_since"] += 1
            continue

        lang, lang_source = video_language(sn)
        if lang not in ("uk", "unknown"):
            stats["not_uk"] += 1
            continue
        if lang == "unknown":
            # Канал перевірила людина, тож невизначене відео вважаємо українським.
            lang, lang_source = "uk", "channel"

        kept.append(
            {
                "video_id": v["id"],
                "channel_id": channel["id"],
                "title": title,
                "description": description[:DESCRIPTION_STORE_CHARS],
                "tags": sn.get("tags") or [],
                "published_at": published,
                "duration_s": seconds,
                "made_for_kids": bool(st.get("madeForKids")),
                "lang": lang,
                "lang_source": lang_source,
            }
        )
    return kept, stats


# ---------------------------------------------------------------- База даних

SCHEMA = [
    """create table if not exists channels (
        channel_id text primary key,
        title text not null,
        url text,
        topic text,
        kids boolean not null default false,
        uk_since date,
        updated_at timestamptz not null default now()
    )""",
    f"""create table if not exists videos (
        video_id text primary key,
        channel_id text not null references channels(channel_id) on delete cascade,
        title text not null,
        description text,
        tags text[],
        published_at timestamptz,
        duration_s integer,
        made_for_kids boolean not null default false,
        lang text not null,
        lang_source text,
        embedding vector({EMBED_DIM}) not null,
        fetched_at timestamptz not null default now()
    )""",
    # Окремий вектор лише для назви (з версії алгоритму 2).
    f"alter table videos add column if not exists title_embedding vector({EMBED_DIM})",
    # Відбиток тексту: якщо назва/опис/теги не змінились, вектор не перераховуємо.
    "alter table videos add column if not exists text_hash text",
    # Сервер шукає точним перебором, приблизний індекс лише займає місце в базі.
    "drop index if exists videos_embedding_idx",
    "create index if not exists videos_channel_idx on videos (channel_id)",
]


def save(conn, channels: list, videos: list, embeddings, title_embeddings=None):
    if title_embeddings is None:
        title_embeddings = [None] * len(videos)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.executemany(
                """insert into channels (channel_id, title, url, topic, kids, uk_since, updated_at)
                   values (%s, %s, %s, %s, %s, %s, now())
                   on conflict (channel_id) do update set
                     title = excluded.title, url = excluded.url, topic = excluded.topic,
                     kids = excluded.kids, uk_since = excluded.uk_since, updated_at = now()""",
                [(c["id"], c["title"], c["url"], c["topic"], c["kids"], c["uk_since"]) for c in channels],
            )
            cur.executemany(
                """insert into videos (video_id, channel_id, title, description, tags, published_at,
                                       duration_s, made_for_kids, lang, lang_source, embedding,
                                       title_embedding, text_hash, fetched_at)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                   on conflict (video_id) do update set
                     channel_id = excluded.channel_id, title = excluded.title,
                     description = excluded.description, tags = excluded.tags,
                     published_at = excluded.published_at, duration_s = excluded.duration_s,
                     made_for_kids = excluded.made_for_kids, lang = excluded.lang,
                     lang_source = excluded.lang_source, embedding = excluded.embedding,
                     title_embedding = excluded.title_embedding, text_hash = excluded.text_hash,
                     fetched_at = now()""",
                [
                    (
                        v["video_id"], v["channel_id"], v["title"], v["description"], v["tags"],
                        v["published_at"], v["duration_s"], v["made_for_kids"], v["lang"],
                        v["lang_source"], emb, temb, v.get("text_hash"),
                    )
                    for v, emb, temb in zip(videos, embeddings, title_embeddings)
                ],
            )


def text_hash(v: dict) -> str:
    """Відбиток усього, від чого залежать вектори. Змінилась модель, алгоритм чи текст — новий відбиток."""
    raw = "\x1f".join([MODEL_NAME, EMBED_PREFIX, str(ALGO_VERSION), title_text(v["title"]),
                        embedding_text(v["title"], v["tags"], v["description"])])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def split_by_hash(conn, videos: list):
    """(відео, яким потрібні нові вектори; відео без змін)."""
    rows = conn.execute(
        "select video_id, text_hash, title_embedding is not null from videos where video_id = any(%s)",
        ([v["video_id"] for v in videos],),
    ).fetchall()
    known = {r[0]: (r[1], r[2]) for r in rows}
    fresh, same = [], []
    for v in videos:
        old_hash, has_title = known.get(v["video_id"], (None, False))
        (same if old_hash == v["text_hash"] and has_title else fresh).append(v)
    return fresh, same


def touch(conn, channels: list, videos: list):
    """Відео без змін: оновлюємо лише метадані й дату, вектори лишаємо."""
    with conn.transaction():
        with conn.cursor() as cur:
            cur.executemany(
                """insert into channels (channel_id, title, url, topic, kids, uk_since, updated_at)
                   values (%s, %s, %s, %s, %s, %s, now())
                   on conflict (channel_id) do update set
                     title = excluded.title, url = excluded.url, topic = excluded.topic,
                     kids = excluded.kids, uk_since = excluded.uk_since, updated_at = now()""",
                [(c["id"], c["title"], c["url"], c["topic"], c["kids"], c["uk_since"]) for c in channels],
            )
            if videos:
                cur.executemany(
                    """update videos set published_at = %s, duration_s = %s, made_for_kids = %s,
                                         lang = %s, lang_source = %s, fetched_at = now()
                       where video_id = %s""",
                    [(v["published_at"], v["duration_s"], v["made_for_kids"], v["lang"], v["lang_source"],
                      v["video_id"]) for v in videos],
                )


def drop_stale(conn, channels: list, videos: list):
    """Відео оброблених каналів, які цього разу не пройшли відбір (наприклад, Shorts за новим
    правилом), прибираємо, щоб база точно відповідала поточним правилам."""
    return conn.execute(
        "delete from videos where channel_id = any(%s) and not (video_id = any(%s))",
        ([c["id"] for c in channels], [v["video_id"] for v in videos]),
    ).rowcount


def cleanup(conn, active_channel_ids: list, had_errors: bool):
    deleted = conn.execute(
        f"delete from videos where fetched_at < now() - interval '{RETENTION_DAYS} days'"
    ).rowcount
    removed_channels = 0
    # Канали, яких більше немає серед approved, прибираємо лише якщо прогін пройшов без помилок,
    # щоб тимчасовий збій API не стер половину бази.
    if not had_errors:
        removed_channels = conn.execute(
            "delete from channels where not (channel_id = any(%s))", (active_channel_ids,)
        ).rowcount
    return deleted, removed_channels


# ---------------------------------------------------------------- Головне


def main():
    yt = YouTube(env("YOUTUBE_API_KEY"))
    env("DATABASE_URL")
    rows = load_sheet(env("CHANNELS_SHEET"))
    approved = [r for r in rows if r.get("status", "").lower() == "approved"]
    print(f"Каналів у таблиці: {len(rows)}, зі статусом approved: {len(approved)}.\n")
    if not approved:
        sys.exit("Немає жодного каналу зі status = approved. Нічого робити.")

    channels, videos, errors = [], [], 0
    for row in approved:
        label = row.get("name") or row["channel_url"]
        ref = parse_channel_ref(row["channel_url"])
        if not ref:
            print(f"✗ {label}: не розумію посилання. Потрібне /@назва або /channel/UC…")
            errors += 1
            continue
        try:
            ch = resolve_channel(yt, ref)
            if not ch:
                print(f"✗ {label}: канал не знайдено на YouTube.")
                errors += 1
                continue
            kept, s = fetch_channel_videos(yt, ch, parse_date(row.get("uk_since")), is_yes(row.get("kids")))
        except YouTubeError as e:
            print(f"✗ {label}: помилка YouTube API: {e}")
            errors += 1
            if "quotaExceeded" in str(e) or "keyInvalid" in str(e) or "API_KEY" in str(e):
                print("  Далі немає сенсу продовжувати. Перевірте ключ або квоту.")
                break
            continue

        ch.update(
            url=row["channel_url"],
            topic=row.get("topic") or None,
            kids=is_yes(row.get("kids")),
            uk_since=parse_date(row.get("uk_since")),
        )
        channels.append(ch)
        videos.extend(kept)
        dropped = ", ".join(
            f"{name} {s[key]}"
            for key, name in [("shorts", "Shorts"), ("live", "трансляції"), ("blocked", "недоступні в UA"),
                              ("before_uk_since", "до uk_since"), ("not_uk", "не українською")]
            if s[key]
        )
        print(f"✓ {ch['title']}: {len(kept)} з {s['total']} відео" + (f" (відсіяно: {dropped})" if dropped else ""))

    print(f"\nВитрачено квоти YouTube API: {yt.units} з 10 000 одиниць на день.")
    if not videos:
        sys.exit("Жодного відео не відібрано, базу не змінюю.")

    for v in videos:
        v["text_hash"] = text_hash(v)

    conn = connect_db()
    for stmt in SCHEMA:
        conn.execute(stmt)
    fresh, same = split_by_hash(conn, videos)
    print(f"Нових або змінених відео: {len(fresh)}, без змін: {len(same)}.")

    touch(conn, channels, same)
    if fresh:
        model = load_model()
        print(f"Рахую вектори для {len(fresh)} відео…")
        embeddings = embed(model, [embedding_text(v["title"], v["tags"], v["description"]) for v in fresh])
        title_embeddings = embed(model, [title_text(v["title"]) for v in fresh])
        print("Записую в базу…")
        for i in range(0, len(fresh), 500):  # частинами, щоб не тримати одну величезну транзакцію
            save(conn, channels if i == 0 else [], fresh[i : i + 500],
                 embeddings[i : i + 500], title_embeddings[i : i + 500])
    stale = drop_stale(conn, channels, videos)
    old, removed = cleanup(conn, [c["id"] for c in channels], errors > 0)

    total = conn.execute("select count(*) from videos").fetchone()[0]
    print(f"\nГотово. Оброблено каналів: {len(channels)}. Усього відео в базі: {total}.")
    if stale:
        print(f"Прибрано {stale} відео, які більше не проходять відбір (Shorts тощо).")
    if old:
        print(f"Видалено {old} відео, старших за {RETENTION_DAYS} днів.")
    if removed:
        print(f"Прибрано {removed} каналів, яких більше немає серед approved.")
    if errors:
        print(f"Каналів з помилками: {errors}. Подивіться повідомлення з ✗ вище.")


if __name__ == "__main__":
    main()
