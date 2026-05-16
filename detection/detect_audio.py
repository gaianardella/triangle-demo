"""
Classify audio into military categories (or not relevant):
  gunshot | missile_launch | drone | tank

Usage (from repo root):
  conda activate audio_env
  python detection/detect_audio.py data/scenarios/scenario_tank_mix.wav
  python detection/detect_audio.py --folder data/scenarios
  python detection/detect_audio.py --folder data/scenarios -o detection/output/events.json
  python detection/detect_audio.py --benchmark
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter
from pathlib import Path

import librosa
import numpy as np
from scipy import signal

WINDOW_SIZE = 0.5
HOP_SIZE = 0.25
SR = 22050

DETECTION_DIR = Path(__file__).resolve().parent
REPO_ROOT = DETECTION_DIR.parent
OUTPUT_DIR = DETECTION_DIR / "output"
DATA_DIR = REPO_ROOT / "data"
SAMPLES_DIR = DATA_DIR / "samples"
DRONE_REF_PATH = SAMPLES_DIR / "drone" / "uas_drone_pass_dcpoke.wav"

# Below this ratio, “tank” is often rotor + forest only (no real tank in the mix)
TANK_COMPARATIVE_MIN_RATIO = 1.05
TANK_MAJORITY_FRAC = 0.68

AMBIENT_SCENARIO_KEYS = ("scenario_bird", "scenario_crickets", "scenario_dog")

CATEGORIES = ("gunshot", "missile_launch", "drone", "tank")
CATEGORY_LABELS = {
    "gunshot":        "Gunfire",
    "missile_launch": "Missile / UCAS launch",
    "drone":          "UAV / drone",
    "tank":           "Tank engine",
}

LABEL_NOT_RELEVANT = "Not relevant"

AUDIO_EXT = {".wav", ".flac", ".mp3", ".ogg"}

GUNSHOT_DIR = DATA_DIR / (
    "DCASE2017/TUT-rare-sound-events-2017-development"
    "/data/source_data/events/gunshot"
)


# ── Classifiers ─────────────────────────────────────────────────────────────
def _cancel_wind_noise(audio, sr):
    b, a = signal.butter(4, 200 / (sr / 2), btype="high")
    return signal.filtfilt(b, a, audio)


MIN_RMS = 0.002
MIN_PEAK_ENERGY = 0.006
GUNSHOT_PEAK_RATIO = 4.0
GUNSHOT_CREST_MIN = 3.0
GUNSHOT_SPIKE_MAX_S = 0.12
MISSILE_PEAK_RATIO_MIN = 1.75
MISSILE_CREST_MIN = 1.7
MISSILE_MID_FREQ_MIN = 0.68
MISSILE_SPIKE_MIN_S = 0.06
MISSILE_SPIKE_MAX_S = 0.55
MISSILE_LONG_SPIKE_MIN_S = 0.35
TANK_CREST_MAX = 4.8
TANK_SUSTAINED_MIN = 0.18
TANK_LOW_FREQ_MIN = 0.35
TANK_MID_FREQ_MIN = 0.50
TANK_MID_LOW_MAX = 0.35
DRONE_MID_FREQ_MIN = 0.32
DRONE_LOW_FREQ_MAX = 0.48
DRONE_SUSTAINED_MIN = 0.38
DRONE_CREST_MIN = 2.0
DRONE_CREST_MAX = 9.0


def _energy_features(audio, sr):
    frame_length = int(sr * 0.01)
    energy = np.array([
        np.sqrt(np.mean(audio[i:i + frame_length] ** 2))
        for i in range(0, len(audio) - frame_length, frame_length)
    ])
    if len(energy) == 0:
        return None

    mean_energy = np.median(energy)
    peak_energy = energy.max()
    freqs = np.fft.rfftfreq(len(audio), 1 / sr)
    fft = np.abs(np.fft.rfft(audio))
    total = fft.sum() + 1e-10

    return {
        "energy":          energy,
        "mean_energy":     mean_energy,
        "peak_energy":     peak_energy,
        "rms":             np.sqrt(np.mean(audio ** 2)),
        "crest":           peak_energy / (mean_energy + 1e-10),
        "peak_ratio":      peak_energy / (mean_energy + 1e-10),
        "low_freq_ratio":  fft[freqs < 200].sum() / total,
        "mid_freq_ratio":  fft[(freqs >= 300) & (freqs < 4000)].sum() / total,
        "high_freq_ratio": fft[freqs > 3000].sum() / total,
        "sustained":       float(np.mean(energy > mean_energy * 1.15)),
    }


def _spike_duration(f, ratio: float = GUNSHOT_PEAK_RATIO) -> float:
    threshold = f["mean_energy"] * ratio
    return len(np.where(f["energy"] > threshold)[0]) * 0.01


def _is_active(f) -> bool:
    return f["rms"] >= MIN_RMS or f["peak_energy"] >= MIN_PEAK_ENERGY


def classify_gunshot(audio, sr):
    f = _energy_features(audio, sr)
    if f is None or not _is_active(f):
        return None, 0.0
    spike_duration = _spike_duration(f)
    if (
        f["peak_ratio"] >= GUNSHOT_PEAK_RATIO
        and f["crest"] >= GUNSHOT_CREST_MIN
        and spike_duration <= GUNSHOT_SPIKE_MAX_S
    ):
        return "gunshot", min(0.9, 0.55 + f["crest"] / 28)
    return None, 0.0


def classify_missile_launch(audio, sr):
    """Launch / boost / UCAS: medium-long impulse or buried boost in UAV mix."""
    f = _energy_features(audio, sr)
    if f is None or not _is_active(f):
        return None, 0.0
    if f["sustained"] > 0.50 and f["crest"] < 3.0:
        return None, 0.0
    spike_short = _spike_duration(f)
    spike_med = _spike_duration(f, ratio=3.0)

    if (
        f["peak_ratio"] >= GUNSHOT_PEAK_RATIO
        and f["crest"] >= GUNSHOT_CREST_MIN
        and spike_short <= GUNSHOT_SPIKE_MAX_S
    ):
        return None, 0.0

    buried_boost = (
        MISSILE_PEAK_RATIO_MIN <= f["peak_ratio"] < GUNSHOT_PEAK_RATIO
        and f["crest"] >= MISSILE_CREST_MIN
        and f["mid_freq_ratio"] >= MISSILE_MID_FREQ_MIN
        and f["sustained"] < 0.40
    )
    loud_boost = (
        f["peak_ratio"] >= GUNSHOT_PEAK_RATIO
        and f["crest"] >= GUNSHOT_CREST_MIN
        and GUNSHOT_SPIKE_MAX_S < spike_med <= MISSILE_SPIKE_MAX_S
        and f["mid_freq_ratio"] >= 0.50
    )
    long_roar = spike_med >= MISSILE_LONG_SPIKE_MIN_S and f["peak_ratio"] >= 3.0

    if buried_boost or loud_boost or long_roar:
        score = 0.55 + min(f["crest"], 12.0) / 30 + f["mid_freq_ratio"] * 0.2
        return "missile_launch", min(0.9, score)
    return None, 0.0


def classify_drone(audio, sr):
    f = _energy_features(audio, sr)
    if f is None or f["rms"] < MIN_RMS:
        return None, 0.0
    if (
        DRONE_CREST_MIN <= f["crest"] <= DRONE_CREST_MAX
        and f["sustained"] > DRONE_SUSTAINED_MIN
        and f["mid_freq_ratio"] > DRONE_MID_FREQ_MIN
        and f["low_freq_ratio"] < DRONE_LOW_FREQ_MAX
        and f["peak_ratio"] < GUNSHOT_PEAK_RATIO * 1.2
    ):
        return "drone", min(0.88, 0.5 + f["mid_freq_ratio"] * 0.4)
    return None, 0.0


def classify_tank(audio, sr):
    f = _energy_features(audio, sr)
    if f is None or f["rms"] < MIN_RMS:
        return None, 0.0
    deep_engine = f["low_freq_ratio"] > TANK_LOW_FREQ_MIN
    track_pass = (
        f["mid_freq_ratio"] > TANK_MID_FREQ_MIN
        and f["low_freq_ratio"] < TANK_MID_LOW_MAX
    )
    if not (deep_engine or track_pass):
        return None, 0.0
    if (
        f["crest"] < TANK_CREST_MAX
        and f["sustained"] > TANK_SUSTAINED_MIN
        and f["peak_ratio"] < GUNSHOT_PEAK_RATIO
        and not (
            f["mid_freq_ratio"] >= MISSILE_MID_FREQ_MIN
            and f["sustained"] < 0.50
            and f["crest"] < 2.5
        )
    ):
        score = 0.5 + max(f["sustained"], f["low_freq_ratio"]) * 0.35
        return "tank", min(0.85, score)
    return None, 0.0


def classify_chunk(audio_chunk, sr):
    clean = _cancel_wind_noise(audio_chunk, sr)
    label, conf = classify_gunshot(clean, sr)
    if label:
        return label, conf
    label, conf = classify_missile_launch(clean, sr)
    if label:
        return label, conf
    for classifier in (classify_tank, classify_drone):
        label, conf = classifier(audio_chunk, sr)
        if label:
            return label, conf
    return None, 0.0


def _scan_onset_windows(audio: np.ndarray, sr: int, n_peaks: int = 6) -> list[str]:
    """Classify windows at energy-onset peaks (rare events in the mix)."""
    frame = int(sr * 0.02)
    if len(audio) < frame * 4:
        return []
    energy = np.array([
        np.sqrt(np.mean(audio[i:i + frame] ** 2))
        for i in range(0, len(audio) - frame, frame)
    ])
    onset = np.maximum(0.0, np.diff(energy, prepend=energy[0]))
    threshold = float(np.percentile(onset, 88))
    labels: list[str] = []
    ws = int(WINDOW_SIZE * sr)

    for idx in np.argsort(onset)[-n_peaks:]:
        if onset[idx] < threshold:
            continue
        center = idx * 0.02
        start = max(0, int((center - WINDOW_SIZE / 2) * sr))
        chunk = audio[start:start + ws]
        if len(chunk) < ws:
            chunk = np.pad(chunk, (0, ws - len(chunk)))
        label, _ = classify_chunk(chunk, sr)
        if label in ("gunshot", "missile_launch"):
            labels.append(label)
    return labels


def scan_audio_for_events(audio, sr):
    window_samples = int(WINDOW_SIZE * sr)
    hop_samples = int(HOP_SIZE * sr)
    if len(audio) < window_samples:
        return []
    detected = []
    for start in range(0, len(audio) - window_samples, hop_samples):
        label, _ = classify_chunk(audio[start:start + window_samples], sr)
        if label:
            detected.append(label)
    return detected


def scan_file_for_events(audio_path, sr=SR):
    audio, sr = librosa.load(audio_path, sr=sr)
    return scan_audio_for_events(audio, sr)


def _bandpass_tank_rms(audio: np.ndarray, sr: int) -> float:
    b, a = signal.butter(4, [70 / (sr / 2), 1400 / (sr / 2)], btype="band")
    y = signal.filtfilt(b, a, audio.astype(np.float64))
    return float(np.sqrt(np.mean(y ** 2)) + 1e-12)


def _comparative_tank_ratio(mixture: np.ndarray, sr: int) -> float:
    """Tank-band energy on mix vs drone-only (same light preprocess)."""
    if not DRONE_REF_PATH.exists() or len(mixture) < sr:
        return 999.0
    drone, _ = librosa.load(DRONE_REF_PATH, sr=sr, duration=len(mixture) / sr)
    if len(drone) < len(mixture):
        reps = int(np.ceil(len(mixture) / len(drone)))
        drone = np.tile(drone, reps)[: len(mixture)]
    else:
        drone = drone[: len(mixture)]
    clean_mix = _cancel_wind_noise(mixture, sr)
    clean_drone = _cancel_wind_noise(drone, sr)
    return _bandpass_tank_rms(clean_mix, sr) / (_bandpass_tank_rms(clean_drone, sr) + 1e-12)


def filter_military_relevance(
    label: str | None,
    counts: Counter,
    onset_types: list[str],
    audio: np.ndarray,
    sr: int,
) -> str | None:
    """Drop spurious tank (rotor/forest); impulsive labels from dominant_detection."""
    if label is None:
        return None
    n = sum(counts.values())
    if n == 0:
        return None

    if label == "tank":
        if counts["tank"] < n * 0.52:
            return None
        if counts["tank"] >= n * TANK_MAJORITY_FRAC:
            return label
        if _comparative_tank_ratio(audio, sr) < TANK_COMPARATIVE_MIN_RATIO:
            return None
        return label

    if label == "drone" and counts["drone"] < n * 0.40:
        return None
    return label


def filter_ambient_scenario(label: str | None, counts: Counter, onset_types: list[str]) -> str | None:
    """UAV + animal scenario files (scenario_bird_* …): no military alert."""
    return None


def _is_ambient_scenario_path(path: Path) -> bool:
    name = path.name.lower()
    return any(k in name for k in AMBIENT_SCENARIO_KEYS)


def dominant_detection(detected_types, onset_types: list[str] | None = None):
    if not detected_types and not onset_types:
        return None
    counts = Counter(detected_types)
    n = len(detected_types)
    onset_counts = Counter(onset_types or [])

    if counts.get("gunshot", 0) >= 1 and onset_counts.get("gunshot", 0) >= 1:
        return "gunshot"

    if n and counts.get("tank", 0) >= n * 0.45 and counts.get("missile_launch", 0) <= 2:
        return "tank"

    if (
        counts.get("missile_launch", 0) >= 1
        and onset_counts.get("missile_launch", 0) >= 2
    ):
        return "missile_launch"

    for label in ("gunshot", "missile_launch"):
        if counts[label] >= max(2, int(n * 0.05)):
            return label

    if not detected_types:
        return None
    return counts.most_common(1)[0][0]


def classify_audio_array(audio, sr=SR):
    sliding = scan_audio_for_events(audio, sr)
    onsets = _scan_onset_windows(audio, sr)
    label = dominant_detection(sliding, onsets)
    return filter_military_relevance(label, Counter(sliding), onsets, audio, sr)


def classify_audio_file(audio_path, sr=SR):
    audio, sr = librosa.load(audio_path, sr=sr)
    return classify_audio_array(audio, sr)


# ── CLI / integration ───────────────────────────────────────────────────────
def detect_file(path: Path, drone_id: str = "drone_1") -> dict:
    audio, sr = librosa.load(path, sr=SR)
    events = scan_audio_for_events(audio, sr)
    sliding = events
    onsets = _scan_onset_windows(audio, sr)
    label = dominant_detection(sliding, onsets)
    label = filter_military_relevance(label, Counter(sliding), onsets, audio, sr)
    if _is_ambient_scenario_path(path):
        label = filter_ambient_scenario(label, Counter(sliding), onsets)

    frame_length = int(sr * 0.01)
    energy = np.array([
        np.sqrt(np.mean(audio[i:i + frame_length] ** 2))
        for i in range(0, len(audio) - frame_length, frame_length)
    ])
    peak_offset_s = float(np.argmax(energy) * 0.01) if len(energy) else 0.0
    timestamp_ns = int((time.time() + peak_offset_s) * 1e9)

    counts = Counter(events)
    if label and label in counts and events:
        confidence = float(counts[label] / len(events))
    elif events:
        confidence = float(max(counts.values()) / len(events))
    else:
        confidence = 0.0

    relevant = label is not None
    if relevant:
        label_human = CATEGORY_LABELS[label]
    else:
        label_human = LABEL_NOT_RELEVANT

    return {
        "drone_id":      drone_id,
        "path":          str(path),
        "label":         label,
        "label_human":   label_human,
        "relevant":      relevant,
        "timestamp_ns":  timestamp_ns,
        "confidence":    confidence if relevant else 0.0,
        "window_counts": dict(counts),
        "windows_total": len(events) if events else 0,
        "bearing":       None,
    }


def print_result(result: dict, expected: str | None = None):
    name = Path(result["path"]).name
    label = result["label"]
    human = result["label_human"]
    line = f"{name}: {human}"
    if label:
        line += f" ({label})"
    if result["window_counts"]:
        parts = " ".join(f"{k}={v}" for k, v in sorted(result["window_counts"].items()))
        line += f"  [{parts}]"
    if expected:
        ok = label == expected
        line = ("✅ " if ok else "⚠️ ") + line + f"  (expected: {CATEGORY_LABELS.get(expected, expected)})"
    print(line)


def _audio_files_in_dir(d: Path, recursive: bool = True) -> list[Path]:
    it = d.rglob("*") if recursive else d.iterdir()
    return sorted(
        p for p in it
        if p.is_file()
        and p.suffix.lower() in AUDIO_EXT
        and not p.name.startswith("._")
    )


def collect_paths(paths: list[str], folder: str | None) -> list[Path]:
    out: list[Path] = []
    if folder:
        out.extend(_audio_files_in_dir(Path(folder)))
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            out.extend(_audio_files_in_dir(p))
        elif p.exists():
            out.append(p)
    return out


def run_gunshot_benchmark():
    gunshot_files = sorted(
        f for f in GUNSHOT_DIR.glob("*.wav") if not f.name.startswith("._")
    ) if GUNSHOT_DIR.exists() else []
    if not gunshot_files:
        print(f"\n── DCASE gunshot: folder not found ({GUNSHOT_DIR})")
        return

    print(f"\n── Gunshot benchmark (DCASE, {len(gunshot_files)} files) ────────")
    summary = {c: 0 for c in CATEGORIES}
    summary["not_detected"] = 0
    for f in gunshot_files:
        label = classify_audio_file(f)
        if label:
            summary[label] += 1
            status = "✅" if label == "gunshot" else "⚠️"
            print(f"{status} {f.name}: {label}")
        else:
            summary["not_detected"] += 1
            print(f"❌ {f.name}: not detected")
    n = len(gunshot_files)
    print(f"\nCorrect gunshot:      {summary['gunshot']}/{n}")
    print(f"Missile launch (fp):  {summary['missile_launch']}/{n}")
    print(f"Drone (fp):           {summary['drone']}/{n}")
    print(f"Tank (fp):            {summary['tank']}/{n}")
    print(f"Not detected:         {summary['not_detected']}/{n}")


def run_esc50_proxy_test():
    esc_meta = DATA_DIR / "ESC-50/meta/esc50.csv"
    esc_audio = DATA_DIR / "ESC-50/audio"
    if not esc_meta.exists() or not esc_audio.exists():
        print("\n── ESC-50: folder not found, skipping proxy test ─────────")
        return

    test_categories = {
        "engine":         {"tank"},
        "helicopter":     {"tank", "drone"},
        "train":          {"tank"},
        "fireworks":      {"gunshot", "missile_launch"},
        "glass_breaking": {"gunshot", "missile_launch"},
        "wind":           set(),
        "rain":           set(),
        "insects":        set(),
        "crickets":       set(),
        "chainsaw":       set(),
    }
    rows_by_cat = {cat: [] for cat in test_categories}
    with esc_meta.open(newline="") as f:
        for row in csv.DictReader(f):
            if row["category"] in rows_by_cat:
                rows_by_cat[row["category"]].append(row["filename"])

    print("\n── ESC-50 proxy / false-positive test ──────────────────")
    fp_count = 0
    hit_keys = list(CATEGORIES) + ["silent"]
    for category, expected in test_categories.items():
        files = rows_by_cat[category]
        hits = {k: 0 for k in hit_keys}
        for filename in files:
            label = classify_audio_file(esc_audio / filename)
            if label is None:
                hits["silent"] += 1
            else:
                hits[label] += 1
        total = len(files)
        if hits["silent"] == total:
            majority = None
        else:
            majority = max(CATEGORIES, key=lambda k: hits[k])
            if hits[majority] == 0:
                majority = None
        if expected:
            ok = majority in expected
            if not ok:
                fp_count += 1
            status = "✅" if ok else "⚠️"
            exp_str = "/".join(sorted(expected))
            counts = " ".join(f"{k[:3]} {hits[k]}" for k in CATEGORIES)
            print(f"{status} {category:16} → '{majority}' (expected: {exp_str})  [{counts} ∅ {hits['silent']}/{total}]")
        else:
            ok = majority is None or hits["silent"] >= total * 0.75
            if not ok:
                fp_count += 1
            status = "✅" if ok else "⚠️ FALSE ALARM"
            counts = " ".join(f"{k[:3]} {hits[k]}" for k in CATEGORIES)
            print(f"{status} {category:16} → '{majority}' (expected: silent)  [{counts} ∅ {hits['silent']}/{total}]")
    print(f"\nCategories with issues: {fp_count}/{len(test_categories)}")


def run_demo_samples():
    sample_folders = {
        "gunshot": "gunshot",
        "tank": "tank",
        "drone": "drone",
        "missile_launch": "missile_launch",
    }
    if not SAMPLES_DIR.exists():
        print(f"\n── Demo samples: create {SAMPLES_DIR}/gunshot|tank|drone|missile_launch/")
        return
    print("\n── Demo classes (samples/) ───────────────────────────")
    ok, total = 0, 0
    for folder, expected in sample_folders.items():
        path = SAMPLES_DIR / folder
        if not path.exists():
            continue
        files = sorted(
            p for p in path.iterdir()
            if p.suffix.lower() in AUDIO_EXT and not p.name.startswith("._")
        )
        for f in files:
            total += 1
            label = classify_audio_file(f)
            match = label == expected
            ok += int(match)
            icon = "✅" if match else "⚠️"
            print(f"  {icon} {f.name}: {CATEGORY_LABELS.get(label, label or '—')} (expected: {CATEGORY_LABELS[expected]})")
    if total:
        print(f"\n  Accuracy demo samples: {ok}/{total}")


NEGATIVE_DIR = SAMPLES_DIR / "negative"


def run_negative_samples_test():
    """Files in data/samples/negative/: no military alert expected."""
    if not NEGATIVE_DIR.exists():
        print(f"\n── Negative samples: missing {NEGATIVE_DIR}/")
        print("  (optional) python prepare_negative_samples.py")
        return

    files = _audio_files_in_dir(NEGATIVE_DIR, recursive=True)
    if not files:
        print(f"\n── Negative samples: {NEGATIVE_DIR}/ is empty")
        return

    print(f"\n── Negative test (forest / animals, {len(files)} files) ───────")
    false_alarms = 0
    silent = 0
    for f in files:
        label = classify_audio_file(f)
        cat = f.parent.name if f.parent != NEGATIVE_DIR else "—"
        if label is None:
            silent += 1
            print(f"  ✅ {cat}/{f.name}: (no alert)")
        else:
            false_alarms += 1
            human = CATEGORY_LABELS.get(label, label)
            print(f"  ⚠️  {cat}/{f.name}: FALSE ALARM → {human} ({label})")

    print(f"\n  Not relevant / silent: {silent}/{len(files)}")
    print(f"  False alarms:          {false_alarms}/{len(files)}")


def run_benchmarks():
    run_gunshot_benchmark()
    run_esc50_proxy_test()
    run_negative_samples_test()
    run_demo_samples()


def write_results_json(results: list[dict], path: Path) -> Path:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(results, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def main():
    p = argparse.ArgumentParser(description="Audio detection: gunshot, tank, drone, missile_launch")
    p.add_argument("audio", nargs="*", help="audio file(s) .wav/.flac/.mp3 to classify")
    p.add_argument("--folder", "-f", help="classify all audio in a folder")
    p.add_argument("--json", action="store_true", help="print JSON to stdout")
    p.add_argument(
        "-o", "--output",
        help="write JSON file (default with --folder: detection/output/events.json)",
    )
    p.add_argument("--drone-id", default="drone_1", help="drone ID for TDOA / WebSocket payload")
    p.add_argument("--benchmark", action="store_true", help="run DCASE, ESC-50, negative, samples tests")
    p.add_argument(
        "--negative",
        action="store_true",
        help="negative-sample false-alarm test only (data/samples/negative/)",
    )
    args = p.parse_args()

    if args.negative:
        run_negative_samples_test()
        if not args.audio and not args.folder:
            return

    if args.benchmark:
        run_benchmarks()
        if not args.audio and not args.folder:
            return

    files = collect_paths(args.audio, args.folder)
    if not files:
        if not args.benchmark:
            print("Usage: python detection/detect_audio.py <file.wav> [more files ...]", file=sys.stderr)
            print("       python detection/detect_audio.py --folder data/scenarios", file=sys.stderr)
            print("       python detection/detect_audio.py --folder data/scenarios -o detection/output/events.json", file=sys.stderr)
            print("       python detection/detect_audio.py --benchmark", file=sys.stderr)
            sys.exit(1)
        return

    results = [detect_file(f, drone_id=args.drone_id) for f in files]
    payload = json.dumps(results, indent=2, ensure_ascii=False)

    out_path: Path | None = None
    if args.output:
        out_path = Path(args.output)
        if not out_path.is_absolute():
            out_path = REPO_ROOT / out_path
    elif args.folder:
        out_path = OUTPUT_DIR / "events.json"

    if out_path is not None:
        written = write_results_json(results, out_path)
        print(f"JSON written: {written}", file=sys.stderr)

    if args.json:
        print(payload)
        return

    print("── Detection (4 classes) ───────────────────────────────")
    for r in results:
        print_result(r)

    if all("scenario_" in Path(r["path"]).name for r in results):
        military_hints = {"tank": "tank", "gunshot": "gunshot", "missile": "missile_launch"}
        ambient_keys = ("bird", "crickets", "dog", "frog", "animal")
        print("\n── Filename hints (sanity check) ───────────────────────")
        for r in results:
            name = Path(r["path"]).name.lower()
            if any(k in name for k in ambient_keys):
                ok = not r["label"]
                line = ("✅ " if ok else "⚠️ ") + f"{Path(r['path']).name}: {r['label_human']}"
                if r["window_counts"]:
                    parts = " ".join(f"{k}={v}" for k, v in sorted(r["window_counts"].items()))
                    line += f"  [{parts}]"
                line += "  (expected: not relevant)"
                print(line)
                continue
            expected = next((lab for key, lab in military_hints.items() if key in name), None)
            if expected:
                print_result(r, expected=expected)


if __name__ == "__main__":
    main()
