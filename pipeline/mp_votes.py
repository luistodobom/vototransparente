"""Attribute every plenary vote to individual MPs.

The official vote record gives each parliamentary group's position plus the MPs who broke with it
("A Favor: PS, 6-PSD, …, Fulano (PSD)"). Combined with the roster (who held a seat, for which party,
on each date) we derive each MP's vote:

  named in the record             -> that position (exact)
  absent per attendance record    -> ausente
  group listed in the record      -> the group's position (presumed: MPs vote as a block)
  group not listed                -> ausente (the record omits groups that did not vote)
  not in office that day          -> not applicable

Output: data/processed/mp_votes.json
  { "<LEG>": { "mps": [cadId…], "votes": [voteId…],
               "rows": ["<one char per MP>"…]   # aligned with votes × mps
               "partial": [voteId…],            # votes with unnamed dissenters (split counts > named MPs)
               "stats": {cadId: {F, C, A, X, dissent: [voteId…]}} } }
Codes: F favor, C contra, A abstenção, X ausente, "-" not in office, "?" unknown.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict

from .config import LEGISLATURES, PROCESSED
from .deputies import in_office, load_roster, resolve_mp

CODE = {"favor": "F", "contra": "C", "abstencao": "A", "ausente": "X"}


def load_attendance():
    """(leg, date) -> {"absent": {cadId: (status code, reason)}, "sources": [url…]}.

    Several meetings can share a date (keys 'YYYY-MM-DD#2'); an MP counts as absent only if absent from all.
    """
    p = PROCESSED / "attendance.json"
    if not p.exists():
        return {}
    data = json.loads(p.read_text())
    out: dict = {}
    for leg, days in data.items():
        for key, rec in days.items():
            day = key[:10]
            absent = {a["id"]: (a.get("status"), a.get("reason")) for a in rec.get("absent", []) if a.get("id")}
            cur = out.get((leg, day))
            if cur is None:
                out[(leg, day)] = {"absent": absent, "sources": [rec.get("source")]}
            else:  # present at either meeting that day -> not absent
                cur["absent"] = {k: v for k, v in cur["absent"].items() if k in absent}
                cur["sources"].append(rec.get("source"))
    return out


def build():
    roster = load_roster()
    attendance = load_attendance()
    initiatives = json.loads((PROCESSED / "initiatives.json").read_text())
    out = {}
    unresolved: Counter = Counter()
    stats: Counter = Counter()
    for leg in LEGISLATURES:
        votes = sorted(
            ((v, ini) for ini in initiatives if ini["leg"] == leg for v in ini["votes"] if v.get("date")),
            key=lambda x: (x[0]["date"], str(x[0]["id"])),
        )
        mp_ids = sorted(
            (cid for cid, d in roster["deputies"].items() if leg in d["legs"]),
            key=lambda c: roster["deputies"][c]["name"],
        )
        idx = {c: i for i, c in enumerate(mp_ids)}
        office_cache: dict = {}
        rows, vote_ids, partial = [], [], []
        att_src: dict = {}
        mstats: dict = defaultdict(lambda: {"F": 0, "C": 0, "A": 0, "X": 0, "dissent": [], "known": 0, "absent": 0})
        for v, ini in votes:
            date = v["date"]
            office = office_cache.get(date)
            if office is None:
                office = office_cache[date] = in_office(roster, leg, date)
            row = ["-"] * len(mp_ids)
            named: dict[str, str] = {}
            named_by_party: Counter = Counter()
            for m in v.get("mps") or []:
                cid = resolve_mp(m["name"], m["party"], leg, date, roster)
                if cid is None:
                    unresolved[(leg, m["name"], m["party"])] += 1
                    continue
                named[cid] = m["vote"]
                named_by_party[m["party"]] += 1
            att = attendance.get((leg, date))
            absent = att["absent"] if att else {}
            if att:
                att_src[v["id"]] = att["sources"]
            positions = v.get("positions") or {}
            for cid, party in office.items():
                if cid not in idx:
                    continue
                if cid in named:
                    code = CODE[named[cid]]
                    stats["named"] += 1
                elif cid in absent:
                    code = "X"
                    stats["absent_attendance"] += 1
                elif party in positions:
                    code = CODE[positions[party]]
                    stats["group"] += 1
                elif party == "Ninsc":
                    code = "X"  # unaffiliated MPs are always named when they vote
                    stats["ninsc_unlisted"] += 1
                else:
                    code = "X"
                    stats["group_unlisted"] += 1
                row[idx[cid]] = code
                ms = mstats[cid]
                ms[code] += 1
                if att:  # attendance known for this date: participation can be measured
                    ms["known"] += 1
                    ms["absent"] += code == "X"
                if cid in named and party in positions and positions[party] != "ausente" \
                        and named[cid] != positions[party] and named[cid] != "ausente":
                    ms["dissent"].append(v["id"])
            # unnamed dissenters: split counts larger than the MPs actually named
            for party, split in (v.get("splits") or {}).items():
                main = positions.get(party)
                minority = sum(c for k, c in split.items() if k not in (main, "ausente") and isinstance(c, int))
                if minority > named_by_party.get(party, 0):
                    partial.append(v["id"])
                    break
            rows.append("".join(row))
            vote_ids.append(v["id"])
        out[leg] = {"mps": mp_ids, "votes": vote_ids, "rows": rows, "partial": sorted(set(partial)),
                    "attendance": att_src, "stats": dict(mstats)}
        print(f"  attendance known for {len(att_src)}/{len(vote_ids)} votes")
        print(f"{leg}: {len(mp_ids)} MPs × {len(vote_ids)} votes, {len(set(partial))} votes with unnamed dissenters")
    print("attribution:", dict(stats))
    if unresolved:
        print(f"unresolved named MPs: {sum(unresolved.values())} mentions, {len(unresolved)} distinct, e.g.",
              list(unresolved.most_common(8)))
    (PROCESSED / "mp_votes.json").write_text(json.dumps(out, separators=(",", ":")))
    return out


if __name__ == "__main__":
    build()
