"""Thin adapter over the OpenAI chat format, so swapping a model is base URL + key + name.
Every call is logged to logs/calls.jsonl with tokens, latency and estimated cost (no user text).
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Type, TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "logs" / "calls.jsonl"

# name -> (base_url, default model, env var for key, INR per 1M input, INR per 1M output)
PRESETS: dict[str, tuple[str, str, str, float, float]] = {
    "sarvam": ("https://api.sarvam.ai/v1", "sarvam-105b", "SARVAM_API_KEY", 15.0, 60.0),
    "openai": ("https://api.openai.com/v1", "gpt-6-luna", "OPENAI_API_KEY", 0.0, 0.0),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-3.5-flash-lite", "GEMINI_API_KEY", 0.0, 0.0),
    "anthropic": ("https://api.anthropic.com/v1/", "claude-haiku-5-5", "ANTHROPIC_API_KEY", 0.0, 0.0),
    "groq": ("https://api.groq.com/openai/v1", "openai/gpt-oss-120b", "GROQ_API_KEY", 0.0, 0.0),
    "cerebras": ("https://api.cerebras.ai/v1", "gpt-oss-120b", "CEREBRAS_API_KEY", 0.0, 0.0),
    "openrouter": ("https://openrouter.ai/api/v1", "google/gemma-4-31b-it:free", "OPENROUTER_API_KEY", 0.0, 0.0),
}

T = TypeVar("T", bound=BaseModel)


def load_env(path: Path = ROOT / ".env") -> None:
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


@dataclass
class ModelConfig:
    provider: str = "sarvam"
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    temperature: float = 0.0
    max_tokens: int = 6000
    # Sarvam-105B thinks by default (2k+ hidden tokens per call); "off" sends reasoning_effort=null.
    reasoning: str = "off"

    @classmethod
    def from_env(cls) -> "ModelConfig":
        load_env()
        p = os.environ.get("LLM_PROVIDER", "sarvam")
        base, model, keyvar, _, _ = PRESETS.get(p, ("", "", "LLM_API_KEY", 0, 0))
        return cls(
            provider=p,
            model=os.environ.get("LLM_MODEL", model),
            base_url=os.environ.get("LLM_BASE_URL", base),
            api_key=os.environ.get("LLM_API_KEY") or os.environ.get(keyvar, ""),
            reasoning=os.environ.get("LLM_REASONING", "off"),
        )


class LLMError(RuntimeError):
    pass


@dataclass
class LLM:
    cfg: ModelConfig = field(default_factory=ModelConfig.from_env)
    tag: str = ""  # e.g. case id during evals, written into the log

    def __post_init__(self) -> None:
        headers = {"api-subscription-key": self.cfg.api_key} if self.cfg.provider == "sarvam" else None
        # max_retries=0: the loop in chat() decides what to retry
        self.client = OpenAI(base_url=self.cfg.base_url, api_key=self.cfg.api_key or "missing", default_headers=headers, timeout=60, max_retries=0)

    # ---------- raw call ----------
    def _kwargs(self, messages: list[dict], json_mode: bool) -> dict[str, Any]:
        """Per-provider parameter differences."""
        p, m = self.cfg.provider, self.cfg.model.lower()
        kw: dict[str, Any] = dict(model=self.cfg.model, messages=messages)
        reasoning_openai = p == "openai" and (m.startswith(("o1", "o3", "o4", "gpt-5", "gpt-6")))
        if p == "sarvam":
            kw["max_tokens"] = self.cfg.max_tokens
            kw["extra_body"] = {"reasoning_effort": None if self.cfg.reasoning == "off" else self.cfg.reasoning}
            return kw
        if reasoning_openai:
            kw["max_completion_tokens"] = self.cfg.max_tokens  # these models reject max_tokens and temperature=0
        else:
            kw["max_tokens"] = self.cfg.max_tokens
            kw["temperature"] = self.cfg.temperature
        if json_mode and p in ("openai", "gemini", "groq", "cerebras"):
            kw["response_format"] = {"type": "json_object"}
        return kw

    def chat(self, messages: list[dict], step: str, json_mode: bool = False) -> str:
        kwargs = self._kwargs(messages, json_mode)
        last_err: Exception | None = None
        for attempt in range(2):
            t0 = time.time()
            try:
                r = self.client.chat.completions.create(**kwargs)
                text = r.choices[0].message.content or ""
                self._log(step, r, time.time() - t0, finish=getattr(r.choices[0], "finish_reason", None))
                return text
            except Exception as e:
                last_err = e
                self._log_error(step, e, time.time() - t0)
                status = getattr(e, "status_code", None)
                transient = status in (408, 409, 429, 500, 502, 503, 504) or status is None
                if not transient or "insufficient_quota" in str(e) or "no credits" in str(e):
                    break
                time.sleep(2 * (attempt + 1))
        raise LLMError(f"{self.cfg.provider}/{self.cfg.model} failed at step '{step}': {str(last_err)[:300]}")

    # ---------- structured call ----------
    def chat_model(self, messages: list[dict], schema: Type[T], step: str) -> T:
        """Ask for JSON matching `schema`; on parse/validation error, retry once with the error shown."""
        text = self.chat(messages, step, json_mode=True)
        try:
            return schema.model_validate(extract_json(text))
        except (ValueError, ValidationError) as e:
            fix = messages + [
                {"role": "assistant", "content": text[:4000]},
                {"role": "user", "content": f"That was not valid JSON for the schema. Error: {str(e)[:800]}\nReturn ONLY the corrected JSON object."},
            ]
            text2 = self.chat(fix, step + ":repair", json_mode=True)
            try:
                return schema.model_validate(extract_json(text2))
            except (ValueError, ValidationError) as e2:
                raise LLMError(f"model output did not match the expected format at step '{step}': {str(e2)[:200]}")

    # ---------- logging ----------
    def _log_error(self, step: str, e: Exception, secs: float) -> None:
        LOG.parent.mkdir(exist_ok=True)
        with LOG.open("a") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "tag": self.tag, "step": step, "provider": self.cfg.provider,
                                "model": self.cfg.model, "error": type(e).__name__, "status": getattr(e, "status_code", None),
                                "latency_s": round(secs, 2), "in_tokens": 0, "out_tokens": 0, "cost_inr": 0.0}) + "\n")

    def _log(self, step: str, r: Any, secs: float, finish: str | None = None) -> None:
        u = getattr(r, "usage", None)
        pin = getattr(u, "prompt_tokens", 0) or 0
        pout = getattr(u, "completion_tokens", 0) or 0
        _, _, _, cin, cout = PRESETS.get(self.cfg.provider, ("", "", "", 0, 0))
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "tag": self.tag, "step": step,
            "provider": self.cfg.provider, "model": self.cfg.model,
            "in_tokens": pin, "out_tokens": pout, "latency_s": round(secs, 2),
            "cost_inr": round(pin / 1e6 * cin + pout / 1e6 * cout, 5),
            "finish": finish,  # "length" = output was cut off at max_tokens
        }
        LOG.parent.mkdir(exist_ok=True)
        with LOG.open("a") as f:
            f.write(json.dumps(rec) + "\n")


def extract_json(text: str) -> Any:
    """Models wrap JSON in fences or prose; take the outermost {...} object."""
    t = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    t = re.sub(r"^```(?:json)?|```$", "", t.strip(), flags=re.M).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    start = t.find("{")
    if start < 0:
        raise ValueError("no JSON object in model output")
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(t[start:], start):
        if in_str:
            esc = (ch == "\\") and not esc
            if ch == '"' and not esc:
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(t[start:i + 1])
    raise ValueError("unbalanced JSON object in model output")
