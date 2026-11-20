"""Живий агент Студії для журі: невеликий веб-API на ноутбуці, доступний через ngrok.

Сторінка https://movnyi-feed-production.up.railway.app/creators/studio/try (на неї веде QR) звертається сюди:
    GET  /api/health          — чи агент онлайн, скільки запусків сьогодні
    POST /api/run {idea}      — поставити запуск у чергу → job_id
    GET  /api/jobs/{job_id}   — статус, кроки агента людською мовою, готовий звіт

Обмеження: не більше MAX_CONCURRENT агентів одночасно (решта в черзі), PER_IP_PER_HOUR запусків
з однієї адреси на годину, DAILY_LIMIT запусків на день. Кожен вдалий звіт публікується на сайт.

Запуск (з кореня репозиторію; MCP Студії й Playwright MCP мають працювати):
    backend\\.venv\\Scripts\\python -m uvicorn studio.web:app --port 8200
    ngrok http 8200
"""

import asyncio
import datetime as dt
import os
import time
import uuid
from collections import defaultdict, deque
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .agent.run import make_model, run_agent, save_report

STUDIO_URL = os.getenv("STUDIO_MCP_URL", "http://127.0.0.1:8100/mcp")
PLAYWRIGHT_URL = os.getenv("PLAYWRIGHT_MCP_URL", "http://127.0.0.1:8931/mcp")
MODEL_ID = os.getenv("STUDIO_MODEL")
ORIGINS = [o.strip() for o in os.getenv(
    "STUDIO_WEB_ORIGINS", "https://movnyi-feed-production.up.railway.app,http://localhost:8000").split(",") if o.strip()]
MAX_CONCURRENT = int(os.getenv("STUDIO_MAX_CONCURRENT", "2"))
PER_IP_PER_HOUR = int(os.getenv("STUDIO_PER_IP_PER_HOUR", "4"))
DAILY_LIMIT = int(os.getenv("STUDIO_DAILY_LIMIT", "60"))
PUBLISH = os.getenv("STUDIO_PUBLISH", "1") == "1"
JOB_TTL_SECONDS = 6 * 3600

app = FastAPI(title="Студія ідей — живий агент")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "ngrok-skip-browser-warning"],
)

model = make_model(MODEL_ID)
jobs: dict = {}
semaphore = asyncio.Semaphore(MAX_CONCURRENT)
ip_hits: dict = defaultdict(deque)
daily = {"date": dt.date.today(), "count": 0}

# Що показувати глядачеві замість назв інструментів.
LABELS = {
    "get_dataset_info": "Перевіряю, які ігри є в даних",
    "browser_snapshot": "Читаю сторінку трендів",
    "analyze_niche_coverage": "Рахую, скільки відео є кожною мовою",
    "find_content_gaps": "Шукаю теми, яких немає українською",
    "validate_video_package": "Перевіряю назву, опис і теги",
    "save_content_plan": "Зберігаю план відео",
}


def label(tool: str, args: dict) -> str:
    if tool == "browser_navigate":
        return f"Відкриваю сайт трендів: {urlparse(args.get('url', '')).hostname or '…'}"
    if tool == "score_video_idea":
        idea = (args.get("idea") or "").strip()
        return f"Оцінюю ідею: «{idea[:90]}{'…' if len(idea) > 90 else ''}»"
    return LABELS.get(tool, "Працюю з даними")


class RunBody(BaseModel):
    idea: str = Field(min_length=10, max_length=300)


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def cleanup():
    cutoff = time.time() - JOB_TTL_SECONDS
    for job_id in [j for j, job in jobs.items() if job["created"] < cutoff]:
        jobs.pop(job_id, None)
    if daily["date"] != dt.date.today():
        daily.update(date=dt.date.today(), count=0)


def publish(path):
    from . import publish_report

    conn = publish_report.C.connect_db()
    try:
        conn.execute(publish_report.TABLE_SQL)
        return publish_report.publish(conn, path)
    finally:
        conn.close()


async def worker(job):
    async with semaphore:
        job["status"] = "running"
        job["started"] = time.time()

        def on_event(event):
            if event["type"] == "tool_call":
                job["steps"].append({"label": label(event["tool"], event.get("args") or {}), "tool": event["tool"], "ok": None})
            elif event["type"] == "tool_result":
                for step in reversed(job["steps"]):
                    if step["tool"] == event["tool"] and step["ok"] is None:
                        step["ok"] = event["ok"]
                        break
            elif event["type"] == "connected":
                job["browser"] = str(event["status"].get("playwright", "")).startswith("підключено")

        try:
            report, log, status = await run_agent(job["idea"], "live", model, STUDIO_URL, PLAYWRIGHT_URL,
                                                  verbose=False, on_event=on_event)
            if not report.strip():
                raise RuntimeError("агент не повернув звіту")
            path = await asyncio.to_thread(save_report, job["idea"], "live", MODEL_ID, report, log, status)
            job["report_md"] = report
            if PUBLISH:
                try:
                    job["report_id"] = await asyncio.to_thread(publish, path)
                except Exception as e:  # звіт однаково показуємо, навіть якщо публікація не вдалася
                    print(f"Публікація не вдалася: {e}")
            job["status"] = "done"
        except Exception as e:
            print(f"Запуск {job['id']} завершився помилкою: {type(e).__name__}: {e}")
            job["status"] = "error"
            job["error"] = "Агент не зміг завершити роботу. Спробуйте ще раз за хвилину."
        finally:
            job["finished"] = time.time()


@app.get("/api/health")
def health():
    cleanup()
    running = sum(1 for j in jobs.values() if j["status"] == "running")
    queued = sum(1 for j in jobs.values() if j["status"] == "queued")
    return {"ok": True, "model": MODEL_ID, "running": running, "queued": queued,
            "today": daily["count"], "daily_limit": DAILY_LIMIT}


@app.post("/api/run")
async def run(body: RunBody, request: Request):
    cleanup()
    ip, now = client_ip(request), time.time()
    hits = ip_hits[ip]
    while hits and hits[0] < now - 3600:
        hits.popleft()
    if len(hits) >= PER_IP_PER_HOUR:
        raise HTTPException(429, "З цього пристрою вже було кілька запусків за годину. Спробуйте пізніше.")
    if daily["count"] >= DAILY_LIMIT:
        raise HTTPException(429, "Ліміт запусків на сьогодні вичерпано. Готові звіти є на сторінці Студії.")
    hits.append(now)
    daily["count"] += 1

    job = {"id": uuid.uuid4().hex[:12], "idea": body.idea.strip(), "status": "queued", "steps": [], "browser": None,
           "report_md": None, "report_id": None, "error": None, "created": now, "started": None, "finished": None}
    jobs[job["id"]] = job
    asyncio.create_task(worker(job))
    ahead = sum(1 for j in jobs.values() if j["status"] in ("queued", "running") and j["id"] != job["id"])
    return {"job_id": job["id"], "ahead": ahead}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Запуск не знайдено")
    ahead = sum(1 for j in jobs.values() if j["status"] == "queued" and j["created"] < job["created"])
    return {
        "status": job["status"], "idea": job["idea"], "ahead": ahead if job["status"] == "queued" else 0,
        "browser": job["browser"], "steps": job["steps"][-30:], "report_md": job["report_md"],
        "report_id": job["report_id"], "error": job["error"],
        "elapsed": round((job["finished"] or time.time()) - (job["started"] or job["created"])),
    }
