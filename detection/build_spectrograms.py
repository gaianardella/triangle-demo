"""
Pre-render spectrograms + classifier verdicts for movie/Scene3.

Each scenario gets trimmed to AUDIO_LEN_SEC starting at scenario.start_sec.
The first SWITCH_SEC seconds plays the raw mix; the rest plays the preprocessed audio.

Writes to movie/public/spectrograms/:
  <id>_raw.png      mel spectrogram of the trimmed raw mix
  <id>_pre.png      mel spectrogram of the trimmed preprocessed audio
  <id>_raw.wav      PCM16 trimmed raw mix (Remotion <Audio>)
  <id>_pre.wav      PCM16 trimmed preprocessed (Remotion <Audio>)
  manifest.json     per-scenario metadata + classifier verdict

Run:
  uv run --with librosa --with matplotlib --with scipy --with numpy --with soundfile \\
         detection/build_spectrograms.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import librosa
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import soundfile as sf
from matplotlib.colors import LinearSegmentedColormap

sys.path.insert(0, str(Path(__file__).resolve().parent))
from detect_audio import (  # noqa: E402
    HOP_SIZE,
    WINDOW_SIZE,
    classify_chunk,
    detect_file,
    prepare_audio_for_detection,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCEN_DIR = REPO_ROOT / "data" / "scenarios"
OUT_DIR = REPO_ROOT / "movie" / "public" / "spectrograms"
SRC_MANIFEST = REPO_ROOT / "movie" / "src" / "spectrogramManifest.json"

SR = 22050
FPS = 30
PNG_W, PNG_H = 1120, 360
DPI = 100
HEAD_FRAMES = 15
TAIL_FRAMES = 0

AUDIO_LEN_SEC = 7.0
SWITCH_SEC = 2.0

HUD_CMAP = LinearSegmentedColormap.from_list(
    "hud",
    [
        (0.00, "#000000"),
        (0.25, "#06120c"),
        (0.55, "#13402b"),
        (0.80, "#3eaa78"),
        (1.00, "#a8ffd1"),
    ],
)

SCENARIOS = [
    {"id": "tank",    "title": "TANK ENGINE under DRONE BUZZ",    "start_sec": 0.0},
    {"id": "gunshot", "title": "GUNSHOT under DRONE BUZZ",        "start_sec": 3.0},
    {"id": "missile", "title": "MISSILE LAUNCH under DRONE BUZZ", "start_sec": 11.0},
]


def best_window_time(
    audio: np.ndarray, sr: int, target_label: str, apply_wind_hp: bool,
) -> float | None:
    """Time (s, center) of the highest-confidence window matching target_label."""
    ws = int(WINDOW_SIZE * sr)
    hop = int(HOP_SIZE * sr)
    best_conf = -1.0
    best_center = None
    for start in range(0, len(audio) - ws, hop):
        chunk = audio[start:start + ws]
        label, conf = classify_chunk(chunk, sr, apply_wind_hp=apply_wind_hp)
        if label == target_label and conf > best_conf:
            best_conf = conf
            best_center = (start + ws / 2) / sr
    return best_center


def per_window_scan(audio: np.ndarray, sr: int, apply_wind_hp: bool) -> tuple[dict[str, float], dict[str, int], int]:
    ws = int(WINDOW_SIZE * sr)
    hop = int(HOP_SIZE * sr)
    peaks: dict[str, float] = {}
    counts: dict[str, int] = {}
    total = 0
    for start in range(0, len(audio) - ws, hop):
        total += 1
        label, conf = classify_chunk(audio[start:start + ws], sr, apply_wind_hp=apply_wind_hp)
        if label:
            peaks[label] = max(peaks.get(label, 0.0), float(conf))
            counts[label] = counts.get(label, 0) + 1
    return peaks, counts, total


def render_spectrogram_array(audio: np.ndarray, sr: int, png_path: Path) -> None:
    mel = librosa.feature.melspectrogram(
        y=audio, sr=sr, n_mels=128, hop_length=512, fmin=30, fmax=sr // 2,
    )
    mel_db = librosa.power_to_db(mel, ref=np.max)

    fig = plt.figure(figsize=(PNG_W / DPI, PNG_H / DPI), dpi=DPI)
    ax = fig.add_axes((0.0, 0.0, 1.0, 1.0))
    ax.imshow(
        mel_db,
        origin="lower",
        aspect="auto",
        cmap=HUD_CMAP,
        vmin=-80,
        vmax=0,
        interpolation="nearest",
    )
    ax.set_axis_off()
    fig.savefig(png_path, dpi=DPI, facecolor="black", pad_inches=0)
    plt.close(fig)


def trim_audio(audio: np.ndarray, sr: int, start_sec: float, length_sec: float) -> np.ndarray:
    start_sample = int(start_sec * sr)
    n = int(length_sec * sr)
    clip = audio[start_sample:start_sample + n]
    if len(clip) < n:
        clip = np.pad(clip, (0, n - len(clip)))
    return clip


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "fps": FPS,
        "headFrames": HEAD_FRAMES,
        "tailFrames": TAIL_FRAMES,
        "switchSec": SWITCH_SEC,
        "audioLenSec": AUDIO_LEN_SEC,
        "scenarios": [],
        "totalFrames": 0,
    }
    total = 0

    for s in SCENARIOS:
        sid = s["id"]
        start_sec = float(s["start_sec"])
        mix = SCEN_DIR / f"scenario_{sid}_mix.wav"
        pre = SCEN_DIR / f"scenario_{sid}_preprocessed.wav"
        if not mix.exists() or not pre.exists():
            print(f"skip {sid}: missing {mix.name} or {pre.name}", file=sys.stderr)
            continue

        verdict = detect_file(pre, denoise=False)
        if isinstance(verdict, list):
            verdict = verdict[0]

        full_pre, sr = librosa.load(pre, sr=SR)
        full_mix, _ = librosa.load(mix, sr=SR)

        prepared, apply_wind_hp = prepare_audio_for_detection(full_pre, sr, denoise=False)
        peaks, counts, total_windows = per_window_scan(prepared, sr, apply_wind_hp)

        label = verdict.get("label")
        share_conf = float(verdict.get("confidence") or 0.0)
        peak_conf = float(peaks.get(label, share_conf)) if label else 0.0
        silent = max(0, total_windows - sum(counts.values()))

        # Verdict trigger time relative to the trimmed clip.
        if sid == "tank":
            full_trigger_sec = start_sec + 3.0
        else:
            best = best_window_time(prepared, sr, label or "", apply_wind_hp)
            full_trigger_sec = float(best) if best is not None else start_sec + 1.0
        trimmed_trigger_sec = max(0.0, min(AUDIO_LEN_SEC, full_trigger_sec - start_sec))
        verdict_at_frame = HEAD_FRAMES + int(round(trimmed_trigger_sec * FPS))

        # Trim both audios to the same window.
        trimmed_mix = trim_audio(full_mix, sr, start_sec, AUDIO_LEN_SEC)
        trimmed_pre = trim_audio(full_pre, sr, start_sec, AUDIO_LEN_SEC)

        raw_wav = OUT_DIR / f"{sid}_raw.wav"
        pre_wav = OUT_DIR / f"{sid}_pre.wav"
        sf.write(raw_wav, trimmed_mix, sr, subtype="PCM_16")
        sf.write(pre_wav, trimmed_pre, sr, subtype="PCM_16")

        raw_png = OUT_DIR / f"{sid}_raw.png"
        pre_png = OUT_DIR / f"{sid}_pre.png"
        render_spectrogram_array(trimmed_mix, sr, raw_png)
        render_spectrogram_array(trimmed_pre, sr, pre_png)

        scen_frames = HEAD_FRAMES + int(round(AUDIO_LEN_SEC * FPS)) + TAIL_FRAMES
        switch_at_frame = HEAD_FRAMES + int(round(SWITCH_SEC * FPS))

        entry = {
            "id": sid,
            "title": s["title"],
            "rawPng": raw_png.name,
            "prePng": pre_png.name,
            "rawAudio": raw_wav.name,
            "preAudio": pre_wav.name,
            "audioStartSec": round(start_sec, 3),
            "durationSec": round(AUDIO_LEN_SEC, 3),
            "startFrame": total,
            "frames": scen_frames,
            "switchAtFrame": switch_at_frame,
            "verdictAtFrame": verdict_at_frame,
            "verdictTriggerSec": round(trimmed_trigger_sec, 3),
            "verdict": {
                "label": label,
                "labelHuman": verdict.get("label_human"),
                "relevant": bool(verdict.get("relevant")),
                "peakConfidence": round(peak_conf, 3),
                "shareConfidence": round(share_conf, 3),
                "windowCounts": counts,
                "silentWindows": silent,
                "totalWindows": total_windows,
            },
        }
        manifest["scenarios"].append(entry)
        total += scen_frames
        breakdown = " ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "—"
        print(
            f"{sid}: start@{start_sec:.1f}s len={AUDIO_LEN_SEC:.1f}s -> {scen_frames} frames | "
            f"trigger@{trimmed_trigger_sec:.2f}s (frame {verdict_at_frame}) | "
            f"label={entry['verdict']['labelHuman']!r} peak={peak_conf:.3f} "
            f"share={share_conf:.3f} | windows[{breakdown} silent={silent}/{total_windows}]",
            file=sys.stderr,
        )

    manifest["totalFrames"] = total
    payload = json.dumps(manifest, indent=2) + "\n"
    (OUT_DIR / "manifest.json").write_text(payload)
    SRC_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    SRC_MANIFEST.write_text(payload)
    print(
        f"wrote {len(manifest['scenarios'])} scenarios, total {total} frames\n"
        f"  -> {OUT_DIR}/manifest.json\n"
        f"  -> {SRC_MANIFEST}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
