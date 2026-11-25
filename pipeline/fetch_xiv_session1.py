"""Fill the XIV legislature's 1st session (Oct 2019 – Sep 2020) gap in the open data.

The official `IniciativasXIV` open-data dump only contains initiatives with IniId >= 45227
(2nd and 3rd legislative sessions). This script scrapes the official initiative pages
(DetalheIniciativa.aspx?BID=<id>) for the missing ids and converts each one into a record
shaped like an entry of the open-data JSON, so `pipeline.normalize` can consume it.

Candidate BIDs = BIDs < 45227 referenced by the maisliberdade XIV export and the old DB,
plus a contiguous scan from (lowest XIV seed - 300) up to 45226. Only pages whose header
reads "<Tipo> <n>/XIV/<sessão>" are kept.

Raw HTML (the initiative web-part only, to keep the cache small) is cached under
data/raw/xiv_s1_html/<BID>.html so re-runs are cheap.

Output: data/raw/opendata/IniciativasXIV_s1.json

Run:  python -m pipeline.fetch_xiv_session1 [--workers 4] [--refresh]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from .config import OPENDATA_DIR, RAW, ROOT, USER_AGENT

URL = "https://www.parlamento.pt/ActividadeParlamentar/Paginas/DetalheIniciativa.aspx?BID={}"
CACHE = RAW / "xiv_s1_html"
OUT = OPENDATA_DIR / "IniciativasXIV_s1.json"
FIRST_OPENDATA_BID = 45227          # lowest IniId present in the official XIV dump
SCAN_MARGIN = 300
SEED_FILES = [
    (ROOT / "validation/maisliberdade/raw/iniciativas_XIV.csv", "initiativas.iniciativa_url_res"),
    (ROOT / "validation/reference/old_parliament_data.csv", "proposal_gov_link"),
]
BID_RE = re.compile(r"BID=(\d+)", re.I)
TITLE_RE = re.compile(r"^\s*(.+?)\s+(\d+)/([IVXLC]+)/(\d+)", re.S)
MP_RE = re.compile(r"^(.+?)\s*\(([^)]+)\)\s*$")
REUNIAO_RE = re.compile(r"(\d{4}-\d{2}-\d{2})(?:.*?n\.º\s*(\d+))?", re.S)

_local = threading.local()


# ----------------------------------------------------------------------------- fetching
DAR_RE = re.compile(r"DAR I s[ée]rie n\.º\s*(\d+),\s*(\d{4})\.(\d{2})\.(\d{2}),\s*da\s*(\d)\.ª\s*SL\s*da\s*([XVI]+)\s*Leg\s*\(p[áa]g\.\s*([\d-]+)\)")
ROMAN = {"XIII": 13, "XIV": 14, "XV": 15}


def _session() -> requests.Session:
    s = getattr(_local, "s", None)
    if s is None:
        s = _local.s = requests.Session()
        s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "pt-PT,pt;q=0.9"})
    return s


def _extract_fragment(html: str) -> str:
    """Keep only the initiative web-part (the page chrome is ~95% of the bytes)."""
    soup = BeautifulSoup(html, "lxml")
    node = soup.find(id=lambda x: x and x.endswith("_ctl00_pnlIniciativa"))
    if node is None:
        raise ValueError("no pnlIniciativa in page (unexpected layout / error page)")
    return str(node)


def fetch(bid: int, refresh: bool = False) -> str:
    path = CACHE / f"{bid}.html"
    if path.exists() and not refresh:
        return path.read_text(encoding="utf-8")
    last = None
    for attempt in range(6):
        try:
            r = _session().get(URL.format(bid), timeout=60)
            if r.status_code == 404:
                frag = ""
            else:
                r.raise_for_status()
                r.encoding = "utf-8"
                frag = _extract_fragment(r.text)
            path.write_text(frag, encoding="utf-8")
            return frag
        except (requests.RequestException, ValueError) as e:  # pragma: no cover - network
            last = e
            wait = min(60, 2 ** attempt + attempt)
            print(f"  retry {attempt + 1} BID={bid} ({e}); sleeping {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"failed BID={bid}: {last}")


def seed_bids() -> set[int]:
    out: set[int] = set()
    csv.field_size_limit(sys.maxsize)
    for path, col in SEED_FILES:
        if not path.exists():
            print(f"  (seed file missing: {path.relative_to(ROOT)})")
            continue
        with path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                m = BID_RE.search(row.get(col) or "")
                if m and int(m.group(1)) < FIRST_OPENDATA_BID:
                    out.add(int(m.group(1)))
    return out


# ----------------------------------------------------------------------------- parsing
def _by_suffix(node, suffix: str):
    return node.find(id=lambda x: x and x.endswith(suffix))


def _top(node, name: str):
    """Top-level control of the initiative web-part (not one nested in rptFases etc.)."""
    rx = re.compile(r"_g_[0-9a-f_]+_ctl00_" + name + "$")
    return node.find(id=lambda x: x and rx.search(x))


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ")).strip() if node else ""


def _detail_html(node) -> str | None:
    """Rebuild 'A Favor: <I>PS</I>, <I> PSD</I><BR>Contra:<I>CH</I>' like the open data."""
    if node is None:
        return None
    chunks, cur = [], []
    for el in node.children:
        name = getattr(el, "name", None)
        if name == "br":
            chunks.append(cur)
            cur = []
        elif name in ("i", "em"):
            cur.append(f"<I>{el.get_text()}</I>")
        elif name is None:
            cur.append(str(el).replace("\xa0", " "))
        else:
            cur.append(el.get_text())
    chunks.append(cur)
    lines = ["".join(c).strip() for c in chunks]
    lines = [ln for ln in lines if ln]
    return "<BR>".join(lines) or None


def _absences(detail: str | None) -> list[str] | None:
    if not detail:
        return None
    for chunk in detail.split("<BR>"):
        head, _, body = chunk.partition(":")
        if head.strip() == "Ausência":
            return [t.strip() for t in re.findall(r"<I>(.*?)</I>", body) if t.strip()] or None
    return None


def parse(bid: int, frag: str) -> dict | None:
    if not frag:
        return None
    soup = BeautifulSoup(frag, "lxml")
    m = TITLE_RE.match(_text(_top(soup, "lblTitulo")))
    if not m:
        return None
    tipo, nr, leg, sel = m.group(1).strip(), m.group(2), m.group(3), m.group(4)
    if leg != "XIV":
        return None

    deputies, gps, others = [], [], []
    for a in soup.find_all(id=re.compile(r"_g_[0-9a-f_]+_ctl00_rptAutores_ctl\d+_hplAutor$")):
        name = _text(a)
        mm = MP_RE.match(name)
        bm = BID_RE.search(a.get("href") or "")
        if mm and "Biografia" in (a.get("href") or ""):
            deputies.append({"GP": mm.group(2).strip(), "idCadastro": bm.group(1) if bm else None,
                             "nome": mm.group(1).strip()})
        elif name:
            others.append(name)
    # Non-deputy authors (Governo, Cidadãos, comissões, ALRA…) are plain text: "Autor: Governo".
    lbl_autor = _text(_top(soup, "lblAutor"))
    lbl_autor = re.sub(r"^Autor(?:es)?\s*:\s*", "", lbl_autor).strip()
    if lbl_autor and lbl_autor not in others:
        others.append(lbl_autor)
    for o in others:
        if o.upper() == o and len(o) <= 8 and o != "PAR":  # party acronym (e.g. 'PS', 'CDS-PP')
            gps.append({"GP": o})
    other_names = [o for o in others if {"GP": o} not in gps]
    if other_names:
        nome = other_names[0]
        if nome.lower().startswith("governo"):
            nome = "Governo"
        if nome.startswith("Comissão"):  # same encoding as the open data
            autor_outros = {"iniAutorComissao": nome, "nome": "Comissões", "sigla": "C"}
        else:
            sigla = {"Governo": "V", "PAR": "R", "Cidadãos": "Z"}.get(nome)
            if nome.startswith("Assembleia Legislativa da Região Autónoma"):
                sigla = "M" if "Madeira" in nome else "A"
            autor_outros = {"iniAutorComissao": None, "nome": nome, "sigla": sigla}
    elif gps:
        autor_outros = {"iniAutorComissao": None, "nome": "Grupos Parlamentares", "sigla": "G"}
    elif deputies:
        autor_outros = {"iniAutorComissao": None, "nome": "Deputados", "sigla": "D"}
    else:
        autor_outros = None

    pdf = _top(soup, "ucLinkDocumento_hplDocumentoPDF")
    events = []
    n = 0
    for lbl in soup.find_all(id=lambda x: x and re.search(r"rptFases_ctl\d+_lblEvento$", x)):
        prefix = lbl["id"][: -len("lblEvento")]
        data = _text(soup.find(id=prefix + "lblData")) or None
        votes = []
        for res in soup.find_all(id=lambda x: x and x.startswith(prefix)
                                 and re.search(r"rptVotacoes_ctl\d+_lblResultado$", x)):
            vp = res["id"][: -len("lblResultado")]
            reun = _text(soup.find(id=vp + "lblReuniao"))
            rm = REUNIAO_RE.search(reun.split(",", 1)[0])
            resultado = _text(res)
            unanime = "unanime" if "unanimidade" in resultado.lower() else None
            resultado = re.sub(r"\s*por unanimidade\s*$", "", resultado, flags=re.I).strip() or None
            detalhe = _detail_html(soup.find(id=vp + "lblDetalhes"))
            # "Votação em 2020-07-23 na Reunião Plenária n.º 76, <descrição do objeto votado>"
            desc = reun.split(",", 1)[1].lstrip(" •\t") if "," in reun else ""
            n += 1
            votes.append({
                "ausencias": _absences(detalhe),
                "data": rm.group(1) if rm else data,
                "descricao": desc.strip() or None,
                "detalhe": detalhe,
                "id": f"s1-{bid}-{n}",
                "publicacao": None,
                "resultado": resultado,
                "reuniao": rm.group(2) if rm and rm.group(2) else None,
                "tipoReuniao": "RP" if "Plenária" in reun else None,
                "unanime": unanime,
            })
        pubs = None
        if votes:
            # "[DAR I série n.º 17, 2019.12.13, da 1.ª SL da XIV Leg (pág. 69-70)]" -> official transcript URL
            blob = " ".join(_text(x) for x in soup.find_all(id=lambda x: x and x.startswith(prefix)))
            dm = DAR_RE.search(blob)
            if dm:
                nr, y, mo, d, sl, leg, pgs = dm.groups()
                first = pgs.split("-")[0]
                pubs = [{"pubTipo": "DAR I série", "pubNr": nr, "pubSL": sl, "pubdt": f"{y}-{mo}-{d}", "pag": [pgs],
                         "URLDiario": f"https://debates.parlamento.pt/catalogo/r3/dar/01/{ROMAN.get(leg, 14):02d}/"
                                      f"{int(sl):02d}/{int(nr):03d}/{y}-{mo}-{d}/{first}?pgs={pgs}&org=PLC"}]
        events.append({"DataFase": data, "Fase": _text(lbl), "Votacao": votes or None, "PublicacaoFase": pubs})

    return {
        "IniId": str(bid),
        "IniLeg": leg,
        "IniNr": nr,
        "IniSel": sel,
        "IniDescTipo": tipo,
        "IniTitulo": _text(_top(soup, "ucLinkDocumento_lblDocumentoTitulo")) or None,
        "IniAutorGruposParlamentares": gps or None,
        "IniAutorDeputados": deputies or None,
        "IniAutorOutros": autor_outros,
        "IniLinkTexto": pdf.get("href") if pdf is not None else None,
        "IniEventos": events,
        "_source": URL.format(bid),
    }


# ----------------------------------------------------------------------------- main
def _fetch_all(bids, workers, refresh):
    pages = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch, b, refresh): b for b in sorted(bids)}
        for i, f in enumerate(as_completed(futs), 1):
            b = futs[f]
            try:
                pages[b] = f.result()
            except Exception as e:  # pragma: no cover - network
                print(f"  giving up on BID={b}: {e}", file=sys.stderr)
            if i % 200 == 0:
                print(f"  {i}/{len(futs)} pages")
    return pages


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--refresh", action="store_true", help="ignore the HTML cache")
    args = ap.parse_args()
    workers = max(1, min(args.workers, 4))
    CACHE.mkdir(parents=True, exist_ok=True)

    seeds = seed_bids()
    print(f"{len(seeds)} seed BIDs < {FIRST_OPENDATA_BID}")
    pages = _fetch_all(seeds, workers, args.refresh)
    records = {b: r for b, p in pages.items() if (r := parse(b, p))}
    if not records:
        sys.exit("no XIV initiative found among the seed BIDs")
    start = min(records) - SCAN_MARGIN
    scan = set(range(start, FIRST_OPENDATA_BID)) - set(pages)
    print(f"scanning {len(scan)} more BIDs in [{start}, {FIRST_OPENDATA_BID - 1}]")
    pages.update(_fetch_all(scan, workers, args.refresh))
    for b in scan:
        if b in pages and (r := parse(b, pages[b])):
            records[b] = r

    out = [records[b] for b in sorted(records)]
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    nvotes = sum(len(e["Votacao"] or []) for r in out for e in r["IniEventos"])
    by_sel = {}
    for r in out:
        by_sel[r["IniSel"]] = by_sel.get(r["IniSel"], 0) + 1
    print(f"wrote {len(out)} XIV initiatives (by session {by_sel}), {nvotes} votes -> "
          f"{OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
