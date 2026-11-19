"""Збирає з сирих відповідей YouTube (data/raw/*.jsonl) готовий датасет Студії.

Що робить:
1. Об'єднує всі зібрані відео, прибирає дублікати.
2. Відсіює Shorts (до 3 хвилин) і трансляції.
3. Визначає мову відео (uk / ru / en) тим самим модулем, що й сервер розширення.
4. Визначає гру за назвою, тегами й описом (аліаси з games.json).
5. Рахує два вектори на відео моделлю e5-small: назва окремо і назва+теги+опис.

Результат у data/dataset/:
    videos.jsonl     — по одному відео на рядок; номер рядка = номер вектора
    emb_full.npy     — вектори назви+тегів+опису (float16)
    emb_title.npy    — вектори назв (float16)
    manifest.json    — коли зібрано, скільки відео за грою й мовою, модель

Запуск (з кореня репозиторію):
    backend\\.venv\\Scripts\\python studio\\data_tools\\build_dataset.py
"""

import datetime as dt
import json
import re
from collections import Counter

import numpy as np

from common import DATASET_DIR, RAW_DIR, load_games, read_jsonl, save_json

import config as C  # noqa: E402  (модуль бекенду)
from lang import source_language  # noqa: E402
from youtube import parse_duration  # noqa: E402

FOREIGN_MIN_CONF = {"en": 0.7, "ru": 0.8, "uk": 0.8}

# Коли мову звуку не вказано, «англійська» вгадується лише з того, що текст латиницею. Так проходять
# французькі, іспанські, португальські, німецькі відео; відсіюємо їх за діакритикою й службовими словами.
NON_EN_LETTERS = re.compile(r"[àâäçéèêëîïôöûùüÿñãõáíóúßœ]", re.IGNORECASE)
NON_EN_WORDS = {"le", "la", "les", "des", "du", "et", "avec", "pour", "sur", "dans", "el", "los", "las", "con",
                "para", "por", "una", "del", "com", "não", "uma", "der", "die", "das", "und", "mit", "ist", "nicht"}


def looks_non_english(text: str) -> bool:
    words = re.findall(r"[a-zà-ÿ]+", text.lower())
    return len(NON_EN_LETTERS.findall(text)) >= 2 or sum(w in NON_EN_WORDS for w in words) >= 2


def game_matcher(games):
    compiled = []
    for g in games:
        aliases = sorted(g["aliases"], key=len, reverse=True)
        pat = re.compile(r"(?<![\w])(" + "|".join(re.escape(a) for a in aliases) + r")(?![\w])", re.IGNORECASE)
        compiled.append((g["slug"], pat))

    def match(text):
        hits = Counter()
        for slug, pat in compiled:
            n = len(pat.findall(text))
            if n:
                hits[slug] = n
        return hits.most_common(1)[0][0] if hits else None

    return match


def load_raw():
    """video_id → {'video': ресурс API, 'sources': set, 'search_games': Counter, 'in_catalog': bool}"""
    merged = {}
    for path in sorted(RAW_DIR.glob("*.jsonl")):
        for row in read_jsonl(path):
            v = row["video"]
            entry = merged.setdefault(v["id"], {"video": v, "fetched_at": row["fetched_at"],
                                                "sources": set(), "search_games": Counter(), "in_catalog": False})
            if row["fetched_at"] >= entry["fetched_at"]:
                entry["video"], entry["fetched_at"] = v, row["fetched_at"]
            entry["sources"].add(row["source"])
            if row.get("game"):
                entry["search_games"][row["game"]] += 1
            if row["source"] == "catalog":
                entry["in_catalog"] = True
    return merged


def build_records(merged, games):
    match_game = game_matcher(games)
    records, dropped = [], Counter()
    for vid, e in merged.items():
        v = e["video"]
        sn, cd, st = v.get("snippet", {}), v.get("contentDetails", {}), v.get("statistics", {})
        seconds = parse_duration(cd.get("duration", ""))
        if sn.get("liveBroadcastContent", "none") != "none":
            dropped["трансляції"] += 1
            continue
        if seconds <= C.SHORTS_MAX_SECONDS:
            dropped["Shorts і відео до 3 хв"] += 1
            continue

        if e["in_catalog"]:
            lang, conf = "uk", 0.95  # каталог містить лише перевірені українські відео
        else:
            lang, conf, src = source_language(sn)
            if lang == "en" and src == "latin-only" and looks_non_english(f"{sn.get('title', '')} {sn.get('description', '')[:300]}"):
                lang = "other"
        if lang not in FOREIGN_MIN_CONF or conf < FOREIGN_MIN_CONF[lang]:
            dropped["мову не визначено або інша мова"] += 1
            continue

        title, tags = sn.get("title", ""), sn.get("tags") or []
        description = C.clean_description(sn.get("description", ""), limit=500)
        game = match_game(f"{title} {' '.join(tags)} {description[:300]}")
        if not game and e["search_games"]:
            game = e["search_games"].most_common(1)[0][0]  # відео знайдено пошуком саме цієї гри
        if not game:
            dropped["гру не визначено"] += 1
            continue

        records.append({
            "video_id": vid,
            "title": title,
            "description": description,
            "tags": tags[:15],
            "channel_id": sn.get("channelId"),
            "channel_title": sn.get("channelTitle", ""),
            "published_at": sn.get("publishedAt"),
            "duration_s": seconds,
            "views": int(st.get("viewCount", 0) or 0),
            "likes": int(st.get("likeCount", 0) or 0),
            "comments": int(st.get("commentCount", 0) or 0),
            "lang": lang,
            "game": game,
            "in_catalog": e["in_catalog"],
            "sources": sorted(e["sources"]),
            "fetched_at": e["fetched_at"],
        })
    return records, dropped


def main():
    games = load_games()
    merged = load_raw()
    if not merged:
        raise SystemExit("У data/raw немає даних. Спершу запустіть collect_youtube.py і export_ua_catalog.py.")
    records, dropped = build_records(merged, games)
    print(f"Сирих відео: {len(merged)}, у датасет: {len(records)}.")
    for reason, n in dropped.most_common():
        print(f"  відсіяно ({reason}): {n}")

    model = C.load_model()
    print("Рахую вектори…")
    emb_full = C.embed(model, [C.embedding_text(r["title"], r["tags"], r["description"]) for r in records])
    emb_title = C.embed(model, [C.title_text(r["title"]) for r in records])

    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    with open(DATASET_DIR / "videos.jsonl", "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    np.save(DATASET_DIR / "emb_full.npy", np.asarray(emb_full, dtype=np.float16))
    np.save(DATASET_DIR / "emb_title.npy", np.asarray(emb_title, dtype=np.float16))

    counts = Counter((r["game"], r["lang"]) for r in records)
    manifest = {
        "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "model": C.MODEL_NAME,
        "embed_prefix": C.EMBED_PREFIX,
        "algo_version": C.ALGO_VERSION,
        "videos": len(records),
        "by_game": {g["slug"]: {lang: counts.get((g["slug"], lang), 0) for lang in ("uk", "ru", "en")} for g in games},
        "oldest_fetch": min(r["fetched_at"] for r in records) if records else None,
    }
    save_json(DATASET_DIR / "manifest.json", manifest)

    print("\nВідео за грою (uk / ru / en):")
    for slug, c in manifest["by_game"].items():
        print(f"  {slug:12} {c['uk']:5} / {c['ru']:5} / {c['en']:5}")
    print(f"\nГотово: {DATASET_DIR}")


if __name__ == "__main__":
    main()
