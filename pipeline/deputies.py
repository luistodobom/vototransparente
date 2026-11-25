"""MP roster helpers: load `data/processed/deputies.json` and resolve vote names to cadastre ids.

The roster is produced by `pipeline.fetch_deputies`. Votes list dissenting MPs as
'Name (PARTY)'; `resolve_mp` maps such a name to the MP's persistent cadastre id
(DepCadId / idCadastro / Biografia.aspx?BID=<id>).
"""
from __future__ import annotations

import json
import re
import unicodedata

from .config import PROCESSED

ROSTER_PATH = PROCESSED / "deputies.json"

# Party spellings seen in the open data / votes -> canonical codes used by the votes.
PARTY_ALIASES = {
    "CHEGA": "CH", "LIVRE": "L", "CDS": "CDS-PP", "CDS–PP": "CDS-PP", "PPD/PSD": "PSD",
    "NINSC": "Ninsc", "NI": "Ninsc", "N INSC": "Ninsc", "NÃO INSCRITO": "Ninsc", "NAO INSCRITO": "Ninsc",
    "PEV": "PEV", "PCP": "PCP", "PS": "PS", "PSD": "PSD", "CH": "CH", "IL": "IL", "BE": "BE",
    "L": "L", "CDS-PP": "CDS-PP", "PAN": "PAN", "JPP": "JPP",
}

# Names in votes that the automatic matcher can't resolve: (leg, normalized name) -> cadId.
MANUAL_OVERRIDES: dict[tuple[str, str], str] = {}

_PARTICLES = {"de", "da", "do", "das", "dos", "e", "d"}


def party_code(p: str | None) -> str | None:
    if not p:
        return p
    p = p.strip()
    return PARTY_ALIASES.get(p.upper(), p)


def norm(name: str) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _tokens(name: str) -> list[str]:
    return [t for t in norm(name).split() if t not in _PARTICLES]


def load_roster(path=ROSTER_PATH) -> dict:
    return json.loads(path.read_text())


def _covers(periods: list[dict], date: str) -> dict | None:
    for p in periods:
        if p["from"] <= date and (p["to"] is None or date <= p["to"]):
            return p
    return None


def party_on(dep_leg: dict, date: str) -> str | None:
    p = _covers(dep_leg["parties"], date)
    return p["party"] if p else None


def in_office(roster: dict, leg: str, date: str) -> dict[str, str | None]:
    """{cadId: party} for every MP holding a seat in `leg` on `date` (YYYY-MM-DD)."""
    out = {}
    for cid, dep in roster["deputies"].items():
        dl = dep["legs"].get(leg)
        if dl and _covers(dl["inOffice"], date):
            out[cid] = party_on(dl, date)
    return out


def _party_in_leg(dl: dict, party: str | None) -> bool:
    return party is None or any(p["party"] == party for p in dl["parties"])


def leg_for_date(roster: dict, date: str) -> str | None:
    for leg, m in roster.get("legislatures", {}).items():
        if m["start"] <= date and (m["end"] is None or date <= m["end"]):
            return leg
    return None


def _pick(roster, cands, leg, party, date, key=None):
    """Narrow candidate ids using legislature, office-on-date and party (strictest first).
    Ties are broken by the parliamentary name used in that legislature (`key`)."""
    deps = roster["deputies"]
    in_leg = [c for c in cands if leg in deps[c]["legs"]]
    tiers = []
    if date:
        office = [c for c in in_leg if _covers(deps[c]["legs"][leg]["inOffice"], date)]
        tiers.append([c for c in office if party_on(deps[c]["legs"][leg], date) == party])
        tiers.append([c for c in office if _party_in_leg(deps[c]["legs"][leg], party)])
        tiers.append(office)
    tiers.append([c for c in in_leg if deps[c]["legs"][leg]["inOffice"] and _party_in_leg(deps[c]["legs"][leg], party)])
    tiers.append([c for c in in_leg if _party_in_leg(deps[c]["legs"][leg], party)])
    for t in tiers:
        if len(t) == 1:
            return t[0]
        if len(t) > 1:
            same = [c for c in t if key and norm(deps[c]["legs"][leg].get("name", "")) == key]
            return same[0] if len(same) == 1 else None  # ambiguous at the strictest non-empty tier
    return None


def _token_match(roster, name, leg, party, date):
    """Fallback: vote name tokens are a subset of an MP's full/parliamentary name tokens
    (same first name), among MPs of that legislature."""
    toks = _tokens(name)
    if not toks:
        return None
    deps = roster["deputies"]
    hits = []
    for cid, dep in deps.items():
        if leg not in dep["legs"]:
            continue
        for cand in (dep["legs"][leg].get("name", ""), dep["name"], dep["fullName"]):
            ct = _tokens(cand)
            if ct and ct[0] == toks[0] and set(toks) <= set(ct):
                hits.append(cid)
                break
    return _pick(roster, hits, leg, party, date, norm(name)) if hits else None


def resolve_mp(name: str, party: str | None, leg: str, date: str | None, roster: dict) -> str | None:
    """Map a vote's 'Name (PARTY)' to a cadastre id, or None if not resolvable."""
    party = party_code(party)
    key = norm(name)
    # Some votes on an initiative happen after its legislature ended (e.g. XIV initiatives
    # voted in 2022-2023): the voters are the MPs of the legislature in office on that date.
    if date:
        leg = leg_for_date(roster, date) or leg
    if (leg, key) in MANUAL_OVERRIDES:
        return MANUAL_OVERRIDES[(leg, key)]
    cands = roster["aliases"].get(key, [])
    if cands:
        got = _pick(roster, cands, leg, party, date, key)
        if got:
            return got
    return _token_match(roster, name, leg, party, date)
