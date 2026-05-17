"""
Compare detection methods on data/scenarios/*_mix.wav (held-out style benchmark).

Methods:
  heuristic       — detect_audio.py rules, no denoise
  heuristic_denoise
  embed           — YAMNet prototypes (classify_embed.py)
  embed_denoise
  embed_hpss      — HPSS percussive + YAMNet

Usage (repo root):
  pip install -r detection/requirements-ml.txt
  python detection/classify_embed.py --fit
  python detection/eval_scenarios.py
  python detection/eval_scenarios.py --methods heuristic embed
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DETECTION_DIR = Path(__file__).resolve().parent
REPO_ROOT = DETECTION_DIR.parent
SCENARIOS_DIR = REPO_ROOT / "data" / "scenarios"
OUTPUT_DIR = DETECTION_DIR / "output"
REPORT_PATH = OUTPUT_DIR / "eval_report.json"

if str(DETECTION_DIR) not in sys.path:
    sys.path.insert(0, str(DETECTION_DIR))

MILITARY_HINTS = {
    "gunshot": "gunshot",
    "missile": "missile_launch",
    "tank": "tank",
}
AMBIENT_KEYS = ("bird", "crickets", "dog", "frog", "animal")

ALL_METHODS = (
    "heuristic",
    "heuristic_denoise",
    "embed",
    "embed_denoise",
    "embed_hpss",
)


def expected_label_from_name(name: str) -> str | None:
    low = name.lower()
    if any(k in low for k in AMBIENT_KEYS):
        return None
    for key, lab in MILITARY_HINTS.items():
        if key in low:
            return lab
    return None


def list_mix_scenarios(folder: Path) -> list[Path]:
    return sorted(
        p for p in folder.glob("scenario_*_mix.wav")
        if p.is_file() and not p.name.startswith("._")
    )


def classify_heuristic(path: Path, *, denoise: bool) -> str | None:
    from detect_audio import classify_audio_file

    return classify_audio_file(path, denoise=denoise)


_prototypes_cache = None


def _get_prototypes():
    global _prototypes_cache
    if _prototypes_cache is None:
        from classify_embed import load_prototypes

        _prototypes_cache = load_prototypes()
    return _prototypes_cache


def classify_embed(path: Path, *, denoise: bool, hpss: bool) -> str | None:
    from classify_embed import classify_file, load_yamnet

    load_yamnet()
    enhance = "hpss" if hpss else None
    label, _ = classify_file(
        path, _get_prototypes(), denoise=denoise, enhance=enhance,
    )
    return label


def run_method(method: str, path: Path) -> str | None:
    if method == "heuristic":
        return classify_heuristic(path, denoise=False)
    if method == "heuristic_denoise":
        return classify_heuristic(path, denoise=True)
    if method == "embed":
        return classify_embed(path, denoise=False, hpss=False)
    if method == "embed_denoise":
        return classify_embed(path, denoise=True, hpss=False)
    if method == "embed_hpss":
        return classify_embed(path, denoise=False, hpss=True)
    raise ValueError(method)


def label_ok(pred: str | None, expected: str | None) -> bool:
    return pred == expected


def run_eval(
    scenarios: list[Path],
    methods: tuple[str, ...],
    *,
    verbose: bool = True,
) -> dict:
    rows = []
    summary: dict[str, dict[str, int]] = {m: {"ok": 0, "n": 0} for m in methods}

    for path in scenarios:
        expected = expected_label_from_name(path.name)
        row: dict = {
            "file": path.name,
            "expected": expected,
            "predictions": {},
            "correct": {},
        }
        for method in methods:
            try:
                pred = run_method(method, path)
            except Exception as exc:
                pred = None
                row["predictions"][method] = f"ERROR: {exc}"
                if verbose:
                    print(f"  {method} {path.name}: ERROR {exc}", file=sys.stderr)
                continue
            row["predictions"][method] = pred
            ok = label_ok(pred, expected)
            row["correct"][method] = ok
            summary[method]["n"] += 1
            summary[method]["ok"] += int(ok)

        rows.append(row)

    report = {
        "scenarios_dir": str(SCENARIOS_DIR),
        "files": len(scenarios),
        "methods": list(methods),
        "summary": {
            m: {
                "accuracy": summary[m]["ok"] / summary[m]["n"] if summary[m]["n"] else 0.0,
                "correct": summary[m]["ok"],
                "total": summary[m]["n"],
            }
            for m in methods
        },
        "rows": rows,
    }
    return report


def print_table(report: dict) -> None:
    methods = report["methods"]
    print("\n── Scenario benchmark (*_mix.wav) ──────────────────────")
    header = f"{'file':<28}" + "".join(f"{m[:10]:>12}" for m in methods) + f"{'expected':>14}"
    print(header)
    print("-" * len(header))
    for row in report["rows"]:
        exp = row["expected"] or "—"
        cells = []
        for m in methods:
            pred = row["predictions"].get(m)
            if isinstance(pred, str) and pred.startswith("ERROR"):
                cells.append("ERR")
            elif row["correct"].get(m):
                cells.append("OK")
            elif pred is None and row["expected"] is None:
                cells.append("OK")
            else:
                cells.append("MISS")
        line = f"{row['file']:<28}" + "".join(f"{c:>12}" for c in cells) + f"{exp:>14}"
        print(line)

    print("\n── Accuracy ───────────────────────────────────────────")
    for m in methods:
        s = report["summary"][m]
        pct = 100.0 * s["accuracy"]
        print(f"  {m:<20} {s['correct']}/{s['total']}  ({pct:.0f}%)")


def main() -> None:
    p = argparse.ArgumentParser(description="Benchmark detection methods on scenarios")
    p.add_argument(
        "--scenarios-dir",
        type=Path,
        default=SCENARIOS_DIR,
        help="folder with scenario_*_mix.wav",
    )
    p.add_argument(
        "--methods",
        nargs="+",
        choices=ALL_METHODS,
        default=list(ALL_METHODS),
    )
    p.add_argument("-o", "--output", type=Path, default=REPORT_PATH)
    p.add_argument("--fit-embed", action="store_true", help="run classify_embed --fit first")
    args = p.parse_args()

    if args.fit_embed:
        from classify_embed import fit_prototypes, save_prototypes

        print("Fitting YAMNet prototypes…", file=sys.stderr)
        save_prototypes(fit_prototypes(verbose=True))

    scenarios = list_mix_scenarios(args.scenarios_dir)
    if not scenarios:
        print(f"No scenario_*_mix.wav in {args.scenarios_dir}", file=sys.stderr)
        sys.exit(1)

    embed_methods = {m for m in args.methods if m.startswith("embed")}
    if embed_methods:
        from classify_embed import PROTOTYPES_PATH

        if not PROTOTYPES_PATH.exists():
            print(
                f"Missing {PROTOTYPES_PATH}. Run: python detection/classify_embed.py --fit",
                file=sys.stderr,
            )
            sys.exit(1)

    report = run_eval(scenarios, tuple(args.methods))
    print_table(report)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"\nReport: {args.output}")


if __name__ == "__main__":
    main()
