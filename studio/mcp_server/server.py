"""MCP-сервер «Студія»: аналіз українського покриття ігрових ніш на YouTube.

Окремий процес, працює лише з локальним датасетом (studio/data/dataset/), без мережі.

Запуск (з кореня репозиторію):
    backend\\.venv\\Scripts\\python -m studio.mcp_server.server
    backend\\.venv\\Scripts\\python -m studio.mcp_server.server --port 8100 --dataset studio/data/dataset

Адреса для клієнта MCP: http://127.0.0.1:8100/mcp (транспорт streamable HTTP).
Сервер побудований на FastMCP (пакет fastmcp).
"""

import argparse
import datetime as dt
import json
import re
import sys
from collections import Counter
from enum import Enum
from pathlib import Path
from typing import Annotated, List, Optional

import numpy as np
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from . import rules as R
from .dataset import GAMES_PATH, STUDIO_DIR, Dataset, DatasetError
from .models import (
    ContentGap, ContentGaps, DatasetInfo, GameInfo, IdeaScore, LangStats, NicheCoverage,
    PackageIssue, PackageReport, SavedPlan, ScoreComponent, VideoRef,
)

sys.path.insert(0, str(STUDIO_DIR.parent / "backend"))
from lang import detect_text  # noqa: E402  (той самий модуль визначення мови, що в сервері розширення)

PLANS_DIR = STUDIO_DIR / "data" / "plans"

with open(GAMES_PATH, encoding="utf-8") as _f:
    _GAMES = json.load(_f)["games"]
# Перелік ігор — enum у схемі кожного інструмента: модель не може передати гру, якої немає в датасеті.
Game = Enum("Game", {g["slug"]: g["slug"] for g in _GAMES}, type=str)

mcp = FastMCP(
    "ukrainian-gaming-studio",
    instructions=(
        "Інструменти для українських ігрових YouTube-креаторів. Дані — локальний знімок популярних "
        "англо-, російсько- й україномовних відео за грою. Усі часові вікна відраховуються від дати "
        "збирання датасету (as_of). Помилки повертаються як помилки інструмента з кодом на початку "
        "повідомлення; порожній список у результаті означає «нічого не знайдено», а не збій."
    ),
)
_state = {"dataset": None}


def ds() -> Dataset:
    if _state["dataset"] is None:
        raise ToolError("DATASET_UNAVAILABLE: датасет не завантажено. Запустіть studio/data_tools/build_dataset.py і перезапустіть сервер.")
    return _state["dataset"]


def _median(values):
    return int(np.median(values)) if len(values) else 0


def _lang_stats(d: Dataset, rows, sims=None) -> LangStats:
    order = rows[np.argsort(-d.views[rows])] if len(rows) else rows
    top = []
    for i in order[:3]:
        s = None if sims is None else sims[int(i)]
        top.append(VideoRef(**d.ref(i, s)))
    latest = max((d.videos[int(i)]["published_at"][:10] for i in rows), default=None)
    return LangStats(
        videos=int(len(rows)),
        channels=len({d.videos[int(i)]["channel_id"] for i in rows}),
        total_views=int(d.views[rows].sum()) if len(rows) else 0,
        median_views=_median(d.views[rows]),
        latest_published=latest,
        top_videos=top,
    )


# ---------------------------------------------------------------- 1. Покриття ніші

@mcp.tool(
    name="analyze_niche_coverage",
    description=(
        "Порівнює, скільки відео про гру (або про тему в межах гри) є українською, російською й англійською "
        "за однаковий період, і робить висновок, чи ніша недопокрита українською. Використовуй першим, "
        "щоб зрозуміти загальну ситуацію в грі чи темі. topic — коротка тема довільною мовою "
        "(напр. «виживання 100 днів»); без topic аналізується вся гра. Відео рахується «про тему», якщо його "
        "схожість перевищує медіану схожості відео тієї ж мови на margin (компенсує те, що тексти однією мовою "
        "здаються моделі ближчими)."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
def analyze_niche_coverage(
    game: Annotated[Game, Field(description="Гра з датасету")],
    topic: Annotated[Optional[str], Field(min_length=2, max_length=120, description="Тема в межах гри; не вказано — вся гра")] = None,
    window_days: Annotated[int, Field(ge=30, le=365, description="Період у днях до дати датасету")] = 180,
    min_similarity: Annotated[float, Field(ge=0.70, le=0.95, description="Нижня межа схожості відео з темою")] = R.TOPIC_MIN_SIMILARITY,
    margin: Annotated[float, Field(ge=0.0, le=0.10, description="На скільки схожість має перевищувати медіану відео тієї ж мови")] = R.TOPIC_MARGIN,
) -> NicheCoverage:
    d = ds()
    game = game.value
    rows = d.rows(game, window_days=window_days)
    sims, thresholds = None, None
    if topic:
        qf, qt = d.embed_query(topic)
        s = d.query_similarity(qf, qt, rows)
        sims = dict(zip(rows.tolist(), s.tolist()))
        keep = np.zeros(len(rows), dtype=bool)
        thresholds = {}
        for lang in ("uk", "ru", "en"):
            m = d.lang[rows] == lang
            thresholds[lang] = round(R.topic_threshold(s[m], min_similarity, margin), 3)
            keep |= m & (s >= thresholds[lang])
        rows = rows[keep]

    by_lang = {lang: _lang_stats(d, rows[d.lang[rows] == lang], sims) for lang in ("uk", "ru", "en")}
    uk, foreign = by_lang["uk"].videos, by_lang["ru"].videos + by_lang["en"].videos
    verdict = R.coverage_verdict(uk, foreign)
    return NicheCoverage(
        game=game,
        topic=topic,
        window_days=window_days,
        as_of=d.as_of.date().isoformat(),
        min_similarity=min_similarity if topic else None,
        margin=margin if topic else None,
        thresholds=thresholds,
        by_lang=by_lang,
        uk_to_foreign_ratio=round(uk / foreign, 3) if foreign else None,
        verdict=verdict,
        verdict_rule=(
            f"insufficient_data: іноземних < {R.MIN_FOREIGN_FOR_VERDICT}; underserved: українських ≤ "
            f"max(1, {R.UNDERSERVED_RATIO:.0%} іноземних); saturated: українських ≥ {R.SATURATED_RATIO:.0%} іноземних; "
            "інакше competitive."
        ),
        notes=[
            "Іноземні відео — популярні (за переглядами) середні й довгі відео з пошуку YouTube, не всі відео гри.",
            "Українські — каталог перевірених україномовних каналів + пошук українською; відео поза ними не враховано.",
        ],
    )


# ---------------------------------------------------------------- 2. Прогалини

@mcp.tool(
    name="find_content_gaps",
    description=(
        "Шукає підтеми гри, які популярні англійською й російською, але не мають близького українського відео. "
        "Групує популярні іноземні відео за змістом і для кожної групи шукає найближче українське відео "
        "гри; якщо схожість нижча за covered_similarity — це прогалина. Підтеми з менш ніж min_videos відео "
        "або min_channels каналів пропускаються (одне вірусне відео — ще не тренд). Результат упорядкований за "
        "сумарними переглядами. Порожній список gaps — прогалин немає (успіх, не помилка)."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
def find_content_gaps(
    game: Annotated[Game, Field(description="Гра з датасету")],
    window_days: Annotated[int, Field(ge=30, le=365, description="Брати іноземні відео за стільки днів до дати датасету")] = 180,
    min_foreign_views: Annotated[int, Field(ge=0, le=100_000_000, description="Мінімум переглядів іноземного відео")] = 50_000,
    covered_similarity: Annotated[float, Field(ge=0.80, le=0.95, description="З якої схожості українське відео вважається таким, що покриває підтему")] = R.COVERED_SIMILARITY,
    max_gaps: Annotated[int, Field(ge=1, le=10, description="Скільки прогалин повернути")] = 5,
    min_videos: Annotated[int, Field(ge=1, le=20, description="Мінімум іноземних відео в підтемі")] = 2,
    min_channels: Annotated[int, Field(ge=1, le=10, description="Мінімум різних каналів у підтемі: тренд — коли тему знімають кілька авторів, а не одне вірусне відео")] = 2,
) -> ContentGaps:
    from sklearn.cluster import AgglomerativeClustering

    d = ds()
    game = game.value
    foreign = d.rows(game, langs=("ru", "en"), window_days=window_days, min_views=min_foreign_views)
    uk = d.rows(game, langs=("uk",))  # українське покриття — за весь час: старе відео теж закриває тему
    base = dict(game=game, window_days=window_days, as_of=d.as_of.date().isoformat(),
                min_foreign_views=min_foreign_views, covered_similarity=covered_similarity,
                min_videos=min_videos, min_channels=min_channels)
    if len(foreign) == 0:
        return ContentGaps(**base, foreign_videos_considered=0, subtopics_found=0, covered_subtopics=0,
                           small_subtopics_skipped=0, gaps=[])

    if len(foreign) == 1:
        labels = np.array([0])
    else:
        labels = AgglomerativeClustering(
            n_clusters=None, metric="cosine", linkage="average", distance_threshold=R.CLUSTER_DISTANCE,
        ).fit_predict(d.emb_full[foreign])

    uk_sim = d.pair_similarity(foreign, uk) if len(uk) else None
    gaps, covered, small = [], 0, 0
    for label in sorted(set(labels.tolist())):
        idx = np.nonzero(labels == label)[0]
        members = foreign[idx]
        channels = {d.videos[int(i)]["channel_id"] for i in members}
        if len(members) < min_videos or len(channels) < min_channels:
            small += 1
            continue
        best_uk = None
        if uk_sim is not None:
            sub = uk_sim[idx]
            flat = int(np.argmax(sub))
            r, c = divmod(flat, sub.shape[1])
            best_uk = (int(uk[c]), float(sub[r, c]))
        if best_uk and best_uk[1] >= covered_similarity:
            covered += 1
            continue
        order = members[np.argsort(-d.views[members])]
        langs = Counter(d.lang[members].tolist())
        gaps.append(ContentGap(
            gap_id=f"{game}-{label}",
            representative_title=d.videos[int(order[0])]["title"],
            foreign_videos=int(len(members)),
            channels=len(channels),
            by_lang={"ru": langs.get("ru", 0), "en": langs.get("en", 0)},
            total_views=int(d.views[members].sum()),
            median_views=_median(d.views[members]),
            newest_published=max(d.videos[int(i)]["published_at"][:10] for i in members),
            examples=[VideoRef(**d.ref(i)) for i in order[:3]],
            closest_uk=VideoRef(**d.ref(best_uk[0], best_uk[1])) if best_uk else None,
        ))
    gaps.sort(key=lambda g: -g.total_views)
    return ContentGaps(**base, foreign_videos_considered=int(len(foreign)), subtopics_found=len(set(labels.tolist())),
                       covered_subtopics=covered, small_subtopics_skipped=small, gaps=gaps[:max_gaps])


# ---------------------------------------------------------------- 3. Оцінка ідеї

@mcp.tool(
    name="score_video_idea",
    description=(
        "Оцінює конкретну ідею відео для гри за трьома складовими з явними правилами: попит (перегляди схожих "
        "іноземних відео за період), конкуренція (кількість схожих українських відео) і свіжість (частка схожих "
        "іноземних відео за останні 30 днів). Схожість рахується відносно медіани відео тієї ж мови, тож мова, "
        "якою написана ідея, майже не впливає на результат. Повертає рівні, числа, правило висновку й відео-докази. "
        "Використовуй, щоб порівняти кілька ідей або перевірити ідею креатора."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
def score_video_idea(
    game: Annotated[Game, Field(description="Гра з датасету")],
    idea: Annotated[str, Field(min_length=10, max_length=300, description="Ідея відео одним-двома реченнями, будь-якою мовою")],
    window_days: Annotated[int, Field(ge=30, le=365, description="Період для попиту й свіжості")] = 180,
    min_similarity: Annotated[float, Field(ge=0.70, le=0.95, description="Нижня межа схожості відео з ідеєю")] = R.TOPIC_MIN_SIMILARITY,
    margin: Annotated[float, Field(ge=0.0, le=0.10, description="На скільки схожість має перевищувати медіану відео тієї ж мови")] = R.TOPIC_MARGIN,
) -> IdeaScore:
    d = ds()
    game = game.value
    qf, qt = d.embed_query(idea)

    # Іноземні: поріг окремо для ru і en (кожна мова зі своєю медіаною), потім об'єднуємо.
    foreign = d.rows(game, langs=("ru", "en"), window_days=window_days)
    f_sim = d.query_similarity(qf, qt, foreign)
    f_keep = np.zeros(len(foreign), dtype=bool)
    f_thr = []
    for lang in ("ru", "en"):
        m = d.lang[foreign] == lang
        thr = R.topic_threshold(f_sim[m], min_similarity, margin)
        f_thr.append(thr)
        f_keep |= m & (f_sim >= thr)
    f_rows, f_sims = foreign[f_keep], f_sim[f_keep]

    uk = d.rows(game, langs=("uk",))
    u_sim = d.query_similarity(qf, qt, uk)
    u_thr = R.topic_threshold(u_sim, min_similarity, margin)
    u_rows, u_sims = uk[u_sim >= u_thr], u_sim[u_sim >= u_thr]

    total_views = int(d.views[f_rows].sum()) if len(f_rows) else 0
    fresh_share = float((d.age_days(f_rows) <= R.FRESH_DAYS).mean()) if len(f_rows) else 0.0
    demand = R.demand_level(total_views)
    competition = R.competition_level(int(len(u_rows)))
    freshness = R.freshness_level(fresh_share, len(f_rows) > 0)
    verdict = R.idea_verdict(demand, competition, freshness)

    def top(rows, sims, n=3):
        order = np.argsort(-sims)[:n]
        return [VideoRef(**d.ref(rows[i], sims[i])) for i in order]

    return IdeaScore(
        game=game, idea=idea, window_days=window_days, as_of=d.as_of.date().isoformat(), min_similarity=min_similarity,
        margin=margin, thresholds={"foreign": round(max(f_thr), 3), "uk": round(u_thr, 3)},
        components=[
            ScoreComponent(name="demand", level=demand, value=total_views,
                           explanation=f"{len(f_rows)} схожих іноземних відео за {window_days} днів, разом {total_views:,} переглядів "
                                       f"(high ≥ {R.DEMAND_HIGH_VIEWS:,}, medium ≥ {R.DEMAND_MEDIUM_VIEWS:,})".replace(",", " ")),
            ScoreComponent(name="competition", level=competition, value=len(u_rows),
                           explanation=f"{len(u_rows)} схожих українських відео за весь час "
                                       f"(low ≤ {R.COMPETITION_LOW_MAX}, medium ≤ {R.COMPETITION_MEDIUM_MAX})"),
            ScoreComponent(name="freshness", level=freshness, value=round(fresh_share, 3),
                           explanation=f"{fresh_share:.0%} схожих іноземних відео вийшли за останні {R.FRESH_DAYS} днів "
                                       f"(high ≥ {R.FRESHNESS_HIGH_SHARE:.0%}, medium ≥ {R.FRESHNESS_MEDIUM_SHARE:.0%})"),
        ],
        verdict=verdict,
        verdict_rule="weak_demand, якщо попит low; crowded, якщо конкуренція high; strong_opportunity, якщо попит "
                     "medium/high і конкуренція low; worth_trying, якщо попит high і конкуренція medium; інакше moderate.",
        foreign_evidence=top(f_rows, f_sims),
        uk_evidence=top(u_rows, u_sims),
    )


# ---------------------------------------------------------------- 4. Перевірка пакування

RU_LETTERS = re.compile(r"[ыэёъЫЭЁЪ]")
HASHTAG = re.compile(r"(?<![\w#])#[\w\u0400-\u04FF]+")


def _game_pattern(game):
    aliases = sorted(next(g for g in _GAMES if g["slug"] == game)["aliases"], key=len, reverse=True)
    return re.compile(r"(?<![\w])(" + "|".join(re.escape(a) for a in aliases) + r")(?![\w])", re.IGNORECASE)


def _tags_chars(tags):
    # YouTube рахує коми між тегами, а теги з пробілами — разом з лапками.
    return sum(len(t) + (2 if " " in t else 0) for t in tags) + max(0, len(tags) - 1)


@mcp.tool(
    name="validate_video_package",
    description=(
        "Перевіряє назву, опис і теги майбутнього відео перед публікацією: обмеження YouTube (довжина назви, "
        "сумарна довжина тегів, кількість хештегів), українська мова без російських літер, чи згадана гра в "
        "назві або тегах, чи є хештеги й ключове слово на початку опису. Повертає список проблем з "
        "рівнем error/warning/suggestion; valid=true, якщо помилок рівня error немає."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
def validate_video_package(
    game: Annotated[Game, Field(description="Гра, про яку відео")],
    title: Annotated[str, Field(min_length=1, max_length=200, description="Назва відео")],
    description: Annotated[str, Field(max_length=5000, description="Опис відео")] = "",
    tags: Annotated[Optional[List[Annotated[str, Field(min_length=1, max_length=100)]]], Field(max_length=60, description="Теги без #")] = None,
) -> PackageReport:
    game = game.value
    tags = tags or []
    issues: List[PackageIssue] = []

    def add(sev, field, code, msg):
        issues.append(PackageIssue(severity=sev, field=field, code=code, message=msg))

    tlen = len(title)
    if tlen > R.TITLE_MAX:
        add("error", "title", "TITLE_TOO_LONG", f"Назва {tlen} символів, YouTube дозволяє {R.TITLE_MAX}.")
    elif tlen > R.TITLE_VISIBLE:
        add("suggestion", "title", "TITLE_MAY_BE_CUT", f"Назва {tlen} символів: у пошуку видно приблизно перші {R.TITLE_VISIBLE}.")
    elif tlen < R.TITLE_SHORT:
        add("warning", "title", "TITLE_TOO_SHORT", f"Назва лише {tlen} символів: додайте, про що саме відео.")

    for field, text in (("title", title), ("description", description), ("tags", " ".join(tags))):
        if RU_LETTERS.search(text):
            add("error", field, "RUSSIAN_LETTERS", "Є літери ы, э, ё або ъ: текст має бути українською.")

    lang, conf, _ = detect_text(f"{title}\n{description[:500]}")
    detected = lang if lang in ("uk", "ru") and conf >= 0.85 else "unknown"
    if detected == "ru":
        add("error", "title", "NOT_UKRAINIAN", "Назва чи опис визначаються як російськомовні.")
    elif detected == "unknown":
        add("suggestion", "title", "LANGUAGE_UNCLEAR", "Мову не вдалося впевнено визначити: додайте українських слів у назву чи опис.")

    pattern = _game_pattern(game)
    if not pattern.search(title) and not any(pattern.search(t) for t in tags):
        add("warning", "title", "GAME_NOT_MENTIONED", "Гра не згадана ні в назві, ні в тегах: відео гірше знаходитиметься.")

    chars = _tags_chars(tags)
    if chars > R.TAGS_MAX_CHARS:
        add("error", "tags", "TAGS_TOO_LONG", f"Теги займають {chars} символів, YouTube дозволяє {R.TAGS_MAX_CHARS}.")
    lowered = [t.strip().lower() for t in tags]
    if len(set(lowered)) < len(lowered):
        add("warning", "tags", "DUPLICATE_TAGS", "Є повторювані теги.")
    if not tags:
        add("suggestion", "tags", "NO_TAGS", "Додайте 5–15 тегів: гра, режим, назва моду чи апдейту.")

    hashtags = len(HASHTAG.findall(description))
    if not description.strip():
        add("warning", "description", "EMPTY_DESCRIPTION", "Опис порожній: перші рядки опису впливають на пошук.")
    else:
        if not pattern.search(description[:R.DESCRIPTION_HEAD]):
            add("suggestion", "description", "GAME_NOT_IN_DESCRIPTION_HEAD",
                f"Назвіть гру в перших {R.DESCRIPTION_HEAD} символах опису: їх видно без розгортання.")
        if hashtags > R.HASHTAGS_MAX:
            add("error", "description", "TOO_MANY_HASHTAGS", f"{hashtags} хештегів: якщо їх понад {R.HASHTAGS_MAX}, YouTube ігнорує всі.")
        elif hashtags == 0:
            add("suggestion", "description", "NO_HASHTAGS", "Додайте 2–3 хештеги, наприклад #українською і назву гри.")

    return PackageReport(game=game, valid=not any(i.severity == "error" for i in issues), detected_language=detected,
                         title_length=tlen, tags_total_chars=chars, hashtags=hashtags, issues=issues)


# ---------------------------------------------------------------- 5. Збереження плану

@mcp.tool(
    name="save_content_plan",
    description=(
        "Зберігає обрану ідею відео в контент-план креатора (studio/data/plans/<creator>.json) разом з "
        "обґрунтуванням, структурою сценарію й id відео-доказів з датасету. Перевіряє, що всі докази існують "
        "у датасеті й стосуються цієї гри; ту саму ідею вдруге не дублює (повертає created=false)."
    ),
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def save_content_plan(
    creator: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,39}$", description="Ідентифікатор креатора: латиниця, цифри, - і _")],
    game: Annotated[Game, Field(description="Гра")],
    idea_title: Annotated[str, Field(min_length=5, max_length=120, description="Робоча назва ідеї")],
    rationale: Annotated[str, Field(min_length=20, max_length=1000, description="Чому саме ця ідея: цифри й висновки інструментів")],
    evidence_video_ids: Annotated[List[Annotated[str, Field(pattern=r"^[\w-]{11}$")]], Field(min_length=1, max_length=10, description="id відео з датасету, що підтверджують ідею")],
    outline: Annotated[List[Annotated[str, Field(min_length=3, max_length=200)]], Field(min_length=1, max_length=12, description="Структура сценарію: пункти по порядку")],
) -> SavedPlan:
    d = ds()
    game = game.value
    missing = [v for v in evidence_video_ids if v not in d.by_id]
    if missing:
        raise ToolError(f"UNKNOWN_EVIDENCE: цих відео немає в датасеті: {', '.join(missing)}")
    wrong_game = [v for v in evidence_video_ids if d.videos[d.by_id[v]]["game"] != game]
    if wrong_game:
        raise ToolError(f"EVIDENCE_GAME_MISMATCH: ці відео не про {game}: {', '.join(wrong_game)}")

    path = PLANS_DIR / f"{creator}.json"
    plans = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    key = re.sub(r"\s+", " ", idea_title.strip().lower())
    for p in plans:
        if p["game"] == game and p["key"] == key:
            return SavedPlan(plan_id=p["plan_id"], created=False, path=str(path), plans_total=len(plans))

    plan_id = f"{creator}-{len(plans) + 1:03d}"
    plans.append({
        "plan_id": plan_id, "key": key, "game": game, "idea_title": idea_title.strip(), "rationale": rationale.strip(),
        "evidence": [d.ref(d.by_id[v]) for v in evidence_video_ids], "outline": outline,
        "dataset_as_of": d.as_of.date().isoformat(),
        "saved_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    })
    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(plans, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return SavedPlan(plan_id=plan_id, created=True, path=str(path), plans_total=len(plans))


# ---------------------------------------------------------------- Допоміжне (не входить у три обов'язкові)

@mcp.tool(
    name="get_dataset_info",
    description="Які ігри є в датасеті, скільки в кожній відео за мовами, дата й модель датасету. Виклич на початку, щоб знати можливості.",
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
def get_dataset_info() -> DatasetInfo:
    d = ds()
    by_game = d.manifest.get("by_game", {})
    return DatasetInfo(
        built_at=d.manifest["built_at"], model=d.manifest["model"], videos=len(d.videos),
        games=[GameInfo(slug=g["slug"], name=g["name"], videos=by_game.get(g["slug"], {"uk": 0, "ru": 0, "en": 0})) for g in _GAMES],
    )


def main():
    p = argparse.ArgumentParser(description="MCP-сервер Студії")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8100)
    p.add_argument("--dataset", default=None, help="папка датасету (за замовчуванням studio/data/dataset)")
    p.add_argument("--transport", choices=["http", "stdio"], default="http", help="http = streamable HTTP на /mcp")
    args = p.parse_args()

    try:
        dataset = Dataset(Path(args.dataset)) if args.dataset else Dataset()
        dataset.embed_query("розігрів моделі")  # модель вантажиться одразу, а не на першому запиті
        _state["dataset"] = dataset
        print(f"Датасет: {len(dataset.videos)} відео, зібрано {dataset.manifest['built_at']}", file=sys.stderr)
    except DatasetError as e:
        # Сервер однаково стартує, а інструменти повертають DATASET_UNAVAILABLE: так збій видно клієнту.
        print(f"УВАГА: {e}", file=sys.stderr)

    try:
        if args.transport == "http":
            print(f"MCP-сервер: http://{args.host}:{args.port}/mcp (зупинити: Ctrl+C)", file=sys.stderr)
            # stateless_http: інструментам Студії сесія не потрібна (кожен виклик незалежний), а без неї
            # сервер не тримає фонові SSE-потоки, які MCP SDK 1.x шумно закриває після кожного клієнта.
            mcp.run(transport="http", host=args.host, port=args.port, show_banner=False, stateless_http=True)
        else:
            mcp.run(transport="stdio", show_banner=False)
    except KeyboardInterrupt:
        print("MCP-сервер зупинено.", file=sys.stderr)


if __name__ == "__main__":
    main()
