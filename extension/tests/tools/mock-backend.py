#!/usr/bin/env python3
"""Тестовий бекенд для розширення «Мовний фід». Лише стандартна бібліотека Python 3.8+.

Для чого: перевіряти розширення без Supabase, ключа YouTube і моделі на 0,5 ГБ.
Він повторює контракт і перевірки справжнього server.py (довжина installId, формат videoId,
дозволені типи подій, ліміти на розмір пачок), а ще:
  * друкує кожен запит у консоль, щоб бачити, що саме розширення надсилає (перевірка приватності);
  * дозволяє підмінити мову відео та зімітувати збої (500, 429, затримку) без зміни коду.

Запуск:      python tests/tools/mock-backend.py            (порт 8000)
             python tests/tools/mock-backend.py --port 9000
У розширенні: API_BASE = "http://localhost:8000", USE_MOCK = false.

Керування під час роботи (у браузері або curl):
  GET  /__state                         що сервер знає, останні запити
  GET  /__set?ru=ID1,ID2&uk=ID3         які videoId вважати російськими / українськими (для /classify)
  GET  /__set?fail=classify:500         зламати ендпоінт (500, 429, timeout) ; fail=off  прибрати збої
  GET  /__set?fail=alternatives:500:2   зламати лише перші 2 запити, далі працює (перевірка повтору запиту)
  GET  /__set?delay=1500                затримка відповіді в мс
  GET  /__set?level=1..5                який рівень драбини віддавати в /alternatives (за замовчуванням за id)
  GET  /__set?source=ru|uk|unknown      мова вихідного відео в /alternatives
  GET  /__reset                         скинути все
"""
import argparse
import datetime as dt
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

VIDEO_ID = re.compile(r"^[\w-]{11}$")  # як у backend/server.py
EVENT_TYPES = {"panel_shown", "alt_clicked", "wish_sent", "kids_hidden_daily"}
WISH_DAILY_LIMIT = 20
WISH_TEXT_MAX = 280

LOCK = threading.Lock()
STATE = {}


def reset():
    STATE.clear()
    STATE.update(
        ru=set(), uk=set(), fail={}, delay_ms=0, level=None, source="ru",
        log=[], wishes=[], events=[], actions=[], classify_calls=[],
    )


reset()


def fake_items(video_id, level):
    if level in (0, 5):
        return []
    lang = "en" if level == 4 else "uk"
    return [
        {
            "videoId": f"alt{level}{i:08d}"[:11].ljust(11, "x"),
            "title": f"Тестова альтернатива {i + 1} (рівень {level})",
            "channel": "Тестовий канал",
            "lang": lang,
            "hasUkDub": level == 4 and i == 0,
            "score": round(0.9 - i * 0.05, 2),
        }
        for i in range(3)
    ]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # власний лог нижче
        pass

    # ---- допоміжне
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")

    def _send(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _note(self, path, body, status):
        entry = {"t": dt.datetime.now().strftime("%H:%M:%S"), "method": self.command, "path": path, "body": body, "status": status}
        with LOCK:
            STATE["log"].append(entry)
            del STATE["log"][:-200]
        print(f"{entry['t']} {self.command} {path} -> {status}  {json.dumps(body, ensure_ascii=False) if body else ''}", flush=True)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            return "__invalid_json__"

    def _name(self, path):
        return path.strip("/").split("/")[0]

    def _maybe_fail(self, name):
        with LOCK:
            key = name if name in STATE["fail"] else "all"
            fail, delay = STATE["fail"].get(key), STATE["delay_ms"]
            if fail and fail.count(":") == 1:  # "500:2" = зламати 2 запити, далі працювати
                how, _, n = fail.partition(":")
                if int(n) <= 1:
                    del STATE["fail"][key]
                else:
                    STATE["fail"][key] = f"{how}:{int(n) - 1}"
                fail = how
        if delay:
            time.sleep(delay / 1000)
        return fail

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---- GET
    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        name = self._name(u.path)

        if name == "__set":
            with LOCK:
                if "ru" in q:
                    STATE["ru"] = {x for x in q["ru"].split(",") if x}
                if "uk" in q:
                    STATE["uk"] = {x for x in q["uk"].split(",") if x}
                if "delay" in q:
                    STATE["delay_ms"] = int(q["delay"])
                if "level" in q:
                    STATE["level"] = None if q["level"] == "auto" else int(q["level"])
                if "source" in q:
                    STATE["source"] = q["source"]
                if "fail" in q:
                    if q["fail"] == "off":
                        STATE["fail"] = {}
                    else:
                        ep, _, how = q["fail"].partition(":")  # how: 500 | 429 | timeout | 500:3 (лише 3 рази)
                        STATE["fail"][ep] = how or "500"
            return self._send(200, {"ok": True})
        if name == "__reset":
            reset()
            return self._send(200, {"ok": True})
        if name == "__state":
            with LOCK:
                snap = {k: (sorted(v) if isinstance(v, set) else v) for k, v in STATE.items()}
            return self._send(200, snap)
        if name == "health":
            return self._send(200, {"ok": True, "videos": 0, "channels": 0, "mock": True})

        if name == "alternatives":
            fail = self._maybe_fail("alternatives")
            if fail:
                return self._fail(fail, u.path)
            vid, mode = q.get("videoId", ""), q.get("mode", "default")
            if not VIDEO_ID.match(vid):
                self._note(self.path, None, 400)
                return self._send(400, {"detail": "Некоректний videoId"})
            if mode not in ("default", "kids"):
                self._note(self.path, None, 422)
                return self._send(422, {"detail": "mode"})
            with LOCK:
                lvl, src = STATE["level"], STATE["source"]
                if vid in STATE["uk"]:
                    src = "uk"
                elif vid in STATE["ru"]:
                    src = "ru"
            level = 0 if src == "uk" else (lvl if lvl is not None else 1 + sum(map(ord, vid)) % 5)
            conf = 0.0 if src == "unknown" else 0.95
            resp = {"source": {"lang": src, "confidence": conf}, "level": level, "items": fake_items(vid, level)}
            self._note(self.path, None, 200)
            return self._send(200, resp)

        self._note(self.path, None, 404)
        self._send(404, {"detail": "Not Found"})

    def _fail(self, how, path, body=None):
        if how == "timeout":
            time.sleep(30)
            return
        status = int(how) if how.isdigit() else 500
        self._note(path, body, status)
        extra = {"ok": False, "reason": "limit"} if status == 429 else {"detail": "mock failure"}
        self._send(status, extra)

    # ---- POST
    def do_POST(self):
        u = urlparse(self.path)
        name = self._name(u.path)
        body = self._body()
        fail = self._maybe_fail(name)
        if fail:
            return self._fail(fail, u.path, body)

        def bad(msg):
            self._note(u.path, body, 422)
            return self._send(422, {"detail": msg})

        if not isinstance(body, dict):
            return bad("Очікувався JSON-об'єкт")

        if name == "classify":
            ids = body.get("videoIds")
            if not isinstance(ids, list) or len(ids) > 50:
                return bad("videoIds: до 50 штук")
            ids = [v for v in dict.fromkeys(ids) if isinstance(v, str) and VIDEO_ID.match(v)]
            with LOCK:
                STATE["classify_calls"].append(len(ids))
                ru, uk = set(STATE["ru"]), set(STATE["uk"])
            results = {}
            for v in ids:
                if v in ru:
                    results[v] = {"lang": "ru", "confidence": 0.95, "source": "defaultAudioLanguage"}
                elif v in uk:
                    results[v] = {"lang": "uk", "confidence": 0.95, "source": "defaultAudioLanguage"}
                else:
                    results[v] = {"lang": "unknown", "confidence": 0, "source": "not_found"}
            self._note(u.path, {"videoIds": f"{len(ids)} шт."}, 200)
            return self._send(200, {"results": results})

        if name in ("wish", "events", "actions"):
            iid = body.get("installId")
            if not isinstance(iid, str) or not 8 <= len(iid) <= 64:
                return bad("installId: 8–64 символи")

        if name == "wish":
            if not VIDEO_ID.match(str(body.get("videoId", ""))):
                self._note(u.path, body, 400)
                return self._send(400, {"detail": "Некоректний videoId"})
            if len(body.get("userText") or "") > WISH_TEXT_MAX or len(body.get("videoTitle") or "") > 300:
                return bad("задовгий текст")
            with LOCK:
                used = sum(1 for w in STATE["wishes"] if w["installId"] == iid)
                if used >= WISH_DAILY_LIMIT:
                    self._note(u.path, body, 429)
                    return self._send(429, {"ok": False, "reason": "limit"})  # як справжній сервер
                STATE["wishes"].append(body)
            self._note(u.path, body, 200)
            return self._send(200, {"ok": True})

        if name == "events":
            if body.get("type") not in EVENT_TYPES:
                self._note(u.path, body, 400)
                return self._send(400, {"detail": "Невідомий тип події"})
            if body.get("count") is not None and not (isinstance(body["count"], int) and 0 <= body["count"] <= 100000):
                return bad("count")
            if body.get("date") is not None:
                try:
                    dt.date.fromisoformat(body["date"])
                except Exception:
                    return bad("date")
            with LOCK:
                STATE["events"].append(body)
            self._note(u.path, body, 200)
            return self._send(200, {"ok": True})

        if name == "actions":
            items = body.get("items")
            if not isinstance(items, list) or not 1 <= len(items) <= 100:
                return bad("items: від 1 до 100")
            for it in items:
                if not isinstance(it, dict) or it.get("action") not in ("suggested", "hid") or not VIDEO_ID.match(str(it.get("videoId", ""))):
                    return bad("Некоректний елемент items")
            with LOCK:
                STATE["actions"].extend({"installId": iid, **it} for it in items)
            self._note(u.path, {"installId": iid, "items": f"{len(items)} шт.", "actions": sorted({i['action'] for i in items})}, 200)
            return self._send(200, {"ok": True, "inserted": len(items)})

        self._note(u.path, body, 404)
        self._send(404, {"detail": "Not Found"})


def make_server(port):
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    srv = make_server(args.port)
    print(f"Тестовий бекенд: http://localhost:{args.port}  (Ctrl+C щоб зупинити)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
