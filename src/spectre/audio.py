"""USB audio capture.

The radio feeds audio through a USB sound card into ALSA.  We read 16-bit mono
PCM at 16 kHz in 20 ms frames and push raw bytes onto an ``asyncio.Queue`` so
the STT worker can forward them to ElevenLabs without any buffering beyond one
network round-trip.

We shell out to ``arecord`` (the canonical ALSA capture tool) instead of going
through PortAudio/sounddevice.  Several USB audio adapters that the field
operator might plug in (e.g. AB13X-class dongles on Pi OS Trixie) advertise
zero input channels to PortAudio even though ALSA happily exposes them as
capture devices, which makes ``sounddevice`` refuse to open them.  ``arecord``
doesn't have that limitation, is preinstalled with ``alsa-utils``, and has
identical latency for our 20 ms frame cadence.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
from collections.abc import Iterator

import numpy as np

log = logging.getLogger(__name__)

# 20 ms @ 16 kHz mono s16le = 640 byte frames.  ElevenLabs accepts any chunk
# size but VAD behaves best with fixed-cadence frames.
FRAME_MS = 20
SAMPLE_RATE = 16000
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 320 samples
FRAME_BYTES = FRAME_SAMPLES * 2  # int16


class AudioError(RuntimeError):
    """Raised when the audio subsystem cannot produce frames."""


def _apply_gain(frame: bytes, gain: float) -> bytes:
    """Scale a raw s16le frame by ``gain``, saturating at int16 bounds."""
    if gain == 1.0:
        return frame
    samples = np.frombuffer(frame, dtype=np.int16).astype(np.int32)
    samples = np.clip(samples * gain, -32768, 32767).astype(np.int16)
    return samples.tobytes()


async def capture(
    device: str,
    gain: float,
    out: asyncio.Queue[bytes],
    stop: asyncio.Event,
) -> None:
    """Stream 20 ms PCM frames from ``device`` onto ``out`` until ``stop`` is set.

    Spawns ``arecord`` and reads raw 16-bit mono 16 kHz PCM from its stdout in
    20 ms (640-byte) chunks.  We bypass PortAudio entirely because some USB
    audio adapters report ``max_input_channels == 0`` to it on Pi OS Trixie,
    even though ALSA exposes them as proper capture devices.
    """
    if shutil.which("arecord") is None:
        raise AudioError(
            "arecord not found.  Install ALSA tools: `sudo apt install alsa-utils`."
        )

    # NB: we deliberately do NOT pass ``-q`` here — arecord's stderr is the
    # only place ALSA errors (busy device, bad rate, missing card) surface,
    # and ``-q`` silences it.  We capture stderr below and drain it both for
    # live logging and for inclusion in the AudioError message on crash.
    cmd = [
        "arecord",
        "-D", device,
        "-f", "S16_LE",
        "-r", str(SAMPLE_RATE),
        "-c", "1",
        "-t", "raw",
        "--buffer-size=8000",
    ]
    log.info("audio: starting %s", " ".join(cmd))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise AudioError(f"arecord exec failed: {exc}") from exc

    log.info("audio: capture started (device=%s, gain=%.2f, pid=%d)", device, gain, proc.pid)

    assert proc.stdout is not None
    assert proc.stderr is not None

    # Buffer of stderr lines so we can attach the last few to AudioError on
    # crash — most ALSA failures (busy device, missing card) print exactly one
    # diagnostic line and exit immediately, before we can read it from a
    # background task.
    stderr_lines: list[str] = []

    async def _drain_stderr() -> None:
        """Forward arecord's stderr into our log so ALSA errors are visible."""
        assert proc.stderr is not None
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            text = line.decode(errors="replace").rstrip()
            stderr_lines.append(text)
            log.warning("audio[arecord]: %s", text)

    stderr_task = asyncio.create_task(_drain_stderr())

    try:
        while not stop.is_set():
            try:
                data = await asyncio.wait_for(
                    proc.stdout.readexactly(FRAME_BYTES), timeout=2.0
                )
            except asyncio.IncompleteReadError as exc:
                if exc.partial:
                    log.warning(
                        "audio: arecord exited mid-frame (got %d/%d bytes)",
                        len(exc.partial), FRAME_BYTES,
                    )
                # Wait briefly for stderr to flush so we can include arecord's
                # actual error text in the exception (e.g. "Device or resource
                # busy" when something else holds the USB capture device).
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(proc.wait(), timeout=0.5)
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stderr_task, timeout=0.5)
                rc = proc.returncode
                tail = " | ".join(stderr_lines[-3:]) or "<no stderr>"
                raise AudioError(
                    f"arecord exited unexpectedly (rc={rc}). stderr: {tail}. "
                    f"If 'Device or resource busy', mask pipewire/pulseaudio: "
                    f"`sudo systemctl mask pipewire pipewire-pulse wireplumber pulseaudio` "
                    f"and `sudo pkill -9 -f pipewire`.  "
                    f"Otherwise check SPECTRE_AUDIO_DEVICE in /opt/spectre/.env "
                    f"against `arecord -L`."
                ) from exc
            except asyncio.TimeoutError:
                # No PCM in 2 s — radio quiet, that's fine; loop on stop flag.
                continue

            frame = _apply_gain(data, gain)
            try:
                out.put_nowait(frame)
            except asyncio.QueueFull:
                # Upstream (STT/WebSocket) is lagging.  Drop oldest frame so we
                # don't grow memory unboundedly on a bad link.
                with contextlib.suppress(asyncio.QueueEmpty):
                    out.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    out.put_nowait(frame)
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        stderr_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await stderr_task

    log.info("audio: capture stopped")


def iter_wav_frames(path: str) -> Iterator[bytes]:
    """Yield 20 ms s16le mono@16k frames from a WAV file.

    Used by ``scripts/test_stt.py`` and unit tests to exercise the pipeline
    without real hardware.  We do the resampling ourselves to avoid pulling in
    a heavy dependency like librosa on the Pi.
    """
    import wave

    with wave.open(path, "rb") as wf:
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        sample_rate = wf.getframerate()
        if sample_width != 2:
            raise AudioError(f"WAV must be 16-bit PCM; got sample_width={sample_width}")

        raw = wf.readframes(wf.getnframes())

    samples = np.frombuffer(raw, dtype=np.int16)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)

    if sample_rate != SAMPLE_RATE:
        # Linear interpolation is good enough for speech at these rates and
        # keeps us off scipy on the Pi.
        ratio = SAMPLE_RATE / sample_rate
        new_len = int(round(len(samples) * ratio))
        xp = np.arange(len(samples))
        x = np.linspace(0, len(samples) - 1, num=new_len)
        samples = np.interp(x, xp, samples).astype(np.int16)

    raw = samples.tobytes()
    for offset in range(0, len(raw) - FRAME_BYTES + 1, FRAME_BYTES):
        yield raw[offset : offset + FRAME_BYTES]
