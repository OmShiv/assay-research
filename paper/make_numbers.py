"""Turn experiments/results/*.json into LaTeX macros and tables so that every number in the
paper is generated, never typed. Run after experiments/run_all.py:

    python paper/make_numbers.py
"""

import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(os.path.dirname(HERE), "experiments", "results")
TABLES = os.path.join(HERE, "tables")
os.makedirs(TABLES, exist_ok=True)


def load(name):
    with open(os.path.join(RESULTS, name + ".json"), "r", encoding="utf-8") as fh:
        return json.load(fh)


def camel(s):
    return "".join(p.capitalize() for p in re.split(r"[^A-Za-z0-9]+", s) if p)


def roman(n):
    return ["Zero", "One", "Two", "Three", "Four", "Five"][n]


def fmt_int(v):
    return f"{int(round(v)):,}"


def fmt_pct(v, d=0):
    return f"{v * 100:.{d}f}\\%"


def fmt_ms(v):
    return f"{v * 1000:,.0f}"


macros = []


def macro(name, value):
    name = re.sub(r"[^A-Za-z]", "", name)
    macros.append(f"\\newcommand{{\\{name}}}{{{value}}}")


def write_table(name, body):
    with open(os.path.join(TABLES, name + ".tex"), "w", encoding="utf-8") as fh:
        fh.write(body)


# ------------------------------------------------------------------ E1
e1 = load("e1_tokens")
rows = e1["rows"]
macro("EstimatorName", e1["estimator"].replace("_", "\\_"))
lines = ["\\begin{tabular}{lrrrrrrrr}", "\\toprule",
         "Repository & Lines & Modules & Full dump & Exploration & Index & Modules only & Brief & Gain \\\\", "\\midrule"]
for r in rows:
    lines.append(f"{r['corpus']} & {fmt_int(r['lines'])} & {r['modules']} & {fmt_int(r['full_dump'])} & {fmt_int(r['exploration'])} & "
                 f"{fmt_int(r['index'])} & {fmt_int(r['index_modules_only'])} & {fmt_int(r['brief'])} & {r['reduction_vs_exploration']:.0f}$\\times$ \\\\")
    for k in ("full_dump", "exploration", "index", "index_modules_only", "brief", "lines", "modules", "files"):
        macro("EOne" + camel(r["corpus"]) + camel(k), fmt_int(r[k]))
    macro("EOne" + camel(r["corpus"]) + "XFull", f"{r['reduction_vs_full']:.0f}")
    macro("EOne" + camel(r["corpus"]) + "XExpl", f"{r['reduction_vs_exploration']:.0f}")
lines += ["\\bottomrule", "\\end{tabular}"]
write_table("e1", "\n".join(lines) + "\n")
ext = [r for r in rows if r["corpus"] != "assay"]
macro("EOneMinXFull", f"{min(r['reduction_vs_full'] for r in ext):.0f}")
macro("EOneMaxXFull", f"{max(r['reduction_vs_full'] for r in ext):.0f}")
macro("EOneMinXExpl", f"{min(r['reduction_vs_exploration'] for r in ext):.0f}")
macro("EOneMaxXExpl", f"{max(r['reduction_vs_exploration'] for r in ext):.0f}")
macro("EOneMinBrief", fmt_int(min(r["brief"] for r in ext)))
macro("EOneMaxBrief", fmt_int(max(r["brief"] for r in ext)))
macro("EOneNumCorpora", str(len(ext)))

# ------------------------------------------------------------------ E2
e2 = load("e2_incremental")
lines = ["\\begin{tabular}{lrrrrrr}", "\\toprule",
         "Repository & Files & Cold & One edit & No change & Gain (edit) & Gain (no change) \\\\", "\\midrule"]
for r in e2["rows"]:
    lines.append(f"{r['corpus']} & {r['files']} & {fmt_ms(r['cold_s'])} & {fmt_ms(r['warm_1file_s'])} & {fmt_ms(r['warm_noop_s'])} & "
                 f"{r['speedup_1file']:.1f}$\\times$ & {r['speedup_noop']:.1f}$\\times$ \\\\")
    for k, nm in (("cold_s", "Cold"), ("warm_1file_s", "WarmEdit"), ("warm_noop_s", "WarmNoop")):
        macro("ETwo" + camel(r["corpus"]) + nm, fmt_ms(r[k]))
    macro("ETwo" + camel(r["corpus"]) + "Files", str(r["files"]))
    macro("ETwo" + camel(r["corpus"]) + "SpeedupNoop", f"{r['speedup_noop']:.0f}")
    macro("ETwo" + camel(r["corpus"]) + "SpeedupEdit", f"{r['speedup_1file']:.1f}")
lines += ["\\bottomrule", "\\end{tabular}"]
write_table("e2", "\n".join(lines) + "\n")
ext2 = [r for r in e2["rows"] if r["corpus"] != "assay"]
macro("ETwoMaxSpeedupNoop", f"{max(r['speedup_noop'] for r in ext2):.0f}")
macro("ETwoMinSpeedupNoop", f"{min(r['speedup_noop'] for r in ext2):.0f}")
macro("ETwoMaxSpeedupEdit", f"{max(r['speedup_1file'] for r in ext2):.0f}")
macro("ETwoMinSpeedupEdit", f"{min(r['speedup_1file'] for r in ext2):.1f}")

# ------------------------------------------------------------------ E3
e3 = load("e3_staleness")
lines = ["\\begin{tabular}{lrrrrr}", "\\toprule",
         "Repository & Modules & Repo hash & Cone hash & Self hash & Self hash misses \\\\", "\\midrule"]
for r in e3["rows"]:
    ks = r["k"]
    i = ks.index(4) if 4 in ks else len(ks) - 1
    lines.append(f"{r['corpus']} & {r['modules']} & {fmt_pct(r['cost']['repo'][i])} & {fmt_pct(r['cost']['cone'][i], 1)} & "
                 f"{fmt_pct(r['cost']['self'][i], 1)} & {fmt_pct(r['missed']['self'][i], 1)} \\\\")
    macro("EThree" + camel(r["corpus"]) + "K", str(ks[i]))
    macro("EThree" + camel(r["corpus"]) + "ConeCost", fmt_pct(r["cost"]["cone"][i], 1))
    macro("EThree" + camel(r["corpus"]) + "SelfCost", fmt_pct(r["cost"]["self"][i], 1))
    macro("EThree" + camel(r["corpus"]) + "SelfMissed", fmt_pct(r["missed"]["self"][i], 1))
lines += ["\\bottomrule", "\\end{tabular}"]
write_table("e3", "\n".join(lines) + "\n")
# per-corpus data files for the PGFPlots version of the E3 figure (read by assay.tex)
for r in e3["rows"]:
    datrows = ["k repo self cone selfmissed"]
    for i, k in enumerate(r["k"]):
        datrows.append(f'{k} {r["cost"]["repo"][i]:.4f} {r["cost"]["self"][i]:.4f} {r["cost"]["cone"][i]:.4f} {r["missed"]["self"][i]:.4f}')
    with open(os.path.join(TABLES, f'e3_{r["corpus"]}.dat'), "w", encoding="utf-8") as fh:
        fh.write("\n".join(datrows) + "\n")
ext3 = [r for r in e3["rows"] if r["corpus"] != "assay" and 4 in r["k"]]
macro("EThreeTrials", str(e3["trials"]))
macro("EThreeMinConeCost", fmt_pct(min(r["cost"]["cone"][r["k"].index(4)] for r in ext3), 1))
macro("EThreeMaxConeCost", fmt_pct(max(r["cost"]["cone"][r["k"].index(4)] for r in ext3), 1))
macro("EThreeMinSelfMissed", fmt_pct(min(r["missed"]["self"][r["k"].index(4)] for r in ext3), 0))
macro("EThreeMaxSelfMissed", fmt_pct(max(r["missed"]["self"][r["k"].index(4)] for r in ext3), 0))

# ------------------------------------------------------------------ E4
e4 = load("e4_gate")
lines = ["\\begin{tabular}{p{7.2cm}lp{4.6cm}}", "\\toprule", "Scenario & Gate & Mechanisms that fired \\\\", "\\midrule"]
for r in e4["results"]:
    lines.append(f"{r['scenario']} & {'PASS' if r['passed'] else 'FAIL'} & {', '.join(r['mechanisms']) or 'none'} \\\\")
lines += ["\\bottomrule", "\\end{tabular}"]
write_table("e4", "\n".join(lines) + "\n")
macro("EFourScenarios", str(len(e4["results"])))
macro("EFourBlocked", str(sum(1 for r in e4["results"] if not r["passed"])))
macro("EFourPassed", str(sum(1 for r in e4["results"] if r["passed"])))
macro("EFourAllExpected", "all" if all(r["passed"] == r["expected_pass"] for r in e4["results"]) else "not all")

# ------------------------------------------------------------------ E5
e5 = load("e5_blast")
lines = ["\\begin{tabular}{lrrrrrrr}", "\\toprule",
         "Repository & Modules & Median radius & p90 radius & L0 & L1 & L2 & L3 \\\\", "\\midrule"]
for r in e5["rows"]:
    lv = r["levels"]
    lines.append(f"{r['corpus']} & {r['modules']} & {fmt_pct(r['median_br_fraction'], 1)} & {fmt_pct(r['p90_br_fraction'], 1)} & "
                 f"{lv[0]} & {lv[1]} & {lv[2]} & {lv[3]} \\\\")
    macro("EFive" + camel(r["corpus"]) + "Median", fmt_pct(r["median_br_fraction"], 1))
    macro("EFive" + camel(r["corpus"]) + "Pninety", fmt_pct(r["p90_br_fraction"], 1))
    macro("EFive" + camel(r["corpus"]) + "Single", fmt_pct(r["single_module_share"], 0))
    macro("EFive" + camel(r["corpus"]) + "TopModule", r["top"][0]["module"].replace("_", "\\_"))
    macro("EFive" + camel(r["corpus"]) + "TopBr", str(r["top"][0]["br"]))
    macro("EFive" + camel(r["corpus"]) + "LThree", str(lv[3]))
    macro("EFive" + camel(r["corpus"]) + "Modules", str(r["modules"]))
lines += ["\\bottomrule", "\\end{tabular}"]
write_table("e5", "\n".join(lines) + "\n")
ext5 = [r for r in e5["rows"] if r["corpus"] != "assay"]
macro("EFiveMinSingle", fmt_pct(min(r["single_module_share"] for r in ext5), 0))
macro("EFiveMaxSingle", fmt_pct(max(r["single_module_share"] for r in ext5), 0))

# ------------------------------------------------------------------ implementation size and a real brief
SRC = os.path.join(os.path.dirname(HERE), "src", "assay")
TESTS = os.path.join(os.path.dirname(HERE), "tests")


def count_lines(d):
    n = 0
    for fn in os.listdir(d):
        if fn.endswith(".py"):
            with open(os.path.join(d, fn), "r", encoding="utf-8") as fh:
                n += sum(1 for line in fh if line.strip() and not line.strip().startswith("#"))
    return n


def count_tests(d):
    n = 0
    for fn in os.listdir(d):
        if fn.startswith("test_") and fn.endswith(".py"):
            with open(os.path.join(d, fn), "r", encoding="utf-8") as fh:
                n += sum(1 for line in fh if line.startswith("def test_"))
    return n


macro("LocCore", fmt_int(count_lines(SRC)))
macro("LocTests", fmt_int(count_lines(TESTS)))
macro("NumTests", str(count_tests(TESTS)))
brief = next((r["brief_text"] for r in rows if r["corpus"] == "fastapi"), rows[0]["brief_text"])
brief_lines = [ln.replace("\t", "  ") for ln in brief.splitlines()]
with open(os.path.join(TABLES, "brief_fastapi.txt"), "w", encoding="utf-8") as fh:
    fh.write("\n".join(ln[:118] + (" .." if len(ln) > 118 else "") for ln in brief_lines[:16]) + "\n")

with open(os.path.join(HERE, "numbers.tex"), "w", encoding="utf-8") as fh:
    fh.write("% generated by paper/make_numbers.py from experiments/results; do not edit\n")
    fh.write("\n".join(macros) + "\n")
print(f"wrote {len(macros)} macros to paper/numbers.tex and {len(os.listdir(TABLES))} tables")
