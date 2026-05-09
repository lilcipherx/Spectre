"""ElevenLabs Scribe v2 Realtime client.

We hold one persistent WebSocket open for the duration of the session.  Audio
frames flow in; transcript events flow out via an ``asyncio.Queue`` that the
display worker consumes.

Reconnects are handled with exponential backoff capped at 30 s so a short
network blip (cell-tower handover, interference) doesn't kill the whole run.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
from dataclasses import dataclass
from typing import Any

from elevenlabs.client import ElevenLabs
from elevenlabs.realtime.connection import RealtimeEvents
from elevenlabs.realtime.scribe import AudioFormat, CommitStrategy

from .audio import SAMPLE_RATE
from .config import Config

log = logging.getLogger(__name__)

MODEL_ID = "scribe_v2_realtime"


@dataclass
class Transcript:
    """One line of text flowing toward the display."""

    text: str
    final: bool  # True = committed/finalised, False = interim partial


class ScribeStream:
    """Stateful wrapper around an ElevenLabs realtime connection.

    One instance per process.  Call :meth:`run` once; it loops forever, pulling
    PCM frames off ``audio_in`` and pushing :class:`Transcript`\\ s onto
    ``text_out`` until ``stop`` is set.
    """

    def __init__(
        self,
        cfg: Config,
        audio_in: asyncio.Queue[bytes],
        text_out: asyncio.Queue[Transcript],
        stop: asyncio.Event,
    ) -> None:
        self._cfg = cfg
        self._audio_in = audio_in
        self._text_out = text_out
        self._stop = stop
        self._client = ElevenLabs(api_key=cfg.elevenlabs_api_key)
        # When True, server errors emitted during shutdown are benign and
        # demoted to DEBUG so they don't pollute the operator's screen.
        self._closing = False

    # ------------------------------------------------------------------ #
    # Public entry point                                                 #
    # ------------------------------------------------------------------ #

    async def run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                await self._run_once()
                backoff = 1.0  # successful session; reset backoff
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - we log & retry
                log.warning("stt: session failed: %s", exc)
                await self._emit("[connection lost]", final=False)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                    return  # stop requested while we were sleeping
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 30.0)

    # ------------------------------------------------------------------ #
    # Internals                                                          #
    # ------------------------------------------------------------------ #

    async def _run_once(self) -> None:
        log.info("stt: connecting (lang=%s, model=%s)", self._cfg.language, MODEL_ID)
        connection = await self._client.speech_to_text.realtime.connect(
            {
                "model_id": MODEL_ID,
                "audio_format": AudioFormat.PCM_16000,
                "sample_rate": SAMPLE_RATE,
                "commit_strategy": CommitStrategy.VAD,
                "vad_silence_threshold_secs": self._cfg.vad_silence_sec,
                "vad_threshold": self._cfg.vad_threshold,
                "min_speech_duration_ms": self._cfg.min_speech_ms,
                "min_silence_duration_ms": self._cfg.min_silence_ms,
                "language_code": self._cfg.language,
            }
        )

        connection.on(RealtimeEvents.PARTIAL_TRANSCRIPT, self._on_partial)
        connection.on(RealtimeEvents.COMMITTED_TRANSCRIPT, self._on_committed)
        connection.on(RealtimeEvents.ERROR, self._on_error)
        connection.on(RealtimeEvents.AUTH_ERROR, self._on_auth_error)
        connection.on(RealtimeEvents.QUOTA_EXCEEDED, self._on_quota)
        connection.on(RealtimeEvents.RATE_LIMITED, self._on_rate_limit)

        sender = asyncio.create_task(self._send_loop(connection))
        stop_waiter = asyncio.create_task(self._stop.wait())

        try:
            done, _pending = await asyncio.wait(
                {sender, stop_waiter},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                exc = task.exception()
                if exc:
                    raise exc
        finally:
            self._closing = True
            sender.cancel()
            stop_waiter.cancel()
            for task in (sender, stop_waiter):
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            try:
                await connection.close()
            except Exception as exc:  # noqa: BLE001
                log.debug("stt: connection close raised: %s", exc)

    async def _send_loop(self, connection: Any) -> None:
        while not self._stop.is_set():
            frame = await self._audio_in.get()
            encoded = base64.b64encode(frame).decode("ascii")
            await connection.send({"audio_base_64": encoded})

    # -- Event handlers.  The SDK calls these from its reader task, so they
    # -- must be non-blocking and schedule coroutines instead of awaiting.

    def _on_partial(self, data: dict) -> None:
        text = (data.get("transcript") or data.get("text") or "").strip()
        if text:
            self._schedule_emit(text, final=False)

    def _on_committed(self, data: dict) -> None:
        text = (data.get("transcript") or data.get("text") or "").strip()
        if text:
            self._schedule_emit(text, final=True)

    def _on_error(self, data: dict) -> None:
        # The server reliably emits a final "User ended conversation" error
        # after we send a close frame; that's not a real failure.
        if self._closing:
            log.debug("stt: server error during shutdown (ignored): %s", data)
            return
        log.error("stt: server error: %s", data)

    def _on_auth_error(self, data: dict) -> None:
        log.error("stt: auth error: %s", data)
        self._stop.set()  # no point retrying a bad key

    def _on_quota(self, data: dict) -> None:
        log.error("stt: quota exceeded: %s", data)
        self._schedule_emit("[quota exceeded]", final=True)

    def _on_rate_limit(self, data: dict) -> None:
        log.warning("stt: rate limited: %s", data)

    # ------------------------------------------------------------------ #
    # Helpers                                                            #
    # ------------------------------------------------------------------ #

    def _schedule_emit(self, text: str, final: bool) -> None:
        asyncio.get_running_loop().create_task(self._emit(text, final))

    async def _emit(self, text: str, final: bool) -> None:
        await self._text_out.put(Transcript(text=text, final=final))
