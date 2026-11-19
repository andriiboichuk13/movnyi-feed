"""Тести MCP-сервера Студії на синтетичному датасеті, через справжній MCP-клієнт (in-memory).

Модель e5 замінено детермінованою заглушкою: тести перевіряють логіку інструментів і контракти,
а не якість векторів. Запуск: backend\\.venv\\Scripts\\python -m pytest studio/tests -q
"""

import asyncio
import datetime as dt
import json

import numpy as np
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from studio.mcp_server import server as S
from studio.mcp_server.dataset import Dataset

TOPICS = ["hardcore", "mod", "city", "pvp"]


def _unit(v):
    return (v / np.linalg.norm(v)).astype(np.float32)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    rng = np.random.default_rng(0)
    centroids = {k: _unit(rng.normal(size=384)) for k in TOPICS}
    videos, full, title = [], [], []
    as_of = dt.datetime(2026, 11, 19, tzinfo=dt.timezone.utc)

    def add(vid, name, lang, topic, views, days, game="minecraft"):
        noise = _unit(rng.normal(size=384))
        videos.append(dict(video_id=vid, title=name, description="", tags=[], channel_id=f"UC{lang}{topic}",
                           channel_title=f"{lang} {topic}", published_at=(as_of - dt.timedelta(days=days)).isoformat(),
                           duration_s=900, views=views, likes=0, comments=0, lang=lang, game=game,
                           in_catalog=lang == "uk", sources=["search"], fetched_at=as_of.isoformat()))
        full.append(_unit(0.95 * centroids[topic] + 0.31 * noise))
        title.append(_unit(0.95 * centroids[topic] + 0.31 * _unit(rng.normal(size=384))))

    for lang in ("en", "ru"):
        for j in range(4):
            add(f"hc{lang}{j:07d}", f"100 days hardcore {j}", lang, "hardcore", 500_000, 20 + j)
        for j in range(3):
            add(f"md{lang}{j:07d}", f"Insane new mod {j}", lang, "mod", 900_000, 10 + j)
    add("hcuk0000001", "100 днів хардкору", "uk", "hardcore", 30_000, 100)
    add("rbxen000001", "Roblox obby", "en", "pvp", 200_000, 10, game="roblox")

    (tmp_path / "videos.jsonl").write_text("\n".join(json.dumps(v, ensure_ascii=False) for v in videos), encoding="utf-8")
    np.save(tmp_path / "emb_full.npy", np.array(full, dtype=np.float16))
    np.save(tmp_path / "emb_title.npy", np.array(title, dtype=np.float16))
    (tmp_path / "manifest.json").write_text(json.dumps({"built_at": as_of.isoformat(), "model": "test", "by_game": {}}))

    def embedder(texts):
        return np.array([centroids["hardcore"] if "хардкор" in t.lower() else centroids["mod"] if "мод" in t.lower()
                         else centroids["pvp"] for t in texts])

    monkeypatch.setitem(S._state, "dataset", Dataset(tmp_path, embedder=embedder))
    monkeypatch.setattr(S, "PLANS_DIR", tmp_path / "plans")
    return Client(S.mcp)


def call(client, name, args, **kw):
    async def run():
        async with client as c:
            return await c.call_tool(name, args, **kw)
    return asyncio.run(run())


def test_lists_tools_with_schemas(client):
    async def run():
        async with client as c:
            return await c.list_tools()
    tools = {t.name: t for t in asyncio.run(run())}
    assert {"analyze_niche_coverage", "find_content_gaps", "score_video_idea",
            "validate_video_package", "save_content_plan"} <= set(tools)
    assert all(t.outputSchema for t in tools.values())


def test_gap_found_and_covered_topic_skipped(client):
    r = call(client, "find_content_gaps", {"game": "minecraft"}).structured_content
    assert r["covered_subtopics"] == 1
    assert [g["representative_title"].startswith("Insane new mod") for g in r["gaps"]] == [True]


def test_empty_result_is_not_error(client):
    r = call(client, "find_content_gaps", {"game": "minecraft", "min_foreign_views": 10_000_000})
    assert not r.is_error and r.structured_content["gaps"] == []


def test_invalid_game_rejected_by_schema(client):
    with pytest.raises(ToolError):
        call(client, "find_content_gaps", {"game": "zelda"})


def test_idea_scoring(client):
    r = call(client, "score_video_idea", {"game": "minecraft", "idea": "Огляд нового моду"}).structured_content
    assert r["verdict"] == "strong_opportunity"


def test_package_validation_flags_russian(client):
    r = call(client, "validate_video_package", {"game": "minecraft", "title": "Выживание 100 дней"}).structured_content
    assert not r["valid"] and "RUSSIAN_LETTERS" in [i["code"] for i in r["issues"]]


def test_plan_saved_once_and_evidence_checked(client):
    args = {"creator": "andrii", "game": "minecraft", "idea_title": "Огляд нового моду",
            "rationale": "Мод популярний англійською, українською нічого.", "evidence_video_ids": ["mden0000000"],
            "outline": ["Тизер", "Вступ"]}
    assert call(client, "save_content_plan", args).structured_content["created"] is True
    assert call(client, "save_content_plan", args).structured_content["created"] is False
    bad = call(client, "save_content_plan", {**args, "evidence_video_ids": ["rbxen000001"]}, raise_on_error=False)
    assert bad.is_error and "EVIDENCE_GAME_MISMATCH" in bad.content[0].text


def test_single_creator_subtopics_skipped(client):
    r = call(client, "find_content_gaps", {"game": "minecraft", "min_channels": 3}).structured_content
    assert r["gaps"] == [] and r["small_subtopics_skipped"] >= 1
