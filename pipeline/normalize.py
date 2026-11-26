"""Normalize the Parliament open-data dumps into a compact, site-friendly dataset.

Output: data/processed/initiatives.json — a list of initiatives that had at least
one plenary vote, each with its authors, timeline and every vote (per-party
positions + individual dissenting MPs).
"""
from __future__ import annotations

import html
import json
import re
from collections import Counter
from datetime import date

from .config import LEGISLATURES, OPENDATA_DIR, PROCESSED, SCRAPED

VOTE_KEYS = {"A Favor": "favor", "Contra": "contra", "Abstenção": "abstencao", "Ausência": "ausente"}

# Phases that are an actual vote on the initiative itself (procedural requests excluded).
SUBSTANTIVE_PHASES = {
    "Votação na generalidade": "generalidade",
    "Votação na especialidade": "especialidade",
    "Votação final global": "final_global",
    "Votação Deliberação": "deliberacao",
    "Votação global": "global",
    "Votação final": "final",
    "Votação novo decreto": "novo_decreto",
    "Confirmação do decreto": "confirmacao_decreto",
}

LEG_YEARS = {"XIII": (2015, 2019), "XIV": (2019, 2022), "XV": (2022, 2024), "XVI": (2024, 2025), "XVII": (2025, 2029)}

_ITEM = re.compile(r"<I>(.*?)</I>", re.S)
_COUNT = re.compile(r"^(\d+)-(.+)$")
_MP = re.compile(r"^(.+?)\s*\(([^)]+)\)$")


def parse_detail(detail: str | None):
    """Parse 'A Favor: <I>PS</I>, <I>3-PSD</I>, <I>Fulano (PSD)</I><BR>Contra: …'.

    Returns (party_votes, mps) where party_votes = {party: {position: count|None}}
    (None = whole group) and mps = [{name, party, vote}].
    """
    party: dict[str, dict[str, int | None]] = {}
    mps: list[dict] = []
    if not detail:
        return party, mps
    for chunk in re.split(r"<BR>", detail, flags=re.I):
        if ":" not in chunk:
            continue
        head, body = chunk.split(":", 1)
        pos = VOTE_KEYS.get(html.unescape(head).strip())
        if not pos:
            continue
        for raw in _ITEM.findall(body):
            tok = html.unescape(raw).strip()
            if not tok:
                continue
            m = _MP.match(tok)
            if m and not _COUNT.match(tok):
                mps.append({"name": m.group(1).strip(), "party": m.group(2).strip(), "vote": pos})
                continue
            m = _COUNT.match(tok)
            if m:
                party.setdefault(m.group(2).strip(), {})[pos] = int(m.group(1))
            else:
                party.setdefault(tok, {})[pos] = None
    return party, mps


def summarize_party(pv: dict[str, int | None]) -> tuple[str, bool]:
    """Collapse a party's votes into a single position + 'split' flag."""
    real = {k: v for k, v in pv.items() if k != "ausente"}
    if not real:
        return "ausente", False
    if len(real) == 1:
        return next(iter(real)), False
    # Split vote. Entries without a count represent "the rest of the group" -> majority.
    whole = [k for k, v in real.items() if v is None]
    if whole:
        return whole[0], True
    return max(real.items(), key=lambda kv: kv[1])[0], True


def ref_label(ini: dict) -> str:
    sel = ini.get("IniSel")
    return f"{ini['IniDescTipo']} n.º {ini['IniNr']}/{ini['IniLeg']}" + (f"/{sel}.ª" if sel else "")


def authors(ini: dict) -> dict:
    parties = [g["GP"] for g in ini.get("IniAutorGruposParlamentares") or [] if g.get("GP")]
    deputies = [
        {"name": d["nome"], "party": d.get("GP"), "id": d.get("idCadastro")}
        for d in ini.get("IniAutorDeputados") or []
    ]
    other = ini.get("IniAutorOutros") or {}
    other_name = other.get("nome") if isinstance(other, dict) else None
    if not parties and deputies:
        parties = sorted({d["party"] for d in deputies if d["party"]})
    if other_name and other_name.lower().startswith("governo"):
        label = "Governo"
    elif parties:
        label = ", ".join(parties)
    else:
        label = other_name or "—"
    return {"label": label, "parties": parties, "deputies": deputies, "other": other_name}


def _dar_ref(event: dict) -> dict | None:
    """The DAR I série publication of an event: {url, number, session, date, pages}."""
    for pub in event.get("PublicacaoFase") or []:
        if (pub.get("pubTipo") or "").startswith("DAR I") and pub.get("URLDiario"):
            return {"url": pub["URLDiario"], "nr": pub.get("pubNr"), "sl": pub.get("pubSL"),
                    "date": pub.get("pubdt"), "pages": ", ".join(pub.get("pag") or [])}
    return None


def normalize_leg(leg: str, data: list[dict]):
    out = []
    parties_seen: Counter = Counter()
    for ini in data:
        votes = []
        timeline = []
        for e in ini.get("IniEventos") or []:
            timeline.append({"date": e.get("DataFase"), "phase": e.get("Fase")})
            dar = _dar_ref(e)
            for v in e.get("Votacao") or []:
                phase_key = SUBSTANTIVE_PHASES.get(e.get("Fase"))
                if not phase_key:
                    continue
                pv, mps = parse_detail(v.get("detalhe"))
                positions, splits = {}, {}
                for p, d in pv.items():
                    pos, split = summarize_party(d)
                    positions[p] = pos
                    if split:
                        splits[p] = {k: v for k, v in d.items()}
                    parties_seen[p] += 1
                for p in v.get("ausencias") or []:
                    if _MP.match(p):  # individual MP, e.g. 'Miguel Arruda (Ninsc)': already in mps
                        continue
                    positions.setdefault(p, "ausente")
                unanimous = (v.get("unanime") or "").lower() == "unanime"
                votes.append({
                    "id": v.get("id"),
                    "date": v.get("data") or e.get("DataFase"),
                    "phase": phase_key,
                    "phaseLabel": e.get("Fase"),
                    "result": (v.get("resultado") or "").strip() or None,
                    "unanimous": unanimous,
                    "description": (v.get("descricao") or "").strip() or None,
                    "positions": positions,
                    "splits": splits or None,
                    "mps": mps or None,
                    "meeting": v.get("reuniao"),
                    # official transcript (Diário da Assembleia da República, I série) where the vote is recorded
                    "dar": dar,
                    "source": v["id"].startswith("s1-") and "page" or "opendata",
                })
        if not votes:
            continue
        votes.sort(key=lambda x: (x["date"] or "", x["id"] or ""))
        timeline = sorted([t for t in timeline if t["date"]], key=lambda t: t["date"])
        last = votes[-1]
        out.append({
            "id": str(ini["IniId"]),
            "leg": leg,
            "type": ini["IniDescTipo"],
            "number": ini.get("IniNr"),
            "session": ini.get("IniSel"),
            "ref": ref_label(ini),
            "title": re.sub(r"\s+", " ", (ini.get("IniTitulo") or "")).strip(),
            "authors": authors(ini),
            "dateIn": timeline[0]["date"] if timeline else None,
            "textUrl": ini.get("IniLinkTexto"),
            "url": f"https://www.parlamento.pt/ActividadeParlamentar/Paginas/DetalheIniciativa.aspx?BID={ini['IniId']}",
            "timeline": timeline,
            "votes": votes,
            "lastVoteDate": last["date"],
            "lastResult": last["result"],
        })
    _fill_unanimous(out)
    return out, parties_seen


def _fill_unanimous(rows):
    """Unanimous votes often have no detail; fill every party present at the time as 'favor'.

    The parties present are taken from the closest-in-time vote that does have a detail.
    A unanimous vote whose detail only lists absences (e.g. 'Ausência: CH') is filled too,
    keeping the absent parties as 'ausente'.
    """
    detailed = sorted(
        (v["date"], tuple(sorted(p for p in v["positions"])))
        for r in rows for v in r["votes"] if len(v["positions"]) >= 5 and v["date"]
    )
    if not detailed:
        return
    import bisect
    dates = [d for d, _ in detailed]
    for r in rows:
        for v in r["votes"]:
            if v["unanimous"] and v["date"] and all(p == "ausente" for p in v["positions"].values()):
                i = min(bisect.bisect_left(dates, v["date"]), len(dates) - 1)
                if i > 0 and dates[i] != v["date"] and (
                        date.fromisoformat(v["date"]) - date.fromisoformat(dates[i - 1])
                        < date.fromisoformat(dates[i]) - date.fromisoformat(v["date"])):
                    i -= 1  # the previous detailed vote is closer in time
                absent = v["positions"]
                v["positions"] = {p: absent.get(p, "favor") for p in detailed[i][1]}
                v["positions"].update(absent)
                v["inferred"] = True


def main():
    allrows = []
    for leg in LEGISLATURES:
        data = json.loads((OPENDATA_DIR / f"Iniciativas{leg}.json").read_text())
        extra = SCRAPED / f"Iniciativas{leg}_s1.json"  # scraped 1st session (see fetch_xiv_session1)
        if extra.exists():
            have = {str(i["IniId"]) for i in data}
            add = [i for i in json.loads(extra.read_text()) if str(i["IniId"]) not in have]
            print(f"{leg}: +{len(add)} initiatives from {extra.name}")
            data = data + add
        rows, parties = normalize_leg(leg, data)
        print(f"{leg}: {len(data)} initiatives, {len(rows)} with substantive votes, "
              f"{sum(len(r['votes']) for r in rows)} votes; parties={dict(parties.most_common())}")
        allrows.extend(rows)
    allrows.sort(key=lambda r: r["lastVoteDate"] or "", reverse=True)
    (PROCESSED / "initiatives.json").write_text(json.dumps(allrows, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {len(allrows)} initiatives")


if __name__ == "__main__":
    main()
