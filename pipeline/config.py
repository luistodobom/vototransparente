"""Shared configuration for the VotoTransparente data pipeline."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA = ROOT / "data"
RAW = DATA / "raw"                 # downloaded open-data dumps + documents (gitignored)
OPENDATA_DIR = RAW / "opendata"
TEXTS_DIR = RAW / "texts"
SESSIONS_DIR = RAW / "sessions"
PROCESSED = DATA / "processed"     # normalized intermediates (gitignored, rebuilt by the pipeline)
SCRAPED = DATA / "scraped"         # one-off / incremental HTML scrapes, committed (CI never re-scrapes history)
SUMMARIES = DATA / "summaries"     # Gemini outputs, committed (expensive to regenerate)
SITE_DATA = ROOT / "site" / "src" / "data"

# Legislatures to include. Roman numeral -> label. Add older ones here to extend history.
LEGISLATURES = ["XIV", "XV", "XVI", "XVII"]
CURRENT_LEG = LEGISLATURES[-1]     # the only legislature whose data can still change

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-4-hydrogen")
GEMINI_CONCURRENCY = int(os.getenv("GEMINI_CONCURRENCY", "32"))

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

OPENDATA_INDEX = "https://www.parlamento.pt/Cidadania/Paginas/DAIniciativas.aspx"
INFOBASE_INDEX = "https://www.parlamento.pt/Cidadania/Paginas/DAInformacaoBase.aspx"

for d in (OPENDATA_DIR, TEXTS_DIR, SESSIONS_DIR, PROCESSED, SCRAPED, SUMMARIES):
    d.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":  # `python -m pipeline.config --current` -> e.g. "XVII" (used by CI)
    import sys
    if sys.argv[1:] == ["--current"]:
        print(CURRENT_LEG)
    else:
        print(" ".join(LEGISLATURES))
