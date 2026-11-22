"""Render a suite JSON (``run_suite.py --out-json``) as Markdown tables.

    python scripts/suite/report.py docs/results/suite.json --out docs/results/suite.md
    python scripts/suite/report.py docs/results/suite.json --readme   # README summary table

Every number in the output is read from the JSON; nothing is computed here except formatting
and the acceptance checks of docs/001-router_enhancements.md, which are applied to the JSON.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
import json
from typing import Any

LOOM = "loom_served"
FAILFAST = "loom_failfast"
LONGMEM = "loom_longmem"
LONGMEM_WARM = "loom_longmem_warm"
# Policies the acceptance criteria are applied to, when present in the JSON.
CANDIDATES = (LOOM, FAILFAST, LONGMEM, LONGMEM_WARM)
COMPETENT = "competent"
# Acceptance criterion 1 of the proposal: lower CI bound of (Loom - competent) >= -0.15 pp.
TIE_MARGIN_PP = -0.15
DYNAMICS_POLICIES = (LOOM, FAILFAST, LONGMEM, "loom_bench", COMPETENT, "static_m3")
SHORT = {
    "loom_served": "Loom served",
    "loom_bench": "Loom bench",
    "loom_failfast": "Loom fail-fast",
    "loom_longmem": "Loom long-memory",
    "loom_longmem_warm": "Loom long-memory warm",
    "loom_raw": "Loom no PID",
    "static_m3": "Static M=3",
    "competent": "Competent",
    "competent_noretry": "Comp. no retry",
    "sr_window": "SR window",
    "sw_ucb": "SW-UCB",
    "oracle": "Oracle",
}


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.2f}"


def _num(v: float | None, digits: int = 1) -> str:
    return "n/a" if v is None else f"{v:.{digits}f}"


def _ci(c: dict[str, Any]) -> str:
    return f"{c['mean_diff_pp']:+.2f} [{c['ci95_low_pp']:+.2f}, {c['ci95_high_pp']:+.2f}]"


def _mean(entry: dict[str, Any], pol: str, metric: str) -> float | None:
    m = entry["policies"].get(pol, {}).get(metric)
    return None if m is None else m["mean"]


def _table(header: list[str], rows: list[list[str]], align: str = "") -> list[str]:
    align = align or ":--" + "|:-:" * (len(header) - 1)
    return [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(align.split("|")) + " |",
        *("| " + " | ".join(r) + " |" for r in rows),
    ]


def psr_table(data: dict[str, Any]) -> list[str]:
    """Mean PSR (%) for every scenario and policy."""
    pols = data["policies"]
    rows = []
    for name, e in data["scenarios"].items():
        rows.append([f"`{name}`"] + [_pct(_mean(e, p, "psr")) for p in pols])
    return _table(["Scenario", *(SHORT.get(p, p) for p in pols)], rows)


def regret_table(data: dict[str, Any]) -> list[str]:
    """Regret vs the oracle (pp, mean and 95% CI) for every non-oracle policy."""
    pols = [p for p in data["policies"] if p != data["reference"]]
    rows = []
    for name, e in data["scenarios"].items():
        rows.append([f"`{name}`"] + [_ci(e["regret_vs_oracle"][p]) for p in pols])
    return _table(["Scenario", *(SHORT.get(p, p) for p in pols)], rows)


def comparison_table(data: dict[str, Any]) -> list[str]:
    """Paired PSR differences (pp) with 95% CI and wins/ties/losses."""
    keys: list[str] = []
    for e in data["scenarios"].values():
        for k in e["comparisons"]:
            if k not in keys:
                keys.append(k)
    out: list[str] = []
    for k in keys:
        first, second = k.split("_vs_")
        out += ["", f"**{SHORT.get(first, first)} - {SHORT.get(second, second)}** (pp)", ""]
        rows = []
        for name, e in data["scenarios"].items():
            c = e["comparisons"].get(k)
            if c is not None:
                rows.append([f"`{name}`", _ci(c), f"{c['wins']}/{c['ties']}/{c['losses']}"])
        out += _table(["Scenario", "Mean [95% CI]", "W/T/L"], rows)
    return out


def dynamics_table(data: dict[str, Any], policies: list[str]) -> list[str]:
    """Incident dynamics: failed payments on a dead route, divert/restore seconds, hops, p99."""
    rows = []
    for name, e in data["scenarios"].items():
        if "seconds_to_divert" not in e["policies"][policies[0]]:
            continue
        for p in policies:
            m = e["policies"][p]
            div = m["seconds_to_divert"]
            res = m["seconds_to_restore"]
            rows.append(
                [
                    f"`{name}`",
                    SHORT.get(p, p),
                    _num(m["dead_route_failed_payments"]["mean"]),
                    _num(div["mean"]) + (f" ({div['missing']} never)" if div["missing"] else ""),
                    _num(res["mean"]) + (f" ({res['missing']} never)" if res["missing"] else ""),
                    _num(m["added_hops"]["mean"], 3),
                    _num(m["latency_p99_ms"]["mean"], 0),
                ]
            )
    return _table(
        [
            "Scenario",
            "Policy",
            "Failed on dead route",
            "Divert (s)",
            "Restore (s)",
            "Added hops",
            "p99 latency (ms)",
        ],
        rows,
    )


def soak_table(data: dict[str, Any]) -> list[str]:
    """Annual lost GMV vs the oracle (audit section 6 model), per component, $k."""
    out: list[str] = []
    for name, e in data["scenarios"].items():
        lost = e.get("annual_lost_gmv")
        if not lost:
            continue
        comps = list(next(iter(lost.values())))
        rows = []
        for pol, by_comp in lost.items():
            cells = []
            for c in comps:
                v = by_comp[c]
                cells.append(
                    "n/a"
                    if v["mean"] is None
                    else f"{v['mean'] / 1e3:,.0f} [{v['ci95'][0] / 1e3:,.0f}, "
                    f"{v['ci95'][1] / 1e3:,.0f}]"
                )
            rows.append([SHORT.get(pol, pol), *cells])
        rows.sort(key=lambda r: float(lost_total(lost, r[0])))
        out += ["", f"`{name}`: annual lost GMV vs the oracle, $k [95% CI]", ""]
        out += _table(["Policy", *comps], rows)
    return out


def lost_total(lost: dict[str, Any], short_name: str) -> float:
    """Mean total loss of a policy given its display name (for sorting)."""
    for pol, by_comp in lost.items():
        if SHORT.get(pol, pol) == short_name:
            return float(by_comp["total"]["mean"])
    return float("inf")


def acceptance(data: dict[str, Any]) -> list[str]:
    """The proposal's acceptance criteria 1, 3, 4 and 5, for each candidate Loom policy."""
    scen = data["scenarios"]
    lines: list[str] = []
    for cand in (c for c in CANDIDATES if c in data["policies"]):
        name = SHORT.get(cand, cand)
        lines += ["", f"**{name}**", ""]
        key = f"{cand}_vs_{COMPETENT}"
        if all(key in e["comparisons"] for e in scen.values()):
            passing = [
                n for n, e in scen.items() if e["comparisons"][key]["ci95_low_pp"] >= TIE_MARGIN_PP
            ]
            failing = [n for n in scen if n not in passing]
            lines.append(
                f"1. Lower 95% bound of ({name} - competent) >= {TIE_MARGIN_PP} pp: "
                f"**{len(passing)} of {len(scen)} scenarios**"
                + (f"; misses {', '.join(f'`{n}`' for n in failing)}." if failing else ".")
            )
        refs = [p for p in ("sr_window", "sw_ucb") if p in data["policies"]]
        if refs:
            ties = 0
            for e in scen.values():
                ok = True
                for r in refs:
                    c = e["comparisons"].get(f"{cand}_vs_{r}")
                    if c is None or c["ci95_low_pp"] < TIE_MARGIN_PP:
                        ok = False
                ties += ok
            lines.append(
                f"3. Ties or beats SR window and SW-UCB (same margin): "
                f"**{ties} of {len(scen)} scenarios**."
            )
        for sname, e in scen.items():
            lost = e.get("annual_lost_gmv")
            if not lost or cand not in lost:
                continue
            best = min(lost, key=lambda p: lost[p]["total"]["mean"])
            lines.append(
                f"4. `{sname}`: lowest annual lost GMV is **{SHORT.get(best, best)}** "
                f"({name}: ${lost[cand]['total']['mean'] / 1e3:,.0f}k; "
                f"{SHORT.get(best, best)}: ${lost[best]['total']['mean'] / 1e3:,.0f}k)."
            )
    amb = sum(
        (e["policies"][p]["ambiguous_retries"]["mean"] or 0.0)
        for e in scen.values()
        for p in data["policies"]
    )
    lines += ["", f"5. Ambiguous retries, all policies and scenarios: **{amb:g}**."]
    return lines


def readme_table(data: dict[str, Any]) -> list[str]:
    """Compact table for the README: PSR of the main policies and Loom - competent."""
    main = (LOOM, FAILFAST, LONGMEM, LONGMEM_WARM, COMPETENT, "static_m3", "oracle")
    pols = [p for p in main if p in data["policies"]]
    cand = next((c for c in (LONGMEM, FAILFAST) if c in data["policies"]), LOOM)
    rows = []
    for name, e in data["scenarios"].items():
        c = e["comparisons"].get(f"{cand}_vs_{COMPETENT}")
        rows.append(
            [f"`{name}`"]
            + [_pct(_mean(e, p, "psr")) for p in pols]
            + ([_ci(c)] if c is not None else [])
        )
    header = [
        "Scenario",
        *(SHORT[p] + " PSR %" for p in pols),
        f"{SHORT[cand]} - competent, pp [95% CI]",
    ]
    return _table(header, rows)


def render(data: dict[str, Any]) -> str:
    """The full Markdown report."""
    seeds = data["seeds"]
    out = [
        "# Scenario suite: results",
        "",
        "> Generated by `scripts/suite/report.py` from `docs/results/suite.json`; do not edit "
        "by hand. Regenerate with:",
        ">",
        f"> `{data['command']}`",
        ">",
        "> `python scripts/suite/report.py docs/results/suite.json --out docs/results/suite.md`",
        "",
        f"Split **{data['split']}**, {data['n_seeds']} paired seeds ({seeds[0]} to {seeds[-1]}). "
        "Every policy in a run sees the same arrivals and the same common random numbers, so "
        "differences are paired per seed; intervals are 95% t-intervals over seeds. Scenario "
        "definitions: `scripts/suite/scenarios.py`; metric definitions: the Stage 0 entry in "
        "`docs/decisions-log.md`. This is simulator evidence only.",
        "",
        "## Acceptance criteria",
        "",
        "The criteria are the targets of `docs/001-router_enhancements.md` for the finished "
        "redesign, applied here to each stage as it stands: *Loom served* is the served "
        "default (Stage 0 behaviour), *Loom fail-fast* adds Stage 1 (technical gate, one "
        "cascade retry, rate floor; `python -m router_core.server --fail-fast`), *Loom "
        "long-memory* adds Stage 2 (multi-timescale beliefs, win-probability setpoint; "
        "`--fail-fast --long-memory`), and *Loom long-memory warm* starts that from a "
        "simulated previous day's ledger (`--priors`; see `previous_day_priors` in "
        "`scripts/suite/policies.py`). Criterion 2 (wins with the CI excluding 0) is read from "
        "the paired tables below.",
        *acceptance(data),
        "",
        "## Mean PSR (%)",
        "",
        *psr_table(data),
        "",
        "## Regret vs the oracle (pp, mean [95% CI])",
        "",
        "Oracle PSR minus policy PSR. The oracle ignores capacity, so in the capacity scenarios "
        "a policy can beat it.",
        "",
        *regret_table(data),
        "",
        "## Paired comparisons",
        *comparison_table(data),
        "",
        "## Incident dynamics (means over seeds)",
        "",
        "*Failed on dead route*: payments whose last attempt went to a hard-down acquirer. "
        "*Divert*: seconds from incident start until the affected acquirer's share of first "
        "attempts in the following window drops below 20%. *Restore*: seconds from incident end "
        "until it is back to half its pre-incident share. *Never* counts seeds where it did not "
        "happen before the run ended.",
        "",
        *dynamics_table(data, [p for p in DYNAMICS_POLICIES if p in data["policies"]]),
        "",
        "## Day-long soak scored with the audit's revenue model",
        "",
        "Audit §6 base case: $1B GMV a year, 50% of failed payments recovered by the shopper, "
        "2 h of fast-fail and 2 h of timeout outage and 20 h of gray failure per year, the rest "
        "steady. Each component's loss rate is measured against the oracle in the soak (incident "
        "plus a 10 min tail, per payment that arrived during the incident) instead of the "
        "audit's assumed coefficients.",
        *soak_table(data),
        "",
    ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> None:
    """Write the report, or print the README table."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("json", help="suite JSON from run_suite.py --out-json")
    parser.add_argument("--out", default=None, help="write the full report here")
    parser.add_argument("--readme", action="store_true", help="print the README table")
    args = parser.parse_args(argv)
    data = json.loads(Path(args.json).read_text(encoding="utf-8"))
    if args.readme:
        print("\n".join(readme_table(data)))
        print()
        print("\n".join(acceptance(data)))
        return
    text = render(data)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
