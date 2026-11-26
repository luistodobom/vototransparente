"""Provenance manifest: which official files the site was built from, when they were retrieved,
and a SHA-256 fingerprint of each, so anyone can verify the published data against the source.

Writes data/processed/sources.json (also exported to the site's /sobre/ page).

Open-data dumps live in data/raw/opendata/; the committed scrapes (1st session of the XIV legislature,
plenary attendance) live in data/scraped/. A file's "retrieved" time is its modification time, except for
the committed one-off XIV 1st-session scrape: a fresh checkout resets its mtime, so while its SHA-256 is
unchanged the previously published retrieval time is kept.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .config import DATA, LEGISLATURES, OPENDATA_DIR, PROCESSED, SCRAPED, SITE_DATA

DATASETS = [
    # (directory, file name pattern, label, official page)
    (OPENDATA_DIR, "Iniciativas{leg}.json", "Iniciativas — {leg} Legislatura (dados abertos)",
     "https://www.parlamento.pt/Cidadania/Paginas/DAIniciativas.aspx"),
    (OPENDATA_DIR, "InformacaoBase{leg}.json", "Informação Base — deputados da {leg} Legislatura (dados abertos)",
     "https://www.parlamento.pt/Cidadania/Paginas/DAInformacaoBase.aspx"),
    (SCRAPED, "Iniciativas{leg}_s1.json", "Iniciativas da 1.ª sessão da {leg} Legislatura (recolhidas das páginas oficiais de cada iniciativa)",
     "https://www.parlamento.pt/ActividadeParlamentar/Paginas/IniciativasLegislativas.aspx"),
]


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def stamp(path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def previous_files() -> dict:
    """file name -> entry from the last published manifest (site/src/data/meta.json)."""
    try:
        prev = json.loads((SITE_DATA / "meta.json").read_text())["sources"]["files"]
        return {f["file"]: f for f in prev}
    except Exception:
        return {}


def main():
    prev = previous_files()
    files = []
    for leg in LEGISLATURES:
        for folder, pattern, label, page in DATASETS:
            p = folder / pattern.format(leg=leg)
            if p.exists():
                digest = sha256(p)
                retrieved = stamp(p)
                old = prev.get(p.name)
                if folder == SCRAPED and old and old.get("sha256") == digest and old.get("retrieved"):
                    retrieved = old["retrieved"]
                files.append({"label": label.format(leg=leg), "page": page, "file": p.name,
                              "retrieved": retrieved, "bytes": p.stat().st_size, "sha256": digest})
    att = SCRAPED / "attendance.json"
    if att.exists():
        n = sum(len(days) for days in json.loads(att.read_text()).values())
        files.append({"label": f"Registo de presenças nas reuniões plenárias ({n} reuniões)",
                      "page": "https://www.parlamento.pt/DeputadoGP/Paginas/reunioesplenarias.aspx",
                      "file": att.name, "retrieved": stamp(att), "bytes": att.stat().st_size,
                      "sha256": sha256(att)})
    summaries = list((DATA / "summaries").glob("*.json"))
    out = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "files": files,
           "summaries": len(summaries)}
    (PROCESSED / "sources.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"provenance: {len(files)} source files")


if __name__ == "__main__":
    main()
