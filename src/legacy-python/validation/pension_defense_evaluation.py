"""Audit artifacts, compare fixed cash baselines, and verify causal prefixes."""
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import PROJECT_ROOT
from pension_defense_review import LOCK, load_market, simulate, source_schedule
from result_paths import research_result_path

STEMS = (("pension_defense_long", 6), ("pension_defense_long_cost3", 6),
         ("pension_defense_long_delay1", 6), ("pension_defense_domestic", 3),
         ("pension_defense_domestic_cost3", 3), ("pension_defense_domestic_delay1", 3))


def main():
    directory = research_result_path("pension_defense_all_summary.csv").parent
    frames = []
    for stem, expected in STEMS:
        frame = pd.read_csv(directory/(stem+"_summary.csv"))
        if len(frame) != expected or frame[["CAGR", "MDD", "Return"]].isna().any().any():
            raise ValueError(f"Incomplete results: {stem}")
        manifest = json.loads((directory/(stem+"_manifest.json")).read_text(encoding="utf-8"))
        if manifest["Design"] != LOCK:
            raise ValueError("Changed experiment design")
        for entry in manifest["InputsAndCode"]:
            if hashlib.sha256((PROJECT_ROOT/entry["File"]).read_bytes()).hexdigest() != entry["SHA256"]:
                raise ValueError(f"Changed input/code: {entry['File']}")
        frames.append(frame)
    pd.concat(frames, ignore_index=True).to_csv(directory/"pension_defense_all_summary.csv", index=False)
    windows = pd.read_csv(directory/"pension_defense_long_windows.csv")
    rows = []
    for profile, reference in (("ROTATE_USD", "USD_CASH"), ("ROTATE_KRW", "KRW_SHORT"), ("FX_SWITCH", "USD_CASH")):
        base = frames[0].set_index("Profile")
        candidate, control = base.loc[profile], base.loc[reference]
        sample = windows[windows.Profile.eq(profile)].set_index("Start")
        reference_windows = windows[windows.Profile.eq(reference)].set_index("Start")
        positive = int((sample.CAGR-reference_windows.CAGR > 1e-10).sum())
        differences = []
        for stress in frames[1:3]:
            stress = stress.set_index("Profile")
            differences.append(float(stress.at[profile, "CAGR"]-stress.at[reference, "CAGR"]))
        row = {"Profile": profile, "Reference": reference,
            "CAGRDelta": float(candidate.CAGR-control.CAGR),
            "MDDDelta": float(candidate.MDD-control.MDD), "PositiveSubperiods": positive,
            "Cost3CAGRDelta": differences[0], "Delay1CAGRDelta": differences[1]}
        row["HistoricalFollowupPass"] = bool(row["CAGRDelta"] > 1e-10 and
            row["MDDDelta"] >= -LOCK["screen"]["long_mdd_deterioration_max"]-1e-10 and
            positive >= LOCK["screen"]["positive_subperiods_min"] and min(differences)>1e-10)
        rows.append(row)
    pd.DataFrame(rows).to_csv(directory/"pension_defense_decisions.csv", index=False)
    checks = []
    schedule = source_schedule()
    for universe in ("LONG", "DOMESTIC"):
        prices, risk, fx, dates = load_market(universe)
        profiles = ("ROTATE_USD", "ROTATE_KRW", "FX_SWITCH") if universe == "LONG" else ("ROTATE",)
        cutoffs = ("2018-12-31", "2022-06-30") if universe == "LONG" else ("2025-04-30", "2026-06-30")
        for profile in profiles:
            full = pd.read_csv(directory/f"pension_defense_{universe.lower()}_{profile.lower()}_daily.csv", index_col="Date", parse_dates=True)
            for cutoff in cutoffs:
                prefix, _, _, _ = simulate({name: frame.loc[:cutoff] for name, frame in prices.items()},
                    risk, schedule.loc[:cutoff], profile, (dates[0], cutoff),
                    fx=fx.loc[:cutoff] if fx is not None else None)
                pd.testing.assert_frame_equal(prefix, full.loc[:cutoff], check_exact=False,
                    check_freq=False, rtol=1e-12, atol=1e-12)
                checks.append({"Universe": universe, "Profile": profile, "Cutoff": cutoff, "Passed": True})
    (directory/"pension_defense_validation.json").write_text(json.dumps({
        "PrimaryRuns": 27, "InputHashesVerified": True,
        "PrefixChecks": checks, "EvaluatorSHA256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "ComparisonMeaning": "ROTATE_KRW is compared with its KRW short-bond cash baseline, not the USD-cash model. The domestic sample is implementation evidence only.",
    }, indent=2), encoding="utf-8")
    print(pd.DataFrame(rows).to_string(index=False))
    print(f"27 valid primary runs; {len(checks)} actual-data prefix checks passed.")


if __name__ == "__main__":
    main()
