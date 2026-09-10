"""Subsonic response envelope.

Subsonic clients negotiate XML or JSON through the `f` parameter and expect the
*same* document shape either way: scalar values become XML attributes, nested
dicts/lists become child elements, and the reserved key `value` becomes element
text. Errors are transported inside a normal HTTP 200 response.
"""

import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from fastapi.responses import Response

from app.core.settings import APP_NAME, APP_VERSION, SUBSONIC_API_VERSION

XMLNS = "http://subsonic.org/restapi"
SERVER_TYPE = "simpmusic-app"

ERROR_GENERIC = 0
ERROR_MISSING_PARAM = 10
ERROR_CLIENT_TOO_OLD = 20
ERROR_SERVER_TOO_OLD = 30
ERROR_BAD_CREDENTIALS = 40
ERROR_TOKEN_UNSUPPORTED = 41
ERROR_NOT_AUTHORIZED = 50
ERROR_NOT_FOUND = 70


class SubsonicError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _append(parent: ET.Element, name: str, data: dict) -> None:
    element = ET.SubElement(parent, name)
    _fill(element, data)


def _fill(element: ET.Element, data: dict) -> None:
    for key, value in data.items():
        if value is None:
            continue
        if key == "value":
            element.text = str(value)
        elif isinstance(value, dict):
            _append(element, key, value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _append(element, key, item)
                else:
                    child = ET.SubElement(element, key)
                    child.text = str(item)
        elif isinstance(value, bool):
            element.set(key, "true" if value else "false")
        else:
            element.set(key, str(value))


def _strip_none(data: Any) -> Any:
    if isinstance(data, dict):
        return {key: _strip_none(value) for key, value in data.items() if value is not None}
    if isinstance(data, list):
        return [_strip_none(item) for item in data]
    return data


def _envelope(body: dict | None, status: str = "ok") -> dict:
    return {
        "status": status,
        "version": SUBSONIC_API_VERSION,
        "type": SERVER_TYPE,
        "serverVersion": APP_VERSION,
        "openSubsonic": True,
        **(body or {}),
    }


def _param(request: Request, name: str) -> str | None:
    """Prefer the merged query+form parameters collected by the dispatcher."""
    params = getattr(request.state, "subsonic_params", None)
    if params is not None:
        return params.get(name)
    return request.query_params.get(name)


def render(request: Request, body: dict | None = None, status: str = "ok") -> Response:
    payload = _strip_none(_envelope(body, status))
    fmt = (_param(request, "f") or "xml").lower()

    if fmt in ("json", "jsonp"):
        document = json.dumps({"subsonic-response": payload}, ensure_ascii=False)
        if fmt == "jsonp":
            callback = _param(request, "callback") or "callback"
            return Response(f"{callback}({document});", media_type="application/javascript; charset=utf-8")
        return Response(document, media_type="application/json; charset=utf-8")

    root = ET.Element("subsonic-response", {"xmlns": XMLNS})
    _fill(root, payload)
    document = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return Response(document, media_type="application/xml; charset=utf-8")


def render_error(request: Request, code: int, message: str) -> Response:
    return render(request, {"error": {"code": code, "message": message}}, status="failed")


def server_info() -> dict:
    return {"name": APP_NAME, "version": APP_VERSION, "api": SUBSONIC_API_VERSION}
