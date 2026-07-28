"""Preparação da mídia e geração do resultado dublado com FFmpeg.

Versão 2.0:
- Somente YouTube na interface pública.
- Melhor sincronização do take com o diálogo original.
- Atenuação mais agressiva da voz central original para preservar trilha.
- Mixagem com ganho automático da voz do jogador.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
from scipy.io import wavfile
from scipy.signal import correlate, resample_poly

from models import MediaState


class MediaError(RuntimeError):
    pass


SUPPORTED_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi"}
MAX_UPLOAD_BYTES = 160 * 1024 * 1024
MIN_CLIP_SECONDS = 3.0
MAX_CLIP_SECONDS = 45.0
TARGET_SR = 16_000


class MediaService:
    def __init__(self) -> None:
        self.ffmpeg = shutil.which("ffmpeg")
        self.ffprobe = shutil.which("ffprobe")
        if not self.ffmpeg or not self.ffprobe:
            raise RuntimeError("FFmpeg e FFprobe precisam estar instalados no PATH.")

    @staticmethod
    def validate_clip(start: float, duration: float) -> tuple[float, float]:
        if start < 0:
            raise MediaError("O início do trecho não pode ser negativo.")
        if duration < MIN_CLIP_SECONDS or duration > MAX_CLIP_SECONDS:
            raise MediaError(f"A duração deve ficar entre {MIN_CLIP_SECONDS:.0f} e {MAX_CLIP_SECONDS:.0f} segundos.")
        return float(start), float(duration)

    def prepare_uploaded(self, source_path: Path, work_dir: Path, start: float, duration: float) -> MediaState:
        start, duration = self.validate_clip(start, duration)
        if source_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise MediaError("Formato de vídeo não suportado. Use MP4, MKV, WebM, MOV, M4V ou AVI.")
        media = MediaState(
            status="processing",
            title=source_path.stem[:80],
            source_kind="upload",
            clip_start=start,
            clip_duration=duration,
            source_path=source_path,
            message="Convertendo o trecho...",
        )
        self._prepare(source_path, work_dir, media)
        return media

    def download_youtube(self, url: str, work_dir: Path, start: float, duration: float) -> MediaState:
        start, duration = self.validate_clip(start, duration)
        parsed = urlparse(url.strip())
        allowed_hosts = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in allowed_hosts:
            raise MediaError("Cole um link válido do YouTube ou youtu.be.")
        try:
            import yt_dlp
            from yt_dlp.utils import download_range_func
        except ImportError as exc:
            raise MediaError("O componente yt-dlp não está instalado.") from exc

        output = work_dir / "youtube_source.%(ext)s"
        options: dict[str, object] = {
            "format": "bv*[height<=720]+ba/b[height<=720]/best",
            "outtmpl": str(output),
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "overwrites": True,
            "max_filesize": MAX_UPLOAD_BYTES,
            "download_ranges": download_range_func(None, [(start, start + duration)]),
            "force_keyframes_at_cuts": True,
        }
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
                title = str(info.get("title") or "Vídeo do YouTube")[:80]
        except Exception as exc:
            raise MediaError(
                "Não foi possível baixar o vídeo do YouTube. Em hospedagem gratuita, o YouTube pode bloquear o servidor. "
                "Se o erro persistir, publique no Render ou tente outro vídeo. Detalhes: " + str(exc)
            ) from exc

        candidates = sorted(work_dir.glob("youtube_source.*"), key=lambda p: p.stat().st_mtime, reverse=True)
        source = next((p for p in candidates if p.suffix.lower() in SUPPORTED_EXTENSIONS), None)
        if source is None:
            raise MediaError("O yt-dlp terminou, mas o arquivo de vídeo não foi encontrado.")
        media = MediaState(
            status="processing",
            title=title,
            source_kind="youtube",
            clip_start=0.0,
            clip_duration=duration,
            source_path=source,
            message="Preparando o trecho baixado...",
        )
        self._prepare(source, work_dir, media)
        return media

    def _prepare(self, source: Path, work_dir: Path, media: MediaState) -> None:
        clip = work_dir / "clip.mp4"
        mix = work_dir / "mix.wav"
        reference = work_dir / "reference.wav"
        background = work_dir / "background.wav"
        reference_aligned = work_dir / "reference_aligned.wav"
        for path in (clip, mix, reference, background, reference_aligned):
            path.unlink(missing_ok=True)

        command = [
            self.ffmpeg,
            "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{media.clip_start:.3f}", "-i", str(source),
            "-t", f"{media.clip_duration:.3f}",
            "-map", "0:v:0", "-map", "0:a:0",
            "-vf", "scale='min(1280,iw)':-2:force_original_aspect_ratio=decrease,fps=30",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ac", "2", "-ar", "44100",
            "-movflags", "+faststart", "-shortest", str(clip),
        ]
        self._run(command, "Não foi possível converter o trecho do vídeo.")
        if not clip.is_file() or clip.stat().st_size < 2048:
            raise MediaError("O trecho de vídeo não foi criado.")

        channels = self._probe_channels(clip)
        media.channel_count = channels
        self._run([
            self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(clip), "-vn", "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(mix),
        ], "Não foi possível extrair o áudio do vídeo.")

        # Separação leve e gratuita: isola a faixa central para a referência (diálogo)
        # e reduz com mais força o centro no fundo para preservar a trilha sonora.
        if channels >= 2:
            filter_complex = (
                "[0:a]pan=mono|c0=0.5*c0+0.5*c1,highpass=f=80,lowpass=f=7800,"
                "dynaudnorm=f=120:g=7,agate=threshold=0.015:ratio=2.0:attack=12:release=120,"
                "aresample=16000[voice];"
                "[0:a]pan=stereo|c0=0.53*c0-0.47*c1|c1=0.53*c1-0.47*c0,"
                "highpass=f=35,alimiter=limit=0.96[bg]"
            )
        else:
            filter_complex = (
                "[0:a]highpass=f=80,lowpass=f=7800,dynaudnorm=f=120:g=7,aresample=16000[voice];"
                "[0:a]pan=stereo|c0=0.16*c0|c1=0.16*c0,highpass=f=35,alimiter=limit=0.96[bg]"
            )
        self._run([
            self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(mix),
            "-filter_complex", filter_complex,
            "-map", "[voice]", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(reference),
            "-map", "[bg]", "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(background),
        ], "Não foi possível separar a referência e o fundo sonoro.")

        media.clip_path = clip
        media.mix_path = mix
        media.reference_path = reference
        media.background_path = background
        media.status = "ready"
        media.message = "Trecho pronto para a rodada."
        media.clip_start = 0.0

    def convert_take(self, input_path: Path, output_path: Path, duration: float) -> None:
        self._run([
            self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(input_path), "-t", f"{duration:.3f}",
            "-af", "highpass=f=70,lowpass=f=9000,aresample=16000,apad,atrim=0:" + f"{duration:.3f}",
            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(output_path),
        ], "Não foi possível converter a gravação do microfone.")

    def render_result(self, media: MediaState, take_path: Path, output_path: Path) -> Path:
        if not media.clip_path or not media.background_path or not media.reference_path:
            raise MediaError("A mídia da rodada não está pronta.")
        mixed = output_path.with_suffix(".wav")
        aligned_take = output_path.with_name(output_path.stem + "_aligned.wav")
        aligned_take.unlink(missing_ok=True)
        mixed.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
        duration = media.clip_duration

        gain, trim_start, pad_start = self._align_and_gain(media.reference_path, take_path, aligned_take, duration)
        voice_volume = max(0.72, min(1.05, gain * 0.92))

        filter_complex = (
            f"[1:a]aresample=44100,highpass=f=70,lowpass=f=9800,"
            f"dynaudnorm=f=120:g=5,acompressor=threshold=-22dB:ratio=2.4:attack=8:release=100,"
            f"volume={voice_volume:.4f},apad,atrim=0:{duration:.3f}[voice];"
            f"[0:a]atrim=0:{duration:.3f},volume=1.0[bg];"
            "[bg][voice]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
            "alimiter=limit=0.95[mix]"
        )
        self._run([
            self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(media.background_path), "-i", str(aligned_take),
            "-filter_complex", filter_complex, "-map", "[mix]",
            "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(mixed),
        ], "Não foi possível mixar a voz com a trilha.")

        self._run([
            self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(media.clip_path), "-i", str(mixed),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-movflags", "+faststart", str(output_path),
        ], "Não foi possível gerar o vídeo dublado.")
        return output_path

    def _align_and_gain(self, reference_path: Path, take_path: Path, output_path: Path, duration: float) -> tuple[float, float, float]:
        ref = self._read_mono_wav(reference_path, TARGET_SR)
        take = self._read_mono_wav(take_path, TARGET_SR)
        lag_samples = self._estimate_lag(ref, take, TARGET_SR)

        if lag_samples > 0:
            # take começou depois => adiciona silêncio no início
            aligned = np.pad(take, (lag_samples, 0))
            pad_start = lag_samples / TARGET_SR
            trim_start = 0.0
        elif lag_samples < 0:
            # take começou cedo => remove o excesso inicial
            trim = min(len(take), abs(lag_samples))
            aligned = take[trim:]
            pad_start = 0.0
            trim_start = trim / TARGET_SR
        else:
            aligned = take
            pad_start = 0.0
            trim_start = 0.0

        target_len = int(duration * TARGET_SR)
        if len(aligned) < target_len:
            aligned = np.pad(aligned, (0, target_len - len(aligned)))
        else:
            aligned = aligned[:target_len]

        ref_rms = self._voiced_rms(ref)
        take_rms = self._voiced_rms(aligned)
        gain = float(np.clip(ref_rms / max(take_rms, 1e-4), 0.65, 1.18))
        aligned = np.clip(aligned * gain, -1.0, 1.0)
        wavfile.write(output_path, TARGET_SR, (aligned * 32767.0).astype(np.int16))
        return gain, trim_start, pad_start

    @staticmethod
    def _read_mono_wav(path: Path, target_sr: int) -> np.ndarray:
        sr, data = wavfile.read(path)
        audio = np.asarray(data, dtype=np.float32)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if np.issubdtype(data.dtype, np.integer):
            audio /= float(np.iinfo(data.dtype).max)
        audio = np.nan_to_num(audio)
        if sr != target_sr:
            gcd = math.gcd(int(sr), target_sr)
            audio = resample_poly(audio, target_sr // gcd, int(sr) // gcd).astype(np.float32)
        if audio.size == 0:
            return np.zeros(1, dtype=np.float32)
        audio -= float(np.mean(audio))
        peak = float(np.max(np.abs(audio)))
        if peak > 1e-6:
            audio /= peak
        return audio.astype(np.float32)

    @staticmethod
    def _moving_env(audio: np.ndarray, sr: int) -> np.ndarray:
        hop = max(1, sr // 100)  # 10 ms
        frame = max(4, sr // 50)  # 20 ms
        if len(audio) < frame:
            env = np.abs(audio)
            return env / (np.max(env) + 1e-8)
        padded = np.pad(np.abs(audio), (0, hop - len(audio) % hop if len(audio) % hop else 0))
        env = padded.reshape(-1, hop).mean(axis=1)
        kernel = np.ones(max(3, frame // hop), dtype=np.float32)
        smooth = np.convolve(env, kernel / kernel.size, mode="same")
        smooth = smooth - smooth.min()
        return (smooth / (smooth.max() + 1e-8)).astype(np.float32)

    def _estimate_lag(self, reference: np.ndarray, take: np.ndarray, sr: int) -> int:
        ref_env = self._moving_env(reference, sr)
        take_env = self._moving_env(take, sr)
        max_len = 300
        if len(ref_env) > max_len:
            ref_env = resample_poly(ref_env, max_len, len(ref_env)).astype(np.float32)
        if len(take_env) > max_len:
            take_env = resample_poly(take_env, max_len, len(take_env)).astype(np.float32)
        corr = correlate(take_env - np.mean(take_env), ref_env - np.mean(ref_env), mode="full")
        lag_idx = int(np.argmax(corr)) - (len(ref_env) - 1)
        # limita a compensação para evitar cortes absurdos
        max_seconds = 1.5
        lag_seconds = np.clip(lag_idx / 100.0, -max_seconds, max_seconds)
        return int(round(lag_seconds * sr))

    @staticmethod
    def _voiced_rms(audio: np.ndarray) -> float:
        frame = 320
        if len(audio) < frame:
            return float(np.sqrt(np.mean(audio ** 2) + 1e-12))
        vals = []
        for idx in range(0, len(audio) - frame + 1, 160):
            segment = audio[idx:idx + frame]
            rms = float(np.sqrt(np.mean(segment ** 2) + 1e-12))
            if rms >= 0.04:
                vals.append(rms)
        if not vals:
            return float(np.sqrt(np.mean(audio ** 2) + 1e-12))
        return float(np.median(vals))

    def _probe_channels(self, path: Path) -> int:
        command = [
            self.ffprobe, "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=channels", "-of", "json", str(path),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
        try:
            payload = json.loads(completed.stdout)
            return int(payload["streams"][0].get("channels", 2))
        except Exception:
            return 2

    @staticmethod
    def safe_filename(name: str) -> str:
        base = Path(name).stem
        clean = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
        return (clean[:60] or "video") + Path(name).suffix.lower()

    def _run(self, command: list[str], message: str) -> None:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=os.environ.copy(),
        )
        if completed.returncode != 0:
            details = completed.stderr.strip()[-1200:] or "erro desconhecido do FFmpeg"
            raise MediaError(f"{message} Detalhes: {details}")
