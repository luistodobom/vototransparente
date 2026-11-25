# Instituto +Liberdade: parliamentary activity data (validation source)

Source page: https://maisliberdade.pt/escrutinio/atividade-parlamentar/

The page embeds two Power BI "publish to web" reports. They have no CSV or
JSON download, so we query the public Power BI API with the embed resource keys.
You don't need to log in.

| Report | Resource key | Model id | Last refresh |
|---|---|---|---|
| XVI (iframe 1) | `5f0a131c-20e4-407e-b097-89009623b11e` | 5525618 | 2025-03-22 |
| XV (iframe 2)  | `8811f11b-8c6f-48b0-a176-9ce01c124ede` | 2066650 | 2024-10-15 |

API host: `https://wabi-west-europe-e-primary-api.analysis.windows.net`. Every call
sends the header `X-PowerBI-ResourceKey: <key>`.

1. `GET /public/reports/<key>/modelsAndExploration?preferReadOnlySession=true` returns the model id and the pages.
2. `POST /public/reports/conceptualschema` with `{"modelIds":[<id>]}` returns the tables and columns.
3. `POST /public/reports/querydata?synchronous=true` runs a SemanticQueryDataShapeCommand that
   groups on every column of the table. The response comes back in Power BI's compressed "DSR" format, and
   `fetch_maisliberdade.py` decodes it (value dictionaries, repeat and null bitmasks).

To reproduce: `python3 fetch_maisliberdade.py [outdir]` (stdlib only; it writes to `raw/` by default).

## Files (`raw/`)
- `iniciativas_XVI.csv`: 702 votes, 2024-04-24 to 2025-03-14 (XVI report)
- `iniciativas_XV.csv`: 1282 votes, 2022-04-22 to 2024-01-11 (XV report)
- `iniciativas_XV_from_XVI_report.csv`: 1114 rows. This is a different (older or partial?) copy of XV that sits inside the XVI model, and some of its dates are after XV ended.
- `iniciativas_XIV.csv`: 1523 rows, also from the XVI model. It is not shown on the page.
- `schema_*_report.json` and `fetch_meta.json`: the schema and the fetch metadata.

The row counts match a server-side `COUNT` and `COUNT DISTINCT` of `iniciativa_id` (702 and 1282).

## Fields
- `iniciativa_tipo`: Projeto de Lei, Projeto de Resolução or Proposta de Lei
- `iniciativa_fase`: always "Votação na generalidade". The dataset contains only the
  generalidade vote. Votes on especialidade and final global are not included.
- `iniciativa_data`: the vote date
- `iniciativa_votacao_res`: Aprovado or Rejeitado
- `iniciativa_votacao_<party>`: Favor, Contra, Abst. (a few say "Ver link"). The parties are ps, psd, ch, il, be, pcp, l and pan, plus `cds-pp` in XVI.
- `iniciativa_autor` (party), `1 Deputado Proponente`, `Outros Deputados`, `Subscritores`
- `iniciativa_titulo`, `iniciativa_url_res`. The url_res is the parlamento.pt `DetalheIniciativa.aspx?BID=<id>` page.
- `iniciativa_votacao_desc`: notes, for example a substitution text or ties
- `iniciativa_votacao_contra_sua_iniciativa`

## Caveats
- **The data has no "n.º X/XV/1.ª" number.** `iniciativa_id` is an internal id of this dataset. To join against
  parlamento.pt open data, use the `BID` in `iniciativa_url_res`, which is the IniciativaID / IniId.
- The endpoints are undocumented Power BI internals and the keys can be revoked.
- Only one vote is recorded per initiative, the one in the generalidade phase. Individual deputies' votes and
  non-inscritos are not included.
