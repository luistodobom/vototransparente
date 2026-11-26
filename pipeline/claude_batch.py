"""Summaries written by Claude subagents (instead of the Gemini API).

  prepare   extract each unsummarized initiative's text into data/raw/claude/in/<id>.txt (metadata + text,
            capped so one file is cheap to read) and split the ids into batch files
  ingest    validate data/raw/claude/out/<id>.json against the summary schema and copy valid ones into
            data/summaries/<id>.json, tagged with the model that wrote them

The subagent instructions live in data/raw/claude/INSTRUCTIONS.md (written by `prepare`).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date

from pypdf import PdfReader

from .config import PROCESSED, RAW, SUMMARIES, TEXTS_DIR
from .summarize import CATEGORIES, FISCAL, SCHEMA

WORK = RAW / "claude"
IN, OUT, BATCHES = WORK / "in", WORK / "out", WORK / "batches"
MAX_CHARS = 30_000  # ~8k tokens; the start of a text holds the proposal itself (articles/recommendations)
MODEL = "claude-sonnet-5-5"


def pdf_text(path) -> str:
    try:
        reader = PdfReader(str(path))
        parts = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
            if sum(map(len, parts)) > MAX_CHARS * 1.3:
                break
        return "\n".join(parts)
    except Exception as e:  # noqa: BLE001
        return f"(erro ao ler o PDF: {e})"


def clean(t: str) -> str:
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n\n", t)
    return t.strip()


INSTRUCTIONS = f"""# Como escrever os resumos

Cada ficheiro `in/<id>.txt` contém os metadados de uma iniciativa da Assembleia da República e o texto integral
(eventualmente truncado). Para cada id do teu lote escreve `out/<id>.json` — um único objeto JSON, UTF-8, com
exatamente estes campos:

- "short_title": título curto e claro (máx. 80 caracteres), sem siglas obscuras.
- "summary": o que a iniciativa propõe, concretamente (80–160 palavras).
- "key_points": lista de 3 a 6 medidas/pontos concretos, uma frase cada.
- "plain_language": explicação em linguagem simples, para alguém sem conhecimentos jurídicos (50–110 palavras).
- "context": porque é que o proponente diz que é necessária (exposição de motivos), 40–100 palavras.
- "analysis": análise crítica e equilibrada — pontos fortes, fragilidades, inconsistências, riscos (60–140 palavras).
- "fiscal_impact": {{"direction": um de {FISCAL}, "explanation": 1–3 frases; montantes só se constarem do texto}}.
- "affected": 1 a 5 grupos diretamente afetados (ex.: "inquilinos", "médicos do SNS").
- "categories": 1 a 3 chaves desta lista, a mais relevante primeiro: {list(CATEGORIES)}
  ({"; ".join(f"{k} = {v}" for k, v in CATEGORIES.items())}).
- "tags": 2 a 5 palavras-chave específicas, em minúsculas.
- "document_matches_title": true se o texto corresponde de facto à iniciativa indicada nos metadados.

Regras:
- Escreve em português europeu (PT-PT: "facto", "equipa", "registo", "utente").
- Baseia-te APENAS no texto e nos metadados. Não inventes números, datas ou efeitos.
- Sê neutro e imparcial; não uses linguagem valorativa sobre partidos. Na análise sê genuinamente crítico (bom e mau),
  sem opinião política.
- Projetos de Resolução são recomendações ao Governo, não vinculativas — deixa isso claro quando relevante.
- Se o texto estiver vazio ou ilegível, resume com base no título e diz na análise que o texto integral não foi analisado.
- Não escrevas mais nada além dos ficheiros JSON pedidos.
"""


def prepare(batch_size: int):
    for d in (IN, OUT, BATCHES):
        d.mkdir(parents=True, exist_ok=True)
    rows = json.loads((PROCESSED / "initiatives.json").read_text())
    todo = [r for r in rows if not (SUMMARIES / f"{r['id']}.json").exists()]
    for r in todo:
        dest = IN / f"{r['id']}.txt"
        if dest.exists():
            continue
        pdf, txt = TEXTS_DIR / f"{r['id']}.pdf", TEXTS_DIR / f"{r['id']}.txt"
        body = pdf_text(pdf) if pdf.exists() else txt.read_text() if txt.exists() else "(texto integral indisponível)"
        body = clean(body)
        truncated = len(body) > MAX_CHARS
        dest.write_text(
            f"ID: {r['id']}\nIniciativa: {r['ref']}\nTipo: {r['type']}\nTítulo oficial: {r['title']}\n"
            f"Autoria: {r['authors']['label']}\nLegislatura: {r['leg']}\n"
            f"--- TEXTO INTEGRAL{' (truncado)' if truncated else ''} ---\n{body[:MAX_CHARS]}\n"
        )
    for old in BATCHES.glob("batch_*.txt"):
        old.unlink()
    ids = [r["id"] for r in todo]
    for i in range(0, len(ids), batch_size):
        (BATCHES / f"batch_{i // batch_size:03d}.txt").write_text("\n".join(ids[i:i + batch_size]) + "\n")
    (WORK / "INSTRUCTIONS.md").write_text(INSTRUCTIONS)
    print(f"{len(ids)} initiatives -> {len(list(BATCHES.glob('batch_*.txt')))} batches of ≤{batch_size} in {BATCHES}")


def check(d: dict) -> list[str]:
    errs = []
    for k in SCHEMA["required"]:
        if k not in d:
            errs.append(f"missing {k}")
    for k in ("short_title", "summary", "plain_language", "context", "analysis"):
        if k in d and (not isinstance(d[k], str) or len(d[k].strip()) < 10):
            errs.append(f"bad {k}")
    if isinstance(d.get("short_title"), str) and len(d["short_title"]) > 120:
        errs.append("short_title too long")
    fi = d.get("fiscal_impact")
    if not isinstance(fi, dict) or fi.get("direction") not in FISCAL or not fi.get("explanation"):
        errs.append("bad fiscal_impact")
    for k, lo, hi in (("key_points", 1, 8), ("affected", 1, 6), ("tags", 1, 8), ("categories", 1, 3)):
        v = d.get(k)
        if not isinstance(v, list) or not (lo <= len(v) <= hi) or not all(isinstance(x, str) and x for x in v):
            errs.append(f"bad {k}")
    if isinstance(d.get("categories"), list) and any(c not in CATEGORIES for c in d["categories"]):
        errs.append("unknown category")
    return errs


def ingest(force: bool):
    ok, bad = 0, {}
    for f in sorted(OUT.glob("*.json")):
        dest = SUMMARIES / f.name
        if dest.exists() and not force:
            continue
        try:
            d = json.loads(f.read_text())
        except json.JSONDecodeError as e:
            bad[f.stem] = [f"invalid JSON: {e}"]
            continue
        errs = check(d)
        if errs:
            bad[f.stem] = errs
            continue
        d["categories"] = d["categories"][:3]
        src = (IN / f.name.replace(".json", ".txt")).read_text() if (IN / f.name.replace(".json", ".txt")).exists() else ""
        d["_meta"] = {"model": MODEL, "prompt_version": 1, "generated": date.today().isoformat(),
                      "source": "title" if "(texto integral indisponível)" in src else "pdf"}
        dest.write_text(json.dumps(d, ensure_ascii=False, indent=1))
        ok += 1
    print(f"ingested {ok}; invalid {len(bad)}")
    for k, v in list(bad.items())[:20]:
        print(f"  {k}: {v}")
    return bad


def main(argv=None):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("prepare")
    pp.add_argument("--batch-size", type=int, default=40)
    pi = sub.add_parser("ingest")
    pi.add_argument("--force", action="store_true")
    a = p.parse_args(argv)
    if a.cmd == "prepare":
        prepare(a.batch_size)
    else:
        bad = ingest(a.force)
        sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
