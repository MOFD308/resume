"""All Claude calls: per-item extraction and newsletter synthesis."""
import json
import logging

import anthropic

from .models import Brief, Extraction

log = logging.getLogger(__name__)

# Keeps each request well inside the context window; a 3-hour video transcript is ~40k tokens.
MAX_CONTENT_CHARS = 400_000

EXTRACT_SYSTEM = """You read content from stock-market commentators (YouTube transcripts, tweets, \
Discord messages, blog pages) and extract it into structured data for a personal trading dashboard.

Price levels:
- Only extract levels the author actually states for a specific instrument. Never invent or infer \
levels that are not in the text. If there are none, return an empty list.
- Normalise tickers: "spy"/"标普ETF" -> SPY, "纳指"/"NQ" futures -> NQ, "ES" -> ES, "SPX" stays SPX, \
"$NVDA" -> NVDA. Keep index, ETF and futures symbols distinct.
- Transcripts are auto-generated and may mis-hear numbers ("five eighty five" = 585). Use the \
instrument's context to read them correctly; if a number is ambiguous, lower the conviction.
- level_type: support/resistance for key levels, target for price objectives, stop for \
invalidation, entry for suggested entries, pivot for a line in the sand that flips bias.

Macro: mark is_macro when the content discusses rates, the Fed, inflation, jobs, war/geopolitics, \
fiscal policy, liquidity, credit or similar big-picture drivers of markets.

All free-text fields (summary, note, macro_summary, view, market_impact) must be written in \
Simplified Chinese. Tickers and numbers stay as-is."""

BRIEF_SYSTEM = """You are writing a concise Chinese-language market newsletter for one private \
investor. You are given structured data aggregated from several commentators the reader follows. \
Synthesise it: highlight where multiple sources agree (consensus levels), point out disagreements, \
and relate levels to the current price when it is given. Do not add levels or facts that are not in \
the data. Attribute views to their authors by name. This is a digest of other people's opinions, \
not investment advice."""


class LLM:
    def __init__(self, model: str, effort: str = "medium"):
        self.client = anthropic.Anthropic()
        self.model = model
        self.effort = effort

    def _parse(self, system: str, user: str | list, schema, max_tokens: int = 16000):
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            messages=[{"role": "user", "content": user}],
            output_format=schema,
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("model declined the request")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("response hit max_tokens")
        return response.parsed_output

    def extract(self, item: dict) -> Extraction:
        content = item["content"] or ""
        if len(content) > MAX_CONTENT_CHARS:
            raise ValueError(f"content is {len(content)} chars, over the {MAX_CONTENT_CHARS} limit")
        user = (
            f"Source type: {item['kind']}\nAuthor: {item['author']}\n"
            f"Author focus: {item['category']}\nPublished (UTC): {item['published_at']}\n"
            f"Title: {item.get('title') or ''}\n\n<content>\n{content}\n</content>"
        )
        images = json.loads(item.get("images") or "[]")
        if images:  # Discord screenshots: charts or level tables as pictures
            user = [*({"type": "image", "source": {"type": "url", "url": u}} for u in images),
                    {"type": "text", "text": user + "\n\nThe attached images are part of this content."}]
        return self._parse(EXTRACT_SYSTEM, user, Extraction)

    def brief(self, kind: str, data: dict, instructions: str | None = None) -> Brief:
        user = (f"Newsletter: {kind}\n\n" + (f"{instructions}\n\n" if instructions else "")
                + "Data (JSON):\n" + json.dumps(data, ensure_ascii=False, default=str))
        return self._parse(BRIEF_SYSTEM, user, Brief)
