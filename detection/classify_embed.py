"""
Embedding classifier (YAMNet) for military audio events.

Build class prototypes from data/samples/, then classify scenario mixes by
cosine similarity on sliding windows (generalizes better than hand-tuned RMS rules).

Usage (repo root, audio_env):
  pip install -r detection/requirements-ml.txt
  python detection/classify_embed.py --fit
  python detection/classify_embed.py data/scenarios/scenario_gunshot_mix.wav
  python detection/classify_embed.py --folder data/scenarios
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import librosa
import numpy as np

DETECTION_DIR = Path(__file__).resolve().parent
REPO_ROOT = DETECTION_DIR.parent
if str(DETECTION_DIR) not in sys.path:
    sys.path.insert(0, str(DETECTION_DIR))

DATA_DIR = REPO_ROOT / "data"
SAMPLES_DIR = DATA_DIR / "samples"
OUTPUT_DIR = DETECTION_DIR / "output"
PROTOTYPES_PATH = OUTPUT_DIR / "embed_prototypes.npz"
DEFAULT_CLEAN_EXPORT_DIR = OUTPUT_DIR / "ml_clean"

SR = 16_000  # YAMNet
WINDOW_SAMPLES = 15_600  # ~0.975 s
HOP_SAMPLES = 8_000  # 0.5 s

MILITARY_LABELS = ("gunshot", "missile_launch", "tank")
BACKGROUND_LABEL = "background"

TRAIN_DIRS: dict[str, tuple[str, ...]] = {
    "gunshot": ("gunshot",),
    "missile_launch": ("missile_launch", "explosion"),
    "tank": ("tank",),
    BACKGROUND_LABEL: ("forest", "drone"),
}

AUDIO_EXT = {".wav", ".flac", ".mp3", ".ogg"}
DCASE_GUNSHOT = DATA_DIR / (
    "DCASE2017/TUT-rare-sound-events-2017-development"
    "/data/source_data/events/gunshot"
)
DRONE_REF = SAMPLES_DIR / "drone" / "uas_drone_pass_dcpoke.wav"
FOREST_REF = SAMPLES_DIR / "forest"


def _list_audio(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(
        p for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in AUDIO_EXT and not p.name.startswith("._")
    )


def collect_training_paths() -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = {k: [] for k in (*MILITARY_LABELS, BACKGROUND_LABEL)}
    for label, subdirs in TRAIN_DIRS.items():
        for sub in subdirs:
            out[label].extend(_list_audio(SAMPLES_DIR / sub))
    if DCASE_GUNSHOT.exists():
        out["gunshot"].extend(_list_audio(DCASE_GUNSHOT)[:40])
    return out


def load_waveform(path: Path, sr: int = SR, max_duration_s: float | None = 30.0) -> np.ndarray:
    y, _ = librosa.load(path, sr=sr, mono=True, duration=max_duration_s)
    return y.astype(np.float32)


_yamnet_model: Any | None = None


def load_yamnet():
    global _yamnet_model
    if _yamnet_model is not None:
        return _yamnet_model
    import tensorflow_hub as hub

    _yamnet_model = hub.load("https://tfhub.dev/google/yamnet/1")
    return _yamnet_model


def embed_chunk(model: Any, chunk: np.ndarray) -> np.ndarray:
    if len(chunk) < WINDOW_SAMPLES:
        chunk = np.pad(chunk, (0, WINDOW_SAMPLES - len(chunk)))
    else:
        chunk = chunk[:WINDOW_SAMPLES]
    _, embeddings, _ = model(chunk)
    return embeddings.numpy().mean(axis=0)


def embed_waveform(model: Any, waveform: np.ndarray) -> np.ndarray:
    if len(waveform) < WINDOW_SAMPLES // 2:
        return np.zeros((0, 1024), dtype=np.float32)
    rows = []
    step = HOP_SAMPLES
    for start in range(0, len(waveform) - WINDOW_SAMPLES + 1, step):
        rows.append(embed_chunk(model, waveform[start : start + WINDOW_SAMPLES]))
    if not rows and len(waveform) >= WINDOW_SAMPLES // 2:
        rows.append(embed_chunk(model, waveform))
    return np.stack(rows, dtype=np.float32) if rows else np.zeros((0, 1024), dtype=np.float32)


def random_crops(waveform: np.ndarray, n_crops: int, crop_samples: int) -> list[np.ndarray]:
    if len(waveform) <= crop_samples:
        return [waveform]
    crops = []
    for _ in range(n_crops):
        start = int(np.random.randint(0, max(1, len(waveform) - crop_samples)))
        crops.append(waveform[start : start + crop_samples])
    return crops


def _normalize_rows(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True) + 1e-9
    return m / norms


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x.astype(np.float64) ** 2)) + 1e-12)


def _loop_to_length(x: np.ndarray, n: int) -> np.ndarray:
    if len(x) >= n:
        return x[:n]
    return np.tile(x, int(np.ceil(n / len(x))))[:n]


def _mix_uav_crop(
    event: np.ndarray,
    drone: np.ndarray,
    forest: np.ndarray | None,
    *,
    event_snr_db: float,
    forest_snr_db: float = -18.0,
) -> np.ndarray:
    """Synthetic UAV crop for training (event buried under rotor, like real scenarios)."""
    n = len(event)
    d = _loop_to_length(drone, n)
    mix = d.copy()
    er, dr = _rms(event), _rms(d)
    if dr > 1e-12 and er > 1e-12:
        gain = dr * (10 ** (event_snr_db / 20.0)) / er
        mix = mix + event.astype(np.float32) * gain
    if forest is not None and len(forest) > 0:
        f = _loop_to_length(forest, n)
        fr, dr2 = _rms(f), _rms(d)
        if dr2 > 1e-12 and fr > 1e-12:
            mix = mix + f * (dr2 * (10 ** (forest_snr_db / 20.0)) / fr)
    peak = float(np.max(np.abs(mix)))
    if peak > 0.99:
        mix = mix * (0.95 / peak)
    return mix.astype(np.float32)


def _vectors_from_waveform(
    model: Any,
    wav: np.ndarray,
    crops_per_file: int,
    crop_samples: int,
) -> list[np.ndarray]:
    vectors: list[np.ndarray] = []
    for crop in random_crops(wav, crops_per_file, crop_samples):
        embs = embed_waveform(model, crop)
        if len(embs):
            vectors.append(embs.mean(axis=0))
    return vectors


def fit_prototypes(
    *,
    crops_per_file: int = 6,
    crop_duration_s: float = 4.0,
    augment_mix: bool = True,
    mix_crops_per_file: int = 8,
    seed: int = 42,
    verbose: bool = True,
) -> dict[str, np.ndarray]:
    np.random.seed(seed)
    model = load_yamnet()
    paths_by_label = collect_training_paths()
    crop_samples = int(crop_duration_s * SR)
    prototypes: dict[str, np.ndarray] = {}

    drone_wav = load_waveform(DRONE_REF) if DRONE_REF.exists() else None
    forest_files = _list_audio(FOREST_REF) if FOREST_REF.is_dir() else []
    forest_wav = load_waveform(forest_files[0]) if forest_files else None

    for label in (*MILITARY_LABELS, BACKGROUND_LABEL):
        files = paths_by_label[label]
        if not files:
            if verbose:
                print(f"  skip {label}: no training files", file=sys.stderr)
            continue
        vectors: list[np.ndarray] = []
        for path in files:
            wav = load_waveform(path)
            vectors.extend(_vectors_from_waveform(model, wav, crops_per_file, crop_samples))
            if (
                augment_mix
                and label in MILITARY_LABELS
                and drone_wav is not None
            ):
                for _ in range(mix_crops_per_file):
                    crop = random_crops(wav, 1, crop_samples)[0]
                    snr = float(np.random.uniform(-14.0, -4.0))
                    mixed = _mix_uav_crop(crop, drone_wav, forest_wav, event_snr_db=snr)
                    vectors.extend(_vectors_from_waveform(model, mixed, 2, crop_samples))
        if label == BACKGROUND_LABEL and drone_wav is not None:
            vectors.extend(_vectors_from_waveform(model, drone_wav, crops_per_file, crop_samples))
            if forest_wav is not None:
                vectors.extend(_vectors_from_waveform(model, forest_wav, crops_per_file, crop_samples))
        if not vectors:
            continue
        proto = np.mean(np.stack(vectors), axis=0)
        proto /= np.linalg.norm(proto) + 1e-9
        prototypes[label] = proto.astype(np.float32)
        n_aug = " + UAV mix aug" if augment_mix and label in MILITARY_LABELS else ""
        if verbose:
            print(
                f"  {label}: {len(files)} files{n_aug} → prototype dim {proto.shape[0]}",
                file=sys.stderr,
            )

    if not prototypes:
        raise RuntimeError(
            "No prototypes built. Add clips under data/samples/{gunshot,tank,missile_launch,forest,drone}/"
        )
    return prototypes


def save_prototypes(prototypes: dict[str, np.ndarray], path: Path = PROTOTYPES_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **prototypes)
    return path


def load_prototypes(path: Path = PROTOTYPES_PATH) -> dict[str, np.ndarray]:
    if not path.exists():
        raise FileNotFoundError(
            f"Missing {path}. Run: python detection/classify_embed.py --fit"
        )
    data = np.load(path)
    return {k: data[k].astype(np.float32) for k in data.files}


def _similarity_matrix(
    embeddings: np.ndarray,
    prototypes: dict[str, np.ndarray],
) -> tuple[list[str], np.ndarray]:
    labels = list(prototypes.keys())
    P = np.stack([prototypes[k] for k in labels])
    E = _normalize_rows(embeddings)
    return labels, E @ P.T


def window_scores(
    embeddings: np.ndarray,
    prototypes: dict[str, np.ndarray],
) -> list[tuple[str, float]]:
    if len(embeddings) == 0:
        return []
    labels, sims = _similarity_matrix(embeddings, prototypes)
    return [(labels[int(np.argmax(row))], float(row[np.argmax(row)])) for row in sims]


def aggregate_label(
    embeddings: np.ndarray,
    prototypes: dict[str, np.ndarray],
    *,
    sim_threshold: float = 0.22,
    bg_margin: float = 0.02,
    min_military_windows: int = 2,
) -> str | None:
    if len(embeddings) == 0:
        return None

    labels, sims = _similarity_matrix(embeddings, prototypes)
    idx = {l: i for i, l in enumerate(labels)}
    if BACKGROUND_LABEL not in idx:
        return None

    qualifying: list[tuple[str, float]] = []
    for row in sims:
        bg = float(row[idx[BACKGROUND_LABEL]])
        best_lab, best_sim = None, -1.0
        for lab in MILITARY_LABELS:
            if lab not in idx:
                continue
            s = float(row[idx[lab]])
            if s > best_sim:
                best_lab, best_sim = lab, s
        if best_lab and best_sim >= sim_threshold and best_sim >= bg + bg_margin:
            qualifying.append((best_lab, best_sim))

    if len(qualifying) < min_military_windows:
        return None

    counts = Counter(l for l, _ in qualifying)
    best_label, _ = counts.most_common(1)[0]
    mean_sim = float(np.mean([s for l, s in qualifying if l == best_label]))
    if mean_sim < sim_threshold:
        return None
    return best_label


def classify_waveform(
    waveform: np.ndarray,
    prototypes: dict[str, np.ndarray],
    model: Any | None = None,
    *,
    sim_threshold: float = 0.35,
) -> tuple[str | None, dict[str, Any]]:
    if model is None:
        model = load_yamnet()
    embs = embed_waveform(model, waveform)
    preds = window_scores(embs, prototypes)
    label = aggregate_label(embs, prototypes, sim_threshold=sim_threshold)
    counts = Counter(l for l, _ in preds)
    return label, {
        "window_counts": dict(counts),
        "windows_total": len(preds),
        "qualifying_military_windows": sum(
            1 for l, _ in preds if l in MILITARY_LABELS
        ),
    }


def prepare_waveform_for_ml(
    path: Path,
    *,
    denoise: bool = False,
    enhance: str | None = None,
) -> np.ndarray:
    """Audio exactly as fed to YAMNet (16 kHz, optional HPSS + drone_denoise)."""
    wav = load_waveform(path, max_duration_s=None)

    if denoise or enhance:
        from drone_denoise import load_drone_reference, preprocess_for_detection

        if enhance == "hpss":
            from separate import enhance_hpss

            wav = enhance_hpss(wav, SR)
        if denoise:
            ref_path = SAMPLES_DIR / "drone" / "uas_drone_pass_dcpoke.wav"
            ref = load_drone_reference(ref_path, SR, len(wav)) if ref_path.exists() else None
            wav = preprocess_for_detection(wav, SR, ref)

    return wav


def export_clean_audio(
    path: Path,
    export_root: Path,
    *,
    denoise: bool = False,
    enhance: str | None = None,
    filename: str = "clean.wav",
) -> Path:
    """Save ML pre-classification audio under export_root/<scenario_stem>/clean.wav."""
    wav = prepare_waveform_for_ml(path, denoise=denoise, enhance=enhance)
    out_dir = export_root / path.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / filename
    from scipy.io import wavfile

    wavfile.write(str(out_path), SR, np.clip(wav, -1.0, 1.0))
    return out_path


def classify_file(
    path: Path,
    prototypes: dict[str, np.ndarray] | None = None,
    *,
    denoise: bool = False,
    enhance: str | None = None,
    sim_threshold: float = 0.22,
    waveform: np.ndarray | None = None,
) -> tuple[str | None, dict[str, Any]]:
    if prototypes is None:
        prototypes = load_prototypes()
    wav = (
        waveform
        if waveform is not None
        else prepare_waveform_for_ml(path, denoise=denoise, enhance=enhance)
    )

    model = load_yamnet()
    label, meta = classify_waveform(wav, prototypes, model, sim_threshold=sim_threshold)
    meta["path"] = str(path)
    return label, meta


def main() -> None:
    p = argparse.ArgumentParser(description="YAMNet embedding classifier")
    p.add_argument("audio", nargs="*", help="audio file(s)")
    p.add_argument("--folder", "-f", help="folder of wav files")
    p.add_argument("--fit", action="store_true", help="train prototypes from data/samples/")
    p.add_argument(
        "--no-augment-mix",
        action="store_true",
        help="disable synthetic UAV mix augmentation during --fit",
    )
    p.add_argument("--denoise", action="store_true", help="drone_denoise before embedding")
    p.add_argument("--hpss", action="store_true", help="HPSS percussive stem before embedding")
    p.add_argument("--sim-threshold", type=float, default=0.22)
    p.add_argument("-o", "--output", type=Path, help="write JSON results")
    p.add_argument(
        "--export-clean",
        nargs="?",
        const=str(DEFAULT_CLEAN_EXPORT_DIR),
        default=None,
        metavar="DIR",
        help=(
            "save pre-YAMNet audio per file under DIR/<scenario_stem>/clean.wav "
            f"(default dir: {DEFAULT_CLEAN_EXPORT_DIR.relative_to(REPO_ROOT)}); "
            "use with --denoise for UAV-cleaned clips"
        ),
    )
    args = p.parse_args()

    if args.fit:
        protos = fit_prototypes(augment_mix=not args.no_augment_mix, verbose=True)
        out = save_prototypes(protos)
        print(f"Prototypes saved: {out}")
        if not args.audio and not args.folder:
            return

    paths: list[Path] = []
    if args.folder:
        paths.extend(_list_audio(Path(args.folder)))
    for raw in args.audio:
        pth = Path(raw)
        if pth.is_dir():
            paths.extend(_list_audio(pth))
        elif pth.exists():
            paths.append(pth)

    if not paths and not args.fit:
        p.error("provide --fit and/or audio paths")

    enhance = "hpss" if args.hpss else None
    export_root = Path(args.export_clean) if args.export_clean else None
    if export_root and not export_root.is_absolute():
        export_root = REPO_ROOT / export_root

    results = []
    for path in paths:
        wav: np.ndarray | None = None
        clean_path: str | None = None
        if export_root is not None:
            wav = prepare_waveform_for_ml(path, denoise=args.denoise, enhance=enhance)
            out_dir = export_root / path.stem
            out_dir.mkdir(parents=True, exist_ok=True)
            written = out_dir / "clean.wav"
            from scipy.io import wavfile

            wavfile.write(str(written), SR, np.clip(wav, -1.0, 1.0))
            clean_path = str(written)
            print(f"  clean audio: {written}", file=sys.stderr)

        label, meta = classify_file(
            path,
            denoise=args.denoise,
            enhance=enhance,
            sim_threshold=args.sim_threshold,
            waveform=wav,
        )
        if clean_path:
            meta["clean_audio_path"] = clean_path
        human = label or "not relevant"
        print(f"{path.name}: {human}  {meta.get('window_counts', {})}")
        results.append({"path": str(path), "label": label, **meta})

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
