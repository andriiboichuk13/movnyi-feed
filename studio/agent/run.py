"""Запуск агента Студії.

Перед запуском мають працювати (кожен в окремому вікні, з кореня репозиторію):
    backend\\.venv\\Scripts\\python -m studio.mcp_server.server          # Студія, порт 8100
    npx @playwright/mcp@latest --port 8931 --host 127.0.0.1 --isolated                   # Playwright MCP, порт 8931

Запуск агента:
    backend\\.venv\\Scripts\\python -m studio.agent.run "Хочу знімати Minecraft, люблю будувати міста" --creator andrii
    ... --no-browser            # без Playwright (лише датасет)

У backend/.env мають бути OPENROUTER_API_KEY і STUDIO_MODEL (id моделі OpenRouter з підтримкою tools).
Звіт зберігається в studio/data/reports/.
"""

import argparse
import asyncio
import datetime as dt
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .prompts import SYSTEM_PROMPT
from .tools import connect

STUDIO_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = STUDIO_DIR / "data" / "reports"
load_dotenv(STUDIO_DIR.parent / "backend" / ".env")

try:  # кирилиця в консолі Windows
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def make_model(model_id: str):
    from langchain_openai import ChatOpenAI

    key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    if not key:
        sys.exit("Немає OPENROUTER_API_KEY у backend/.env")
    if not model_id:
        sys.exit("Не вказано модель: додайте STUDIO_MODEL=<id моделі OpenRouter> у backend/.env або параметр --model")
    return ChatOpenAI(
        model=model_id,
        api_key=key,
        base_url="https://openrouter.ai/api/v1",
        temperature=0.2,
        default_headers={"X-Title": "Movnyi Feed Studio"},
    )


def _short(value, n=300):
    if isinstance(value, list):  # відповідь MCP-інструмента: список блоків {"type": "text", "text": ...}
        value = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in value)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text[:n] + ("…" if len(text) > n else "")


async def run_agent(idea: str, creator: str, model, studio_url: str, playwright_url: str | None,
                    max_steps: int = 40, verbose: bool = True, on_event=None):
    """Запускає агента й повертає (звіт, журнал викликів інструментів, статус підключень)."""
    from langchain.agents import create_agent

    async with connect(studio_url, playwright_url) as (tools, status):
        return await _run(idea, creator, model, tools, status, max_steps, verbose, create_agent, on_event)


async def _run(idea, creator, model, tools, status, max_steps, verbose, create_agent, on_event=None):
    """on_event(dict) — необов'язковий колбек для живого прогресу (веб-сторінка Студії)."""
    emit = on_event or (lambda event: None)
    emit({"type": "connected", "status": status})
    status_text = "\n".join(f"- {name}: {state}" for name, state in status.items())
    if verbose:
        print("Підключення MCP:\n" + status_text + "\n")

    agent = create_agent(model, tools, system_prompt=SYSTEM_PROMPT + "\n\nСтан підключень зараз:\n" + status_text)
    user = f"Ідентифікатор креатора: {creator}\nЩо хоче знімати креатор: {idea}"

    log, report = [], ""
    async for update in agent.astream({"messages": [{"role": "user", "content": user}]},
                                      config={"recursion_limit": max_steps}, stream_mode="updates"):
        for node, payload in update.items():
            for msg in (payload or {}).get("messages", []):
                if msg.type == "ai":
                    for call in getattr(msg, "tool_calls", []) or []:
                        log.append({"tool": call["name"], "args": call["args"]})
                        emit({"type": "tool_call", "tool": call["name"], "args": call["args"]})
                        if verbose:
                            print(f"→ {call['name']}({_short(call['args'], 200)})")
                    if msg.content and not getattr(msg, "tool_calls", None):
                        report = msg.content if isinstance(msg.content, str) else _short(msg.content, 100000)
                elif msg.type == "tool":
                    failed = getattr(msg, "status", "") == "error"
                    if log and log[-1].get("tool") == msg.name and "result" not in log[-1]:
                        log[-1]["result"] = "error" if failed else "ok"
                    emit({"type": "tool_result", "tool": msg.name, "ok": not failed})
                    if verbose:
                        print(f"  {'✗' if failed else '✓'} {msg.name}: {_short(msg.content)}")
    return report, log, status


def save_report(idea, creator, model_id, report, log, status):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = REPORTS_DIR / f"{stamp}-{creator}.md"
    calls = "\n".join(f"{i + 1}. `{c['tool']}` — {c.get('result', '?')}" for i, c in enumerate(log))
    path.write_text(
        f"# Звіт Студії\n\n- Креатор: {creator}\n- Ідея: {idea}\n- Модель: {model_id}\n- Дата: {stamp}\n"
        + "".join(f"- MCP {k}: {v}\n" for k, v in status.items())
        + f"\n{report}\n\n---\n\n### Виклики інструментів\n\n{calls}\n",
        encoding="utf-8",
    )
    return path


def main():
    p = argparse.ArgumentParser(description="Агент Студії: ніші й ідеї для українського ігрового креатора")
    p.add_argument("idea", nargs="+", help="що хоче знімати креатор, своїми словами")
    p.add_argument("--creator", default="demo", help="ідентифікатор креатора (латиниця, цифри, - і _)")
    p.add_argument("--model", default=os.getenv("STUDIO_MODEL"), help="id моделі OpenRouter (за замовчуванням STUDIO_MODEL)")
    p.add_argument("--studio-url", default="http://127.0.0.1:8100/mcp")
    p.add_argument("--playwright-url", default="http://127.0.0.1:8931/mcp")
    p.add_argument("--no-browser", action="store_true", help="не підключати Playwright MCP")
    p.add_argument("--max-steps", type=int, default=40, help="ліміт кроків агента")
    args = p.parse_args()

    idea = " ".join(args.idea)
    model = make_model(args.model)
    try:
        report, log, status = asyncio.run(run_agent(
            idea, args.creator, model, args.studio_url, None if args.no_browser else args.playwright_url, args.max_steps))
    except RuntimeError as e:
        sys.exit(str(e))
    path = save_report(idea, args.creator, args.model, report, log, status)
    print("\n" + "=" * 70 + "\n" + report + f"\n\nЗвіт збережено: {path}")


if __name__ == "__main__":
    main()
