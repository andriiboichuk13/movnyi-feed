"""Справжній backend/server.py приймає те, що реально шле розширення.

Як працює: node запускає справжній extension/src/background.js і друкує всі HTTP-запити (tests/tools/capture-requests.js).
Ці запити проганяються через справжній FastAPI-застосунок (валідація pydantic, перевірки videoId, ліміти).
Базу даних і YouTube підміняємо заглушками, тож потрібні лише: pip install pytest fastapi httpx numpy pgvector "psycopg[binary]" psycopg_pool python-dotenv requests
"""
import json
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("pgvector")
pytest.importorskip("psycopg_pool")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class FakeCursor:
    def __init__(self, row=None, rows=None):
        self.row, self.rows = row, rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class FakeConn:
    """Записує SQL; на «count(*)» повертає задану кількість використаних бажань."""

    def __init__(self, used=0):
        self.used, self.executed = used, []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(str(sql).split()), params))
        return FakeCursor(row=(self.used,) if "count(*)" in str(sql) else None)

    @contextmanager
    def transaction(self):
        yield

    @contextmanager
    def cursor(self):
        conn = self

        class Cur:
            def executemany(self, sql, rows):
                conn.executed.append((" ".join(sql.split()), list(rows)))

        yield Cur()


@pytest.fixture
def api(monkeypatch):
    conn = FakeConn()

    @contextmanager
    def fake_db():
        yield conn

    monkeypatch.setattr(server, "db", fake_db)
    monkeypatch.setattr(server, "get_sources", lambda ids: {})  # YouTube нікого не знайшов
    client = TestClient(server.app)  # без lifespan: без бази й моделі
    client.conn = conn
    return client


@pytest.fixture(scope="module")
def captured():
    if shutil.which("node") is None:
        pytest.skip("node не знайдено")
    script = ROOT / "extension" / "tests" / "tools" / "capture-requests.js"
    out = subprocess.check_output(["node", str(script)], text=True, encoding="utf-8")
    return json.loads(out)


def replay(api, call):
    if call["method"] == "GET":
        return api.get(call["url"])
    return api.post(call["url"], json=call["body"])


def test_every_request_the_extension_sends_is_accepted_by_the_real_server(api, captured):
    assert len(captured) >= 12
    bad = []
    for c in captured:
        r = replay(api, c)
        if r.status_code != 200:
            bad.append((c["method"], c["url"].split("?")[0], r.status_code, r.text[:160], json.dumps(c["body"], ensure_ascii=False)[:160] if c["body"] else ""))
    assert not bad, "справжній сервер відхилив запити розширення:\n" + "\n".join(map(str, bad))


def test_wish_body_matches_server_limits(api, captured):
    wishes = [c for c in captured if c["url"] == "/wish"]
    assert wishes
    for c in wishes:
        assert len(c["body"]["installId"]) >= 8
        assert len(c["body"].get("userText") or "") <= 280
        assert len(c["body"].get("videoTitle") or "") <= 300, "назва відео довша за ліміт сервера (300): сервер відповість 422"


def test_kids_wish_reaches_database_without_user_text(api, captured):
    kid = next(c for c in captured if c["url"] == "/wish" and c["body"]["userText"] is None)
    assert api.post("/wish", json=kid["body"]).status_code == 200
    insert = [p for sql, p in api.conn.executed if sql.startswith("insert into wishes")][-1]
    assert insert[3] is None  # user_text


def test_classify_unknown_ids_get_unknown_not_missing(api, captured):
    c = next(c for c in captured if c["url"] == "/classify")
    data = api.post("/classify", json=c["body"]).json()
    assert set(data["results"]) == set(c["body"]["videoIds"])
    assert all(v["lang"] == "unknown" and v["confidence"] == 0 for v in data["results"].values())


def test_actions_chunks_fit_server_limit(api, captured):
    sizes = [len(c["body"]["items"]) for c in captured if c["url"] == "/actions"]
    assert sizes and max(sizes) <= 100 and sum(sizes) == 230


# ---------------------------------------------------------------- що сервер має відхиляти
@pytest.mark.parametrize("path,body", [
    ("/wish", {"installId": "short", "videoId": "dQw4w9WgXcQ"}),
    ("/wish", {"installId": "install-id-12345", "videoId": "bad"}),
    ("/wish", {"installId": "install-id-12345", "videoId": "dQw4w9WgXcQ", "userText": "я" * 281}),
    ("/events", {"installId": "install-id-12345", "type": "hack"}),
    ("/events", {"installId": "install-id-12345", "type": "panel_shown", "count": -1}),
    ("/events", {"installId": "install-id-12345", "type": "kids_hidden_daily", "count": 100001}),
    ("/events", {"installId": "install-id-12345", "type": "kids_hidden_daily", "date": "не дата"}),
    ("/actions", {"installId": "install-id-12345", "items": []}),
    ("/actions", {"installId": "install-id-12345", "items": [{"action": "click", "videoId": "dQw4w9WgXcQ"}]}),
    ("/actions", {"installId": "install-id-12345", "items": [{"action": "hid", "videoId": "x"}]}),
    ("/actions", {"installId": "install-id-12345", "items": [{"action": "hid", "videoId": "dQw4w9WgXcQ"}] * 101}),
    ("/classify", {"videoIds": ["dQw4w9WgXcQ"] * 51}),
])
def test_server_rejects_bad_input(api, path, body):
    assert api.post(path, json=body).status_code in (400, 422)


def test_server_rejects_bad_alternatives_params(api):
    assert api.get("/alternatives?videoId=bad").status_code == 400
    assert api.get("/alternatives?videoId=dQw4w9WgXcQ&mode=hack").status_code == 422
    assert api.get("/alternatives").status_code == 422


def test_sql_injection_attempt_in_text_is_stored_as_data(api):
    api.post("/wish", json={"installId": "install-id-12345", "videoId": "dQw4w9WgXcQ", "userText": "'; drop table wishes; --"})
    sql, params = [e for e in api.conn.executed if e[0].startswith("insert into wishes")][-1]
    assert "drop table" not in sql and params[3] == "'; drop table wishes; --"


# ---------------------------------------------------------------- ліміт бажань і розбіжність з розширенням
def test_wish_limit_server_returns_429_with_reason(api):
    api.conn.used = 20
    r = api.post("/wish", json={"installId": "install-id-12345", "videoId": "dQw4w9WgXcQ", "userText": "x"})
    assert r.status_code == 429
    assert r.json() == {"ok": False, "reason": "limit"}


def test_alternatives_unknown_video_returns_level5(api):
    r = api.get("/alternatives?videoId=dQw4w9WgXcQ").json()
    assert r == {"source": {"lang": "unknown", "confidence": 0}, "level": 5, "items": []}


def test_cors_allows_extension_origin(api):
    r = api.options("/alternatives", headers={"Origin": "chrome-extension://abc", "Access-Control-Request-Method": "GET"})
    assert r.status_code in (200, 204)
