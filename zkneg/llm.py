"""Chat with a local open-weight model through Ollama.

Every request is cached on disk by its full content, so experiments are
resumable and re-running an analysis never re-queries the model.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

import requests

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
MODEL = os.environ.get("ZKN_MODEL", "qwen3:8b")
CACHE_PATH = Path(os.environ.get("ZKN_CACHE", Path(__file__).resolve().parent.parent / "runs" / "llm_cache.sqlite"))

_lock = threading.Lock()
_db: sqlite3.Connection | None = None
stats = {"calls": 0, "cached": 0, "prompt_tokens": 0, "output_tokens": 0, "seconds": 0.0}


def _cache() -> sqlite3.Connection:
    global _db
    if _db is None:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _db = sqlite3.connect(CACHE_PATH, check_same_thread=False)
        _db.execute("create table if not exists cache (key text primary key, response text)")
    return _db


def chat(
    messages: list[dict],
    schema: dict | None = None,
    temperature: float = 0.0,
    seed: int = 0,
    model: str | None = None,
    max_tokens: int = 1024,
) -> str:
    """Returns the assistant's reply text. With `schema`, the reply is JSON
    constrained to that schema."""
    body = {
        "model": model or MODEL,
        "messages": messages,
        "stream": False,
        "think": False,
        "options": {"temperature": temperature, "seed": seed, "num_predict": max_tokens, "num_ctx": 8192},
    }
    if schema is not None:
        body["format"] = schema
    key = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    with _lock:
        row = _cache().execute("select response from cache where key = ?", (key,)).fetchone()
    if row:
        stats["cached"] += 1
        return row[0]

    t = time.time()
    for attempt in range(3):
        try:
            r = requests.post(f"{OLLAMA_HOST}/api/chat", json=body, timeout=900)
            r.raise_for_status()
            break
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(5)
    data = r.json()
    text = data["message"]["content"]
    with _lock:
        stats["calls"] += 1
        stats["prompt_tokens"] += data.get("prompt_eval_count", 0)
        stats["output_tokens"] += data.get("eval_count", 0)
        stats["seconds"] += time.time() - t
        _cache().execute("insert or replace into cache values (?, ?)", (key, text))
        _cache().commit()
    return text


def chat_json(messages: list[dict], schema: dict, **kw) -> dict:
    text = chat(messages, schema=schema, **kw)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Constrained decoding can still be cut off by the token limit.
        return {"_unparseable": text}
