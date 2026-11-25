"""Provenance manifest: which official files the site was built from, when they were retrieved,
and a SHA-256 fingerprint of each, so anyone can verify the published data against the source.

Writes data/processed/sources.json (also exported to the site's /sobre/ page).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .config import DATA, LEGISLATURES, OPENDATA_DIR, PROCESSED, RAW

DATASETS = [
    # (glob relative to data/raw/opendata, label, official page)
    ("Iniciativas{leg}.json", "Iniciativas — {leg} Legislatura (dados abertos)",
     "https://www.parlamento.pt/Cidadania/Paginas/DAIniciativas.aspx"),
    ("InformacaoBase{leg}.json", "Informação Base — deputados da {leg} Legislatura (dados abertos)",
     "https://www.parlamento.pt/Cidadania/Paginas/DAInformacaoBase.aspx"),
    ("Iniciativas{leg}_s1.json", "Iniciativas da 1.ª sessão da {leg} Legislatura (recolhidas das páginas oficiais de cada iniciativa)",
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


def main():
    files = []
    for leg in LEGISLATURES:
        for pattern, label, page in DATASETS:
            p = OPENDATA_DIR / pattern.format(leg=leg)
            if p.exists():
                files.append({"label": label.format(leg=leg), "page": page, "file": p.name,
                              "retrieved": stamp(p), "bytes": p.stat().st_size, "sha256": sha256(p)})
    att_dir = RAW / "attendance"
    if att_dir.exists():
        pages = [p for p in att_dir.rglob("*.html")]
        if pages:
            newest = max(pages, key=lambda p: p.stat().st_mtime)
            files.append({"label": f"Registo de presenças nas reuniões plenárias ({len(pages)} reuniões)",
                          "page": "https://www.parlamento.pt/DeputadoGP/Paginas/reunioesplenarias.aspx",
                          "file": "attendance/", "retrieved": stamp(newest), "bytes": sum(p.stat().st_size for p in pages),
                          "sha256": None})
    summaries = list((DATA / "summaries").glob("*.json"))
    out = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "files": files,
           "summaries": len(summaries)}
    (PROCESSED / "sources.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"provenance: {len(files)} source files")


if __name__ == "__main__":
    main()
