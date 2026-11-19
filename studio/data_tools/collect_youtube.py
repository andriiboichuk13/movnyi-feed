"""Збір популярних ігрових відео з YouTube для датасету Студії.

Для кожної гри з config/games.json і кожної мови (en, ru, uk) робить пошук популярних відео
за останні DAYS днів і забирає їхні метадані зі статистикою переглядів.
Відповіді YouTube API зберігаються без змін у data/raw/search_<гра>.jsonl.

Запуск (з кореня репозиторію):
    backend\\.venv\\Scripts\\python studio\\data_tools\\collect_youtube.py
    backend\\.venv\\Scripts\\python studio\\data_tools\\collect_youtube.py --games minecraft roblox --pages 2

Квота: пошук коштує 100 одиниць, метадані — 1 одиницю на 50 відео.
Одна сторінка (50 відео) ≈ 101 одиниця. За замовчуванням шукаються лише середні (4–20 хв) і довгі
(20+ хв) відео, бо серед популярних «будь-якої тривалості» переважають Shorts:
8 ігор × 3 мови × 2 тривалості × 1 сторінка ≈ 4 850 одиниць. Для іноземних відео: --langs en ru (≈ 3 230).
Скрипт зберігає прогрес після кожної сторінки й не перевищує --max-units, тож його можна
безпечно перезапускати: уже зібрані сторінки він пропускає.
"""

import argparse
import datetime as dt
import os
import sys

from common import RAW_DIR, append_jsonl, load_games, load_json, save_json

from youtube import YouTube, YouTubeError  # noqa: E402  (модуль бекенду)

STATE_PATH = RAW_DIR / "collect_state.json"
SEARCH_COST, VIDEOS_COST = 100, 1


def fetch_videos(yt, ids):
    """Повні метадані відео разом зі статистикою (перегляди, лайки, коментарі): 1 одиниця на 50 відео."""
    out = []
    for i in range(0, len(ids), 50):
        data = yt.get("videos", part="snippet,contentDetails,statistics,status", id=",".join(ids[i:i + 50]), maxResults=50)
        out += data.get("items", [])
    return out


def main():
    p = argparse.ArgumentParser(description="Збір ігрових відео з YouTube для Студії")
    p.add_argument("--games", nargs="*", help="slug ігор (за замовчуванням усі з games.json)")
    p.add_argument("--langs", nargs="*", default=["en", "ru", "uk"], choices=["en", "ru", "uk"])
    p.add_argument("--pages", type=int, default=1, help="скільки сторінок по 50 відео на мову (1–4)")
    p.add_argument("--durations", nargs="*", default=["medium", "long"], choices=["any", "medium", "long"],
                   help="тривалість відео в пошуку: medium = 4–20 хв, long = понад 20 хв, any = будь-яка "
                        "(з any у популярному переважають Shorts, які потім однаково відсіюються)")
    p.add_argument("--days", type=int, default=180, help="брати відео за останні N днів")
    p.add_argument("--max-units", type=int, default=3000, help="не витрачати більше одиниць квоти за запуск")
    args = p.parse_args()
    args.pages = max(1, min(4, args.pages))

    key = (os.getenv("YOUTUBE_API_KEY") or "").strip()
    if not key:
        sys.exit("Немає YOUTUBE_API_KEY у backend/.env")
    yt = YouTube(key)

    games = load_games()
    if args.games:
        unknown = set(args.games) - {g["slug"] for g in games}
        if unknown:
            sys.exit(f"Невідомі ігри: {', '.join(sorted(unknown))}. Дивіться studio/config/games.json")
        games = [g for g in games if g["slug"] in args.games]

    state = load_json(STATE_PATH, {"done": {}, "tokens": {}})
    published_after = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def step_key(slug, lang, dur, page):
        # Для any — старий формат ключа, щоб уже зібрані сторінки не збирались повторно.
        return f"{slug}|{lang}|{page}" if dur == "any" else f"{slug}|{lang}|{dur}|{page}"

    steps = [
        (game, lang, dur, page)
        for game in games
        for lang in args.langs if game["queries"].get(lang)
        for dur in args.durations
        for page in range(args.pages)
    ]
    for game, lang, dur, page in steps:
        query = game["queries"][lang]
        step = step_key(game["slug"], lang, dur, page)
        prev = step_key(game["slug"], lang, dur, page - 1)
        if step in state["done"]:
            continue
        if page > 0 and not state["tokens"].get(prev):
            continue  # попередня сторінка була останньою
        if yt.units + SEARCH_COST + VIDEOS_COST > args.max_units:
            print(f"Досягнуто ліміту --max-units {args.max_units}. Запустіть знову пізніше, прогрес збережено.")
            break

        params = dict(part="id", type="video", q=query, maxResults=50, order="viewCount",
                      publishedAfter=published_after, relevanceLanguage=lang)
        if lang == "uk":
            params["regionCode"] = "UA"
        if dur != "any":
            params["videoDuration"] = dur
        if page > 0:
            params["pageToken"] = state["tokens"][prev]
        try:
            found = yt.get("search", **params)
            ids = [it["id"]["videoId"] for it in found.get("items", []) if it.get("id", {}).get("videoId")]
            videos = fetch_videos(yt, ids)
        except YouTubeError as e:
            print(f"✗ {step}: {e}")
            if "quotaExceeded" in str(e) or "keyInvalid" in str(e):
                break
            continue

        fetched_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        append_jsonl(RAW_DIR / f"search_{game['slug']}.jsonl", [
            {"source": "search", "game": game["slug"], "query_lang": lang, "query": query, "duration": dur,
             "fetched_at": fetched_at, "video": v}
            for v in videos
        ])
        state["done"][step] = {"videos": len(videos), "fetched_at": fetched_at}
        state["tokens"][step] = found.get("nextPageToken")
        save_json(STATE_PATH, state)
        print(f"✓ {game['name']} · {lang} · {dur} · сторінка {page + 1}: {len(videos)} відео")

    print(f"\nВитрачено квоти: {yt.units} одиниць. Дані: {RAW_DIR}")


if __name__ == "__main__":
    main()