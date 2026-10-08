"""Lexical retrieval over project docs and YAML definitions (BM25 + exact-acronym boost).

Dense embeddings are an optional later layer; for a corpus this small, BM25 with an acronym boost already finds
"what is VCP" or "how is RVol20 defined" reliably, needs no model, and is fully deterministic.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from nivesh.config import BACKEND_DIR, REPO_DIR, settings

_TOKEN = re.compile(r"[A-Za-z]{2,}|\d+")  # splits snake_case and RVol20 into words and digits
_ACRONYM = re.compile(r"\b[A-Z][A-Z0-9]{1,}\b")
_STOP = frozenset("the a an of to in is are what how why does do for on and or with by as at it this that be".split())
ACRONYM_BOOST = 1.5
MAX_CHUNK_CHARS = 1400


@dataclass(frozen=True)
class Chunk:
    source: str
    heading: str
    text: str


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text) if t.lower() not in _STOP]


def _split(text: str) -> list[str]:
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]
    parts, current = [], ""
    for para in text.split("\n\n"):
        if current and len(current) + len(para) > MAX_CHUNK_CHARS:
            parts.append(current)
            current = ""
        current += para + "\n\n"
    if current.strip():
        parts.append(current)
    return parts


def chunk_markdown(path: Path, rel: str) -> list[Chunk]:
    out, heading, buf = [], path.stem, []

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            out.extend(Chunk(rel, heading, piece.strip()) for piece in _split(body) if piece.strip())

    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if re.match(r"^#{1,4}\s+\S", line):
            flush()
            buf = []
            heading = line.lstrip("# ").strip()
        else:
            buf.append(line)
    flush()
    return out


def chunk_yaml(path: Path, rel: str) -> list[Chunk]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return []
    out: list[Chunk] = []

    def emit(heading: str, value: Any) -> None:
        text = yaml.safe_dump(value, sort_keys=False, allow_unicode=True).strip()
        out.extend(Chunk(rel, heading, piece.strip()) for piece in _split(text) if piece.strip())

    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
                for item in value:
                    name = item.get("name") or item.get("id") or item.get("metric") or key
                    emit(f"{path.stem} / {key} / {name}", item)
            else:
                emit(f"{path.stem} / {key}", {key: value})
    return out


def default_roots() -> list[tuple[Path, str]]:
    return [
        (BACKEND_DIR, "README.md"),
        (settings.context_dir, "*.yaml"),
        (settings.context_dir / "metrics", "*.yaml"),
        (settings.context_dir / "strategies", "*.yaml"),
    ]


class KnowledgeIndex:
    def __init__(self, roots: list[tuple[Path, str]] | None = None) -> None:
        self.chunks: list[Chunk] = []
        for folder, pattern in roots if roots is not None else default_roots():
            if not folder.exists():
                continue
            for path in sorted(folder.glob(pattern)):
                try:
                    rel = path.relative_to(REPO_DIR).as_posix()
                except ValueError:
                    rel = path.name
                loader = chunk_yaml if path.suffix in {".yaml", ".yml"} else chunk_markdown
                self.chunks.extend(loader(path, rel))
        self._docs = [Counter(_tokens(f"{c.heading} {c.text}")) for c in self.chunks]
        self._lengths = [sum(d.values()) for d in self._docs]
        self._avg = (sum(self._lengths) / len(self._lengths)) if self._lengths else 1.0
        df: Counter[str] = Counter()
        for d in self._docs:
            df.update(d.keys())
        n = max(len(self._docs), 1)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, k: int = 4) -> list[dict[str, Any]]:
        terms = _tokens(query)
        acronyms = {a for a in _ACRONYM.findall(query) if len(a) >= 2}
        scored: list[tuple[float, int]] = []
        for i, doc in enumerate(self._docs):
            score = 0.0
            for t in terms:
                f = doc.get(t, 0)
                if f:
                    norm = f * 2.5 / (f + 1.5 * (0.25 + 0.75 * self._lengths[i] / self._avg))
                    score += self._idf.get(t, 0.0) * norm
            if score > 0 and acronyms:
                body = f"{self.chunks[i].heading} {self.chunks[i].text}"
                score += ACRONYM_BOOST * sum(1 for a in acronyms if re.search(rf"\b{re.escape(a)}\b", body))
            elif acronyms:
                body = f"{self.chunks[i].heading} {self.chunks[i].text}"
                score = ACRONYM_BOOST * sum(1 for a in acronyms if re.search(rf"\b{re.escape(a)}\b", body)) * 0.5
            if score > 0:
                scored.append((score, i))
        scored.sort(key=lambda s: -s[0])
        return [
            {
                "source": self.chunks[i].source,
                "heading": self.chunks[i].heading,
                "text": self.chunks[i].text,
                "score": round(s, 3),
            }
            for s, i in scored[:k]
        ]
