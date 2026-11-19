"""Схеми результатів інструментів. З них MCP SDK будує outputSchema, яку бачить модель."""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

Lang = Literal["uk", "ru", "en"]
Level = Literal["low", "medium", "high"]


class VideoRef(BaseModel):
    video_id: str
    title: str
    channel: str
    lang: Lang
    views: int = Field(description="Перегляди на момент збирання датасету")
    published_at: str = Field(description="Дата публікації, YYYY-MM-DD")
    url: str
    similarity: Optional[float] = Field(None, description="Схожість з темою/ідеєю/підтемою, 0–1 (модель e5-small)")


class LangStats(BaseModel):
    videos: int
    channels: int
    total_views: int
    median_views: int
    latest_published: Optional[str]
    top_videos: List[VideoRef] = Field(description="До 3 найпопулярніших відео цієї мови")


class NicheCoverage(BaseModel):
    game: str
    topic: Optional[str]
    window_days: int
    as_of: str = Field(description="Дата датасету, від якої відраховується період")
    min_similarity: Optional[float]
    margin: Optional[float]
    thresholds: Optional[Dict[Lang, float]] = Field(description="Фактичний поріг схожості для кожної мови: max(min_similarity, медіана мови + margin)")
    by_lang: Dict[Lang, LangStats]
    uk_to_foreign_ratio: Optional[float] = Field(description="Українських відео на одне іноземне (ru+en); null, якщо іноземних немає")
    verdict: Literal["underserved", "competitive", "saturated", "insufficient_data"]
    verdict_rule: str = Field(description="Правило, за яким отримано verdict")
    notes: List[str]


class ContentGap(BaseModel):
    gap_id: str
    representative_title: str = Field(description="Назва найпопулярнішого іноземного відео підтеми")
    foreign_videos: int
    channels: int = Field(description="Скільки різних каналів зняли відео цієї підтеми")
    by_lang: Dict[Literal["ru", "en"], int]
    total_views: int
    median_views: int
    newest_published: str
    examples: List[VideoRef]
    closest_uk: Optional[VideoRef] = Field(description="Найближче українське відео гри; null, якщо українських відео гри немає")


class ContentGaps(BaseModel):
    game: str
    window_days: int
    as_of: str
    min_foreign_views: int
    covered_similarity: float
    min_videos: int
    min_channels: int
    foreign_videos_considered: int
    subtopics_found: int
    covered_subtopics: int
    small_subtopics_skipped: int = Field(description="Підтем, відкинутих через замало відео чи каналів")
    gaps: List[ContentGap] = Field(description="Порожній список — прогалин не знайдено (це успішний результат, не помилка)")


class ScoreComponent(BaseModel):
    name: Literal["demand", "competition", "freshness"]
    level: Literal["low", "medium", "high", "unknown"]
    value: float
    explanation: str


class IdeaScore(BaseModel):
    game: str
    idea: str
    window_days: int
    as_of: str
    min_similarity: float
    margin: float
    thresholds: Dict[Literal["foreign", "uk"], float] = Field(description="Фактичні пороги схожості: для іноземних і для українських відео окремо")
    components: List[ScoreComponent]
    verdict: Literal["strong_opportunity", "worth_trying", "moderate", "crowded", "weak_demand"]
    verdict_rule: str
    foreign_evidence: List[VideoRef]
    uk_evidence: List[VideoRef]


class PackageIssue(BaseModel):
    severity: Literal["error", "warning", "suggestion"]
    field: Literal["title", "description", "tags"]
    code: str
    message: str


class PackageReport(BaseModel):
    game: str
    valid: bool = Field(description="true, якщо немає жодної помилки рівня error")
    detected_language: Literal["uk", "ru", "unknown"]
    title_length: int
    tags_total_chars: int
    hashtags: int
    issues: List[PackageIssue]


class SavedPlan(BaseModel):
    plan_id: str
    created: bool = Field(description="false — такий план уже був збережений, повернуто наявний")
    path: str
    plans_total: int


class GameInfo(BaseModel):
    slug: str
    name: str
    videos: Dict[Lang, int]


class DatasetInfo(BaseModel):
    built_at: str
    model: str
    videos: int
    games: List[GameInfo]
