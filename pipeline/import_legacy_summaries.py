"""Import the AI descriptions produced by the previous version of the project (2025, Gemini 2.5 Flash).

Source: validation/reference/old_parliament_data.csv — one row per vote; summaries were generated from
the initiative's full-text PDF (downloaded from its DetalheIniciativa?BID=<id> page), so they are keyed by
the same initiative id we use. Only initiatives without a summary are filled; nothing is overwritten.
"""
from __future__ import annotations

import json
import re

import pandas as pd

from .config import PROCESSED, ROOT, SUMMARIES

LEGACY_CSV = ROOT / "validation" / "reference" / "old_parliament_data.csv"

# old category index -> current category key (old list: see git history, crawlers/prompts.py)
OLD_CATEGORIES = {
    0: "saude", 1: "educacao", 2: "defesa", 3: "justica", 4: "economia", 5: "trabalho",
    6: "ambiente", 7: "energia", 8: "transportes", 9: "habitacao", 10: "internacional", 11: "ciencia",
}


def clean(x) -> str | None:
    if not isinstance(x, str):
        return None
    x = re.sub(r"\s+", " ", x).strip()
    return x or None


def categories(raw) -> list[str]:
    try:
        idx = json.loads(raw) if isinstance(raw, str) else []
    except json.JSONDecodeError:
        idx = [int(n) for n in re.findall(r"\d+", str(raw))]
    out = []
    for i in idx if isinstance(idx, list) else [idx]:
        k = OLD_CATEGORIES.get(int(i)) if str(i).isdigit() else None
        if k and k not in out:
            out.append(k)
    return out[:3]


def main():
    df = pd.read_csv(LEGACY_CSV)
    df["bid"] = df["proposal_gov_link"].str.extract(r"BID=(\d+)")
    df = df[df["proposal_summary_general"].notna() & df["bid"].notna()]
    df["_len"] = df["proposal_summary_general"].str.len()
    # several rows per initiative (one per vote): keep the most complete one
    df = df.sort_values(["bid", "_len", "last_processed_timestamp"]).groupby("bid").tail(1)
    ours = {r["id"] for r in json.loads((PROCESSED / "initiatives.json").read_text())}
    written = skipped = 0
    for _, r in df.iterrows():
        bid = r["bid"]
        out = SUMMARIES / f"{bid}.json"
        if bid not in ours or out.exists():
            skipped += 1
            continue
        fiscal = clean(r.get("proposal_summary_fiscal_impact"))
        data = {
            "short_title": clean(r.get("proposal_short_title")),
            "summary": clean(r.get("proposal_summary_general")),
            "key_points": [],
            "plain_language": clean(r.get("proposal_summary_colloquial")),
            "context": None,
            "analysis": clean(r.get("proposal_summary_analysis")),
            "fiscal_impact": {"direction": None, "explanation": fiscal} if fiscal else None,
            "affected": [],
            "categories": categories(r.get("proposal_category")) or ["outros"],
            "tags": [],
            "_meta": {"model": "gemini-2.5-flash (versão anterior do projeto)", "prompt_version": 0,
                      "source": "pdf", "generated": str(r.get("last_processed_timestamp") or "")[:10] or None,
                      "legacy": True},
        }
        out.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        written += 1
    print(f"legacy summaries written: {written}, skipped (not ours or already summarized): {skipped}")


if __name__ == "__main__":
    main()
