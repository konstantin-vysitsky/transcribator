#!/usr/bin/env python3
"""Speech model helpers: GigaAM (Sber)."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

DEFAULT_MODEL = "v3_e2e_rnnt"
_LEGACY_GIGAAM_CACHE = Path("C:/gigaam-cache")
GIGAAM_PREFIXES = ("v1_", "v2_", "v3_", "multilingual_")

# Доступные модели GigaAM (Сбер). Whisper полностью удалён.
GIGAAM_MODELS = (
    "v3_e2e_rnnt",
    "v3_e2e_ctc",
    "v3_ctc",
    "v3_rnnt",
    "v2_rnnt",
    "v2_ctc",
    "v1_rnnt",
    "v1_ctc",
    "multilingual_ctc",
    "multilingual_rnnt",
    "emo",
)


def is_gigaam_model(model_name: str) -> bool:
    return model_name.startswith(GIGAAM_PREFIXES) or model_name == "emo"


def _cache_is_writable(cache: Path) -> bool:
    try:
        cache.mkdir(parents=True, exist_ok=True)
        probe = cache / ".write_test"
        probe.write_text("ok", encoding="ascii")
        probe.unlink()
        return True
    except OSError:
        return False


def _migrate_legacy_gigaam_cache(target: Path) -> None:
    legacy_dirs = [_LEGACY_GIGAAM_CACHE]
    old_home_cache = Path.home() / ".cache" / "gigaam"
    if old_home_cache != target and old_home_cache.is_dir():
        legacy_dirs.append(old_home_cache)

    for legacy in legacy_dirs:
        if not legacy.is_dir() or legacy.resolve() == target.resolve():
            continue
        for item in legacy.iterdir():
            if not item.is_file():
                continue
            dst = target / item.name
            if dst.exists() and dst.stat().st_size == item.stat().st_size:
                continue
            shutil.copy2(item, dst)


def _needs_ascii_cache_path(path: Path) -> bool:
    """Sentencepiece на Windows не открывает файлы по путям с не-ASCII символами."""
    try:
        str(path).encode("ascii")
        return False
    except UnicodeEncodeError:
        return True


def gigaam_cache_dir() -> str:
    """Стандартный кэш GigaAM: ~/.cache/gigaam (или C:/gigaam-cache при кириллице в профиле)."""
    home_cache = Path.home() / ".cache" / "gigaam"
    if _needs_ascii_cache_path(home_cache):
        fallback = _LEGACY_GIGAAM_CACHE
        fallback.mkdir(parents=True, exist_ok=True)
        _migrate_legacy_gigaam_cache(fallback)
        return str(fallback)

    if _cache_is_writable(home_cache):
        _migrate_legacy_gigaam_cache(home_cache)
        return str(home_cache)

    fallback = _LEGACY_GIGAAM_CACHE
    fallback.mkdir(parents=True, exist_ok=True)
    return str(fallback)


def format_timestamp(seconds: float, srt: bool = False) -> str:
    if seconds < 0:
        seconds = 0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if srt:
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    return f"{h:02d}:{m:02d}:{s:02d}"


def write_srt(segments: list[dict], path: Path) -> None:
    lines: list[str] = []
    for i, seg in enumerate(segments, start=1):
        lines.append(str(i))
        lines.append(
            f"{format_timestamp(seg['start'], srt=True)} --> {format_timestamp(seg['end'], srt=True)}"
        )
        lines.append(seg["text"].strip())
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_txt(segments: list[dict], path: Path) -> None:
    lines = [f"[{format_timestamp(seg['start'])}] {seg['text'].strip()}" for seg in segments]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _audio_to_wav_path(audio: np.ndarray, sample_rate: int = 16000) -> Path:
    import wave

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = Path(tmp.name)
    pcm = np.clip(audio, -1.0, 1.0)
    pcm_i16 = (pcm * 32767).astype(np.int16)
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_i16.tobytes())
    return wav_path


def _patch_gigaam_load_audio() -> None:
    """GigaAM по умолчанию грузит wav через ffmpeg — на Windows мигает консоль."""
    import wave

    import gigaam.preprocess as preprocess
    import torch

    if getattr(preprocess, "_dictation_patched", False):
        return

    def load_audio_from_wave(audio_path: str, sample_rate: int = preprocess.SAMPLE_RATE) -> Any:
        with wave.open(audio_path, "rb") as wf:
            channels = wf.getnchannels()
            width = wf.getsampwidth()
            rate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
        if width != 2:
            raise RuntimeError(f"Unsupported wav sample width: {width}")
        audio = torch.frombuffer(frames, dtype=torch.int16).float() / 32768.0
        if channels > 1:
            audio = audio.view(-1, channels).mean(dim=1)
        if rate != sample_rate:
            import torchaudio.functional as F

            audio = F.resample(audio, rate, sample_rate)
        return audio

    preprocess.load_audio = load_audio_from_wave
    preprocess._dictation_patched = True


def _load_gigaam_model(
    model_name: str,
    cpu_threads: int = 0,
) -> tuple[Any, str, str]:
    import gigaam
    import torch

    if cpu_threads > 0:
        torch.set_num_threads(cpu_threads)
    _patch_gigaam_load_audio()
    model = gigaam.load_model(model_name, download_root=gigaam_cache_dir())
    return model, "cpu", "gigaam"


def transcribe_gigaam(model: Any, audio: np.ndarray) -> str:
    import torch

    pcm = np.clip(audio, -1.0, 1.0).astype(np.float32, copy=False)
    wav = torch.from_numpy(pcm).to(device=model._device, dtype=model._dtype).unsqueeze(0)
    length = torch.full([1], wav.shape[-1], device=model._device)
    encoded, encoded_len = model.forward(wav, length)
    text, _ = model._decode(encoded, encoded_len, length, False)[0]
    return str(text).strip()


def load_model(
    model_size: str = DEFAULT_MODEL,
    cpu_threads: int = 0,
) -> tuple[Any, str, str]:
    if not is_gigaam_model(model_size):
        raise RuntimeError(
            f"Неизвестная модель: {model_size!r}. Поддерживаются только модели "
            f"GigaAM (Сбер): {', '.join(sorted(GIGAAM_MODELS))}."
        )
    return _load_gigaam_model(model_size, cpu_threads)
