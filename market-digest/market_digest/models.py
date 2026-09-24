"""Schemas Claude fills in when reading a post, video transcript, tweet or chat message."""
from typing import Literal

from pydantic import BaseModel, Field

LevelType = Literal["support", "resistance", "target", "stop", "entry", "pivot", "other"]


class PriceLevel(BaseModel):
    ticker: str = Field(description="Ticker symbol, e.g. SPY, QQQ, NVDA, ES (futures), SPX")
    level_type: LevelType
    price: float = Field(description="The price level. For a zone, the lower bound.")
    price_high: float | None = Field(description="Upper bound if the author gave a zone, else null")
    direction: Literal["bullish", "bearish", "neutral"]
    timeframe: Literal["intraday", "swing", "long_term", "unspecified"]
    conviction: Literal["low", "medium", "high"]
    note: str = Field(description="One short sentence in Chinese: what the author says happens at this level")
    quote: str = Field(description="The shortest verbatim excerpt from the source that states this level")


class MacroTheme(BaseModel):
    topic: Literal["rates", "fed", "inflation", "jobs", "geopolitics", "war", "fiscal", "liquidity",
                   "earnings", "credit", "fx", "commodities", "china", "other"]
    view: str = Field(description="The author's view on this theme, in Chinese, 1-2 sentences")
    market_impact: str = Field(description="Expected impact on stocks/bonds/etc., in Chinese")


class Extraction(BaseModel):
    summary: str = Field(description="2-4 sentence Chinese summary of the whole content")
    levels: list[PriceLevel]
    is_macro: bool = Field(description="True if a meaningful part of the content is macro analysis")
    macro_summary: str | None = Field(
        description="If is_macro: a structured Chinese summary of the macro analysis (key points as "
                    "short bullet lines starting with '- '). Otherwise null.")
    macro_themes: list[MacroTheme]
    risk_level: Literal["low", "moderate", "elevated", "high"] | None = Field(
        description="If is_macro: the author's overall assessment of market risk. Otherwise null.")


class Brief(BaseModel):
    headline: str = Field(description="One-line Chinese headline for this newsletter")
    overview: str = Field(description="Chinese overview paragraph(s) synthesising all sources")
    focus: list[str] = Field(description="Chinese bullet points: the most important tickers/levels to watch and why")
    disagreements: list[str] = Field(description="Chinese bullet points where sources disagree; empty if none")
