"""Local ids for Navidrome entities so they never collide with YouTube ids."""

PREFIX = "nd:"


def wrap(navidrome_id: str | None) -> str:
    value = (navidrome_id or "").strip()
    if not value:
        return ""
    if value.startswith(PREFIX):
        return value
    return f"{PREFIX}{value}"


def unwrap(value: str | None) -> str | None:
    text = (value or "").strip()
    if text.startswith(PREFIX):
        return text[len(PREFIX) :] or None
    return None


def is_navidrome(value: str | None) -> bool:
    return bool(unwrap(value))
