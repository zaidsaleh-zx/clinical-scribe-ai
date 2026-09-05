"""LiveKit audio transport for the live Whisper transcription session."""

import asyncio
import io
import logging
import wave
from typing import Awaitable, Callable

from livekit import rtc

logger = logging.getLogger("clinical-scribe.livekit")
AudioCallback = Callable[[bytes], Awaitable[None]]


class LiveKitAudioBridge:
    """Subscribe to one LiveKit room and deliver five-second WAV chunks."""

    def __init__(self, url: str, token: str, on_audio: AudioCallback):
        self.url = url
        self.token = token
        self.on_audio = on_audio
        self.room = rtc.Room()
        self._tasks: set[asyncio.Task] = set()
        self._buffers: dict[str, bytearray] = {}
        self._formats: dict[str, tuple[int, int, int]] = {}
        self._stopping = False

    async def start(self):
        self.room.on("track_subscribed", self._track_subscribed)
        await self.room.connect(self.url, self.token)
        logger.info("Connected to LiveKit room %s", self.room.name)

    def _track_subscribed(self, track, publication, participant):
        if track.kind != rtc.TrackKind.KIND_AUDIO or self._stopping:
            return
        task = asyncio.create_task(self._consume_track(track))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _consume_track(self, track):
        stream = rtc.AudioStream(track, sample_rate=16000, num_channels=1, frame_size_ms=100)
        track_key = track.sid
        buffer = self._buffers.setdefault(track_key, bytearray())
        self._formats[track_key] = (16000, 2, 1)
        bytes_per_second = 16000 * 2

        try:
            async for frame_event in stream:
                if self._stopping:
                    break
                frame = frame_event.frame if hasattr(frame_event, "frame") else frame_event
                buffer.extend(frame.data.tobytes() if hasattr(frame.data, "tobytes") else bytes(frame.data))
                if len(buffer) >= bytes_per_second * 5:
                    chunk = self._make_wav(bytes(buffer))
                    buffer.clear()
                    await self.on_audio(chunk)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("LiveKit audio track failed")
        finally:
            await stream.aclose()
            if buffer and not self._stopping:
                chunk = self._make_wav(bytes(buffer))
                buffer.clear()
                await self.on_audio(chunk)
            self._buffers.pop(track_key, None)
            self._formats.pop(track_key, None)

    @staticmethod
    def _make_wav(pcm: bytes) -> bytes:
        output = io.BytesIO()
        with wave.open(output, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(pcm)
        return output.getvalue()

    async def stop(self):
        self._stopping = True
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.room.disconnect()
