"""Load the verified corpus and serve small per-topic packs.

Decision: no vector database. The corpus is ~24 entries (~15k tokens). After the
user confirms the dispute type, the matching topic pack (a few thousand tokens) goes
straight into the prompt. Retrieval errors cannot happen when there is no retriever.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus" / "sections.json"


@lru_cache
def load() -> dict:
    return json.loads(CORPUS.read_text())


def by_id() -> dict[str, dict]:
    return {s["id"]: s for s in load()["sections"]}


def superseded() -> list[dict]:
    return load()["superseded"]


def pack(topic: str) -> list[dict]:
    return [s for s in load()["sections"] if topic in s["topics"]]


def pack_text(topic: str) -> str:
    out = []
    for s in pack(topic):
        label = "VERBATIM" if s.get("kind") == "verbatim" else "SUMMARY (not statute text)"
        out.append(f"[{s['id']}] {s['act']} {s['section']} — {s['heading']} ({label}; in force from {s['effective_from']})\n{s['text']}")
    return "\n\n".join(out)


def norm(t: str) -> str:
    t = t.replace("“", '"').replace("”", '"').replace("’", "'").replace("—", "-").replace("–", "-")
    return re.sub(r"\s+", " ", t).strip().lower()
