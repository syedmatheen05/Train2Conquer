"""The ONE place Train2Conquer talks to an AI provider: Google Gemini.

Every plan type (Basic, Personalized, Advanced) generates through
`generate_json()`. Nothing else in the project imports an AI SDK.

What it handles, so callers don't have to:
  * configuration  - key from GEMINI_API_KEY, model from GEMINI_MODEL (optional);
  * transient errors (rate limit 429, server 5xx, timeouts, dropped connection)
    -> a few retries with a growing pause, then a clean error. Never an endless loop;
  * permanent errors (bad key, bad request) -> no pointless retries;
  * empty answers and answers cut off by the output limit;
  * messy JSON (```json fences, text around the object) -> safe extraction;
  * logging: only the error TYPE and HTTP status ever reach the logs - never the
    API key, the prompt, or anything about the user.

Errors carry a short safe `code` (config | timeout | rate_limit | provider_error |
invalid_output). Users only ever see friendly text chosen from that code.
"""
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Optional

import config

RETRY_PAUSES_SECONDS = (2, 5, 10)           # waits between transport retries
_sleep = time.sleep                          # replaced in tests so they don't wait


class GeminiError(Exception):
    """code is one of: config, timeout, rate_limit, provider_error, invalid_output."""

    def __init__(self, code, detail=""):
        super().__init__(detail or code)
        self.code = code


@dataclass
class GeminiResult:
    text: str
    model: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cached_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None


def _client():
    api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise GeminiError("config", "GEMINI_API_KEY is not set")
    from google import genai
    from google.genai import types
    return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=config.ai_timeout_seconds() * 1000))


def classify(exc):
    """(code, retry_worthwhile) for an exception raised by the SDK or the network."""
    status = getattr(exc, "code", None)
    name = type(exc).__name__
    if isinstance(status, int):
        if status == 429:
            return "rate_limit", True
        if status in (408, 504):
            return "timeout", True
        if status >= 500:
            return "provider_error", True
        if status in (401, 403):
            return "config", False           # bad / unauthorised key: retrying can't help
        return "provider_error", False       # other 4xx: our request was refused
    if "Timeout" in name or isinstance(exc, TimeoutError):
        return "timeout", True
    if any(word in name for word in ("Connect", "Network", "Protocol", "Remote", "Read")):
        return "provider_error", True        # dropped connection: worth another go
    return "provider_error", False


def extract_json_object(text):
    """Pulls ONE JSON object out of a model answer. Returns a dict or raises ValueError."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("empty answer")
    cleaned = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.S | re.I)
    if fence:
        cleaned = fence.group(1)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("no JSON object in answer")
        try:
            value = json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError("answer is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("answer is not a JSON object")
    return value


def _tokens(response):
    meta = getattr(response, "usage_metadata", None)
    pick = lambda name: (getattr(meta, name, None) if meta is not None else None)
    return pick("prompt_token_count"), pick("candidates_token_count"), pick("cached_content_token_count"), pick("thoughts_token_count")


def generate_json(contents, system_instruction=None, tier="basic"):
    """One logical request to Gemini, with bounded retries for transient failures.

    Returns GeminiResult (the raw answer text + token counts). Raises GeminiError.
    The answer is NOT validated here - plan_generator checks it.
    """
    client = _client()
    model = config.gemini_model()
    settings = {"response_mime_type": "application/json", "max_output_tokens": config.max_output_tokens()}
    if system_instruction:
        settings["system_instruction"] = system_instruction
    max_tries = config.gemini_max_tries()
    for attempt in range(1, max_tries + 1):
        try:
            response = client.models.generate_content(model=model, contents=contents, config=settings)
        except GeminiError:
            raise
        except Exception as exc:
            code, retry = classify(exc)
            # Server log: error type and HTTP status only (no key, no prompt, no user data).
            print(f"GEMINI ERROR tier={tier} try={attempt}/{max_tries} code={code} type={type(exc).__name__} status={getattr(exc, 'code', None)}")
            if retry and attempt < max_tries:
                _sleep(RETRY_PAUSES_SECONDS[min(attempt - 1, len(RETRY_PAUSES_SECONDS) - 1)])
                continue
            raise GeminiError(code, type(exc).__name__)
        text = getattr(response, "text", None)
        tokens_in, tokens_out, cached, reasoning = _tokens(response)
        if not isinstance(text, str) or not text.strip():
            print(f"GEMINI EMPTY ANSWER tier={tier} try={attempt}/{max_tries}")
            if attempt < max_tries:
                _sleep(RETRY_PAUSES_SECONDS[0])
                continue
            raise GeminiError("invalid_output", "empty answer")
        return GeminiResult(text=text, model=model, input_tokens=tokens_in, output_tokens=tokens_out,
                            cached_tokens=cached, reasoning_tokens=reasoning)
    raise GeminiError("provider_error", "no answer")
