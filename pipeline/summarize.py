"""Summarize every voted initiative with Gemini, reading the original document directly.

Results are cached one-file-per-initiative in data/summaries/<id>.json (committed),
so re-runs only process new initiatives. Runs many requests concurrently.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time

from google import genai
from google.genai import types

from .config import GEMINI_API_KEY, GEMINI_CONCURRENCY, GEMINI_MODEL, PROCESSED, SUMMARIES, TEXTS_DIR

PROMPT_VERSION = 1

CATEGORIES = {
    "saude": "Saúde",
    "educacao": "Educação",
    "habitacao": "Habitação",
    "economia": "Economia e Finanças",
    "fiscalidade": "Impostos",
    "trabalho": "Trabalho e Segurança Social",
    "justica": "Justiça",
    "seguranca": "Segurança e Proteção Civil",
    "defesa": "Defesa",
    "ambiente": "Ambiente e Clima",
    "energia": "Energia",
    "agricultura": "Agricultura, Pescas e Mar",
    "transportes": "Transportes e Infraestruturas",
    "cultura": "Cultura e Património",
    "desporto": "Desporto e Juventude",
    "ciencia": "Ciência, Tecnologia e Digital",
    "administracao": "Administração Pública e Estado",
    "instituicoes": "Instituições, Constituição e Eleições",
    "internacional": "Assuntos Europeus e Internacionais",
    "direitos": "Direitos, Liberdades e Igualdade",
    "imigracao": "Imigração",
    "territorio": "Regiões, Autarquias e Território",
    "animais": "Animais",
    "comunicacao": "Comunicação Social",
    "familia": "Família e Ação Social",
    "consumidores": "Consumidores e Concorrência",
}

FISCAL = ["aumenta_despesa", "reduz_despesa", "aumenta_receita", "reduz_receita", "misto", "neutro", "incerto"]

SCHEMA = {
    "type": "object",
    "properties": {
        "short_title": {"type": "string", "description": "Título curto e claro, máx. 80 caracteres, sem siglas obscuras."},
        "summary": {"type": "string", "description": "O que a iniciativa propõe, concretamente. 80–160 palavras."},
        "key_points": {"type": "array", "items": {"type": "string"}, "description": "3 a 6 medidas/pontos concretos, cada um com uma frase."},
        "plain_language": {"type": "string", "description": "Explicação em linguagem simples, como se fosse a um amigo sem conhecimentos jurídicos. 50–110 palavras."},
        "context": {"type": "string", "description": "Porque é que o proponente diz que isto é necessário (exposição de motivos). 40–100 palavras."},
        "analysis": {"type": "string", "description": "Análise crítica e equilibrada: pontos fortes, fragilidades, inconsistências, riscos de implementação. 60–140 palavras."},
        "fiscal_impact": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": FISCAL},
                "explanation": {"type": "string", "description": "1–3 frases. Inclua montantes apenas se constarem do documento."},
            },
            "required": ["direction", "explanation"],
        },
        "affected": {"type": "array", "items": {"type": "string"}, "description": "Grupos diretamente afetados (ex.: 'inquilinos', 'médicos do SNS'). 1–5."},
        "categories": {"type": "array", "items": {"type": "string", "enum": list(CATEGORIES)}, "description": "1 a 3 categorias, a mais relevante primeiro."},
        "tags": {"type": "array", "items": {"type": "string"}, "description": "2 a 5 palavras-chave específicas, em minúsculas."},
        "document_matches_title": {"type": "boolean", "description": "true se o documento corresponde de facto à iniciativa indicada."},
    },
    "required": ["short_title", "summary", "key_points", "plain_language", "context", "analysis",
                 "fiscal_impact", "affected", "categories", "tags", "document_matches_title"],
}

SYSTEM = """És um analista parlamentar rigoroso e imparcial que escreve em português europeu (PT-PT), \
para um site de transparência sobre as votações da Assembleia da República.
Regras:
- Baseia-te APENAS no documento fornecido e nos metadados. Não inventes números, datas ou efeitos.
- Sê neutro: não tomes partido nem uses linguagem valorativa sobre partidos.
- Escreve frases curtas e claras. Evita jargão jurídico; quando inevitável, explica-o.
- Em 'analysis' sê genuinamente crítico (bom e mau), sem opinião política.
- Projetos de Resolução são recomendações ao Governo (não vinculativas) — deixa isso claro quando relevante.
- Usa grafia PT-PT (ex.: 'facto', 'equipa', 'registo', 'utente')."""


def build_prompt(ini: dict) -> str:
    cats = "\n".join(f"- {k}: {v}" for k, v in CATEGORIES.items())
    return f"""Iniciativa: {ini['ref']}
Tipo: {ini['type']}
Título oficial: {ini['title']}
Autoria: {ini['authors']['label']}
Legislatura: {ini['leg']}

Lê o documento anexo (texto integral da iniciativa) e preenche o JSON pedido.
Categorias disponíveis (usa as chaves):
{cats}"""


class Summarizer:
    def __init__(self, model: str, concurrency: int):
        self.client = genai.Client(api_key=GEMINI_API_KEY)
        self.model = model
        self.sem = asyncio.Semaphore(concurrency)
        self.done = 0
        self.failed = 0
        self.fatal: str | None = None  # set when billing/quota makes further requests pointless
        self.t0 = time.time()

    async def run_one(self, ini: dict, total: int):
        out = SUMMARIES / f"{ini['id']}.json"
        pdf = TEXTS_DIR / f"{ini['id']}.pdf"
        txt = TEXTS_DIR / f"{ini['id']}.txt"
        parts: list = []
        if pdf.exists():
            parts.append(types.Part.from_bytes(data=pdf.read_bytes(), mime_type="application/pdf"))
            source = "pdf"
        elif txt.exists():
            parts.append(types.Part.from_text(text="TEXTO DO DOCUMENTO:\n" + txt.read_text()[:400_000]))
            source = "txt"
        else:
            parts.append(types.Part.from_text(text="(Documento indisponível — resume apenas com base no título; "
                                                    "indica na análise que o texto integral não foi analisado.)"))
            source = "title"
        parts.append(types.Part.from_text(text=build_prompt(ini)))
        cfg = types.GenerateContentConfig(
            system_instruction=SYSTEM,
            response_mime_type="application/json",
            response_schema=SCHEMA,
            temperature=0.2,
        )
        async with self.sem:
            if self.fatal:
                return
            for attempt in range(6):
                try:
                    resp = await asyncio.wait_for(
                        self.client.aio.models.generate_content(model=self.model, contents=parts, config=cfg),
                        timeout=300,
                    )
                    data = json.loads(resp.text)
                    data["categories"] = [c for c in data.get("categories", []) if c in CATEGORIES][:3] or ["outros"]
                    data["_meta"] = {"model": self.model, "prompt_version": PROMPT_VERSION, "source": source,
                                     "generated": time.strftime("%Y-%m-%d")}
                    out.write_text(json.dumps(data, ensure_ascii=False, indent=1))
                    self.done += 1
                    break
                except Exception as e:  # noqa: BLE001
                    msg = str(e)
                    if "402" in msg or "prepayment credits are depleted" in msg.lower() or "billing" in msg.lower() and "disabled" in msg.lower():
                        if not self.fatal:
                            self.fatal = msg[:200]
                            print(f"STOP: Gemini billing problem, aborting run: {self.fatal}", file=sys.stderr, flush=True)
                        return
                    if attempt == 5:
                        self.failed += 1
                        print(f"FAIL {ini['id']}: {msg[:200]}", file=sys.stderr, flush=True)
                        return
                    # PDFs Gemini cannot read -> fall back to text-only next attempt
                    if "pdf" in msg.lower() and source == "pdf" and attempt >= 2:
                        parts[0] = types.Part.from_text(text="(Documento PDF ilegível; usa apenas o título.)")
                        source = "title"
                    await asyncio.sleep(min(60, 2 ** attempt + random.random() * 3))
        n = self.done + self.failed
        if n % 50 == 0:
            rate = n / (time.time() - self.t0)
            print(f"{n}/{total} done={self.done} failed={self.failed} {rate:.1f}/s", file=sys.stderr, flush=True)


async def amain(ids, limit, model, concurrency, force, upgrade=False, allow_title=False):
    rows = json.loads((PROCESSED / "initiatives.json").read_text())
    if ids:
        rows = [r for r in rows if r["id"] in set(ids)]
    if not force:
        rows = [r for r in rows if not (SUMMARIES / f"{r['id']}.json").exists()]
    if upgrade:
        # re-run summaries produced by a different (fallback) model
        rows += [r for r in json.loads((PROCESSED / "initiatives.json").read_text())
                 if (SUMMARIES / f"{r['id']}.json").exists()
                 and json.loads((SUMMARIES / f"{r['id']}.json").read_text())["_meta"]["model"] != model]
    if not allow_title:
        missing = set(json.loads((PROCESSED / "texts_missing.json").read_text())) \
            if (PROCESSED / "texts_missing.json").exists() else set()
        rows = [r for r in rows if (TEXTS_DIR / f"{r['id']}.pdf").exists()
                or (TEXTS_DIR / f"{r['id']}.txt").exists() or r["id"] in missing]
    rows = rows[:limit] if limit else rows
    print(f"summarizing {len(rows)} initiatives with {model} (concurrency {concurrency})", file=sys.stderr)
    s = Summarizer(model, concurrency)
    await asyncio.gather(*(s.run_one(r, len(rows)) for r in rows))
    print(f"done={s.done} failed={s.failed} in {time.time() - s.t0:.0f}s", file=sys.stderr)
    if s.fatal:
        sys.exit(2)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--ids", nargs="*")
    p.add_argument("--limit", type=int)
    p.add_argument("--model", default=GEMINI_MODEL)
    p.add_argument("--concurrency", type=int, default=GEMINI_CONCURRENCY)
    p.add_argument("--force", action="store_true")
    p.add_argument("--upgrade", action="store_true", help="redo summaries made by another model")
    p.add_argument("--allow-title", action="store_true", help="summarize even before the text is downloaded")
    a = p.parse_args(argv)
    asyncio.run(amain(a.ids, a.limit, a.model, a.concurrency, a.force, a.upgrade, a.allow_title))


if __name__ == "__main__":
    main()
