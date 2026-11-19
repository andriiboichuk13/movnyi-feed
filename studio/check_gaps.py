"""Швидка перевірка MCP-сервера Студії без Inspector: викликає find_content_gaps для кількох ігор
і зберігає повні відповіді в studio/data/gaps_check.json.

Сервер має бути запущений в іншому вікні:
    backend\\.venv\\Scripts\\python -m studio.mcp_server.server
Запуск (з кореня репозиторію):
    backend\\.venv\\Scripts\\python studio\\check_gaps.py
    backend\\.venv\\Scripts\\python studio\\check_gaps.py minecraft roblox
"""

import asyncio
import json
import sys
from pathlib import Path

from fastmcp import Client

URL = "http://127.0.0.1:8100/mcp"
OUT = Path(__file__).resolve().parent / "data" / "gaps_check.json"


async def main(games):
    results = {}
    async with Client(URL) as c:
        for game in games:
            r = await c.call_tool("find_content_gaps", {"game": game})
            data = r.structured_content
            results[game] = data
            print(f"\n=== {game}: іноземних відео {data['foreign_videos_considered']}, "
                  f"підтем {data['subtopics_found']}, покрито українською {data['covered_subtopics']}, відкинуто поодиноких {data['small_subtopics_skipped']}")
            for g in data["gaps"]:
                uk = g["closest_uk"]
                uk_txt = f"{uk['title'][:50]} ({uk['similarity']})" if uk else "немає"
                print(f"  • {g['representative_title'][:70]}")
                print(f"    {g['foreign_videos']} відео з {g['channels']} каналів, {g['total_views']:,} переглядів; найближче українське: {uk_txt}".replace(",", " "))
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nПовні відповіді: {OUT}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or ["brawl_stars", "minecraft", "valorant"]))
