"""Inspect the published workbook without modifying it or calling a model."""
from collections import Counter
from pathlib import Path
import hashlib
import json
import shutil
import statistics

import openpyxl

ROOT = Path(__file__).resolve().parent
for name in ("raihani2014.s001.doc", "raihani2014.s001.txt", "raihani2014.s002.xlsx"):
    destination = ROOT / name
    if not destination.exists():
        shutil.copyfile(Path("/private/tmp") / name, destination)

source = ROOT / "raihani2014.s002.xlsx"
book = openpyxl.load_workbook(source, read_only=True, data_only=True)
sheet = book["Supporting Information S2"]
values = list(sheet.values)
headers = values[0][:12]
rows = [dict(zip(headers, row[:12])) for row in values[1:] if row[0] is not None]
counts = Counter((r["Treat"], r["Target"]) for r in rows)
summary = {
    "source": "https://journals.plos.org/plosone/article/file?id=10.1371/journal.pone.0113826.s002&type=supplementary",
    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    "sheet": sheet.title,
    "record_count": len(rows),
    "columns": list(headers),
    "dictionary": {r[13]: r[14] for r in values[1:] if r[13] is not None},
    "unique_worker_codes": len({r["WorkerId"] for r in rows}),
    "unique_hit_codes": len({r["HITid"] for r in rows}),
    "empty_cells": {key: sum(r[key] is None for r in rows) for key in headers},
    "country_counts": dict(Counter(r["Country"] for r in rows)),
    "gender_counts": dict(Counter(r["Gender"] for r in rows)),
    "age_zero_count": sum(r["Age"] == 0 for r in rows),
    "age_nonzero_range": [min(r["Age"] for r in rows if r["Age"]), max(r["Age"] for r in rows if r["Age"])],
    "given_range": [min(r["Given"] for r in rows), max(r["Given"] for r in rows)],
    "given_distinct": sorted({r["Given"] for r in rows}),
    "given_out_of_range": sum(not 0 <= r["Given"] <= 1 for r in rows),
    "compliance_label_mismatches": sum(int(r["Given"] + 1e-9 >= r["Target"] / 100) != r["bin_complied"] for r in rows),
    "nothing_label_mismatches": sum(int(r["Given"] == 0) != r["bin_nothing"] for r in rows),
    "groups": [],
}
for (treatment, target), count in sorted(counts.items()):
    group = [r for r in rows if (r["Treat"], r["Target"]) == (treatment, target)]
    amounts = [r["Given"] for r in group]
    summary["groups"].append({
        "treatment": treatment, "target_code": target, "n": count,
        "mean_given_usd": statistics.mean(amounts),
        "median_given_usd": statistics.median(amounts),
        "at_or_above_matched_threshold": sum(x + 1e-9 >= target / 100 for x in amounts),
        "zero_given": amounts.count(0), "equal_split": amounts.count(0.5),
        "valid_age_gender_n": sum(r["Age"] != 0 and r["Gender"] != "*" for r in group),
    })
book.close()
(ROOT / "data_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(summary, ensure_ascii=False, indent=2))
