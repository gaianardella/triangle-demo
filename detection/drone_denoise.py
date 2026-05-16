"""
Front-end denoise for UAV listening: notch harmonics, adaptive spectral subtraction, REPET.

Used before windowed classification in detect_audio.py.
"""

from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np
from scipy import signal
from scipy.ndimage import median_filter

DEFAULT_RPM = 6000
N_FFT = 2048


def loop_to_length(x: np.ndarray, n: int) -> np.ndarray:
    if len(x) >= n:
        return x[:n]
    reps = int(np.ceil(n / len(x)))
    return np.tile(x, reps)[:n]


def cancel_rotor_noise(
    audio: np.ndarray,
    sr: int,
    rpm: float = DEFAULT_RPM,
    n_harmonics: int = 8,
    Q: float = 30.0,
) -> np.ndarray:
    """Notch filter at blade-pass frequency harmonics."""
    fundamental = (rpm / 60.0) * 2
    filtered = audio.astype(np.float64).copy()
    for h in range(1, n_harmonics + 1):
        freq = fundamental * h
        if freq >= sr / 2 - 50:
            break
        b, a = signal.iirnotch(w0=freq, Q=Q, fs=sr)
        filtered = signal.filtfilt(b, a, filtered)
    return filtered.astype(np.float32)


def spectral_subtract_drone(
    mixture: np.ndarray,
    drone_ref: np.ndarray,
    sr: int,
    *,
    alpha: float = 0.45,
    floor: float = 0.12,
    n_fft: int = N_FFT,
) -> np.ndarray:
    """Static magnitude subtraction using a drone-only reference STFT."""
    hop = n_fft // 4
    ref = loop_to_length(drone_ref, len(mixture))

    def stft_mag(y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        S = librosa.stft(y, n_fft=n_fft, hop_length=hop)
        return np.abs(S), S

    mag_mix, S_mix = stft_mag(mixture)
    mag_ref, _ = stft_mag(ref)
    mag_clean = np.maximum(mag_mix - alpha * mag_ref, floor * mag_mix)
    S_clean = mag_clean * np.exp(1j * np.angle(S_mix))
    return librosa.istft(S_clean, hop_length=hop, length=len(mixture)).astype(np.float32)


def adaptive_spectral_subtract(
    mixture: np.ndarray,
    sr: int,
    drone_ref: np.ndarray | None = None,
    *,
    alpha: float = 0.5,
    floor: float = 0.12,
    update_interval_s: float = 1.0,
    noise_decay: float = 0.82,
    n_fft: int = N_FFT,
) -> np.ndarray:
    """
    Subtract a time-varying noise magnitude profile (rotor hum), updated every ~1 s.
    Initial profile from drone reference STFT or the first block of the mixture.
    """
    hop = n_fft // 4
    S = librosa.stft(mixture, n_fft=n_fft, hop_length=hop)
    mag = np.abs(S)
    phase = np.angle(S)
    n_frames = mag.shape[1]
    block_frames = max(1, int(update_interval_s * sr / hop))

    if drone_ref is not None:
        ref = loop_to_length(drone_ref, len(mixture))
        ref_mag = np.abs(librosa.stft(ref, n_fft=n_fft, hop_length=hop))
        noise = np.median(ref_mag, axis=1, keepdims=True)
    else:
        end = min(block_frames, n_frames)
        noise = np.median(mag[:, :end], axis=1, keepdims=True)

    mag_out = np.empty_like(mag)
    for start in range(0, n_frames, block_frames):
        end = min(start + block_frames, n_frames)
        block = mag[:, start:end]
        block_est = np.median(block, axis=1, keepdims=True)
        noise = noise_decay * noise + (1.0 - noise_decay) * block_est
        mag_out[:, start:end] = np.maximum(block - alpha * noise, floor * block)

    S_clean = mag_out * np.exp(1j * phase)
    return librosa.istft(S_clean, hop_length=hop, length=len(mixture)).astype(np.float32)


def repet_suppress_periodic(
    audio: np.ndarray,
    sr: int,
    *,
    time_width: int = 17,
    strength: float = 0.88,
    floor: float = 0.14,
    n_fft: int = N_FFT,
) -> np.ndarray:
    """
    REPET-style separation: median filter along time → repeating (rotor) part, keep transients.
    """
    hop = n_fft // 4
    S = librosa.stft(audio, n_fft=n_fft, hop_length=hop)
    mag = np.abs(S)
    periodic = median_filter(mag, size=(1, time_width))
    transient_mag = np.maximum(mag - strength * periodic, floor * mag)
    S_out = transient_mag * np.exp(1j * np.angle(S))
    return librosa.istft(S_out, hop_length=hop, length=len(audio)).astype(np.float32)


def highpass_wind(audio: np.ndarray, sr: int, cutoff_hz: float = 200.0) -> np.ndarray:
    b, a = signal.butter(4, cutoff_hz / (sr / 2), btype="high")
    return signal.filtfilt(b, a, audio.astype(np.float64)).astype(np.float32)


def normalize_peak(audio: np.ndarray, target: float = 0.9) -> np.ndarray:
    peak = float(np.max(np.abs(audio)))
    if peak < 1e-9:
        return audio
    return (audio / peak * target).astype(np.float32)


def preprocess_for_detection(
    mixture: np.ndarray,
    sr: int,
    drone_ref: np.ndarray | None = None,
    *,
    rpm: float = DEFAULT_RPM,
    use_adaptive_subtract: bool = True,
    use_repet: bool = True,
) -> np.ndarray:
    """Full UAV listen chain: notch → spectral sub → REPET → high-pass → normalize."""
    x = cancel_rotor_noise(mixture, sr, rpm=rpm)
    if drone_ref is not None:
        if use_adaptive_subtract:
            x = adaptive_spectral_subtract(x, sr, drone_ref)
        else:
            x = spectral_subtract_drone(x, drone_ref, sr)
    if use_repet:
        x = repet_suppress_periodic(x, sr)
    x = highpass_wind(x, sr)
    return normalize_peak(x)


def load_drone_reference(path: Path, sr: int, n_samples: int) -> np.ndarray | None:
    if not path.exists():
        return None
    y, _ = librosa.load(path, sr=sr, duration=n_samples / sr if n_samples else None)
    return loop_to_length(y, n_samples) if n_samples else y


def main() -> None:
    import argparse
    from scipy.io import wavfile

    p = argparse.ArgumentParser(description="Denoise UAV mix (notch + spectral sub + REPET)")
    p.add_argument("input", type=Path)
    p.add_argument("-o", "--output", type=Path, required=True)
    p.add_argument("--ref", type=Path, help="drone-only reference WAV")
    p.add_argument("--sr", type=int, default=22050)
    args = p.parse_args()

    y, sr = librosa.load(args.input, sr=args.sr)
    ref = None
    if args.ref and args.ref.exists():
        ref = load_drone_reference(args.ref, sr, len(y))
    out = preprocess_for_detection(y, sr, ref)
    wavfile.write(args.output, sr, out)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
