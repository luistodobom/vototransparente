"""Download each MP's official photo once and store a small WebP thumbnail for the site.

The Parliament serves ~600 KB PNGs; pages show many MPs at once, so we ship 128px thumbnails
(~4 KB). Originals are cached in data/raw/photos/ (gitignored); thumbnails go to
site/public/img/mp/<cadId>.webp. MPs without a photo get no file (the site falls back to initials).
"""
from __future__ import annotations

import io
import json
import sys
from concurrent.futures import ThreadPoolExecutor

from PIL import Image

from .config import PROCESSED, RAW, ROOT
from .fetch_opendata import S

SRC = RAW / "photos"
OUT = ROOT / "site" / "public" / "img" / "mp"
SIZE = 128


def one(cid: str) -> str:
    out = OUT / f"{cid}.webp"
    if out.exists():
        return "cached"
    raw = SRC / f"{cid}.img"
    if not raw.exists():
        try:
            r = S.get(f"https://app.parlamento.pt/webutils/getimage.aspx?id={cid}&type=deputado",
                      timeout=90, allow_redirects=False)
        except Exception:
            return "error"
        if r.status_code != 200 or not r.content:
            raw.write_bytes(b"")  # remember "no photo"
            return "none"
        raw.write_bytes(r.content)
    data = raw.read_bytes()
    if not data:
        return "none"
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return "bad"
    w, h = im.size
    side = min(w, h)
    im = im.crop(((w - side) // 2, 0, (w - side) // 2 + side, side))  # square, keep the top (face)
    im.thumbnail((SIZE, SIZE), Image.LANCZOS)
    im.save(out, "WEBP", quality=78, method=6)
    return "ok"


def main():
    SRC.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    ids = list(json.loads((PROCESSED / "deputies.json").read_text())["deputies"])
    stats: dict[str, int] = {}
    with ThreadPoolExecutor(6) as ex:
        for i, st in enumerate(ex.map(one, ids), 1):
            stats[st] = stats.get(st, 0) + 1
            if i % 100 == 0:
                print(f"{i}/{len(ids)} {stats}", file=sys.stderr, flush=True)
    print(stats)


if __name__ == "__main__":
    main()
