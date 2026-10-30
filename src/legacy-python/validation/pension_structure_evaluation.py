"""Consolidate the predeclared structure experiment without fitting parameters."""
import hashlib
import json
from pathlib import Path

import pandas as pd

from config import PROJECT_ROOT
from result_paths import research_result_path


STEMS = (("pension_structure_current", 10), ("pension_structure_crises", 20),
         ("pension_structure_cost3", 3), ("pension_structure_delay1", 3),
         ("pension_structure_krw", 3))


def main():
    directory = research_result_path("pension_structure_all_summary.csv").parent
    lock_path = PROJECT_ROOT / "validation/pension_structure_lock_20261025.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    frames, input_paths = [], [lock_path]
    for stem, expected in STEMS:
        path = directory / (stem + "_summary.csv")
        frame = pd.read_csv(path)
        if len(frame) != expected or frame[["CAGR", "MDD", "Rows"]].isna().any().any():
            raise ValueError(f"Incomplete experiment: {stem}")
        frames.append(frame)
        input_paths.append(path)
        for path in directory.glob(stem + "_*_manifest.json"):
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if manifest["Lock"] != lock:
                raise ValueError(f"Different design lock: {path.name}")
            for entry in manifest["CodeAndDefinitions"]:
                if hashlib.sha256((PROJECT_ROOT / entry["File"]).read_bytes()).hexdigest() != entry["SHA256"]:
                    raise ValueError(f"Changed code or definition: {entry['File']}")
            input_paths.append(path)
    pd.concat(frames, ignore_index=True).to_csv(directory / "pension_structure_all_summary.csv", index=False)
    windows_path = directory / "pension_structure_current_windows.csv"
    input_paths.append(windows_path)
    windows = pd.read_csv(windows_path)
    current = frames[0].set_index("Variant")
    gates = lock["historical_followup_screen"]
    rows = []
    # Numerical equality is not evidence of a positive return contribution.
    epsilon = 1e-10
    for variant in (*lock["ablation_states"], "SMA_SLEEVES", "EXCESS_SLEEVES"):
        result = current.loc[variant]
        old = frames[1].loc[frames[1].Variant.eq(variant)]
        stress = {}
        for frame, name in zip(frames[2:], ("Cost3", "Delay1", "KRW")):
            sample = frame.loc[frame.Variant.eq(variant), "CAGRDelta"]
            stress[name + "CAGRDelta"] = float(sample.iloc[0]) if len(sample) else None
        complete_stress = all(value is not None for value in stress.values())
        row = {"Variant": variant, "CAGRDelta": float(result.CAGRDelta),
               "MDDDelta": float(result.MDDDelta),
               "PositiveCurrentSubperiods": int((windows.loc[windows.Variant.eq(variant), "CAGRDelta"] > epsilon).sum()),
               "WorstProxyMDDDelta": float(old.MDDDelta.min()), **stress}
        row.update({
            "CurrentReturnPass": row["CAGRDelta"] > gates["current_cagr_gain_min_exclusive"] + epsilon,
            "CurrentRiskPass": row["MDDDelta"] >= -gates["current_mdd_deterioration_max"] - epsilon,
            "SubperiodPass": row["PositiveCurrentSubperiods"] >= gates["current_subperiod_positive_cagr_gain_count_min"],
            "ProxyRiskPass": row["WorstProxyMDDDelta"] >= -gates["proxy_mdd_deterioration_max"] - epsilon,
            "StressEvaluated": complete_stress,
            "StressPass": all(value > epsilon for value in stress.values()) if complete_stress else None,
        })
        row["HistoricalFollowupPass"] = all(row[key] for key in (
            "CurrentReturnPass", "CurrentRiskPass", "SubperiodPass", "ProxyRiskPass", "StressPass"))
        rows.append(row)
    table = pd.DataFrame(rows)
    table.to_csv(directory / "pension_structure_decisions.csv", index=False)
    input_paths.append(Path(__file__))
    (directory / "pension_structure_evaluation_manifest.json").write_text(json.dumps({
        "InputsAndEvaluator": [{"File": str(path.relative_to(PROJECT_ROOT)),
            "SHA256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in input_paths],
        "NumericalTolerance": epsilon,
    }, indent=2), encoding="utf-8")
    print(table[["Variant", "PositiveCurrentSubperiods", "StressEvaluated", "HistoricalFollowupPass"]].to_string(index=False))


if __name__ == "__main__":
    main()
