"""Runtime configuration loaded from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


def _strip_inline_comment(value: str) -> str:
    """Drop everything from the first ``#`` onward.

    python-dotenv keeps inline comments inside the value when there's no
    quoted delimiter (e.g. ``KEY=1   # comment`` parses as ``"1   # comment"``)
    which then explodes any int/float coerce.  We defensively trim them here
    so a stray operator-written comment can never crash the service.
    """
    head, _, _ = value.partition("#")
    return head.strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return _strip_inline_comment(raw).lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    cleaned = _strip_inline_comment(raw)
    if cleaned == "":
        return default
    return float(cleaned)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    cleaned = _strip_inline_comment(raw)
    if cleaned == "":
        return default
    return int(cleaned)


@dataclass(frozen=True)
class Config:
    # Credentials / API
    elevenlabs_api_key: str
    language: str

    # Audio capture
    audio_device: str
    audio_gain: float
    sample_rate: int  # fixed to 16000, exposed for clarity

    # VAD
    vad_silence_sec: float
    vad_threshold: float
    min_speech_ms: int
    min_silence_ms: int

    # Display
    display: str
    fb_path: str
    spi_port: int
    spi_device: int
    gpio_dc: int
    gpio_rst: int
    display_rotate: int
    buffer_words: int

    # Modes
    dry_run: bool

    @classmethod
    def load(cls, env_file: str | None = ".env") -> Config:
        if env_file:
            load_dotenv(env_file, override=False)

        api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
        dry_run = _env_bool("SPECTRE_DRY_RUN", False)
        if not api_key and not dry_run:
            raise RuntimeError(
                "ELEVENLABS_API_KEY is not set. Either export it, put it in .env, "
                "or set SPECTRE_DRY_RUN=1 for a local dry-run."
            )

        return cls._build(api_key=api_key, dry_run=dry_run)

    @classmethod
    def load_for_display_only(cls, env_file: str | None = ".env") -> Config:
        """Load the display-related config only; used by the error screen.

        Skips the ``ELEVENLABS_API_KEY`` requirement so the HUD can still
        paint an error message when the operator hasn't configured their
        ``.env`` yet.
        """
        if env_file:
            load_dotenv(env_file, override=False)
        return cls._build(api_key="", dry_run=True)

    @classmethod
    def _build(cls, api_key: str, dry_run: bool) -> Config:
        return cls(
            elevenlabs_api_key=api_key,
            language=os.getenv("SPECTRE_LANGUAGE", "uz"),
            audio_device=os.getenv("SPECTRE_AUDIO_DEVICE", "default"),
            audio_gain=_env_float("SPECTRE_AUDIO_GAIN", 1.0),
            sample_rate=16000,
            vad_silence_sec=_env_float("SPECTRE_VAD_SILENCE_SEC", 0.6),
            vad_threshold=_env_float("SPECTRE_VAD_THRESHOLD", 0.4),
            min_speech_ms=_env_int("SPECTRE_MIN_SPEECH_MS", 200),
            min_silence_ms=_env_int("SPECTRE_MIN_SILENCE_MS", 400),
            display=os.getenv("SPECTRE_DISPLAY", "fb1"),
            fb_path=os.getenv("SPECTRE_FB_PATH", "/dev/fb1"),
            spi_port=_env_int("SPECTRE_SPI_PORT", 0),
            spi_device=_env_int("SPECTRE_SPI_DEVICE", 0),
            gpio_dc=_env_int("SPECTRE_GPIO_DC", 24),
            gpio_rst=_env_int("SPECTRE_GPIO_RST", 25),
            display_rotate=_env_int("SPECTRE_DISPLAY_ROTATE", 1),
            buffer_words=_env_int("SPECTRE_BUFFER_WORDS", 120),
            dry_run=dry_run,
        )
