#!/usr/bin/env python3
"""Download the per-initiative vote tables behind maisliberdade.pt's
"+Escrutinio da Atividade Parlamentar" Power BI dashboards.

Source page: https://maisliberdade.pt/escrutinio/atividade-parlamentar/
The page embeds two Power BI "publish to web" reports. Their public resource
keys allow anonymous queries against the Power BI public API:

  GET  {API}/public/reports/{key}/modelsAndExploration?preferReadOnlySession=true
  POST {API}/public/reports/conceptualschema              (tables/columns)
  POST {API}/public/reports/querydata?synchronous=true   (semantic query -> DSR)

All requests send header  X-PowerBI-ResourceKey: {key}.
Usage:  python3 fetch_maisliberdade.py [outdir]   (default: ./raw next to this file)
"""
import csv
import datetime as dt
import json
import os
import sys
import urllib.request

API = "https://wabi-west-europe-e-primary-api.analysis.windows.net"

REPORTS = {
    # XVI report (the model also contains Iniciativas_XIV and Iniciativas_XV)
    "XVI": "5f0a131c-20e4-407e-b097-89009623b11e",
    # XV report (separate, older model; table named just "Iniciativas")
    "XV": "8811f11b-8c6f-48b0-a176-9ce01c124ede",
}

# (report, entity, output name)
TABLES = [
    ("XVI", "Iniciativas_XVI", "iniciativas_XVI"),
    ("XVI", "Iniciativas_XV", "iniciativas_XV_from_XVI_report"),
    ("XVI", "Iniciativas_XIV", "iniciativas_XIV"),
    ("XV", "Iniciativas", "iniciativas_XV"),
]

SKIP_SUFFIXES = ("Color",)
SKIP_PREFIXES = ("Color ",)
SKIP_EXACT = {"Column", "Blank", "Blank_XVI"}


def req(method, url, key, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers={
        "X-PowerBI-ResourceKey": key,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
    })
    with urllib.request.urlopen(r, timeout=120) as resp:
        return json.load(resp)


def model_info(key):
    d = req("GET", f"{API}/public/reports/{key}/modelsAndExploration?preferReadOnlySession=true", key)
    m = d["models"][0]
    return m["id"], m.get("LastRefreshTime")


def schema(key, model_id):
    d = req("POST", f"{API}/public/reports/conceptualschema", key,
            {"modelIds": [model_id], "userPreferredLocale": "pt-PT"})
    out = {}
    for e in d["schemas"][0]["schema"]["Entities"]:
        # Only plain columns (Kind 1 / no Measure); measures can't be grouped
        cols = [p["Name"] for p in e["Properties"] if "Measure" not in p]
        out[e["Name"]] = cols
    return out


def keep(col):
    return not (col in SKIP_EXACT or col.endswith(SKIP_SUFFIXES) or col.startswith(SKIP_PREFIXES))


def build_query(entity, cols, model_id, restart=None, count=30000):
    window = {"Count": count}
    if restart:
        window["RestartTokens"] = restart
    return {
        "version": "1.0.0",
        "queries": [{"Query": {"Commands": [{"SemanticQueryDataShapeCommand": {
            "Query": {"Version": 2,
                      "From": [{"Name": "t", "Entity": entity, "Type": 0}],
                      "Select": [{"Column": {"Expression": {"SourceRef": {"Source": "t"}}, "Property": c},
                                  "Name": f"t.{c}"} for c in cols]},
            "Binding": {"Primary": {"Groupings": [{"Projections": list(range(len(cols)))}]},
                        "DataReduction": {"DataVolume": 4, "Primary": {"Window": window}},
                        "Version": 1}}}]}}],
        "cancelQueries": [],
        "modelId": model_id,
    }


def decode_dsr(resp, ncols):
    """Decode Power BI's compressed DSR result into list of row lists."""
    ds = resp["results"][0]["result"]["data"]["dsr"]["DS"][0]
    if "PH" not in ds:
        raise RuntimeError(json.dumps(ds)[:2000])
    dicts = ds.get("ValueDicts", {})
    rows_raw = ds["PH"][0].get("DM0", [])
    schema_ = None
    rows, prev = [], [None] * ncols
    for r in rows_raw:
        if "S" in r:
            schema_ = r["S"]
        vals = r.get("C", [])
        rep = r.get("R", 0)
        nul = r.get("Ø", 0)
        row, vi = [], 0
        for i in range(ncols):
            if rep & (1 << i):
                v = prev[i]
            elif nul & (1 << i):
                v = None
            else:
                v = vals[vi]
                vi += 1
                dn = schema_[i].get("DN")
                if dn is not None and isinstance(v, int):
                    v = dicts[dn][v]
                elif schema_[i].get("T") == 7 and isinstance(v, (int, float)):
                    v = dt.datetime.fromtimestamp(v / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
            row.append(v)
        rows.append(row)
        prev = row
    return rows, ds.get("RT"), ds.get("IC", False)


def fetch_table(key, model_id, entity, cols):
    all_rows, restart = [], None
    while True:
        resp = req("POST", f"{API}/public/reports/querydata?synchronous=true", key,
                   build_query(entity, cols, model_id, restart))
        rows, rt, incomplete = decode_dsr(resp, len(cols))
        all_rows.extend(rows)
        if not rt or not rows:
            break
        restart = rt
    return all_rows


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw")
    os.makedirs(outdir, exist_ok=True)
    meta = {}
    cache = {}
    for rep, entity, name in TABLES:
        key = REPORTS[rep]
        if rep not in cache:
            mid, refreshed = model_info(key)
            sch = schema(key, mid)
            cache[rep] = (mid, sch)
            meta[rep] = {"resource_key": key, "model_id": mid, "last_refresh": refreshed}
            with open(os.path.join(outdir, f"schema_{rep}_report.json"), "w") as f:
                json.dump(sch, f, ensure_ascii=False, indent=1)
        mid, sch = cache[rep]
        cols = [c for c in sch[entity] if keep(c)]
        rows = fetch_table(key, mid, entity, cols)
        path = os.path.join(outdir, f"{name}.csv")
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(cols)
            w.writerows(rows)
        print(f"{rep}/{entity}: {len(rows)} rows -> {path}")
    meta["fetched_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    with open(os.path.join(outdir, "fetch_meta.json"), "w") as f:
        json.dump(meta, f, indent=1)


if __name__ == "__main__":
    main()
