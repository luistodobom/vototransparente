"""Merge normalized votes + Gemini summaries + reference data into the site's build-time data.

Writes to site/src/data/:
  initiatives.json  – every voted initiative, with its summary when available (build-time only)
  meta.json         – legislatures, parties, categories, global stats
  parties.json      – per party/legislature voting statistics and pairwise agreement
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from itertools import combinations

from .config import PROCESSED, SCRAPED, SITE_DATA, SUMMARIES
from .summarize import CATEGORIES

PARTIES = {
    "PS": {"name": "Partido Socialista", "color": "#F2569A"},
    "PSD": {"name": "Partido Social Democrata", "color": "#F58220"},
    "CH": {"name": "Chega", "color": "#2E3F8F"},
    "IL": {"name": "Iniciativa Liberal", "color": "#00AEEF"},
    "BE": {"name": "Bloco de Esquerda", "color": "#B3123A"},
    "PCP": {"name": "Partido Comunista Português", "color": "#D7191C"},
    "PEV": {"name": "Partido Ecologista \"Os Verdes\"", "color": "#3A9D3A"},
    "L": {"name": "LIVRE", "color": "#8DC63F"},
    "CDS-PP": {"name": "CDS – Partido Popular", "color": "#0A6EBD"},
    "PAN": {"name": "Pessoas–Animais–Natureza", "color": "#0E8C7F"},
    "JPP": {"name": "Juntos Pelo Povo", "color": "#2BB673"},
}

# Seats won at each election (start of legislature). Order = left→right for hemicycle drawing.
LEGISLATURES = {
    "XIV": {"label": "XIV Legislatura", "start": "2019-10-25", "end": "2022-03-28",
            "seats": {"BE": 19, "PCP": 10, "PEV": 2, "L": 1, "PAN": 4, "PS": 108, "PSD": 79, "CDS-PP": 5, "IL": 1, "CH": 1}},
    "XV": {"label": "XV Legislatura", "start": "2022-03-29", "end": "2024-03-25",
           "seats": {"BE": 5, "PCP": 6, "L": 1, "PAN": 1, "PS": 120, "PSD": 77, "IL": 8, "CH": 12}},
    "XVI": {"label": "XVI Legislatura", "start": "2024-03-26", "end": "2025-06-02",
            "seats": {"BE": 5, "PCP": 4, "L": 4, "PAN": 1, "PS": 78, "PSD": 78, "CDS-PP": 2, "IL": 8, "CH": 50}},
    "XVII": {"label": "XVII Legislatura", "start": "2025-06-03", "end": None,
             "seats": {"BE": 1, "PCP": 3, "L": 6, "PAN": 1, "JPP": 1, "PS": 58, "PSD": 89, "CDS-PP": 2, "IL": 9, "CH": 60}},
}

TYPE_SLUGS = {
    "Projeto de Lei": "projeto-lei",
    "Proposta de Lei": "proposta-lei",
    "Projeto de Resolução": "projeto-resolucao",
    "Proposta de Resolução": "proposta-resolucao",
    "Projeto de Deliberação": "projeto-deliberacao",
    "Apreciação Parlamentar": "apreciacao-parlamentar",
    "Inquérito Parlamentar": "inquerito-parlamentar",
    "Projeto de Revisão Constitucional": "revisao-constitucional",
    "Projeto de Regimento": "projeto-regimento",
}

KEY_PHASES = ["final_global", "global", "final", "deliberacao", "generalidade"]


def key_vote(votes: list[dict]) -> dict:
    """The vote that best represents the initiative's fate: final > deliberation > generalidade."""
    for ph in KEY_PHASES:
        cand = [v for v in votes if v["phase"] == ph]
        if cand:
            return cand[-1]
    return votes[-1]


def status(ini: dict) -> str:
    kv = key_vote(ini["votes"])
    res = (kv["result"] or "").lower()
    if res.startswith("aprovad"):
        if kv["phase"] == "generalidade" and ini["type"] in ("Projeto de Lei", "Proposta de Lei"):
            return "em_curso"  # passed first reading, still in committee / not yet final
        return "aprovado"
    if res.startswith("rejeitad"):
        return "rejeitado"
    return "outro"


def export_mps(rows: list[dict]):
    """Per-MP data: build-time roster + vote matrix, and lazily-loaded per-legislature vote indexes."""
    dep_p, mv_p = PROCESSED / "deputies.json", PROCESSED / "mp_votes.json"
    if not (dep_p.exists() and mv_p.exists()):
        print("skipping MP export (run pipeline.fetch_deputies and pipeline.mp_votes first)")
        return
    roster = json.loads(dep_p.read_text())["deputies"]
    mv = json.loads(mv_p.read_text())
    # meeting-level attendance per MP (how many plenary meetings they missed, and why)
    att_p = SCRAPED / "attendance.json"
    attendance = json.loads(att_p.read_text()) if att_p.exists() else {}
    from .deputies import in_office, load_roster
    full_roster = load_roster()
    meet: dict = defaultdict(lambda: defaultdict(lambda: Counter()))  # leg -> cid -> Counter
    att_site: dict = {}
    for leg, days in attendance.items():
        for key, rec in days.items():
            day = key[:10]
            absent = {a["id"]: a for a in rec.get("absent", []) if a.get("id")}
            for cid in in_office(full_roster, leg, day):
                c = meet[leg][cid]
                c["meetings"] += 1
                if cid in absent:
                    c[absent[cid].get("status") or "F"] += 1
            site = att_site.setdefault(f"{leg}|{day}", {"absent": {}, "sources": []})
            site["sources"].append(rec.get("source"))
            for cid, a in absent.items():
                site["absent"][cid] = [a.get("status_text") or a.get("status"), a.get("reason")]
    by_vote = {v["id"]: (v, r) for r in rows for v in r["votes"]}
    deputies = {}
    for leg, M in mv.items():
        for cid in M["mps"]:
            d = roster[cid]
            st = M["stats"].get(cid, {"F": 0, "C": 0, "A": 0, "X": 0, "dissent": []})
            L = d["legs"][leg]
            has_photo = (SITE_DATA.parent.parent / "public" / "img" / "mp" / f"{cid}.webp").exists()
            e = deputies.setdefault(cid, {"id": cid, "name": d["name"], "fullName": d.get("fullName"),
                                          "photo": has_photo, "legs": {}})
            e["legs"][leg] = {
                "circle": L.get("circle"),
                "parties": L.get("parties", []),
                "party": (L.get("parties") or [{"party": "?"}])[-1]["party"],
                "inOffice": L.get("inOffice", []),
                "F": st["F"], "C": st["C"], "A": st["A"], "X": st["X"],
                "dissent": st["dissent"],
                "known": st.get("known", 0), "absentVotes": st.get("absent", 0),
                "meetings": dict(meet[leg].get(cid, {})),
            }
        # lazily-loaded vote index for the MP "full record" view, aligned with M["votes"]
        idxrows = []
        for vid in M["votes"]:
            v, ini = by_vote[vid]
            title = (ini.get("summary") or {}).get("short_title") or ini["title"]
            idxrows.append([vid, ini["id"], v["date"], v["phase"], (v["result"] or "")[:1], title[:160], ini["ref"]])
        pub = SITE_DATA.parent.parent / "public" / "data" / "parlamento" / leg
        pub.mkdir(parents=True, exist_ok=True)
        (pub / "votes.json").write_text(json.dumps(idxrows, ensure_ascii=False, separators=(",", ":")))
    (SITE_DATA / "attendance.json").write_text(json.dumps(att_site, ensure_ascii=False, separators=(",", ":")))
    (SITE_DATA / "deputies.json").write_text(json.dumps(deputies, ensure_ascii=False, separators=(",", ":")))
    (SITE_DATA / "mpvotes.json").write_text(json.dumps(
        {leg: {k: M[k] for k in ("mps", "votes", "rows", "partial")} for leg, M in mv.items()}, separators=(",", ":")))
    print(f"exported {len(deputies)} MPs")


def main():
    rows = json.loads((PROCESSED / "initiatives.json").read_text())
    n_sum = 0
    for r in rows:
        sp = SUMMARIES / f"{r['id']}.json"
        r["summary"] = json.loads(sp.read_text()) if sp.exists() else None
        n_sum += r["summary"] is not None
        r["typeSlug"] = TYPE_SLUGS.get(r["type"], "outro")
        kv = key_vote(r["votes"])
        r["keyVoteId"] = kv["id"]
        r["status"] = status(r)
        r["categories"] = (r["summary"] or {}).get("categories") or []
        r["docUrl"] = (r.pop("textUrl", None) or "").replace("http://", "https://") or None

    # ---- party statistics per legislature (based on each initiative's key vote) ----
    stats: dict = defaultdict(lambda: defaultdict(lambda: Counter()))
    agree: dict = defaultdict(lambda: defaultdict(lambda: [0, 0]))  # leg -> "A|B" -> [same, total]
    authored: dict = defaultdict(lambda: defaultdict(Counter))
    for r in rows:
        kv = next(v for v in r["votes"] if v["id"] == r["keyVoteId"])
        pos = {p: x for p, x in kv["positions"].items() if x != "ausente"}
        for p, x in pos.items():
            stats[r["leg"]][p][x] += 1
            stats[r["leg"]][p]["total"] += 1
            res = (kv["result"] or "").lower()
            winning = (x == "favor" and res.startswith("aprov")) or (x == "contra" and res.startswith("rejeit"))
            stats[r["leg"]][p]["winning"] += winning
        for a, b in combinations(sorted(pos), 2):
            cell = agree[r["leg"]][f"{a}|{b}"]
            cell[0] += pos[a] == pos[b]
            cell[1] += 1
        for p in r["authors"]["parties"]:
            authored[r["leg"]][p]["total"] += 1
            authored[r["leg"]][p][r["status"]] += 1
        if r["authors"]["label"] == "Governo":
            authored[r["leg"]]["Governo"]["total"] += 1
            authored[r["leg"]]["Governo"][r["status"]] += 1

    parties_out = {
        leg: {
            "votes": {p: dict(c) for p, c in stats[leg].items()},
            "agreement": {k: {"same": v[0], "total": v[1]} for k, v in agree[leg].items()},
            "authored": {p: dict(c) for p, c in authored[leg].items()},
        }
        for leg in LEGISLATURES
    }

    cat_counts = Counter(c for r in rows for c in r["categories"])
    meta = {
        "generated": max((v["date"] for r in rows for v in r["votes"] if v["date"]), default=None),
        "parties": PARTIES,
        "legislatures": LEGISLATURES,
        "categories": {k: {"label": v, "count": cat_counts.get(k, 0)} for k, v in CATEGORIES.items()},
        "types": {v: {"label": k, "count": sum(1 for r in rows if r["type"] == k)} for k, v in TYPE_SLUGS.items()},
        "stats": {
            "initiatives": len(rows),
            "votes": sum(len(r["votes"]) for r in rows),
            "summarized": n_sum,
            "byStatus": dict(Counter(r["status"] for r in rows)),
            "byLeg": dict(Counter(r["leg"] for r in rows)),
        },
    }
    src_p = PROCESSED / "sources.json"
    meta["sources"] = json.loads(src_p.read_text()) if src_p.exists() else None
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    (SITE_DATA / "initiatives.json").write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")))
    (SITE_DATA / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    (SITE_DATA / "parties.json").write_text(json.dumps(parties_out, ensure_ascii=False, indent=1))
    print(f"exported {len(rows)} initiatives ({n_sum} with summaries) -> {SITE_DATA}")
    export_mps(rows)
    print(meta["stats"])


if __name__ == "__main__":
    main()
