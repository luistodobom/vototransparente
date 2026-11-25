# Vote-data validation report

New dataset: `data/processed/initiatives.json`, built by `pipeline/normalize.py` from the Parliament's open data
(`Iniciativas<LEG>_json.txt`, XIV–XVII) plus `pipeline/fetch_xiv_session1.py` for the XIV 1st session (missing
upstream): 6,236 initiatives, 9,976 substantive votes.

> **Update — XIV 1st session gap filled.** Headline tables below were computed before the gap fill. After it:
> +Liberdade XIV matched 1,523/1,523 (100 %; result agreement 100 %, positions 99.99 %), +Liberdade unmatched 714 → 1;
> old DB 2020 matched 753/770 (97.8 %), exact-date result agreement 100 %; old DB unmatched 921 → 115 (mostly
> procedural-only votes). All 2,299 scraped session-1 votes were cross-checked token-by-token against their pages (0 mismatches).

References:
1. **Old DB** — `validation/reference/old_parliament_data.csv` (5,002 rows, 2020–2025, produced by an LLM parsing session-result PDFs).
2. **+Liberdade** — `validation/maisliberdade/raw/iniciativas_{XIV,XV,XVI}.csv` (3,507 rows; one generalidade vote per initiative).

Re-run: `.venv/bin/python validation/validate_votes.py` → `results.json`, `mismatches_*.csv`, `unmatched_*.csv`.
Manual verdicts (32 cases checked against the official DetalheIniciativa pages) live in `manual_checks.json`.

## Method
- Join on BID (= IniId), then match to our vote on the same date (nearest date as fallback). Same-day ties prefer
  generalidade/deliberação/global phases for +Liberdade, then the best-agreeing vote (ambiguous count reported; this
  slightly flatters agreement).
- Party position — ours: party majority. Old DB: argmax of Favor/Contra/Abstenção ("ausente" only if all 0 and
  Não Votaram > 0). Names normalized (PCP-PEV → PCP+PEV, PSD/CDS-PP → PSD+CDS-PP, CHEGA → CH).
- 476 old-DB rows pointing to votos/moções/diplomas (not initiatives) are excluded.

## Headline numbers
| | Old DB (exact date) | +Liberdade |
|---|---|---|
| Reference records (initiatives with BID) | 4,526 | 3,507 |
| Matched | 3,457 (+148 nearest-date only) | 2,793 |
| Ambiguous (several of our votes that day) | 301 | 18 |
| **Result agreement** | **3,382 / 3,398 = 99.53 %** | **2,793 / 2,793 = 100 %** |
| **Party-position agreement** | **25,028 / 28,484 = 87.9 %** | **22,008 / 22,009 = 99.995 %** |

Per party (old DB / +Liberdade): PS 95.6/100, PSD 91.4/100, PAN 91.5/100, L 94.5/100, PEV 89.5/–, BE 85.3/100,
CH 83.7/99.96, IL 83.6/100, CDS-PP 83.0/100, PCP 82.0/100, JPP 62.0 (n=92)/–.

| Old DB year | Ref | Matched | Coverage | Result | Positions |
|---|---|---|---|---|---|
| 2020 | 770 | 78 | 10.1 % | 100 % | 88.6 % |
| 2021 | 1,006 | 795 | 79.0 % | 99.0 % | 90.6 % |
| 2022 | 546 | 501 | 91.8 % | 99.6 % | 85.6 % |
| 2023 | 1,026 | 976 | 95.1 % | 99.7 % | 87.2 % |
| 2024 | 746 | 709 | 95.0 % | 99.7 % | 89.4 % |
| 2025 | 432 | 398 | 92.1 % | 99.7 % | 83.5 % |

| +Liberdade leg | Ref | Matched | Coverage | Result | Positions |
|---|---|---|---|---|---|
| XIV | 1,523 | 810 | 53.2 % | 100 % | 100 % |
| XV | 1,282 | 1,282 | 100 % | 100 % | 100 % |
| XVI | 702 | 701 | 99.9 % | 100 % | 99.98 % |

**Three-way arbitration** (old DB disagrees with us and +Liberdade has the vote): results — +Liberdade sides with us
10, with the old DB 0. Positions — with us 2,125, with the old DB 0, neither 4.

**Unmatched:** old DB 921 (821 are XIV 1st-session initiatives missing from the open-data dump; 100 are procedural
votes we exclude by design). +Liberdade 714 (713 XIV 1st session; 1 +Liberdade error, BID 284038).

## Root causes
| # | Cause | Example (BID, date) | Verdict |
|---|---|---|---|
| 1 | Old DB LLM error: shifted/swapped party columns, voters marked "Não Votaram", "all favor" defaults | 121356 (2022-06-17): old PSD ausente / PCP favor; official PSD favor, PCP abst. 45698 (2021-06-09): old Aprovado; official Rejeitado | Ours correct |
| 2 | Old DB took a procedural vote (avocação, requerimento) or an amendment for the initiative vote | 172848 (2024-01-11): old "Aprovado, all favor" was the avocação request; official art. 1.º Rejeitado | Ours correct |
| 3 | XIV 1st session missing from `IniciativasXIV_json.txt` (only IniId 45227–121321) | 44182 (2019-12-12) | Upstream gap |
| 4 | Party that did not vote is not listed by the source | 173036 (2023-10-13): PAN absent officially; old DB says contra | Ours faithful |
| 5 | +Liberdade error | 314645 (2025-02-14): ref CH Contra, official Abstenção | Ours correct |
| 6 | +Liberdade fills L in XIV sessions 2–3 when Livre had no MP | 100 rows, e.g. 120920 | Ref artifact |
| 7 | Same party listed in two columns, also on the official page | 315003 (2025-03-14) | Upstream; kept first column, marked split |
| 8 | **Our parser bug (fixed):** unanimous votes whose detail only lists an absence lost the other parties | 120920 (2021-11-19) | Fixed |

## Parser fixes applied to `pipeline/normalize.py`
1. `_fill_unanimous` also fills unanimous votes whose positions are all `ausente` (196 votes).
2. Individual MPs in `ausencias` (e.g. "Miguel Arruda (Ninsc)") are no longer stored as parties (5 votes).
3. Unanimous party list comes from the truly nearest detailed vote (5 votes).

## Conclusions
- +Liberdade validates the new data almost perfectly; the old DB's disagreements are its own extraction errors.
- No PDF parsing is needed for votes: the official structured data is authoritative. The only gap (XIV 1st session,
  absent from both the JSON and XML dumps) is filled by scraping the official initiative pages
  (`pipeline/fetch_xiv_session1.py`), which brings coverage of both references to ~100 %.
