"""Parse artist IDs without accepting lookalike Pixiv hosts."""

import re
from urllib.parse import urlsplit, parse_qs


def parse_pixiv_user_id(value: str) -> str | None:
    """Return a canonical positive ID from a number or official user URL."""
    value = value.strip()
    if not value or any(char.isspace() or ord(char) < 32 for char in value):
        return None
    if re.fullmatch(r"[0-9]+", value):
        artist_id = value
    else:
        try:
            url = urlsplit(value if "://" in value else "https://" + value)
        except ValueError:
            return None
        if url.scheme not in {"http", "https"} or url.netloc.lower() not in {
            "pixiv.net", "www.pixiv.net", "m.pixiv.net"
        }:
            return None
        match = re.fullmatch(r"/(?:[a-z]{2}/)?users/([0-9]+)/?", url.path)
        if match:
            artist_id = match.group(1)
        elif url.path == "/member.php":
            ids = parse_qs(url.query).get("id", [])
            artist_id = ids[0] if len(ids) == 1 else ""
        else:
            return None
        if not re.fullmatch(r"[0-9]+", artist_id):
            return None
    return artist_id.lstrip("0") or None
