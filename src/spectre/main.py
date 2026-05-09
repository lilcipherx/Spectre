"""Top-level orchestrator: wires audio -> STT -> display together."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys

from .audio import capture as capture_audio
from .audio import iter_wav_frames
from .config import Config
from .display import run_display, run_display_status
from .logging_setup import configure as configure_logging
from .stt import ScribeStream, Transcript

log = logging.getLogger(__name__)


async def _main(cfg: Config, wav_file: str | None) -> int:
    audio_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)
    text_q: asyncio.Queue[Transcript] = asyncio.Queue(maxsize=200)
    stop = asyncio.Event()

    # Give the display an immediate splash before anything else wakes up,
    # so the operator isn't staring at a black TFT for the first 500 ms.
    await text_q.put(Transcript(text="connecting…", final=False))

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            # Windows / non-main thread: signals aren't wired to the loop.
            loop.add_signal_handler(sig, stop.set)

    tasks: list[asyncio.Task] = []
    if wav_file:
        log.info("main: replaying WAV file %s", wav_file)
        tasks.append(asyncio.create_task(_pump_wav(wav_file, audio_q, stop)))
    else:
        tasks.append(
            asyncio.create_task(
                capture_audio(cfg.audio_device, cfg.audio_gain, audio_q, stop)
            )
        )

    stt = ScribeStream(cfg, audio_q, text_q, stop)
    tasks.append(asyncio.create_task(stt.run()))

    tasks.append(asyncio.create_task(run_display(cfg, text_q, stop)))

    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    stop.set()
    for task in pending:
        task.cancel()
    for task in pending:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    exit_code = 0
    for task in done:
        exc = task.exception()
        if exc is not None:
            log.error("main: subsystem crashed: %s", exc)
            exit_code = 1
    return exit_code


async def _pump_wav(
    path: str, out: asyncio.Queue[bytes], stop: asyncio.Event
) -> None:
    """Feed a WAV file into the pipeline at real-time speed.

    Used by ``spectre --wav FILE`` to smoke-test the STT + display path without
    touching the radio.  The real radio always emits silence between PTTs, so
    server-side VAD commits happen naturally.  In replay mode there is no
    silence after the file ends, so we synthesise 1.5 s of zeros to give the
    VAD a chance to finalise the last segment before we close.
    """
    FRAME_MS = 20
    from .audio import FRAME_BYTES

    # Give the STT WebSocket a moment to hand-shake before we start firing
    # audio at it — otherwise the first ~500 ms of speech can be dropped.
    log.info("main: WAV warm-up delay (1.5 s)")
    try:
        await asyncio.wait_for(stop.wait(), timeout=1.5)
        return  # stop was requested during warm-up
    except asyncio.TimeoutError:
        pass

    for frame in iter_wav_frames(path):
        if stop.is_set():
            return
        await out.put(frame)
        await asyncio.sleep(FRAME_MS / 1000.0)

    log.info("main: WAV playback finished; padding with silence for VAD flush")
    silence = b"\x00" * FRAME_BYTES
    for _ in range(int(1500 / FRAME_MS)):
        if stop.is_set():
            return
        await out.put(silence)
        await asyncio.sleep(FRAME_MS / 1000.0)

    log.info("main: holding for tail transcripts")
    try:
        # 6 s is enough for Scribe to finalise even the longest single VAD
        # segment.  Anything still pending after that we drop on shutdown.
        await asyncio.wait_for(stop.wait(), timeout=6.0)
    except asyncio.TimeoutError:
        stop.set()


def cli() -> None:
    parser = argparse.ArgumentParser(prog="spectre", description=__doc__)
    parser.add_argument(
        "--wav",
        metavar="FILE",
        help="replay a WAV file instead of capturing from the radio (test mode)",
    )
    parser.add_argument(
        "--env",
        default=".env",
        help="path to .env file (default: .env in cwd)",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="override SPECTRE_LOG_LEVEL",
    )
    args = parser.parse_args()

    configure_logging(args.log_level)
    try:
        cfg = Config.load(env_file=args.env)
    except RuntimeError as exc:
        # Instead of exit-looping under systemd, draw a clear error message on
        # the TFT so the operator sees it and can fix /opt/spectre/.env.
        log.error("spectre: %s", exc)
        try:
            cfg = Config.load_for_display_only(env_file=args.env)
        except Exception as exc2:  # noqa: BLE001
            print(f"spectre: config unrecoverable: {exc2}", file=sys.stderr)
            sys.exit(2)
        # Paint the error and block until SIGTERM.  Exit 0 so systemd does
        # NOT restart-loop the service when the operator hasn't filled in
        # .env yet — they'll read the message on the TFT and fix it.
        asyncio.run(_error_screen(cfg, "NO API KEY — configure .env"))
        sys.exit(0)

    try:
        rc = asyncio.run(_main(cfg, args.wav))
    except KeyboardInterrupt:
        rc = 130
    sys.exit(rc)


async def _error_screen(cfg: Config, message: str) -> None:
    """Paint ``message`` to the HUD and sit there until SIGTERM arrives.

    Used when Spectre can't start normally (missing API key, etc.) so the
    operator sees the error on the TFT instead of a black screen.
    """
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    await run_display_status(cfg, message, stop)
