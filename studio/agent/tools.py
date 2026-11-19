"""Підключення агента до двох MCP-серверів і обмеження для браузера.

Кожен сервер підключається окремо: якщо Playwright MCP недоступний, агент працює далі лише зі Студією
і знає про це з системного повідомлення (статус підключень передається в промпт).
"""

from contextlib import AsyncExitStack, asynccontextmanager
from urllib.parse import urlparse

from langchain_core.tools import StructuredTool, ToolException
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

# Дозволені сторінки трендів (погоджуються з викладачем). Усе інше браузер не відкриє.
ALLOWED_HOSTS = {"modrinth.com", "www.modrinth.com", "www.roblox.com", "roblox.com", "store.steampowered.com"}
# Які інструменти Playwright бачить модель: лише читання сторінок, без кліків, форм і вкладок.
PLAYWRIGHT_TOOLS = {"browser_navigate", "browser_snapshot"}
# Знімок сторінки буває на сотні тисяч символів; моделі вистачає початку.
MAX_PAGE_CHARS = 12_000


def _truncate(content):
    """Обрізає текстові блоки відповіді Playwright, щоб не витрачати контекст моделі."""
    if isinstance(content, str):
        return content[:MAX_PAGE_CHARS] + ("\n…[обрізано]" if len(content) > MAX_PAGE_CHARS else "")
    out = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block["text"]
            if len(text) > MAX_PAGE_CHARS:
                block = {**block, "text": text[:MAX_PAGE_CHARS] + "\n…[обрізано]"}
        out.append(block)
    return out


def _guard(tool: StructuredTool) -> StructuredTool:
    """Обгортка над інструментом Playwright: перевірка адреси й обрізання відповіді."""
    original = tool.coroutine

    async def guarded(**kwargs):
        if tool.name == "browser_navigate":
            host = (urlparse(kwargs.get("url", "")).hostname or "").lower()
            if host not in ALLOWED_HOSTS:
                raise ToolException(f"BLOCKED_URL: сайт {host or '(порожня адреса)'} не входить до дозволених: "
                                    f"{', '.join(sorted(ALLOWED_HOSTS))}")
        content, artifact = await original(**kwargs)
        return _truncate(content), artifact

    return StructuredTool(
        name=tool.name,
        description=tool.description,
        args_schema=tool.args_schema,
        coroutine=guarded,
        response_format="content_and_artifact",
        handle_tool_error=True,  # помилка інструмента повертається моделі як результат, а не валить агента
    )


@asynccontextmanager
async def connect(studio_url: str, playwright_url: str | None):
    """Відкриває по одній MCP-сесії на кожен сервер на весь запуск агента.

    Одна сесія на запуск важлива для Playwright: браузер прив'язаний до сесії, і якщо navigate та snapshot
    ідуть у різних сесіях, знімок робиться в новому порожньому браузері. Для Студії це просто швидше.
    Повертає (інструменти, статус підключень для промпту).
    """
    connections = {"studio": {"transport": "streamable_http", "url": studio_url}}
    if playwright_url:
        connections["playwright"] = {"transport": "streamable_http", "url": playwright_url}
    client = MultiServerMCPClient(connections)

    async with AsyncExitStack() as stack:
        tools, status = [], {}
        try:
            session = await stack.enter_async_context(client.session("studio"))
            studio_tools = await load_mcp_tools(session, server_name="studio")
        except Exception as e:  # без Студії агенту нема з чим працювати
            raise RuntimeError(f"MCP-сервер Студії недоступний за адресою {studio_url}. Запустіть: "
                               f"python -m studio.mcp_server.server ({type(e).__name__})") from e
        tools += studio_tools
        status["studio"] = f"підключено, інструменти: {', '.join(t.name for t in studio_tools)}"

        if playwright_url:
            try:
                pw_session = await stack.enter_async_context(client.session("playwright"))
                pw = [t for t in await load_mcp_tools(pw_session, server_name="playwright") if t.name in PLAYWRIGHT_TOOLS]
                tools += [_guard(t) for t in pw]
                status["playwright"] = f"підключено, інструменти: {', '.join(t.name for t in pw)}"
            except Exception as e:
                status["playwright"] = f"НЕДОСТУПНИЙ ({type(e).__name__}): свіжих трендів з браузера не буде"
        else:
            status["playwright"] = "вимкнено параметром --no-browser"
        yield tools, status
