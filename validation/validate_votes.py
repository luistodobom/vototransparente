"""Validate data/processed/initiatives.json against two reference sources.

  1. validation/reference/old_parliament_data.csv  (old project DB, LLM-parsed session-result PDFs)
  2. validation/maisliberdade/raw/iniciativas_{XIV,XV,XVI}.csv  (+Liberdade dashboard,
     generalidade votes only)

Run:  .venv/bin/python validation/validate_votes.py
Writes validation/results.json and validation/mismatches_*.csv.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent
NEW = ROOT / "data/processed/initiatives.json"
OLD = ROOT / "validation/reference/old_parliament_data.csv"
ML_DIR = OUT / "maisliberdade/raw"
ML_LEGS = ["XIV", "XV", "XVI"]

BID_RE = re.compile(r"BID=(\d+)", re.I)
POS_NAMES = ("favor", "contra", "abstencao", "ausente")


def d(s):
    return date.fromisoformat(str(s)[:10])


def bid_of(url):
    m = BID_RE.search(str(url or ""))
    return m.group(1) if m else None


# ----------------------------------------------------------------------------- new data
def load_new():
    rows = json.loads(NEW.read_text())
    by_bid: dict[str, list[dict]] = defaultdict(list)
    for ini in rows:
        for v in ini["votes"]:
            if not v.get("date"):
                continue
            by_bid[ini["id"]].append({**v, "bid": ini["id"], "leg": ini["leg"], "type": ini["type"],
                                      "ref": ini["ref"]})
    return rows, by_bid


def norm_result(r):
    r = (r or "").strip().lower()
    if r.startswith("aprova"):
        return "Aprovado"
    if r.startswith("rejeita"):
        return "Rejeitado"
    return r or None


# ----------------------------------------------------------------------------- old DB
OLD_PARTY = {"CHEGA": "CH", "LIVRE": "L", "CDS": "CDS-PP", "PPD/PSD": "PSD", "PEV": "PEV"}
OLD_SKIP = {"NiJKM", "NiCR", "Ninsc", "NINSC", "RAR"}  # non-inscritos / not a GP


def old_majority(c: dict) -> str | None:
    vals = {"favor": c.get("Favor") or 0, "contra": c.get("Contra") or 0,
            "abstencao": c.get("Abstenção") or 0}
    if sum(vals.values()) == 0:
        return "ausente" if (c.get("Não Votaram") or 0) > 0 else None
    return max(vals.items(), key=lambda kv: kv[1])[0]


def old_positions(js: str) -> dict[str, str]:
    out = {}
    try:
        data = json.loads(js)
    except Exception:
        return out
    for p, c in data.items():
        if p in OLD_SKIP or not isinstance(c, dict):
            continue
        pos = old_majority(c)
        if pos is None:
            continue
        for part in re.split(r"[/-](?!PP)", p):  # 'PCP-PEV' -> PCP,PEV ; 'PSD/CDS-PP' -> PSD,CDS-PP
            part = OLD_PARTY.get(part.strip(), part.strip())
            out.setdefault(part, pos)
    return out


def load_old():
    df = pd.read_csv(OLD)
    df["bid"] = df["proposal_gov_link"].map(bid_of)
    df["is_ini"] = df["proposal_gov_link"].str.contains("DetalheIniciativa", na=False)
    recs = []
    for i, r in df.iterrows():
        recs.append({
            "row": int(i), "bid": r["bid"], "is_ini": bool(r["is_ini"]),
            "date": str(r["session_date"])[:10], "name": r["proposal_name_from_session"],
            "link_kind": (re.search(r"Paginas/([^?.]+)", str(r["proposal_gov_link"])) or [None, None])[1],
            "result": {1.0: "Aprovado", 0.0: "Rejeitado"}.get(r["proposal_approval_status"]),
            "positions": old_positions(r["voting_details_json"]) if isinstance(r["voting_details_json"], str) else {},
        })
    return recs


# ----------------------------------------------------------------------------- maisliberdade
ML_POS = {"Favor": "favor", "Contra": "contra", "Abst.": "abstencao", "Aus.": "ausente"}
ML_PARTIES = {"ps": "PS", "psd": "PSD", "ch": "CH", "il": "IL", "be": "BE", "pcp": "PCP",
              "l": "L", "pan": "PAN", "cds-pp": "CDS-PP"}


def load_ml():
    recs = []
    for leg in ML_LEGS:
        df = pd.read_csv(ML_DIR / f"iniciativas_{leg}.csv")
        df.columns = [c.replace("initiativas.", "") for c in df.columns]
        for _, r in df.iterrows():
            pos, verlink = {}, []
            for k, p in ML_PARTIES.items():
                col = f"iniciativa_votacao_{k}"
                if col not in df.columns:
                    continue
                v = r[col]
                if v in ML_POS:
                    pos[p] = ML_POS[v]
                elif v == "Ver link":
                    verlink.append(p)
            recs.append({
                "ml_leg": leg, "ml_id": int(r["iniciativa_id"]), "bid": bid_of(r["iniciativa_url_res"]),
                "date": str(r["iniciativa_data"])[:10], "name": r["iniciativa_titulo"],
                "type": r["iniciativa_tipo"], "result": norm_result(r["iniciativa_votacao_res"]),
                "positions": pos, "ver_link": verlink,
                "desc": r.get("iniciativa_votacao_desc") if isinstance(r.get("iniciativa_votacao_desc"), str) else None,
            })
    return recs


# ----------------------------------------------------------------------------- matching
def pos_agreement(ours: dict, ref: dict):
    """Return (agree, compared, diffs, missing_in_ours)."""
    agree = comp = 0
    diffs, missing = {}, []
    for p, rp in ref.items():
        op = ours.get(p)
        if op is None:
            missing.append(p)
            continue
        comp += 1
        if op == rp:
            agree += 1
        else:
            diffs[p] = {"ours": op, "ref": rp}
    return agree, comp, diffs, missing


def match(rec, by_bid, prefer_phases=None, max_days=None):
    """Pick our vote for a reference record: same BID, same/nearest date.

    Among several of our votes on the same date (e.g. generalidade + especialidade + final
    global held together), prefer phases in `prefer_phases`, then the vote whose result and
    positions best agree with the reference (the ambiguity is reported).
    """
    cands = by_bid.get(rec["bid"]) or []
    if not cands:
        return None, "bid_not_found", 0
    rd = d(rec["date"])
    best_gap = min(abs((d(c["date"]) - rd).days) for c in cands)
    if max_days is not None and best_gap > max_days:
        return None, "no_vote_near_date", best_gap
    same = [c for c in cands if abs((d(c["date"]) - rd).days) == best_gap]
    if prefer_phases:
        pref = [c for c in same if c["phase"] in prefer_phases]
        same = pref or same

    def score(c):
        a, n, _, _ = pos_agreement(c["positions"], rec["positions"])
        return ((norm_result(c["result"]) == rec["result"]), a / n if n else 0)
    same.sort(key=score, reverse=True)
    status = "exact_date" if best_gap == 0 else "nearest_date"
    return same[0], status, len(same)


def evaluate(name, recs, by_bid, prefer_phases=None, max_days=None):
    matched, unmatched = [], []
    for rec in recs:
        v, status, n_same = match(rec, by_bid, prefer_phases, max_days)
        if v is None:
            unmatched.append({**rec, "status": status, "gap": n_same})
            continue
        a, n, diffs, missing = pos_agreement(v["positions"], rec["positions"])
        matched.append({
            **rec, "status": status, "n_same_date": n_same, "gap_days": abs((d(v["date"]) - d(rec["date"])).days),
            "our_vote_id": v["id"], "our_date": v["date"], "our_phase": v["phase"], "our_leg": v["leg"],
            "our_result": norm_result(v["result"]), "our_unanimous": v["unanimous"],
            "our_inferred": bool(v.get("inferred")), "our_positions": v["positions"],
            "result_agree": (norm_result(v["result"]) == rec["result"]) if rec["result"] else None,
            "pos_agree": a, "pos_compared": n, "pos_diffs": diffs, "pos_missing_in_ours": missing,
        })
    return matched, unmatched


def summarize(name, recs, matched, unmatched, key_group):
    s: dict = {"source": name, "reference_records": len(recs), "matched": len(matched),
               "unmatched": len(unmatched),
               "unmatched_reasons": dict(Counter(u.get("status", "not_exact_date") for u in unmatched)),
               "match_status": dict(Counter(m["status"] for m in matched)),
               "ambiguous_same_date": sum(1 for m in matched if m["n_same_date"] > 1)}
    ra = [m for m in matched if m["result_agree"] is not None]
    s["result_compared"] = len(ra)
    s["result_agree"] = sum(m["result_agree"] for m in ra)
    s["result_agree_rate"] = round(s["result_agree"] / len(ra), 4) if ra else None
    exact = [m for m in ra if m["status"] == "exact_date"]
    s["result_agree_rate_exact_date"] = round(sum(m["result_agree"] for m in exact) / len(exact), 4) if exact else None
    tot_a = sum(m["pos_agree"] for m in matched)
    tot_n = sum(m["pos_compared"] for m in matched)
    s["position_compared"] = tot_n
    s["position_agree"] = tot_a
    s["position_agree_rate"] = round(tot_a / tot_n, 4) if tot_n else None
    s["votes_all_positions_agree"] = sum(1 for m in matched if m["pos_compared"] and not m["pos_diffs"])
    s["votes_with_position_diff"] = sum(1 for m in matched if m["pos_diffs"])
    per_party = defaultdict(lambda: [0, 0])
    conf = Counter()
    for m in matched:
        for p in m["positions"]:
            if p in m["our_positions"]:
                per_party[p][1] += 1
                per_party[p][0] += p not in m["pos_diffs"]
        for p, dd in m["pos_diffs"].items():
            conf[f"ref={dd['ref']} ours={dd['ours']}"] += 1
    s["per_party"] = {p: {"agree": a, "compared": n, "rate": round(a / n, 4)}
                      for p, (a, n) in sorted(per_party.items(), key=lambda kv: -kv[1][1])}
    s["position_confusion"] = dict(conf.most_common())
    s["missing_party_in_ours"] = dict(Counter(p for m in matched for p in m["pos_missing_in_ours"]).most_common())
    groups = defaultdict(lambda: {"ref": 0, "matched": 0, "res_n": 0, "res_ok": 0, "pos_n": 0, "pos_ok": 0})
    for r in recs:
        groups[key_group(r)]["ref"] += 1
    for m in matched:
        g = groups[key_group(m)]
        g["matched"] += 1
        if m["result_agree"] is not None:
            g["res_n"] += 1
            g["res_ok"] += m["result_agree"]
        g["pos_n"] += m["pos_compared"]
        g["pos_ok"] += m["pos_agree"]
    s["by_group"] = {k: {**g, "coverage": round(g["matched"] / g["ref"], 4) if g["ref"] else None,
                         "result_rate": round(g["res_ok"] / g["res_n"], 4) if g["res_n"] else None,
                         "pos_rate": round(g["pos_ok"] / g["pos_n"], 4) if g["pos_n"] else None}
                     for k, g in sorted(groups.items())}
    return s


def reverse_coverage(by_bid, ref_pairs, legs, phases, date_lo, date_hi, by="leg"):
    """Fraction of OUR votes (filtered) that have a reference record with same BID & date (±1 day)."""
    out = defaultdict(lambda: [0, 0])
    for bid, votes in by_bid.items():
        for v in votes:
            if legs and v["leg"] not in legs:
                continue
            if phases and v["phase"] not in phases:
                continue
            if not (date_lo <= v["date"] <= date_hi):
                continue
            k = v["leg"] if by == "leg" else v["date"][:4]
            out[k][1] += 1
            vd = d(v["date"])
            out[k][0] += any((bid, (vd.fromordinal(vd.toordinal() + o)).isoformat()) in ref_pairs for o in (-1, 0, 1))
    return {k: {"found_in_ref": a, "ours": n, "rate": round(a / n, 4) if n else None}
            for k, (a, n) in sorted(out.items())}


def mismatch_rows(matched):
    rows = []
    for m in matched:
        if m["result_agree"] is False or m["pos_diffs"]:
            rows.append({
                "bid": m["bid"], "ref_date": m["date"], "our_date": m["our_date"], "our_phase": m["our_phase"],
                "our_vote_id": m["our_vote_id"], "n_same_date": m["n_same_date"], "name": m["name"],
                "ref_result": m["result"], "our_result": m["our_result"],
                "result_agree": m["result_agree"], "our_unanimous": m["our_unanimous"],
                "our_inferred": m["our_inferred"],
                "pos_diffs": json.dumps(m["pos_diffs"], ensure_ascii=False),
                "n_pos_diffs": len(m["pos_diffs"]),
                "ref_positions": json.dumps(m["positions"], ensure_ascii=False),
                "our_positions": json.dumps(m["our_positions"], ensure_ascii=False),
            })
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- raw-dump diagnostics
RAW_DIR = ROOT / "data/raw/opendata"


def load_raw_index():
    """IniId -> {leg, sel, type, nr, votes_by_date: {date: [phase,...]}} from the open-data dumps."""
    idx = {}
    for f in sorted(RAW_DIR.glob("Iniciativas*.json")):
        for ini in json.loads(f.read_text()):
            vb = defaultdict(list)
            for e in ini.get("IniEventos") or []:
                for v in e.get("Votacao") or []:
                    vb[(v.get("data") or e.get("DataFase") or "")[:10]].append(e.get("Fase"))
            idx[str(ini["IniId"])] = {"leg": ini["IniLeg"], "sel": ini.get("IniSel"),
                                      "type": ini["IniDescTipo"], "nr": ini.get("IniNr"), "votes_by_date": vb}
    return idx


def classify_unmatched(recs, raw):
    for r in recs:
        info = raw.get(r["bid"])
        if info is None:
            r["reason"] = "initiative_not_in_opendata_dump"
        elif r["date"] in info["votes_by_date"]:
            r["reason"] = "only_procedural_vote_on_date"
            r["raw_phases"] = info["votes_by_date"][r["date"]]
        elif info["votes_by_date"]:
            r["reason"] = "only_procedural_votes_other_dates"
        else:
            r["reason"] = "no_votes_in_dump"
    return dict(Counter(r["reason"] for r in recs))


def classify_nearest(matched, raw):
    out = Counter()
    for m in matched:
        if m["status"] != "nearest_date":
            continue
        info = raw.get(m["bid"]) or {"votes_by_date": {}}
        ph = info["votes_by_date"].get(m["date"])
        m["raw_phases_on_ref_date"] = ph
        out["procedural_vote_on_ref_date" if ph else "no_vote_on_ref_date_in_dump"] += 1
    return dict(out)


def arbitrate(m_old, ml_recs):
    """For old-DB vs ours disagreements, ask the third source (maisliberdade) who is right."""
    ml_by = {(r["bid"], r["date"]): r for r in ml_recs}
    res_c, pos_c = Counter(), Counter()
    for m in m_old:
        ml = ml_by.get((m["bid"], m["date"]))
        if m["result_agree"] is False:
            if ml is None:
                res_c["no_third_source"] += 1
            else:
                res_c["ml_agrees_with_ours" if ml["result"] == m["our_result"] else
                      "ml_agrees_with_old" if ml["result"] == m["result"] else "neither"] += 1
        for p, dd in m["pos_diffs"].items():
            if ml is None or p not in ml["positions"]:
                pos_c["no_third_source"] += 1
                continue
            mp = ml["positions"][p]
            pos_c["ml_agrees_with_ours" if mp == dd["ours"] else
                  "ml_agrees_with_old" if mp == dd["ref"] else "neither"] += 1
        m["ml_available"] = ml is not None
    return {"result_disagreements": dict(res_c), "position_disagreements": dict(pos_c)}


def main():
    _, by_bid = load_new()
    raw = load_raw_index()
    results = {}

    # ---- old DB
    old = load_old()
    old_ini = [r for r in old if r["is_ini"] and r["bid"]]
    old_other = Counter(r["link_kind"] for r in old if not r["is_ini"])
    m_old, u_old = evaluate("old_db", old_ini, by_bid, max_days=None)
    s_old = summarize("old_db", old_ini, m_old, u_old, lambda r: r["date"][:4])
    s_old["unmatched_root_causes"] = classify_unmatched(u_old, raw)
    s_old["nearest_date_root_causes"] = classify_nearest(m_old, raw)
    s_old["excluded_non_initiative_rows"] = dict(old_other)
    s_old["excluded_note"] = ("Rows linking to votos/moções/diplomas (Detalhe-Votos etc.) are not "
                              "initiatives and are out of scope of initiatives.json")
    # exact-date-only view (a nearest-date match is a different vote in most cases)
    m_old_exact = [m for m in m_old if m["status"] == "exact_date"]
    s_old["exact_date_only"] = summarize("old_db_exact", old_ini, m_old_exact,
                                         [r for r in old_ini if r["row"] not in {m["row"] for m in m_old_exact}], lambda r: r["date"][:4])
    old_pairs = {(r["bid"], r["date"]) for r in old_ini}
    lo, hi = min(r["date"] for r in old_ini), max(r["date"] for r in old_ini)
    s_old["reverse_coverage_by_year"] = reverse_coverage(by_bid, old_pairs, None, None, lo, hi, by="year")
    s_old["date_range"] = [lo, hi]
    results["old_db"] = s_old
    mismatch_rows(m_old_exact).to_csv(OUT / "mismatches_old_db.csv", index=False)
    pd.DataFrame([{k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                   for k, v in u.items()} for u in u_old]).to_csv(OUT / "unmatched_old_db.csv", index=False)

    # ---- maisliberdade
    ml = load_ml()
    ml_ok = [r for r in ml if r["bid"]]
    pref = {"generalidade", "deliberacao", "global"}
    m_ml, u_ml = evaluate("maisliberdade", ml_ok, by_bid, prefer_phases=pref)
    s_ml = summarize("maisliberdade", ml_ok, m_ml, u_ml, lambda r: r["ml_leg"])
    s_ml["unmatched_root_causes"] = classify_unmatched(u_ml, raw)
    s_ml["unmatched_by_leg_session"] = dict(Counter(
        f"{u['ml_leg']} BID<45227 (XIV 1st session)" if int(u["bid"]) < 45227 else u["ml_leg"] for u in u_ml))
    results["old_db"]["arbitration_by_maisliberdade"] = arbitrate(m_old_exact, ml_ok)
    s_ml["rows_without_bid"] = len(ml) - len(ml_ok)
    s_ml["matched_phase"] = dict(Counter(m["our_phase"] for m in m_ml))
    m_ml_exact = [m for m in m_ml if m["status"] == "exact_date"]
    s_ml["exact_date_only"] = summarize("maisliberdade_exact", ml_ok, m_ml_exact,
                                        [r for r in ml_ok if (r["ml_leg"], r["ml_id"]) not in {(m["ml_leg"], m["ml_id"]) for m in m_ml_exact}], lambda r: r["ml_leg"])
    ml_pairs = {(r["bid"], r["date"]) for r in ml_ok}
    rc = {}
    for leg in ML_LEGS:
        rr = [r for r in ml_ok if r["ml_leg"] == leg]
        lo, hi = min(r["date"] for r in rr), max(r["date"] for r in rr)
        rc[leg] = {"date_range": [lo, hi], **reverse_coverage(by_bid, ml_pairs, {leg}, pref, lo, hi)
                   .get(leg, {})}
    s_ml["reverse_coverage_by_leg"] = rc
    results["maisliberdade"] = s_ml
    mismatch_rows(m_ml).to_csv(OUT / "mismatches_maisliberdade.csv", index=False)
    pd.DataFrame([{k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                   for k, v in u.items()} for u in u_ml]).to_csv(OUT / "unmatched_maisliberdade.csv", index=False)

    mc = OUT / "manual_checks.json"
    if mc.exists():
        checks = json.loads(mc.read_text())
        results["manual_checks"] = {"n": len(checks), "verdicts": dict(Counter(c["verdict"].split(";")[0] for c in checks)),
                                    "causes": dict(Counter(c["cause"] for c in checks)), "checks": checks}
    (OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1))
    for k, s in results.items():
        if k == "manual_checks":
            continue
        print(f"== {k}: ref={s['reference_records']} matched={s['matched']} unmatched={s['unmatched_reasons']} "
              f"status={s['match_status']} ambiguous={s['ambiguous_same_date']}")
        print(f"   result agree {s['result_agree']}/{s['result_compared']} = {s['result_agree_rate']} "
              f"(exact-date {s['result_agree_rate_exact_date']})")
        print(f"   positions agree {s['position_agree']}/{s['position_compared']} = {s['position_agree_rate']}; "
              f"votes w/ diff {s['votes_with_position_diff']}")
        e = s["exact_date_only"]
        print(f"   [exact-date only] matched={e['matched']} result={e['result_agree_rate']} pos={e['position_agree_rate']}")
        print("   per party:", {p: v["rate"] for p, v in s["per_party"].items()})
        print("   confusion:", dict(list(s["position_confusion"].items())[:8]))
        print("   missing in ours:", s["missing_party_in_ours"])
        print("   by group:", {g: (v["ref"], v["coverage"], v["result_rate"], v["pos_rate"]) for g, v in s["by_group"].items()})
        print("   unmatched causes:", s.get("unmatched_root_causes"), s.get("nearest_date_root_causes"))
        print("   arbitration:", s.get("arbitration_by_maisliberdade"))
        print("   reverse:", s.get("reverse_coverage_by_year") or s.get("reverse_coverage_by_leg"))


if __name__ == "__main__":
    main()
