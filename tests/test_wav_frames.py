"""Smoke test for the WAV replay helper used by ``spectre --wav``."""

import math
import wave

import numpy as np

from spectre.audio import FRAME_BYTES, FRAME_SAMPLES, SAMPLE_RATE, iter_wav_frames


def _write_tone(path, seconds=1.0, freq=440.0, rate=SAMPLE_RATE, channels=1):
    samples = np.arange(int(rate * seconds))
    wave_data = (0.3 * np.sin(2 * math.pi * freq * samples / rate) * 32767).astype(
        np.int16
    )
    if channels > 1:
        wave_data = np.tile(wave_data[:, None], (1, channels))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(wave_data.tobytes())


def test_iter_wav_frames_yields_fixed_size_frames(tmp_path):
    wav = tmp_path / "tone.wav"
    _write_tone(wav, seconds=0.5)
    frames = list(iter_wav_frames(str(wav)))
    assert frames, "expected at least one frame"
    assert all(len(f) == FRAME_BYTES for f in frames)
    # 0.5 s at 16 kHz / 20 ms per frame = 25 frames
    assert len(frames) == int(0.5 * SAMPLE_RATE // FRAME_SAMPLES)


def test_iter_wav_frames_downmixes_stereo(tmp_path):
    wav = tmp_path / "stereo.wav"
    _write_tone(wav, seconds=0.2, channels=2)
    frames = list(iter_wav_frames(str(wav)))
    assert frames and all(len(f) == FRAME_BYTES for f in frames)


def test_iter_wav_frames_resamples_48k(tmp_path):
    wav = tmp_path / "48k.wav"
    _write_tone(wav, seconds=0.25, rate=48000)
    frames = list(iter_wav_frames(str(wav)))
    assert frames and all(len(f) == FRAME_BYTES for f in frames)
