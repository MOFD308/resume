import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


@dataclass
class Source:
    kind: str          # youtube | website | x | discord
    name: str
    category: str = "both"
    channel_id: str = ""
    url: str = ""
    handle: str = ""
    author_ids: list[str] = field(default_factory=list)


@dataclass
class Config:
    raw: dict
    sources: list[Source] = field(default_factory=list)

    def __getitem__(self, key):
        return self.raw[key]

    def get(self, key, default=None):
        return self.raw.get(key, default)

    @property
    def db_path(self) -> Path:
        p = Path(self.raw.get("database", "data/market_digest.db"))
        return p if p.is_absolute() else ROOT / p

    def by_kind(self, kind: str) -> list[Source]:
        return [s for s in self.sources if s.kind == kind]

    def source(self, kind: str, name: str) -> Source | None:
        return next((s for s in self.sources if s.kind == kind and s.name == name), None)


def load_config(path: str | Path | None = None) -> Config:
    _load_dotenv(ROOT / ".env")
    path = Path(path or os.environ.get("MARKET_DIGEST_CONFIG", ROOT / "config.yaml"))
    raw = yaml.safe_load(path.read_text()) or {}
    sources = []
    for kind in ("youtube", "websites", "x", "discord"):
        for entry in raw.get(kind) or []:
            sources.append(Source(kind=kind.rstrip("s") if kind == "websites" else kind, **entry))
    return Config(raw=raw, sources=sources)
