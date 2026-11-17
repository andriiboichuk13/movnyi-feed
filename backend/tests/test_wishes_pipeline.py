"""Юніт-тести для пайплайна екстракції токенів, групування і API для авторів."""

import datetime as dt
import os
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import numpy as np
from fastapi.testclient import TestClient

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import server  # noqa: E402
import wishes_pipeline as wp  # noqa: E402


class FakeEmbedModel:
    """Легка детермінована заглушка ембединг-моделі для швидких офлайн-тестів."""

    def encode(self, texts, normalize_embeddings=True, show_progress_bar=False):
        vecs = []
        for t in texts:
            low = t.lower()
            v = np.zeros(wp.C.EMBED_DIM, dtype=np.float32)
            if any(k in low for k in ("playstation", "ps5", "ігр", "папич", "реланна", "геймплей")):
                v[0] = 1.0
            elif any(k in low for k in ("пельмен", "кулінар", "варит", "їжа", "рецепт")):
                v[1] = 1.0
            elif any(k in low for k in ("гарний", "хочу ще", "позитив")):
                v[2] = 1.0
            elif any(k in low for k in ("хуйня", "погано", "негатив")):
                v[3] = 1.0
            else:
                v[4] = 1.0
            norm = np.linalg.norm(v)
            vecs.append(v / norm if norm > 0 else v)
        return np.asarray(vecs, dtype=np.float32)


def _sample_wish_rows():
    now = dt.datetime(2026, 11, 17, 15, 0, tzinfo=dt.timezone.utc)
    return [
        (3, now, "inst-1", "23EYgzjSwRM", "Папич vs. Реланна, Рыцарь Двойной Луны", "хуйня", 1, 1, "Ігри та кіберспорт", 2, "Проходження і стріми", 0.88, now),
        (2, now - dt.timedelta(hours=2), "inst-2", "Kxf2xome0VM", "ЭТО МОЯ PlayStation 5", "хуйня брат", 2, None, None, None, None, None, None),
        (1, now - dt.timedelta(days=1), "inst-1", "4RLFTPzzwbI", "КАК ВАРИТЬ ПЕЛЬМЕНИ ПРАВИЛЬНО", "гарний вибір, хочу ще", 1, None, None, None, None, None, None),
    ]


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConn:
    def __init__(self):
        self.updated = []

    def execute(self, sql, params=None):
        q = sql.strip().lower()
        if "select id, video_title, user_text" in q:
            return FakeResult([(2, "ЭТО МОЯ PlayStation 5", "хуйня брат")])
        if "from wishes w" in q:
            return FakeResult(_sample_wish_rows())
        if "from topics where parent_id is null" in q:
            return FakeResult([(1, "Ігри та кіберспорт"), (2, "Кулінарія та їжа")])
        if "select id, name, parent_id from topics" in q:
            return FakeResult([(1, "Ігри та кіберспорт", None), (2, "Проходження і стріми", 1)])
        if "insert into topics" in q:
            return FakeResult([(99,)])
        if q.startswith("update wishes"):
            self.updated.append(params)
            return FakeResult([])
        return FakeResult([])


def test_extract_tokens_detects_tech_and_fallback():
    with patch.object(wp, "get_ner_pipeline", return_value=None):
        tokens = wp.extract_tokens("ЭТО МОЯ PlayStation 5", "хочу огляд PS5")
    names = {t["token"] for t in tokens}
    assert "PlayStation 5" in names
    assert "PS5" in names


def test_build_creators_analytics_aggregates_and_groups():
    conn = FakeConn()
    with patch.object(wp, "get_embed_model", return_value=FakeEmbedModel()), \
         patch.object(wp, "get_ner_pipeline", return_value=None):
        data = wp.build_creators_analytics(conn)

    assert data["summary"]["total_wishes"] == 3
    assert data["summary"]["unique_installs"] == 2
    assert data["summary"]["unmet_demand"] == 1
    assert data["summary"]["processed_wishes"] == 1
    assert len(data["topics"]) >= 2
    assert len(data["clusters"]) >= 2
    assert any(t["token"] == "PlayStation 5" for t in data["tokens"])
    assert len(data["timeline"]) == 2
    assert len(data["wishes"]) == 3


def test_creators_endpoints_and_dashboard_route():
    conn = FakeConn()

    @contextmanager
    def fake_db():
        yield conn

    with patch.object(server, "db", fake_db), \
         patch.object(wp, "get_embed_model", return_value=FakeEmbedModel()), \
         patch.object(wp, "get_ner_pipeline", return_value=None):
        client = TestClient(server.app, raise_server_exceptions=True)
        # Перевіряємо HTML-сторінку
        page = client.get("/creators")
        assert page.status_code == 200
        assert "Для авторів" in page.text

        # Перевіряємо API агрегації
        res = client.get("/api/creators/overview")
        assert res.status_code == 200
        payload = res.json()
        assert payload["summary"]["total_wishes"] == 3

        # Перевіряємо запуск обробки
        proc = client.post("/api/creators/process", json={"force": False})
        assert proc.status_code == 200
        assert proc.json()["ok"] is True
        assert proc.json()["processed"] == 1
