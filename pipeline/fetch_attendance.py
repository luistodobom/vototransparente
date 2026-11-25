"""Download per-MP plenary attendance (presenças/faltas) for each reunião plenária.

Source (official, HTML only - there is no open-data dataset for this):
  list:   https://www.parlamento.pt/DeputadoGP/Paginas/reunioesplenarias.aspx
          (ASP.NET/SharePoint form: pick legislature -> "Pesquisar" -> 20 rows/page pager)
  detail: https://www.parlamento.pt/DeputadoGP/Paginas/DetalheReuniaoPlenaria.aspx?BID=<id>
          one row per MP in office that day: name (link Biografia.aspx?BID=<cadastre id>),
          party, "Presença/Falta" status and, for justified absences, "Motivo".

Output: data/processed/attendance.json
  { "<LEG>": { "<YYYY-MM-DD>": { "meeting": "<n.º>", "type": "Ordinária", "bid": "<BID>",
      "n_deputies": 230, "n_present": 221,
      "absent": [{"id": "<DepCadId>", "name": ..., "party": ..., "status": "FJ",
                  "status_text": "Falta Justificada (FJ)", "reason": "Doença" | null}],
      "source": "<detail url>" } } }

If two meetings fall on the same date, the later-listed one is stored under "<date>#2" etc.

Run:  .venv/bin/python -m pipeline.fetch_attendance [--legs XVI XVII] [--refresh-days 45] [--offline]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from .config import LEGISLATURES, PROCESSED, RAW, USER_AGENT

LIST_URL = "https://www.parlamento.pt/DeputadoGP/Paginas/reunioesplenarias.aspx"
DETAIL_URL = "https://www.parlamento.pt/DeputadoGP/Paginas/DetalheReuniaoPlenaria.aspx?BID={bid}"
CACHE = RAW / "attendance"
OUT = PROCESSED / "attendance.json"
DELAY = 0.5  # min seconds between request starts, across all threads (<= 2 req/s)
WORKERS = 4  # concurrent detail-page downloads

_local = threading.local()
_rate_lock = threading.Lock()
_last_start = [0.0]


def _session() -> requests.Session:
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
        _local.s.headers["User-Agent"] = USER_AGENT
    return _local.s


def _throttle() -> None:
    with _rate_lock:
        wait = _last_start[0] + DELAY - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_start[0] = time.monotonic()


def _req(method: str, url: str, **kw) -> requests.Response:
    for attempt in range(6):
        try:
            _throttle()
            r = _session().request(method, url, timeout=180, **kw)
            r.raise_for_status()
            return r
        except requests.RequestException as e:  # pragma: no cover - network
            wait = 2 ** attempt
            print(f"  retry {attempt + 1} for {url[:80]}… ({e}); sleeping {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"failed to {method} {url}")


# ---------------------------------------------------------------- meeting list
def _form_fields(soup: BeautifulSoup) -> dict:
    f = soup.find("form")
    d = {}
    for i in f.find_all("input"):
        if i.get("name") and i.get("type") not in ("submit", "button", "image", "checkbox"):
            d[i["name"]] = i.get("value", "")
    for s in f.find_all("select"):
        o = s.find("option", selected=True) or s.find("option")
        d[s["name"]] = o["value"] if o else ""
    return d


def _postback(soup: BeautifulSoup, target: str, extra: dict | None = None) -> BeautifulSoup:
    d = _form_fields(soup)
    d.update(extra or {})
    d["__EVENTTARGET"] = target
    d["__EVENTARGUMENT"] = ""
    return BeautifulSoup(_req("POST", LIST_URL, data=d).text, "lxml")


def _rows(soup: BeautifulSoup) -> list[dict]:
    out = []
    for a in soup.find_all("a", id=re.compile(r"lvResults_ctrl\d+_hplData$")):
        row = a.find_parent("div", class_="row")
        bid = re.search(r"BID=(\d+)", a["href"]).group(1)
        num = row.find("a", id=re.compile(r"hplNumero$"))
        tipo = row.find_all("div", class_="TextoRegular")
        out.append({
            "date": a.get_text(strip=True),
            "bid": bid,
            "meeting": num.get_text(strip=True) if num else None,
            "agenda_pdf": num["href"] if num and num.get("href") else None,
            "type": tipo[-1].get_text(strip=True) if tipo else None,
        })
    return out


def _pager(soup: BeautifulSoup) -> list[tuple[str, str]]:
    out = []
    for a in soup.find_all("a", href=re.compile("dpgResults")):
        m = re.search(r"__doPostBack\('([^']+)'", a["href"].replace("&#39;", "'"))
        if m:
            out.append((a.get_text(strip=True), m.group(1)))
    return out


def list_meetings(leg: str) -> list[dict]:
    soup = BeautifulSoup(_req("GET", LIST_URL).text, "lxml")
    sel = soup.find("select", attrs={"name": re.compile("ddlLegislatura$")})["name"]
    soup = _postback(soup, sel, {sel: leg})
    btn = soup.find("input", attrs={"name": re.compile("btnPesquisar$")})["name"]
    soup = _postback(soup, "", {btn: "Pesquisar"})
    m = re.search(r"(\d+)\s+registos", soup.get_text(" "))
    total = int(m.group(1)) if m else None
    rows = {r["bid"]: r for r in _rows(soup)}
    visited = {1}
    while total is None or len(rows) < total:
        pager = _pager(soup)
        nums = [(int(t), tg) for t, tg in pager if t.isdigit() and int(t) not in visited]
        if nums:
            page, tg = min(nums)
            visited.add(page)
        else:
            # windowed pager: follow the last "..." / ">" style link
            others = [tg for t, tg in pager if not t.isdigit()]
            if not others:
                break
            tg = others[-1]
        before = len(rows)
        soup = _postback(soup, tg)
        rows.update({r["bid"]: r for r in _rows(soup)})
        if len(rows) == before and not nums:
            break
    if total is not None and len(rows) != total:
        print(f"  WARNING {leg}: listed {len(rows)} meetings but page says {total}", file=sys.stderr)
    return sorted(rows.values(), key=lambda r: (r["date"], int(r["meeting"] or 0)))


# ---------------------------------------------------------------- meeting detail
STATUS_RE = re.compile(r"\(([A-Z]+)\)\s*$")


def parse_detail(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in soup.find_all("a", id=re.compile(r"rptDetalhe_ctl\d+_hplDeputado$")):
        pref = a["id"][: -len("hplDeputado")]
        get = lambda k: (soup.find(id=pref + k).get_text(" ", strip=True) if soup.find(id=pref + k) else "")  # noqa: E731
        m = re.search(r"BID=(\d+)", a.get("href", ""))
        status_text = get("lblPresenca")
        sm = STATUS_RE.search(status_text)
        out.append({
            "id": m.group(1) if m else None,
            "name": a.get_text(strip=True),
            "party": get("lblGP") or None,
            "status_text": status_text,
            "status": sm.group(1) if sm else status_text,
            "reason": get("lblMotivo") or None,
        })
    return out


def extract_fragment(html: str) -> str:
    """Keep only the attendance panel (~300 KB of a ~800 KB page) plus the meeting title."""
    soup = BeautifulSoup(html, "lxml")
    node = soup.find(id=lambda x: x and x.endswith("_ctl00_pnlDetalhe"))
    if node is None:
        raise ValueError("pnlDetalhe not found")
    return f"<html><body>{node}</body></html>"


def is_present(status_text: str) -> bool:
    return status_text.lower().startswith("presen")


def fetch_detail(leg: str, bid: str, mdate: str, refresh_days: int, offline: bool) -> str | None:
    path = CACHE / leg / f"{bid}.html"
    recent = date.fromisoformat(mdate) >= date.today() - timedelta(days=refresh_days)
    if path.exists() and (offline or not recent):
        html = path.read_text(encoding="utf-8")
        if len(html) > 400_000:  # legacy full-page cache entry -> shrink in place
            html = extract_fragment(html)
            path.write_text(html, encoding="utf-8")
        return html
    if offline:
        return None
    for attempt in range(3):
        try:
            html = extract_fragment(_req("GET", DETAIL_URL.format(bid=bid)).text)
            break
        except ValueError:  # truncated/odd page; retry
            time.sleep(2 ** attempt)
    else:
        raise RuntimeError(f"no attendance panel for BID={bid}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return html


# ---------------------------------------------------------------- main
def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--legs", nargs="*", default=LEGISLATURES)
    p.add_argument("--refresh-days", type=int, default=45,
                   help="re-download detail pages of meetings newer than this (justifications get filled in later)")
    p.add_argument("--offline", action="store_true", help="use cached index + detail pages only")
    a = p.parse_args(argv)
    CACHE.mkdir(parents=True, exist_ok=True)

    result = json.loads(OUT.read_text()) if OUT.exists() else {}
    for leg in a.legs:
        idx_path = CACHE / f"index_{leg}.json"
        if a.offline:
            meetings = json.loads(idx_path.read_text())
        else:
            print(f"[{leg}] listing meetings…", flush=True)
            meetings = list_meetings(leg)
            idx_path.write_text(json.dumps(meetings, ensure_ascii=False, indent=1))
        print(f"[{leg}] {len(meetings)} meetings; fetching details…", flush=True)
        legout: dict = {}
        statuses: Counter = Counter()
        done = [0]

        def job(mt):
            html = fetch_detail(leg, mt["bid"], mt["date"], a.refresh_days, a.offline)
            done[0] += 1
            if done[0] % 25 == 0:
                print(f"  {done[0]}/{len(meetings)}", flush=True)
            return html

        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            htmls = list(ex.map(job, meetings))
        for mt, html in zip(meetings, htmls):
            if html is None:
                continue
            deps = parse_detail(html)
            statuses.update(d["status_text"] for d in deps)
            absent = [{k: d[k] for k in ("id", "name", "party", "status", "status_text", "reason")}
                      for d in deps if not is_present(d["status_text"])]
            key, n = mt["date"], 2
            while key in legout:
                key = f"{mt['date']}#{n}"
                n += 1
            legout[key] = {
                "meeting": mt["meeting"],
                "type": mt["type"],
                "bid": mt["bid"],
                "n_deputies": len(deps),
                "n_present": len(deps) - len(absent),
                "absent": absent,
                "source": DETAIL_URL.format(bid=mt["bid"]),
            }
        result[leg] = legout
        empty = sum(1 for v in legout.values() if v["n_deputies"] == 0)
        n_abs = sum(len(v["absent"]) for v in legout.values())
        dates = sorted(k[:10] for k in legout)
        print(f"[{leg}] meetings={len(legout)} (no attendance rows: {empty}) absences={n_abs} "
              f"range={dates[0] if dates else '-'}..{dates[-1] if dates else '-'}")
        print(f"[{leg}] statuses: {dict(statuses.most_common())}")
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
