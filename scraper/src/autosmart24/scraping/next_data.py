from __future__ import annotations

import json
import re

NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)


class NextDataNotFoundError(Exception):
    pass


def extract_next_data(html: str) -> dict:
    match = NEXT_DATA_RE.search(html)
    if not match:
        raise NextDataNotFoundError("__NEXT_DATA__ script tag not found in page")
    return json.loads(match.group(1))
