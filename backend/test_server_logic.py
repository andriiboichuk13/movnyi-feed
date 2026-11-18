"""Чиста логіка пошуку альтернатив зі server.py / config.py / youtube.py (без бази й YouTube)."""
import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("pgvector")
pytest.importorskip("psycopg_pool")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
import config as C  # noqa: E402
import server  # noqa: E402
import youtube  # noqa: E402


def cand(vid, score, channel="c1", rank=None, title="t", tags=None):
    return dict(video_id=vid, title=title, tags=tags or [], channel="Ch", channel_id=channel, score=score, rank=score if rank is None else rank)


# ---------------------------------------------------------------- pick
def test_pick_respects_range_max_items_and_per_channel():
    cands = [cand("a", 0.95, "x"), cand("b", 0.94, "x"), cand("c", 0.93, "x"), cand("d", 0.92, "y"), cand("e", 0.91, "z"), cand("f", 0.5, "w")]
    got = [c["video_id"] for c in server.pick(cands, 0.84)]
    assert got == ["a", "b", "d"], "не більше MAX_PER_CHANNEL=2 з одного каналу, до MAX_ITEMS=3 всього, слабкі відсікаються"


def test_pick_upper_bound_is_exclusive():
    cands = [cand("a", 0.86), cand("b", 0.85)]
    assert [c["video_id"] for c in server.pick(cands, 0.84, 0.86)] == ["b"], "«близька тема» не має захоплювати «ту саму»"


def test_pick_sorts_by_rank_not_score():
    cands = [cand("a", 0.90, rank=0.90), cand("b", 0.89, rank=0.92)]
    assert [c["video_id"] for c in server.pick(cands, 0.84)][0] == "b"


def test_pick_empty_and_no_match():
    assert server.pick([], 0.84) == []
    assert server.pick([cand("a", 0.3)], 0.84) == []


# ---------------------------------------------------------------- ключові слова
def test_keywords_numbers_and_latin_names_and_brand_aliases():
    k = server.keywords("Огляд айфон 16 Pro та Samsung S24, the best")
    assert {"iphone", "16", "pro", "samsung", "s24"} <= k
    assert "the" not in k and "best" in k


def test_keyword_bonus_capped_and_zero_without_overlap():
    q = server.keywords("iPhone 16 Pro 256 RTX 5090 MacBook")
    big = server.keyword_bonus(q, "iPhone 16 Pro 256 RTX 5090 MacBook", [])
    assert 0 < big <= C.KEYWORD_BONUS_CAP
    assert server.keyword_bonus(q, "Борщ з пампушками", []) == 0
    assert server.keyword_bonus(set(), "iPhone", []) == 0


def test_item_shape_matches_what_the_extension_panel_reads():
    server.state["uk_dubs"] = {"dub0000000a"}
    it = server.item(cand("dub0000000a", 0.9123456, title="Назва"))
    assert set(it) == {"videoId", "title", "channel", "lang", "hasUkDub", "score"}
    assert it["hasUkDub"] is True and it["score"] == 0.912 and it["lang"] == "uk"
    assert server.item(cand("other000000", 0.9), "en")["lang"] == "en"


# ---------------------------------------------------------------- схожість і пороги
def test_combined_similarity_weights():
    assert server.combined(1.0, 0.0) == pytest.approx(C.TITLE_WEIGHT)
    assert server.combined(0.8, 0.8) == pytest.approx(0.8)


def test_thresholds_are_consistent():
    assert 0 < C.CLOSE_TOPIC_THRESHOLD <= C.SAME_TOPIC_THRESHOLD < 1
    assert 0 <= C.TITLE_WEIGHT <= 1
    assert C.MAX_ITEMS >= 1 and C.MAX_PER_CHANNEL >= 1
    assert C.INDEX_PREFETCH >= C.MAX_ITEMS * 10, "префетч має бути помітно більшим за кількість відповідей"
    assert C.KIDS_SHORTS_MAX_SECONDS < C.SHORTS_MAX_SECONDS
    assert C.RETENTION_DAYS <= 30, "правила YouTube API: дані не довше 30 днів"


def test_server_and_extension_agree_on_shared_numbers():
    ext = (ROOT / "extension" / "src" / "config.js").read_text(encoding="utf-8")
    num = lambda key: float(re.search(rf"{key}:\s*([\d.]+)", ext).group(1))
    assert num("LANG_THRESHOLD") == C.LANG_THRESHOLD
    assert num("MAX_ALTERNATIVES") == C.MAX_ITEMS
    assert num("WISH_DAILY_LIMIT") == C.WISH_DAILY_LIMIT
    assert num("WISH_TEXT_MAX") == C.WISH_TEXT_MAX
    assert num("FETCH_TIMEOUT_MS") >= 15000, "холодний сервер на Railway відповідає довго; таймаут не має бути агресивним"


def test_event_types_match_extension_usage():
    used = set()
    for f in ("panel.js", "main.js"):
        used |= set(re.findall(r'eventType:\s*"([a-z_]+)"', (ROOT / "extension" / "src" / "content" / f).read_text(encoding="utf-8")))
    assert used <= server.EVENT_TYPES


# ---------------------------------------------------------------- опис і текст для вектора
def test_clean_description_drops_promo_urls_keeps_chapters():
    d = "\n".join([
        "Сьогодні розбираємо новий iPhone",
        "Підписуйся на Telegram: t.me/channel",
        "Промокод SALE10 дає знижку",
        "https://instagram.com/me",
        "00:00 Вступ",
        "1:02:03 - Висновки",
        "🔥🔥🔥",
        "Купити тут: www.shop.ua",
    ])
    out = C.clean_description(d)
    assert "iPhone" in out and "Вступ" in out and "Висновки" in out
    for junk in ("Telegram", "Промокод", "instagram", "shop.ua", "🔥"):
        assert junk not in out
    assert "00:00" not in out, "таймкод прибирається, назва глави лишається"


def test_clean_description_limit_and_empty():
    assert len(C.clean_description("слово " * 500, limit=100)) <= 100
    assert C.clean_description("") == "" and C.clean_description(None) == ""


def test_embedding_text_parts_and_tag_limit():
    t = C.embedding_text("Назва", [f"tag{i}" for i in range(30)] + [""], "Опис відео про щось")
    lines = t.split("\n")
    assert lines[0] == "Назва" and "tag9" in lines[1] and "tag10" not in lines[1] and "Опис" in lines[2]
    assert C.embedding_text("Назва", None, "") == "Назва"
    assert C.title_text(None) == ""


# ---------------------------------------------------------------- YouTube-допоміжне
@pytest.mark.parametrize("iso,seconds", [("PT3M", 180), ("PT1H2M3S", 3723), ("PT45S", 45), ("P1DT1H", 90000), ("", 0)])
def test_parse_duration(iso, seconds):
    assert youtube.parse_duration(iso) == seconds


def test_is_short_default_and_kids_limits():
    assert youtube.is_short(180) and not youtube.is_short(181)
    assert youtube.is_short(60, C.KIDS_SHORTS_MAX_SECONDS) and not youtube.is_short(61, C.KIDS_SHORTS_MAX_SECONDS)
    assert not youtube.is_short(150, C.KIDS_SHORTS_MAX_SECONDS), "дитяча пісня на 2.5 хв — не Shorts"


def test_available_in_ukraine_region_restriction():
    assert youtube.available_in_ukraine({})
    assert not youtube.available_in_ukraine({"regionRestriction": {"blocked": ["UA"]}})
    assert not youtube.available_in_ukraine({"regionRestriction": {"allowed": ["PL"]}})
    assert youtube.available_in_ukraine({"regionRestriction": {"allowed": ["UA", "PL"]}})


def test_timer_collects_stages():
    t = server.Timer()
    t.mark("a")
    t.mark("b")
    assert set(t.parts) == {"a", "b"} and t.line().startswith("total=")
