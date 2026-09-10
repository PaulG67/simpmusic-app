"""Lyrics translation.

Deliberately provider-agnostic and off by default: a self-hosted
LibreTranslate needs no key, and any OpenAI-compatible endpoint (including a
local Ollama or LM Studio) works for higher quality. Results are cached in
SQLite because the same song gets replayed a lot.
"""

import json

import httpx

from app.core.logging import logger
from app.core.settings import settings
from app.db import repo
from app.db.database import session_scope

MAX_LINES = 200

SYSTEM_PROMPT = (
    "Du bist ein Übersetzer für Songtexte. Übersetze jede Zeile einzeln und behalte "
    "die Reihenfolge sowie die Anzahl der Zeilen exakt bei. Übersetze sinngemäss und "
    "natürlich, nicht wörtlich. Antworte ausschliesslich mit einem JSON-Array aus "
    "Strings, ohne weitere Erklärungen."
)


def _lines_of(lyrics: dict) -> list[str]:
    if lyrics.get("synced"):
        return [line.get("text", "") for line in lyrics["synced"]]
    return (lyrics.get("text") or "").splitlines()


async def _libretranslate(lines: list[str], language: str) -> list[str] | None:
    payload = {"q": lines, "source": "auto", "target": language, "format": "text"}
    if settings.translate_api_key:
        payload["api_key"] = settings.translate_api_key

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(f"{settings.translate_url}/translate", json=payload)
    response.raise_for_status()

    translated = response.json().get("translatedText")
    if isinstance(translated, list):
        return [str(item) for item in translated]
    if isinstance(translated, str):
        return translated.splitlines()
    return None


async def _openai(lines: list[str], language: str) -> list[str] | None:
    headers = {"Content-Type": "application/json"}
    if settings.translate_api_key:
        headers["Authorization"] = f"Bearer {settings.translate_api_key}"

    body = {
        "model": settings.translate_model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Zielsprache: {language}\n\n" + json.dumps(lines, ensure_ascii=False),
            },
        ],
    }

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(f"{settings.translate_url}/chat/completions", headers=headers, json=body)
    response.raise_for_status()

    content = response.json()["choices"][0]["message"]["content"].strip()
    if content.startswith("```"):
        content = content.strip("`")
        content = content.split("\n", 1)[1] if "\n" in content else content

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return content.splitlines()

    return [str(item) for item in parsed] if isinstance(parsed, list) else None


async def translate_lyrics(video_id: str, lyrics: dict, language: str | None = None) -> dict | None:
    """Returns the translated lyrics, aligned line-by-line with the original."""
    target = (language or settings.translate_language).lower()

    lines = _lines_of(lyrics)
    if not lines:
        return None

    with session_scope() as session:
        cached = repo.get_translation(session, video_id, target)
    if cached:
        return {"language": target, "lines": cached.split("\n"), "text": cached, "cached": True}

    if not settings.translation_enabled:
        return None

    truncated = lines[:MAX_LINES]
    try:
        if settings.translate_provider == "libretranslate":
            translated = await _libretranslate(truncated, target)
        else:
            translated = await _openai(truncated, target)
    except Exception as exc:
        logger.warning("Übersetzung von {} fehlgeschlagen: {}", video_id, exc)
        return None

    if not translated:
        return None

    # Keep the arrays aligned so synced highlighting stays in step.
    if len(translated) < len(truncated):
        translated += [""] * (len(truncated) - len(translated))
    translated = translated[: len(truncated)]

    text = "\n".join(translated)
    with session_scope() as session:
        repo.save_translation(session, video_id, target, text)

    return {"language": target, "lines": translated, "text": text, "cached": False}
