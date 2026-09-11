"""Audio and cover delivery.

Amperfy seeks and downloads for offline use, so byte-range support is
mandatory: partial requests must answer 206 with an accurate `Content-Range`.
"""

import asyncio
import hashlib
import re
from pathlib import Path

import httpx
from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse

from app.core.logging import logger
from app.core.settings import settings
from app.navidrome import bridge as navidrome
from app.navidrome import ids as nd_ids
from app.ytm import mapper, streams

CHUNK_SIZE = 256 * 1024
RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")

_http = httpx.AsyncClient(
    follow_redirects=True,
    timeout=httpx.Timeout(30.0, read=120.0),
    headers={"User-Agent": "Mozilla/5.0 (compatible; Music-Play)"},
)
_prefetching: set[str] = set()
_prefetch_lock = asyncio.Lock()


async def aclose() -> None:
    await _http.aclose()


def parse_range(header: str | None, size: int | None) -> tuple[int, int | None] | None:
    if not header:
        return None
    match = RANGE_RE.search(header)
    if not match:
        return None

    raw_start, raw_end = match.group(1), match.group(2)
    if raw_start == "":
        if not raw_end or size is None:
            return None
        length = int(raw_end)
        return max(size - length, 0), size - 1

    start = int(raw_start)
    end = int(raw_end) if raw_end else (size - 1 if size else None)
    return start, end


# --------------------------------------------------------------------------
# Audio
# --------------------------------------------------------------------------


def _file_iterator(path: Path, start: int, end: int):
    with path.open("rb") as handle:
        handle.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = handle.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def serve_cached_file(entry: dict, range_header: str | None) -> Response:
    path: Path = entry["path"]
    size = path.stat().st_size
    common = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=86400",
        "Content-Disposition": f'inline; filename="{path.name}"',
    }

    parsed = parse_range(range_header, size)
    if parsed is None:
        return FileResponse(path, media_type=entry["content_type"], headers=common)

    start, end = parsed
    end = min(end if end is not None else size - 1, size - 1)
    if start >= size:
        return Response(status_code=416, headers={**common, "Content-Range": f"bytes */{size}"})

    return StreamingResponse(
        _file_iterator(path, start, end),
        status_code=206,
        media_type=entry["content_type"],
        headers={
            **common,
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(end - start + 1),
        },
    )


async def _prefetch_to_cache(source: streams.StreamSource) -> None:
    """Pull the whole track into the disk cache in the background.

    The playing client only ever receives the proxied range it asked for; this
    just makes the *next* play (and Amperfy's offline download) instant.
    """
    target = settings.audio_cache_dir / f"{source.video_id}.{source.suffix}"
    partial = target.with_name(target.name + ".part")

    try:
        with partial.open("wb") as handle:
            async with _http.stream("GET", source.url) as upstream:
                if upstream.status_code >= 400:
                    raise RuntimeError(f"HTTP {upstream.status_code}")
                async for chunk in upstream.aiter_bytes(CHUNK_SIZE):
                    handle.write(chunk)
        partial.replace(target)
        await run_in_threadpool(
            streams.register_cached, source.video_id, target, source.content_type, source.suffix, source.bitrate
        )
        logger.debug("Titel {} im Cache abgelegt", source.video_id)
    except Exception as exc:
        logger.warning("Vorab-Caching von {} fehlgeschlagen: {}", source.video_id, exc)
        partial.unlink(missing_ok=True)
    finally:
        async with _prefetch_lock:
            _prefetching.discard(source.video_id)


async def _schedule_prefetch(source: streams.StreamSource) -> None:
    if not settings.cache_enabled:
        return
    async with _prefetch_lock:
        if source.video_id in _prefetching:
            return
        _prefetching.add(source.video_id)
    asyncio.create_task(_prefetch_to_cache(source))


async def _proxy(source: streams.StreamSource, range_header: str | None) -> Response:
    headers = {"Range": range_header} if range_header else {}
    upstream = await _http.send(
        _http.build_request("GET", source.url, headers=headers), stream=True
    )

    if upstream.status_code >= 400:
        await upstream.aclose()
        logger.error("Upstream lieferte {} für {}", upstream.status_code, source.video_id)
        return Response(status_code=502, content=b"Upstream nicht erreichbar")

    passthrough = {}
    for header in ("content-length", "content-range"):
        if header in upstream.headers:
            passthrough[header.title()] = upstream.headers[header]

    async def body():
        try:
            async for chunk in upstream.aiter_bytes(CHUNK_SIZE):
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        body(),
        status_code=upstream.status_code,
        media_type=source.content_type,
        headers={
            **passthrough,
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, max-age=3600",
        },
    )


async def stream_navidrome(request: Request, song_id: str) -> Response:
    range_header = request.headers.get("range")
    try:
        url = await run_in_threadpool(navidrome.stream_url, song_id)
    except Exception as exc:
        logger.error("Navidrome-Stream für {} nicht auflösbar: {}", song_id, exc)
        return Response(status_code=502, content=b"Navidrome nicht erreichbar")

    headers = {"Range": range_header} if range_header else {}
    upstream = await _http.send(_http.build_request("GET", url, headers=headers), stream=True)
    if upstream.status_code >= 400:
        await upstream.aclose()
        logger.error("Navidrome lieferte {} für {}", upstream.status_code, song_id)
        return Response(status_code=502, content=b"Navidrome-Stream fehlgeschlagen")

    passthrough = {}
    for header in ("content-length", "content-range", "content-type", "accept-ranges"):
        if header in upstream.headers:
            passthrough[header.title()] = upstream.headers[header]

    async def body():
        try:
            async for chunk in upstream.aiter_bytes(CHUNK_SIZE):
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        body(),
        status_code=upstream.status_code,
        media_type=passthrough.get("Content-Type", "audio/mpeg"),
        headers={
            **{k: v for k, v in passthrough.items() if k != "Content-Type"},
            "Accept-Ranges": passthrough.get("Accept-Ranges", "bytes"),
            "Cache-Control": "private, max-age=86400",
        },
    )


async def fetch_navidrome_cover(cover_id: str, size: int = 544) -> Response:
    try:
        url = await run_in_threadpool(navidrome.cover_url, cover_id, size)
        response = await _http.get(url)
        response.raise_for_status()
    except Exception as exc:
        logger.debug("Navidrome-Cover {} nicht abrufbar: {}", cover_id, exc)
        return Response(status_code=404, content=b"Kein Cover")
    return Response(
        response.content,
        media_type=response.headers.get("content-type", "image/jpeg"),
        headers={"Cache-Control": "public, max-age=604800"},
    )


async def stream_track(request: Request, video_id: str) -> Response:
    range_header = request.headers.get("range")

    navidrome_song = nd_ids.unwrap(video_id)
    if not navidrome_song:
        navidrome_song = await run_in_threadpool(navidrome.resolve_video, video_id)
    if navidrome_song:
        return await stream_navidrome(request, navidrome_song)

    entry = await run_in_threadpool(streams.cached_entry, video_id)
    if entry:
        return serve_cached_file(entry, range_header)

    source = await run_in_threadpool(streams.resolve_source, video_id)
    if source is None:
        return Response(status_code=404, content=b"Titel nicht abspielbar")

    if source.needs_transcode:
        path = await run_in_threadpool(streams.transcode_to_m4a, source)
        if path is not None:
            entry = await run_in_threadpool(streams.cached_entry, video_id)
            if entry:
                return serve_cached_file(entry, range_header)

    if settings.stream_mode == "redirect":
        return RedirectResponse(source.url, status_code=302)

    await _schedule_prefetch(source)
    return await _proxy(source, range_header)


# --------------------------------------------------------------------------
# Cover art
# --------------------------------------------------------------------------


async def fetch_cover(url: str, size: int | None = None) -> Response:
    sized_url = mapper.upscale_thumbnail(url, size or 544) or url
    digest = hashlib.sha1(sized_url.encode("utf-8")).hexdigest()
    path = settings.cover_cache_dir / f"{digest}.jpg"

    if path.exists():
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=604800"})

    try:
        response = await _http.get(sized_url)
        response.raise_for_status()
    except Exception as exc:
        logger.debug("Cover {} nicht abrufbar: {}", sized_url, exc)
        return Response(status_code=404, content=b"Kein Cover")

    path.write_bytes(response.content)
    return Response(
        response.content,
        media_type=response.headers.get("content-type", "image/jpeg"),
        headers={"Cache-Control": "public, max-age=604800"},
    )
