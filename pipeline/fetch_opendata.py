"""Download the Parliament's official open-data 'Iniciativas' JSON dumps.

Source: https://www.parlamento.pt/Cidadania/Paginas/DAIniciativas.aspx
Each legislature has an `Iniciativas<LEG>_json.txt` file with every initiative,
its authors, all procedural events and every plenary vote (per-party detail).
"""
from __future__ import annotations

import argparse
import sys
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .config import LEGISLATURES, OPENDATA_DIR, OPENDATA_INDEX, USER_AGENT

S = requests.Session()
S.headers["User-Agent"] = USER_AGENT


def _get(url: str, **kw) -> requests.Response:
    for attempt in range(6):
        try:
            r = S.get(url, timeout=120, **kw)
            r.raise_for_status()
            return r
        except requests.RequestException as e:  # pragma: no cover - network
            wait = 2 ** attempt
            print(f"  retry {attempt + 1} for {url[:80]}… ({e}); sleeping {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"failed to GET {url}")


def legislature_pages() -> dict[str, str]:
    soup = BeautifulSoup(_get(OPENDATA_INDEX).text, "lxml")
    out = {}
    for a in soup.find_all("a", href=True):
        txt = a.get_text(strip=True)
        if txt.endswith(" Legislatura") and "DAIniciativas.aspx" in a["href"]:
            out[txt.split()[0]] = urljoin(OPENDATA_INDEX, a["href"])
    return out


def json_link(leg_page: str, leg: str) -> str:
    soup = BeautifulSoup(_get(leg_page).text, "lxml")
    for a in soup.find_all("a", href=True):
        if a.get_text(strip=True) == f"Iniciativas{leg}_json.txt":
            return a["href"]
    raise RuntimeError(f"JSON link for {leg} not found on {leg_page}")


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--legs", nargs="*", default=LEGISLATURES)
    p.add_argument("--force", action="store_true")
    a = p.parse_args(argv)
    pages = legislature_pages()
    for leg in a.legs:
        dest = OPENDATA_DIR / f"Iniciativas{leg}.json"
        if dest.exists() and not a.force:
            print(f"{leg}: cached ({dest.stat().st_size / 1e6:.1f} MB)")
            continue
        url = json_link(pages[leg], leg)
        print(f"{leg}: downloading…")
        dest.write_bytes(_get(url).content)
        print(f"{leg}: {dest.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
