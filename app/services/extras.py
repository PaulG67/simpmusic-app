"""Community data sources: SponsorBlock and ReturnYouTubeDislike."""

import httpx

from app.core.logging import logger
from app.core.settings import settings
from app.ytm.cache import TTLCache

SPONSORBLOCK_URL = "https://sponsor.ajay.app/api/skipSegments"
DISLIKE_URL = "https://returnyoutubedislikeapi.com/votes"

# Categories worth skipping in a music context; "music_offtopic" is exactly the
# non-music intro/outro padding that SponsorBlock was extended for.
SKIP_CATEGORIES = ("music_offtopic", "sponsor", "selfpromo", "intro", "outro")

_cache = TTLCache(ttl=21600, max_items=2000)


async def skip_segments(video_id: str) -> list[dict]:
    if not settings.sponsorblock_enabled:
        return []

    cached = _cache.get(f"sb:{video_id}")
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(
                SPONSORBLOCK_URL,
                params={"videoID": video_id, "category": list(SKIP_CATEGORIES)},
            )
        segments = (
            [
                {
                    "start": entry["segment"][0],
                    "end": entry["segment"][1],
                    "category": entry.get("category", ""),
                }
                for entry in response.json()
                if entry.get("segment")
            ]
            if response.status_code == 200
            else []
        )
    except Exception as exc:
        logger.debug("SponsorBlock nicht erreichbar: {}", exc)
        segments = []

    segments.sort(key=lambda entry: entry["start"])
    _cache.set(f"sb:{video_id}", segments)
    return segments


async def votes(video_id: str) -> dict | None:
    if not settings.return_youtube_dislike:
        return None

    cached = _cache.get(f"ryd:{video_id}")
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(DISLIKE_URL, params={"videoId": video_id})
        if response.status_code != 200:
            return None
        data = response.json()
        result = {
            "likes": data.get("likes", 0),
            "dislikes": data.get("dislikes", 0),
            "rating": round(data.get("rating", 0), 2),
            "views": data.get("viewCount", 0),
        }
    except Exception as exc:
        logger.debug("ReturnYouTubeDislike nicht erreichbar: {}", exc)
        return None

    _cache.set(f"ryd:{video_id}", result, ttl=86400)
    return result
