"""
Classifica un file audio in una delle 4 categorie:
  gunshot | missile_launch | drone | tank

Uso:
  conda activate audio_env
  python detect_audio.py data/scenarios/scenario_tank_mix.wav
  python detect_audio.py --folder data/scenarios
  python detect_audio.py --folder data/scenarios --json
  python detect_audio.py --folder data/scenarios -o data/scenarios/detections.json
  python detect_audio.py --benchmark   # test DCASE / ESC-50 / samples
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

DATA_DIR = Path(__file__).resolve().parent / "data"
SAMPLES_DIR = DATA_DIR / "samples"

CATEGORIES = ("gunshot", "missile_launch", "drone", "tank")
CATEGORY_LABELS = {
    "gunshot":        "Arma leggera",
    "missile_launch": "Lancio missile / UCAS",
    "drone":          "UAV / drone",
    "tank":           "Motore carro armato",
}

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
    """Lancio / boost / UCAS: impulso medio-lungo o boost sepolto nel mix UAV."""
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
    """Classifica finestre centrate sui massimi di salita energetica (eventi rari nel mix)."""
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
    return dominant_detection(sliding, onsets)


def classify_audio_file(audio_path, sr=SR):
    audio, sr = librosa.load(audio_path, sr=sr)
    return classify_audio_array(audio, sr)


# ── CLI / integrazione ──────────────────────────────────────────────────────
def detect_file(path: Path, drone_id: str = "drone_1") -> dict:
    audio, sr = librosa.load(path, sr=SR)
    events = scan_audio_for_events(audio, sr)
    label = classify_audio_array(audio, sr)

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

    return {
        "drone_id":      drone_id,
        "path":           str(path),
        "label":          label,
        "label_human":    CATEGORY_LABELS.get(label, label or "silenzio"),
        "timestamp_ns":   timestamp_ns,
        "confidence":     confidence,
        "window_counts":  dict(counts),
        "windows_total":  len(events) if events else 0,
        "bearing":        None,
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
        line = ("✅ " if ok else "⚠️ ") + line + f"  (atteso: {CATEGORY_LABELS.get(expected, expected)})"
    print(line)


def collect_paths(paths: list[str], folder: str | None) -> list[Path]:
    out: list[Path] = []
    if folder:
        d = Path(folder)
        out.extend(
            sorted(
                p for p in d.iterdir()
                if p.suffix.lower() in AUDIO_EXT and not p.name.startswith("._")
            )
        )
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            out.extend(
                sorted(
                    x for x in p.iterdir()
                    if x.suffix.lower() in AUDIO_EXT and not x.name.startswith("._")
                )
            )
        elif p.exists():
            out.append(p)
    return out


def run_gunshot_benchmark():
    gunshot_files = sorted(
        f for f in GUNSHOT_DIR.glob("*.wav") if not f.name.startswith("._")
    ) if GUNSHOT_DIR.exists() else []
    if not gunshot_files:
        print(f"\n── DCASE gunshot: cartella non trovata ({GUNSHOT_DIR})")
        return

    print(f"\n── Benchmark gunshot (DCASE, {len(gunshot_files)} file) ─────────")
    summary = {c: 0 for c in CATEGORIES}
    summary["non rilevato"] = 0
    for f in gunshot_files:
        label = classify_audio_file(f)
        if label:
            summary[label] += 1
            status = "✅" if label == "gunshot" else "⚠️"
            print(f"{status} {f.name}: {label}")
        else:
            summary["non rilevato"] += 1
            print(f"❌ {f.name}: non rilevato")
    n = len(gunshot_files)
    print(f"\nGunshot corretto:     {summary['gunshot']}/{n}")
    print(f"Missile launch (fp):  {summary['missile_launch']}/{n}")
    print(f"Drone (fp):           {summary['drone']}/{n}")
    print(f"Tank (fp):            {summary['tank']}/{n}")
    print(f"Non rilevato:         {summary['non rilevato']}/{n}")


def run_esc50_proxy_test():
    esc_meta = DATA_DIR / "ESC-50/meta/esc50.csv"
    esc_audio = DATA_DIR / "ESC-50/audio"
    if not esc_meta.exists() or not esc_audio.exists():
        print("\n── ESC-50: cartella non trovata, salto test proxy ────────")
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

    print("\n── Test proxy / falsi positivi (ESC-50) ─────────────────")
    fp_count = 0
    hit_keys = list(CATEGORIES) + ["silenzio"]
    for category, expected in test_categories.items():
        files = rows_by_cat[category]
        hits = {k: 0 for k in hit_keys}
        for filename in files:
            label = classify_audio_file(esc_audio / filename)
            if label is None:
                hits["silenzio"] += 1
            else:
                hits[label] += 1
        total = len(files)
        if hits["silenzio"] == total:
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
            print(f"{status} {category:16} → '{majority}' (atteso: {exp_str})  [{counts} ∅ {hits['silenzio']}/{total}]")
        else:
            ok = majority is None or hits["silenzio"] >= total * 0.75
            if not ok:
                fp_count += 1
            status = "✅" if ok else "⚠️ FALSO ALLARME"
            counts = " ".join(f"{k[:3]} {hits[k]}" for k in CATEGORIES)
            print(f"{status} {category:16} → '{majority}' (atteso: silenzio)  [{counts} ∅ {hits['silenzio']}/{total}]")
    print(f"\nCategorie con problemi: {fp_count}/{len(test_categories)}")


def run_demo_samples():
    sample_folders = {
        "gunshot": "gunshot",
        "tank": "tank",
        "drone": "drone",
        "missile_launch": "missile_launch",
    }
    if not SAMPLES_DIR.exists():
        print(f"\n── Demo samples: crea {SAMPLES_DIR}/gunshot|tank|drone|missile_launch/")
        return
    print("\n── Demo classi (samples/) ─────────────────────────────")
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
            print(f"  {icon} {f.name}: {CATEGORY_LABELS.get(label, label or '—')} (atteso: {CATEGORY_LABELS[expected]})")
    if total:
        print(f"\n  Accuracy demo samples: {ok}/{total}")


def run_benchmarks():
    run_gunshot_benchmark()
    run_esc50_proxy_test()
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
    p = argparse.ArgumentParser(description="Detection audio: gunshot, tank, drone, missile_launch")
    p.add_argument("audio", nargs="*", help="file .wav/.flac/.mp3 da classificare")
    p.add_argument("--folder", "-f", help="classifica tutti gli audio in una cartella")
    p.add_argument("--json", action="store_true", help="stampa JSON su stdout")
    p.add_argument(
        "-o", "--output",
        help="salva JSON su file (default con --folder: <cartella>/detections.json)",
    )
    p.add_argument("--drone-id", default="drone_1", help="ID drone per payload TDOA/WebSocket")
    p.add_argument("--benchmark", action="store_true", help="test DCASE gunshot, ESC-50, samples/")
    args = p.parse_args()

    if args.benchmark:
        run_benchmarks()
        if not args.audio and not args.folder:
            return

    files = collect_paths(args.audio, args.folder)
    if not files:
        if not args.benchmark:
            print("Uso: python detect_audio.py <file.wav> [altri file ...]", file=sys.stderr)
            print("     python detect_audio.py --folder data/scenarios", file=sys.stderr)
            print("     python detect_audio.py --folder data/scenarios --json -o data/scenarios/detections.json", file=sys.stderr)
            print("     python detect_audio.py --benchmark", file=sys.stderr)
            sys.exit(1)
        return

    results = [detect_file(f, drone_id=args.drone_id) for f in files]
    payload = json.dumps(results, indent=2, ensure_ascii=False)

    out_path: Path | None = None
    if args.output:
        out_path = Path(args.output)
    elif args.folder and args.json:
        out_path = Path(args.folder) / "detections.json"

    if out_path is not None:
        written = write_results_json(results, out_path)
        print(f"JSON salvato: {written}", file=sys.stderr)

    if args.json:
        print(payload)
        return

    print("── Detection (4 classi) ────────────────────────────────")
    for r in results:
        print_result(r)

    if all("scenario_" in Path(r["path"]).name for r in results):
        hints = {"tank": "tank", "gunshot": "gunshot", "missile": "missile_launch"}
        print("\n── Confronto nome file (solo hint) ─────────────────────")
        for r in results:
            name = Path(r["path"]).name.lower()
            expected = next((lab for key, lab in hints.items() if key in name), None)
            if expected:
                print_result(r, expected=expected)


if __name__ == "__main__":
    main()
