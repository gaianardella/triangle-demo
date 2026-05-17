"""
Lightweight separation / enhancement before classification.

- enhance_hpss: always available (librosa HPSS → percussive stem, keeps transients)
- enhance_demucs: optional, needs `pip install demucs` and PyTorch

Usage:
  python detection/separate.py input.wav -o enhanced.wav
  python detection/separate.py input.wav -o out.wav --method demucs
"""

from __future__ import annotations

import argparse
from pathlib import Path

import librosa
import numpy as np
from scipy.io import wavfile


def enhance_hpss(audio: np.ndarray, sr: int, *, margin: float = 2.0) -> np.ndarray:
    """Percussive component — often preserves gunshots / impacts vs harmonic rotor hum."""
    harm, perc = librosa.effects.hpss(audio.astype(np.float64), margin=margin)
    out = perc.astype(np.float32)
    peak = float(np.max(np.abs(out)))
    if peak > 1e-9:
        out = out * (0.9 / peak)
    return out


def enhance_demucs(audio: np.ndarray, sr: int) -> np.ndarray:
    """
    Demucs 4-stem separation; return 'other' stem (residual not vocals/drums/bass).
    Requires: pip install demucs torch
    """
    import torch
    from demucs.pretrained import get_model
    from demucs.apply import apply_model

    model = get_model("htdemucs")
    model.eval()
    if sr != model.samplerate:
        audio = librosa.resample(audio, orig_sr=sr, target_sr=model.samplerate)
        sr = model.samplerate
    wav = torch.from_numpy(audio).float().unsqueeze(0).unsqueeze(0)
    with torch.no_grad():
        sources = apply_model(model, wav, device="cpu")[0]
    names = model.sources
    other_idx = names.index("other") if "other" in names else -1
    out = sources[other_idx].numpy().squeeze()
    if sr != 44100:
        pass
    peak = float(np.max(np.abs(out)))
    if peak > 1e-9:
        out = (out / peak * 0.9).astype(np.float32)
    return out


def enhance(audio: np.ndarray, sr: int, method: str = "hpss") -> np.ndarray:
    if method == "hpss":
        return enhance_hpss(audio, sr)
    if method == "demucs":
        return enhance_demucs(audio, sr)
    raise ValueError(f"unknown method: {method}")


def main() -> None:
    p = argparse.ArgumentParser(description="Separate / enhance audio for detection")
    p.add_argument("input", type=Path)
    p.add_argument("-o", "--output", type=Path, required=True)
    p.add_argument("--method", choices=("hpss", "demucs"), default="hpss")
    p.add_argument("--sr", type=int, default=22050)
    args = p.parse_args()

    y, sr = librosa.load(args.input, sr=args.sr, mono=True)
    out = enhance(y, sr, method=args.method)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(args.output), sr, out)
    print(f"Wrote {args.output} ({args.method})")


if __name__ == "__main__":
    main()
