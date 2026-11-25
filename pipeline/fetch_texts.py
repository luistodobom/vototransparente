"""Download the full text (PDF) of every initiative that was voted.

The main document's PDF link is on the initiative's detail page
(`…_ucLinkDocumento_hplDocumentoPDF`). PDFs from the old project
(data/raw/legacy/proposal_docs/proposal_<BID>_formato_PDF.pdf) are reused when present.
Fallback: the open-data `IniLinkTexto` (.docx/.doc) converted to plain text.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .config import PROCESSED, ROOT, SUMMARIES, TEXTS_DIR, USER_AGENT

OLD_DOCS = ROOT / "data" / "raw" / "legacy" / "proposal_docs"
S = requests.Session()
S.headers["User-Agent"] = USER_AGENT
S.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=32))


def get(url: str) -> requests.Response | None:
    for attempt in range(5):
        try:
            r = S.get(url, timeout=90)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r
        except requests.RequestException:
            time.sleep(2 ** attempt)
    return None


def existing(ini_id: str):
    for ext in ("pdf", "txt"):
        p = TEXTS_DIR / f"{ini_id}.{ext}"
        if p.exists() and p.stat().st_size > 200:
            return p
    return None


def docx_to_text(content: bytes, suffix: str) -> str | None:
    tmp = TEXTS_DIR / f"_tmp_{time.time_ns()}{suffix}"
    tmp.write_bytes(content)
    try:
        if shutil.which("textutil"):  # macOS: handles .doc and .docx
            out = subprocess.run(["textutil", "-convert", "txt", "-stdout", str(tmp)],
                                 capture_output=True, timeout=60)
            return out.stdout.decode("utf-8", "ignore") or None
        if suffix == ".docx":
            import docx  # python-docx
            return "\n".join(p.text for p in docx.Document(str(tmp)).paragraphs)
    except Exception:
        return None
    finally:
        tmp.unlink(missing_ok=True)
    return None


def fetch_one(ini: dict) -> tuple[str, str]:
    iid = ini["id"]
    if existing(iid):
        return iid, "cached"
    old = OLD_DOCS / f"proposal_{iid}_formato_PDF.pdf"
    if old.exists() and old.stat().st_size > 1000:
        shutil.copy(old, TEXTS_DIR / f"{iid}.pdf")
        return iid, "old"
    r = get(ini["url"])
    if r is not None:
        soup = BeautifulSoup(r.text, "lxml")
        a = soup.find("a", id=lambda x: x and x.endswith("ucLinkDocumento_hplDocumentoPDF"))
        if a and a.get("href"):
            pdf = get(urljoin(ini["url"], a["href"]))
            if pdf is not None and pdf.content[:4] == b"%PDF":
                (TEXTS_DIR / f"{iid}.pdf").write_bytes(pdf.content)
                return iid, "pdf"
    if ini.get("textUrl"):
        url = ini["textUrl"].replace("http://", "https://")
        r = get(url)
        if r is not None:
            if r.content[:4] == b"%PDF":
                (TEXTS_DIR / f"{iid}.pdf").write_bytes(r.content)
                return iid, "pdf-od"
            suffix = ".doc" if ".doc&" in url.lower() else ".docx"
            txt = docx_to_text(r.content, suffix)
            if txt and len(txt) > 200:
                (TEXTS_DIR / f"{iid}.txt").write_text(txt)
                return iid, "docx"
    return iid, "missing"


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--workers", type=int, default=12)
    p.add_argument("--limit", type=int)
    p.add_argument("--unsummarized", action="store_true", help="only initiatives without a summary yet")
    a = p.parse_args(argv)
    rows = json.loads((PROCESSED / "initiatives.json").read_text())
    if a.unsummarized:
        rows = [r for r in rows if not (SUMMARIES / f"{r['id']}.json").exists()]
    rows = rows[: a.limit]
    stats: dict[str, int] = {}
    missing = []
    with ThreadPoolExecutor(a.workers) as ex:
        futs = [ex.submit(fetch_one, r) for r in rows]
        for i, f in enumerate(as_completed(futs), 1):
            iid, st = f.result()
            stats[st] = stats.get(st, 0) + 1
            if st == "missing":
                missing.append(iid)
            if i % 250 == 0:
                print(f"{i}/{len(rows)} {stats}", file=sys.stderr, flush=True)
    print(stats)
    (PROCESSED / "texts_missing.json").write_text(json.dumps(sorted(missing)))


if __name__ == "__main__":
    main()
