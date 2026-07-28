"""Comparação acústica leve para hospedagem gratuita.

Evita modelos de IA pesados. A pontuação usa espectro, MFCC, pitch, ritmo,
duração e uma aproximação cromática, com alinhamento DTW.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from scipy.fft import dct
from scipy.io import wavfile
from scipy.signal import resample, resample_poly, stft
from scipy.spatial.distance import cdist

from models import AudioScore

TARGET_SR = 16_000


class ScoreError(RuntimeError):
    pass


def _read_wav(path: Path) -> np.ndarray:
    if not path.is_file():
        raise ScoreError(f"Áudio não encontrado: {path.name}")
    sr, data = wavfile.read(path)
    audio = np.asarray(data)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if np.issubdtype(audio.dtype, np.integer):
        max_value = float(np.iinfo(audio.dtype).max)
        audio = audio.astype(np.float32) / max_value
    else:
        audio = audio.astype(np.float32)
    audio = np.nan_to_num(audio)
    if sr != TARGET_SR:
        gcd = math.gcd(int(sr), TARGET_SR)
        audio = resample_poly(audio, TARGET_SR // gcd, int(sr) // gcd).astype(np.float32)
    if len(audio) < TARGET_SR // 2:
        raise ScoreError("A gravação tem menos de meio segundo.")
    audio -= float(np.mean(audio))
    peak = float(np.max(np.abs(audio)))
    if peak < 0.005:
        raise ScoreError("A gravação está praticamente silenciosa.")
    audio /= peak
    return _trim_silence(audio)


def _trim_silence(audio: np.ndarray) -> np.ndarray:
    frame = 400
    hop = 160
    if len(audio) < frame:
        return audio
    rms = np.array([
        np.sqrt(np.mean(audio[i:i + frame] ** 2) + 1e-12)
        for i in range(0, len(audio) - frame + 1, hop)
    ])
    threshold = max(0.02, float(np.percentile(rms, 65)) * 0.22)
    active = np.where(rms >= threshold)[0]
    if active.size == 0:
        return audio
    start = max(0, int(active[0] * hop - TARGET_SR * 0.08))
    end = min(len(audio), int(active[-1] * hop + frame + TARGET_SR * 0.08))
    return audio[start:end]


def _mel_filterbank(sr: int, n_fft: int, n_mels: int = 28, fmin: float = 60.0, fmax: float = 7800.0) -> np.ndarray:
    def hz_to_mel(hz: np.ndarray | float) -> np.ndarray:
        return 2595.0 * np.log10(1.0 + np.asarray(hz) / 700.0)

    def mel_to_hz(mel: np.ndarray) -> np.ndarray:
        return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)

    mel_points = np.linspace(hz_to_mel(fmin), hz_to_mel(min(fmax, sr / 2)), n_mels + 2)
    hz_points = mel_to_hz(mel_points)
    bins = np.floor((n_fft + 1) * hz_points / sr).astype(int)
    bins = np.clip(bins, 0, n_fft // 2)
    filters = np.zeros((n_mels, n_fft // 2 + 1), dtype=np.float32)
    for m in range(1, n_mels + 1):
        left, center, right = bins[m - 1], bins[m], bins[m + 1]
        if center <= left:
            center = min(left + 1, n_fft // 2)
        if right <= center:
            right = min(center + 1, n_fft // 2)
        for k in range(left, center):
            filters[m - 1, k] = (k - left) / max(center - left, 1)
        for k in range(center, right):
            filters[m - 1, k] = (right - k) / max(right - center, 1)
    return filters


def _frame_rms(audio: np.ndarray, frame: int = 512, hop: int = 128) -> np.ndarray:
    if len(audio) < frame:
        return np.array([np.sqrt(np.mean(audio ** 2) + 1e-12)], dtype=np.float32)
    return np.array([
        np.sqrt(np.mean(audio[i:i + frame] ** 2) + 1e-12)
        for i in range(0, len(audio) - frame + 1, hop)
    ], dtype=np.float32)


def _pitch_track(audio: np.ndarray, sr: int = TARGET_SR) -> np.ndarray:
    frame = 1024
    hop = 256
    min_lag = max(1, int(sr / 420))
    max_lag = min(frame - 2, int(sr / 70))
    pitches: list[float] = []
    window = np.hanning(frame).astype(np.float32)
    for start in range(0, max(1, len(audio) - frame + 1), hop):
        segment = audio[start:start + frame]
        if len(segment) < frame:
            segment = np.pad(segment, (0, frame - len(segment)))
        if np.sqrt(np.mean(segment ** 2) + 1e-12) < 0.025:
            pitches.append(0.0)
            continue
        segment = (segment - np.mean(segment)) * window
        corr = np.correlate(segment, segment, mode="full")[frame - 1:]
        if corr[0] <= 1e-9:
            pitches.append(0.0)
            continue
        corr /= corr[0]
        search = corr[min_lag:max_lag]
        lag = int(np.argmax(search)) + min_lag
        strength = float(corr[lag])
        pitches.append(float(sr / lag) if strength >= 0.20 else 0.0)
    return np.asarray(pitches, dtype=np.float32)


def _extract(audio: np.ndarray) -> dict[str, np.ndarray | float]:
    n_fft = 512
    _, _, z = stft(
        audio,
        fs=TARGET_SR,
        window="hann",
        nperseg=n_fft,
        noverlap=384,
        nfft=n_fft,
        boundary=None,
        padded=False,
    )
    mag = np.maximum(np.abs(z), 1e-8).astype(np.float32)
    power = mag ** 2
    mel = _mel_filterbank(TARGET_SR, n_fft) @ power
    log_mel = np.log(mel + 1e-7)
    mfcc = dct(log_mel, type=2, axis=0, norm="ortho")[:16]

    freqs = np.linspace(0, TARGET_SR / 2, mag.shape[0], dtype=np.float32)[:, None]
    energy = np.sum(mag, axis=0) + 1e-8
    centroid = np.sum(freqs * mag, axis=0) / energy
    bandwidth = np.sqrt(np.sum(((freqs - centroid[None, :]) ** 2) * mag, axis=0) / energy)
    cumulative = np.cumsum(power, axis=0)
    threshold = cumulative[-1] * 0.85
    roll_idx = np.argmax(cumulative >= threshold[None, :], axis=0)
    rolloff = roll_idx.astype(np.float32) * (TARGET_SR / 2) / max(mag.shape[0] - 1, 1)

    chroma = np.zeros((12, mag.shape[1]), dtype=np.float32)
    hz = np.linspace(0, TARGET_SR / 2, mag.shape[0])
    valid = hz >= 55.0
    midi = np.zeros_like(hz)
    midi[valid] = 69.0 + 12.0 * np.log2(hz[valid] / 440.0)
    pitch_class = np.mod(np.rint(midi).astype(int), 12)
    for bin_index in np.where(valid)[0]:
        chroma[pitch_class[bin_index]] += mag[bin_index]
    chroma /= np.sum(chroma, axis=0, keepdims=True) + 1e-8

    rms = _frame_rms(audio)
    onset = np.maximum(0.0, np.diff(rms, prepend=rms[:1]))
    pitch = _pitch_track(audio)
    spectral = np.vstack([
        centroid / (TARGET_SR / 2),
        bandwidth / (TARGET_SR / 2),
        rolloff / (TARGET_SR / 2),
    ])
    return {
        "mfcc": mfcc,
        "chroma": chroma,
        "spectral": spectral,
        "rms": rms,
        "onset": onset,
        "pitch": pitch,
        "duration": len(audio) / TARGET_SR,
    }


def _safe_cosine_cost(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    a = a / (np.linalg.norm(a, axis=0, keepdims=True) + 1e-8)
    b = b / (np.linalg.norm(b, axis=0, keepdims=True) + 1e-8)
    return np.clip(1.0 - a.T @ b, 0.0, 2.0)


def _dtw_similarity(a: np.ndarray, b: np.ndarray, scale: float) -> float:
    if a.shape[1] < 2 or b.shape[1] < 2:
        return 0.0
    # Limita o custo e a memória para hospedagem gratuita.
    max_frames = 120
    if a.shape[1] > max_frames:
        a = resample(a, max_frames, axis=1)
    if b.shape[1] > max_frames:
        b = resample(b, max_frames, axis=1)
    cost = _safe_cosine_cost(a, b)
    n, m = cost.shape
    acc = np.full((n + 1, m + 1), np.inf, dtype=np.float64)
    acc[0, 0] = 0.0
    band = max(abs(n - m) + 2, int(max(n, m) * 0.28))
    for i in range(1, n + 1):
        j_min = max(1, i - band)
        j_max = min(m, i + band)
        for j in range(j_min, j_max + 1):
            acc[i, j] = cost[i - 1, j - 1] + min(acc[i - 1, j], acc[i, j - 1], acc[i - 1, j - 1])
    normalized = acc[n, m] / max(n + m, 1)
    if not np.isfinite(normalized):
        return 0.0
    return float(np.clip(np.exp(-scale * normalized), 0.0, 1.0))


def _series_similarity(a: np.ndarray, b: np.ndarray) -> float:
    size = 100
    a2 = resample(np.asarray(a, dtype=np.float64), size)
    b2 = resample(np.asarray(b, dtype=np.float64), size)
    a2 = (a2 - np.mean(a2)) / (np.std(a2) + 1e-8)
    b2 = (b2 - np.mean(b2)) / (np.std(b2) + 1e-8)
    corr = float(np.corrcoef(a2, b2)[0, 1])
    if not np.isfinite(corr):
        corr = 0.0
    return float(np.clip((corr + 1.0) / 2.0, 0.0, 1.0))


def _pitch_similarity(ref: np.ndarray, take: np.ndarray) -> float:
    ref_v = ref[ref > 0]
    take_v = take[take > 0]
    if ref_v.size < 3 or take_v.size < 3:
        return 0.25
    median_diff = abs(float(np.median(np.log2(ref_v))) - float(np.median(np.log2(take_v))))
    median_score = math.exp(-2.4 * median_diff)
    ref_log = np.where(ref > 0, np.log2(np.maximum(ref, 1.0)), 0.0)
    take_log = np.where(take > 0, np.log2(np.maximum(take, 1.0)), 0.0)
    contour_score = _series_similarity(ref_log, take_log)
    voiced_ratio = min(ref_v.size / max(len(ref), 1), take_v.size / max(len(take), 1)) / max(
        max(ref_v.size / max(len(ref), 1), take_v.size / max(len(take), 1)), 1e-8
    )
    return float(np.clip(0.55 * median_score + 0.30 * contour_score + 0.15 * voiced_ratio, 0.0, 1.0))


def compare_audio(reference_path: Path, take_path: Path) -> AudioScore:
    reference = _read_wav(reference_path)
    take = _read_wav(take_path)
    ref = _extract(reference)
    rec = _extract(take)

    timbre = _dtw_similarity(ref["mfcc"], rec["mfcc"], scale=3.0)  # type: ignore[arg-type]
    chroma = _dtw_similarity(ref["chroma"], rec["chroma"], scale=2.2)  # type: ignore[arg-type]
    spectral_dtw = _dtw_similarity(ref["spectral"], rec["spectral"], scale=2.8)  # type: ignore[arg-type]
    rhythm = 0.55 * _series_similarity(ref["rms"], rec["rms"]) + 0.45 * _series_similarity(ref["onset"], rec["onset"])  # type: ignore[arg-type]
    pitch = _pitch_similarity(ref["pitch"], rec["pitch"])  # type: ignore[arg-type]
    duration_ratio = min(float(ref["duration"]), float(rec["duration"])) / max(float(ref["duration"]), float(rec["duration"]), 1e-8)
    duration = float(np.clip(duration_ratio ** 1.6, 0.0, 1.0))

    weighted = (
        0.32 * timbre
        + 0.18 * chroma
        + 0.12 * spectral_dtw
        + 0.18 * rhythm
        + 0.12 * pitch
        + 0.08 * duration
    )
    total = float(np.clip(weighted * 100.0, 0.0, 100.0))
    return AudioScore(
        total=round(total, 2),
        timbre=round(timbre * 100.0, 2),
        chroma=round(chroma * 100.0, 2),
        spectral=round(spectral_dtw * 100.0, 2),
        rhythm=round(rhythm * 100.0, 2),
        pitch=round(pitch * 100.0, 2),
        duration=round(duration * 100.0, 2),
    )
