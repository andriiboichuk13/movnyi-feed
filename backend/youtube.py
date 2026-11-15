"""Невелика обгортка над YouTube Data API v3 і спільні перевірки відео."""

import datetime as dt
import re

import requests

from config import SHORTS_MAX_SECONDS

API = "https://www.googleapis.com/youtube/v3"

# Вартість методів у одиницях квоти (з 10 000 на день).
COST = {"search": 100}


class YouTubeError(Exception):
    pass


class YouTube:
    def __init__(self, key: str):
        self.key = key
        self.units = 0

    def get(self, endpoint: str, **params):
        params["key"] = self.key
        r = requests.get(f"{API}/{endpoint}", params=params, timeout=30)
        self.units += COST.get(endpoint, 1)
        if r.status_code != 200:
            try:
                err = r.json()["error"]
                reason = (err.get("errors") or [{}])[0].get("reason", r.status_code)
                message = err.get("message", "")
            except Exception:
                reason, message = r.status_code, r.text[:200]
            raise YouTubeError(f"{endpoint}: {reason}. {message}")
        return r.json()

    def videos(self, ids):
        """Метадані до 50 відео за 1 одиницю квоти."""
        if not ids:
            return []
        data = self.get("videos", part="snippet,contentDetails,status", id=",".join(ids[:50]), maxResults=50)
        return data.get("items", [])


def parse_duration(iso: str) -> int:
    """PT1H2M3S → секунди."""
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def is_short(seconds: int) -> bool:
    # YouTube дозволяє Shorts до 3 хвилин, а за даними API їх не відрізнити від
    # звичайних коротких відео. Тому все, що не довше 3 хвилин, не рекомендуємо.
    return seconds <= SHORTS_MAX_SECONDS


def available_in_ukraine(content_details: dict) -> bool:
    rr = content_details.get("regionRestriction") or {}
    if "UA" in (rr.get("blocked") or []):
        return False
    if "allowed" in rr and "UA" not in rr["allowed"]:
        return False
    return True


def published_at(snippet: dict):
    return dt.datetime.fromisoformat(snippet["publishedAt"].replace("Z", "+00:00"))
