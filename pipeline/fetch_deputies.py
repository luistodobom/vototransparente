"""Build the MP roster from the Parliament's open-data 'Informação Base' datasets.

Source: https://www.parlamento.pt/Cidadania/Paginas/DAInformacaoBase.aspx
Each legislature has an `InformacaoBase<LEG>_json.txt` listing every deputy (including
substitutes) with persistent cadastre id, names, electoral circle, parliamentary-group
history and mandate-situation history (Efetivo, Suplente, Suspenso, ...).

Writes data/processed/deputies.json: {"deputies": {cadId: {...}}, "aliases": {norm name: [cadId]}}.
Run: python -m pipeline.fetch_deputies [--force] [--no-check]
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .config import INFOBASE_INDEX, LEGISLATURES, OPENDATA_DIR, PROCESSED
from .deputies import in_office, norm, party_code, resolve_mp
from .fetch_opendata import _get

PHOTO = "https://app.parlamento.pt/webutils/getimage.aspx?id={}&type=deputado"
# Situations in which the MP actually holds the seat (and votes).
SEATED = {"Efetivo", "Efetivo Temporário", "Efetivo Definitivo"}
# First-situation values that imply the person was elected (vs. a substitute).
ELECTED = {"Efetivo", "Suspenso(Eleito)", "Renunciou", "Falecido/a"}


# ---------------------------------------------------------------- download

def legislature_pages() -> dict[str, str]:
    soup = BeautifulSoup(_get(INFOBASE_INDEX).text, "lxml")
    out = {}
    for a in soup.find_all("a", href=True):
        txt = a.get_text(strip=True)
        if txt.endswith(" Legislatura") and "DAInformacaoBase.aspx" in a["href"]:
            out[txt.split()[0]] = urljoin(INFOBASE_INDEX, a["href"])
    return out


def json_link(leg_page: str, leg: str) -> str:
    soup = BeautifulSoup(_get(leg_page).text, "lxml")
    for a in soup.find_all("a", href=True):
        if a.get_text(strip=True) == f"InformacaoBase{leg}_json.txt":
            return a["href"]
    raise RuntimeError(f"InformacaoBase JSON link for {leg} not found on {leg_page}")


def download(legs, force: bool):
    pages = None
    for leg in legs:
        dest = OPENDATA_DIR / f"InformacaoBase{leg}.json"
        if dest.exists() and not force:
            print(f"{leg}: cached ({dest.stat().st_size / 1e6:.1f} MB)")
            continue
        pages = pages or legislature_pages()
        print(f"{leg}: downloading…")
        dest.write_bytes(_get(json_link(pages[leg], leg)).content)
        print(f"{leg}: {dest.stat().st_size / 1e6:.1f} MB")


# ---------------------------------------------------------------- parse

def _d(s: str | None) -> date | None:
    return date.fromisoformat(s[:10]) if s else None


def _merge(periods: list[tuple[date, date | None, str]]) -> list[tuple[date, date | None, str]]:
    """Union overlapping/adjacent periods. Status of a merged run = latest segment's status."""
    out: list[list] = []
    for f, t, st in sorted(periods, key=lambda p: (p[0], p[1] or date.max)):
        if out and (out[-1][1] is None or f <= out[-1][1] + timedelta(days=1)):
            last = out[-1]
            if last[1] is not None and (t is None or t > last[1]):
                last[1] = t
                last[2] = st
            elif last[1] is not None and t is None:
                last[1] = None
            continue
        out.append([f, t, st])
    return [tuple(p) for p in out]


def parse_leg(leg: str) -> tuple[dict, dict]:
    raw = json.loads((OPENDATA_DIR / f"InformacaoBase{leg}.json").read_text(encoding="utf-8-sig"))
    det = raw["DetalheLegislatura"]
    leg_start, leg_end = _d(det["dtini"]), _d(det["dtfim"])
    # For the ongoing legislature open-ended group periods carry the file's snapshot date
    # as gpDtFim; treat that (the most common end date) as open.
    open_end = leg_end or Counter(
        p["gpDtFim"] for x in raw["Deputados"] for p in x["DepGP"] or []
    ).most_common(1)[0][0]
    open_end = _d(open_end) if isinstance(open_end, str) else open_end

    def end(s):
        e = _d(s)
        return None if e is None or e >= open_end else e

    rows = defaultdict(list)
    for x in raw["Deputados"]:
        rows[str(int(x["DepCadId"]))].append(x)

    out = {}
    for cid, xs in rows.items():
        seated, parties, statuses = [], [], set()
        circle = None
        for x in xs:
            row_seated = []
            sits = sorted(x["DepSituacao"] or [], key=lambda s: (s["sioDtInicio"], s["sioDtFim"] or "9"))
            first = sits[0] if sits else None
            # Single-day placeholder records (e.g. 'Efetivo' 26/10 + 'Suspenso(Eleito)' from 26/10)
            # collide with the real record starting the same day: the longer record wins.
            sits = [s for s in sits if not (
                s["sioDtInicio"] == s["sioDtFim"]
                and any(o is not s and o["sioDtInicio"] == s["sioDtInicio"] and (o["sioDtFim"] or "9") > s["sioDtFim"]
                        for o in sits))]
            # Data artefact: elected MPs who joined the Government (XIV, XV) have their history
            # start a day or two after the opening sitting, leaving them unseated on day 1.
            # Elected statuses (not substitute ones) starting within a week -> seated from day 1.
            if first and first["sioDes"] in ELECTED and leg_start < _d(first["sioDtInicio"]) <= leg_start + timedelta(days=7):
                row_seated.append((leg_start, _d(first["sioDtInicio"]) - timedelta(days=1), "Efetivo"))
            for s in sits:
                if s["sioDes"] in SEATED:
                    row_seated.append((_d(s["sioDtInicio"]), end(s["sioDtFim"]), s["sioDes"]))
            seated += row_seated
            statuses |= {s["sioDes"] for s in sits}
            if row_seated or circle is None:
                circle = x["DepCPDes"]
            for g in x["DepGP"] or []:
                parties.append((_d(g["gpDtInicio"]), end(g["gpDtFim"]), party_code(g["gpSigla"])))
        if statuses <= {"Suplente"}:
            continue  # substitute on the list who was never called up nor elected: not a deputy
        # party periods: dedupe and sort (merge contiguous runs of the same party)
        pmerged = []
        for f, t, p in sorted(set(parties), key=lambda q: (q[0], q[1] or date.max)):
            if pmerged and pmerged[-1][2] == p and pmerged[-1][1] is not None and f <= pmerged[-1][1] + timedelta(days=1):
                pmerged[-1][1] = t if t is None or t > pmerged[-1][1] else pmerged[-1][1]
            else:
                pmerged.append([f, t, p])
        x0 = next((x for x in xs if x["DepCPDes"] == circle), xs[0])
        out[cid] = {
            "name": x0["DepNomeParlamentar"].strip(),
            "fullName": (x0.get("DepNomeCompleto") or "").strip(),
            "leg": {
                "name": x0["DepNomeParlamentar"].strip(),  # parliamentary name used in this legislature
                "circle": circle,
                "parties": [{"party": p, "from": f.isoformat(), "to": t and t.isoformat()} for f, t, p in pmerged],
                "inOffice": [{"from": f.isoformat(), "to": t and t.isoformat(), "status": st}
                             for f, t, st in _merge(seated)],
            },
        }
    meta = {"start": leg_start.isoformat(), "end": leg_end and leg_end.isoformat(),
            "groups": [g["sigla"] for g in raw["GruposParlamentares"]]}
    return out, meta


def build(legs) -> dict:
    deputies: dict[str, dict] = {}
    legs_meta = {}
    for leg in legs:
        parsed, meta = parse_leg(leg)
        legs_meta[leg] = meta
        for cid, d in parsed.items():
            dep = deputies.setdefault(cid, {"id": cid, "name": d["name"], "fullName": d["fullName"],
                                            "photo": PHOTO.format(cid), "legs": {}})
            dep["name"], dep["fullName"] = d["name"], d["fullName"] or dep["fullName"]  # latest leg wins
            dep["legs"][leg] = d["leg"]
    aliases: dict[str, set] = defaultdict(set)
    for leg in legs:  # names as used in each legislature (parliamentary names can change)
        parsed, _ = parse_leg(leg)
        for cid, d in parsed.items():
            for n in (d["name"], d["fullName"]):
                if n:
                    aliases[norm(n)].add(cid)
    return {
        "legislatures": legs_meta,
        "deputies": dict(sorted(deputies.items(), key=lambda kv: int(kv[0]))),
        "aliases": {k: sorted(v, key=int) for k, v in sorted(aliases.items())},
    }


# ---------------------------------------------------------------- sanity checks

EXPECTED = {  # seat totals at the start of each legislature (election results)
    "XIV": {"PS": 108, "PSD": 79, "BE": 19, "PCP": 10, "CDS-PP": 5, "PAN": 4, "PEV": 2, "CH": 1, "IL": 1, "L": 1},
    "XV": {"PS": 120, "PSD": 77, "CH": 12, "IL": 8, "PCP": 6, "BE": 5, "PAN": 1, "L": 1},
    "XVI": {"PSD": 78, "PS": 78, "CH": 50, "IL": 8, "BE": 5, "PCP": 4, "L": 4, "CDS-PP": 2, "PAN": 1},
    "XVII": {"PSD": 89, "CH": 60, "PS": 58, "IL": 9, "L": 6, "PCP": 3, "CDS-PP": 2, "BE": 1, "PAN": 1, "JPP": 1},
}


def _months(a: date, b: date):
    d = date(a.year, a.month, 1)
    while d <= b:
        if d >= a:
            yield d
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def sanity(roster: dict) -> bool:
    ok = True
    for leg, meta in roster["legislatures"].items():
        start = date.fromisoformat(meta["start"])
        stop = date.fromisoformat(meta["end"]) if meta["end"] else date.today()
        counts = []
        for d in [start, *_months(start + timedelta(days=1), stop)]:
            io = in_office(roster, leg, d.isoformat())
            counts.append((d.isoformat(), len(io)))
            if None in io.values():
                print(f"  ! {leg} {d}: {sum(v is None for v in io.values())} seated MPs without a group")
        bad = [c for c in counts if c[1] != 230]
        print(f"{leg}: {len(counts)} sampled dates, seated min/max = "
              f"{min(c[1] for c in counts)}/{max(c[1] for c in counts)}"
              + (f"; ≠230 on {bad}" if bad else " (all 230)"))
        ok &= not bad
        got = Counter(in_office(roster, leg, meta["start"]).values())
        exp = EXPECTED.get(leg)
        if exp:
            diff = {p: (got.get(p, 0), exp.get(p, 0)) for p in set(got) | set(exp) if got.get(p, 0) != exp.get(p, 0)}
            print(f"  start {meta['start']}: {dict(got.most_common())}"
                  + (f"  MISMATCH (got, expected): {diff}" if diff else "  = expected"))
            ok &= not diff
    return ok


def resolution_report(roster: dict) -> None:
    path = PROCESSED / "initiatives.json"
    if not path.exists():
        return
    total = ok = 0
    unresolved = Counter()
    cache = {}
    for ini in json.loads(path.read_text()):
        for v in ini.get("votes") or []:
            for m in v.get("mps") or []:
                key = (m["name"], m["party"], ini["leg"], v["date"])
                if key not in cache:
                    cache[key] = resolve_mp(m["name"], m["party"], ini["leg"], v["date"], roster)
                total += 1
                if cache[key]:
                    ok += 1
                else:
                    unresolved[(ini["leg"], m["name"], m["party"])] += 1
    print(f"vote MP resolution: {ok}/{total} ({100 * ok / max(total, 1):.2f}%)")
    for (leg, n, p), c in unresolved.most_common():
        print(f"  unresolved {leg} {n} ({p}) ×{c}")


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--legs", nargs="*", default=LEGISLATURES)
    p.add_argument("--force", action="store_true", help="re-download even if cached")
    p.add_argument("--no-check", action="store_true", help="skip sanity checks")
    a = p.parse_args(argv)
    download(a.legs, a.force)
    roster = build(a.legs)
    dest = PROCESSED / "deputies.json"
    dest.write_text(json.dumps(roster, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {dest} ({len(roster['deputies'])} deputies, {len(roster['aliases'])} aliases)")
    if not a.no_check:
        sanity(roster)
        resolution_report(roster)


if __name__ == "__main__":
    main()
