"""Експорт українських ігрових відео з нашого каталогу (Supabase) у датасет Студії.

Бере з бази відео ігрових каналів і відео, у назві чи тегах яких згадано гру з games.json,
і дозапитує в YouTube API їхню статистику (перегляди, лайки, коментарі): у нашій базі її немає.
Результат: data/raw/ua_catalog.jsonl у тому самому форматі, що й collect_youtube.py.

Запуск (з кореня репозиторію):
    backend\\.venv\\Scripts\\python studio\\data_tools\\export_ua_catalog.py

Квота: 1 одиниця на 50 відео. Повторний запуск пропускає вже експортовані відео.
"""

import argparse
import datetime as dt
import os
import re
import sys

from common import RAW_DIR, append_jsonl, load_games, read_jsonl

import config as C  # noqa: E402  (модуль бекенду)
from youtube import YouTube, YouTubeError  # noqa: E402

OUT_PATH = RAW_DIR / "ua_catalog.jsonl"
GAMING_TOPICS = ("ігри", "игры", "games", "gaming", "геймінг")


def alias_pattern(games):
    aliases = sorted({a for g in games for a in g["aliases"]}, key=len, reverse=True)
    return re.compile(r"(?<![\w])(" + "|".join(re.escape(a) for a in aliases) + r")(?![\w])", re.IGNORECASE)


def main():
    p = argparse.ArgumentParser(description="Експорт українських ігрових відео з каталогу")
    p.add_argument("--max-units", type=int, default=500)
    args = p.parse_args()

    key = (os.getenv("YOUTUBE_API_KEY") or "").strip()
    if not key:
        sys.exit("Немає YOUTUBE_API_KEY у backend/.env")
    yt = YouTube(key)
    games = load_games()
    pattern = alias_pattern(games)

    conn = C.connect_db()
    rows = conn.execute(
        """select v.video_id, v.title, coalesce(array_to_string(v.tags, ' '), ''), coalesce(c.topic, '')
           from videos v join channels c using (channel_id)
           where v.lang = 'uk'"""
    ).fetchall()
    selected = [
        r[0] for r in rows
        if any(t in r[3].lower() for t in GAMING_TOPICS) or pattern.search(f"{r[1]} {r[2]}")
    ]
    done = {r["video"]["id"] for r in read_jsonl(OUT_PATH)} if OUT_PATH.exists() else set()
    todo = [v for v in selected if v not in done]
    print(f"Українських відео в каталозі: {len(rows)}, ігрових: {len(selected)}, ще не експортовано: {len(todo)}.")

    for i in range(0, len(todo), 50):
        if yt.units + 1 > args.max_units:
            print(f"Досягнуто ліміту --max-units {args.max_units}. Запустіть знову, прогрес збережено.")
            break
        chunk = todo[i : i + 50]
        try:
            data = yt.get("videos", part="snippet,contentDetails,statistics,status", id=",".join(chunk), maxResults=50)
        except YouTubeError as e:
            print(f"✗ {e}")
            break
        fetched_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        append_jsonl(OUT_PATH, [
            {"source": "catalog", "game": None, "query_lang": "uk", "query": None,
             "fetched_at": fetched_at, "video": v}
            for v in data.get("items", [])
        ])
        print(f"✓ {min(i + 50, len(todo))} / {len(todo)}")

    print(f"\nВитрачено квоти: {yt.units} одиниць. Дані: {OUT_PATH}")


if __name__ == "__main__":
    main()
