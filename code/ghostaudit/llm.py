"""Groq client: cached, retried, token-tracked.  Used only for evidence *interpretation*:

* ``classify_intent``  - ambiguous request text (never overrides an explicit negative cue)
* ``describe_image``   - vision transcription of screenshots whose OCR is unusable

All decisions (status, findings, plan) stay in the deterministic engine.  Every call's token usage is taken from the
API response ``usage`` field; nothing is estimated or fabricated.  Without ``GROQ_API_KEY`` the client refuses to run.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

API_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_TEXT_MODEL = os.environ.get("GROQ_TEXT_MODEL", "openai/gpt-oss-20b")
DEFAULT_VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
# USD per 1M tokens (input, output).  Defaults are configurable estimates - verify on Groq's pricing page.
DEFAULT_PRICES = {
    "llama-3.3-70b-versatile": (0.59, 0.79),
    "openai/gpt-oss-20b": (0.15, 0.60),
    "openai/gpt-oss-120b": (0.59, 0.79),
    DEFAULT_VISION_MODEL: (0.11, 0.34),
}

INTENT_SYSTEM = (
    "You classify customer privacy requests. Answer ONLY with JSON: "
    '{"authorizes_deletion": true|false, "reason": "<short>"}. '
    "authorizes_deletion is true only if the person asks for their account/personal data to be deleted, erased or closed "
    "(or asks to verify that a deletion they requested was completed). Requests for access, export, correction, "
    "marketing opt-out, login help, or statements that they want to keep the account are false. Any language.")


@dataclass
class Usage:
    calls: int = 0
    cache_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    failures: int = 0


@dataclass
class GroqClient:
    api_key: str
    text_model: str = DEFAULT_TEXT_MODEL
    vision_model: str = DEFAULT_VISION_MODEL
    cache_dir: Path = Path(".cache/groq")
    max_retries: int = 4
    timeout: float = 60.0
    prices: dict = field(default_factory=lambda: dict(DEFAULT_PRICES))
    usage: dict = field(default_factory=dict)
    transport: object = None            # injectable for tests: callable(payload)->dict

    @classmethod
    def from_env(cls, **kw):
        key = os.environ.get("GROQ_API_KEY", "")
        if not key:
            raise RuntimeError("GROQ_API_KEY is not set (copy .env.example to .env and export it)")
        return cls(api_key=key, text_model=os.environ.get("GROQ_TEXT_MODEL", DEFAULT_TEXT_MODEL),
                   vision_model=os.environ.get("GROQ_VISION_MODEL", DEFAULT_VISION_MODEL),
                   cache_dir=Path(os.environ.get("GROQ_CACHE_DIR", ".cache/groq")), **kw)

    # ------------------------------------------------------------------ core
    def _key(self, payload: dict) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def _post(self, payload: dict) -> dict:
        if self.transport is not None:
            return self.transport(payload)
        import requests
        last = None
        for attempt in range(self.max_retries):
            try:
                r = requests.post(API_URL, json=payload, timeout=self.timeout,
                                  headers={"Authorization": f"Bearer {self.api_key}"})
                if r.status_code == 429 or r.status_code >= 500:
                    wait = float(r.headers.get("retry-after", 2 ** attempt))
                    last = RuntimeError(f"HTTP {r.status_code}")
                    time.sleep(min(wait, 30))
                    continue
                r.raise_for_status()
                return r.json()
            except Exception as exc:  # network error
                last = exc
                time.sleep(2 ** attempt)
        raise RuntimeError(f"Groq request failed after retries: {last}")

    def chat(self, model: str, messages: list, max_tokens: int = 300) -> str:
        payload = {"model": model, "messages": messages, "temperature": 0, "max_tokens": max_tokens}
        u = self.usage.setdefault(model, Usage())
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cp = self.cache_dir / (self._key(payload) + ".json")
        if cp.exists():
            u.cache_hits += 1
            return json.loads(cp.read_text())["text"]
        try:
            data = self._post(payload)
        except Exception:
            u.failures += 1
            raise
        text = data["choices"][0]["message"]["content"]
        use = data.get("usage", {})
        u.calls += 1
        u.input_tokens += int(use.get("prompt_tokens", 0))
        u.output_tokens += int(use.get("completion_tokens", 0))
        cp.write_text(json.dumps({"text": text, "usage": use}))
        return text

    # ------------------------------------------------------------------ tasks
    def classify_intent(self, text: str, fallback: dict) -> dict:
        try:
            out = self.chat(self.text_model, [{"role": "system", "content": INTENT_SYSTEM},
                                              {"role": "user", "content": text}], max_tokens=80)
            j = json.loads(out[out.index("{"): out.rindex("}") + 1])
            return {"authorizes_deletion": bool(j["authorizes_deletion"]), "method": "llm", "confidence": 0.9,
                    "reason": str(j.get("reason", ""))[:200], "ambiguous": False}
        except Exception as exc:
            fb = dict(fallback)
            fb["reason"] += f" (llm unavailable: {type(exc).__name__})"
            return fb

    def describe_image(self, path: Path) -> str:
        b64 = base64.b64encode(Path(path).read_bytes()).decode()
        msg = [{"role": "user", "content": [
            {"type": "text", "text": "Transcribe every resource id (e.g. account_123, support_45), email address, "
                                     "phone number, status and date visible. Plain text, one fact per line. Do not guess."},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}]
        return self.chat(self.vision_model, msg, max_tokens=700)

    # ------------------------------------------------------------------ reporting
    def usage_summary(self) -> dict:
        rows, tot = {}, Usage()
        for m, u in self.usage.items():
            pi, po = self.prices.get(m, (0.0, 0.0))
            cost = u.input_tokens / 1e6 * pi + u.output_tokens / 1e6 * po
            rows[m] = {"calls": u.calls, "cache_hits": u.cache_hits, "input_tokens": u.input_tokens,
                       "output_tokens": u.output_tokens, "total_tokens": u.input_tokens + u.output_tokens,
                       "est_cost_usd": round(cost, 6), "failures": u.failures}
            tot.calls += u.calls; tot.input_tokens += u.input_tokens; tot.output_tokens += u.output_tokens
        total_cost = sum(r["est_cost_usd"] for r in rows.values())
        return {"models": rows, "calls": tot.calls, "input_tokens": tot.input_tokens, "output_tokens": tot.output_tokens,
                "total_tokens": tot.input_tokens + tot.output_tokens, "est_cost_usd": round(total_cost, 6)}
