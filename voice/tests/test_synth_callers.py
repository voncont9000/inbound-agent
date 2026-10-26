import asyncio
import base64
import io
import struct
import wave

import numpy as np
import pytest

import eval.synth_callers as synth_callers
from eval.synth_callers import (
    WEBRTC_SAMPLE_RATE,
    _synthesize_utterance,
    combine_segment_wavs,
    phone_band_from_wav,
    split_paused_utterance,
    utterance_audio_path,
)


@pytest.fixture(autouse=True)
def reset_speechify_limiter(monkeypatch):
    monkeypatch.setattr(synth_callers, "_speechify_request_lock", asyncio.Lock())
    monkeypatch.setattr(synth_callers, "_last_speechify_request_started", None)


def _wav(samples: list[int]) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio_file:
        audio_file.setnchannels(1)
        audio_file.setsampwidth(2)
        audio_file.setframerate(WEBRTC_SAMPLE_RATE)
        audio_file.writeframes(np.asarray(samples, dtype=np.int16).tobytes())
    return output.getvalue()


class _SynthesisResponse:
    def __init__(self, status: int, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def text(self) -> str:
        return "rate limited"

    async def json(self) -> dict[str, str]:
        return {"audio_data": base64.b64encode(b"audio").decode()}


class _SynthesisSession:
    def __init__(self, responses, clock) -> None:
        self.responses = list(responses)
        self.clock = clock
        self.starts = []

    def post(self, *_args, **_kwargs):
        self.starts.append(self.clock())
        status, headers = self.responses.pop(0)
        return _SynthesisResponse(status, headers)


def _patch_clock(monkeypatch):
    clock = [0.0]
    sleeps = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay):
        sleeps.append(delay)
        clock[0] += delay
        await real_sleep(0)

    monkeypatch.setattr(synth_callers.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(synth_callers.asyncio, "sleep", fake_sleep)
    return clock, sleeps


def test_split_paused_utterance_and_selects_distinct_audio_cache_path(tmp_path) -> None:
    utterance = "My number is 07700 900 | pause 1.4 | 456."

    parts, pauses = split_paused_utterance(utterance)

    assert parts == ["My number is 07700 900", "456."]
    assert pauses == [1.4]
    path = utterance_audio_path(tmp_path, "knowledge_check", 4, utterance, "catriona")
    assert path.name.startswith("4-paused-")
    assert (
        utterance_audio_path(
            tmp_path,
            "knowledge_check",
            4,
            "My number is 07700 900456.",
            "catriona",
        )
        != path
    )
    assert utterance_audio_path(tmp_path, "knowledge_check", 4, utterance, "other-voice") != path
    assert split_paused_utterance("Hello there.") == (["Hello there."], [])


def test_combine_segment_wavs_inserts_silence_without_splitting_the_line() -> None:
    combined = combine_segment_wavs([_wav([100, -100]), _wav([50])], [0.01])
    with wave.open(io.BytesIO(combined), "rb") as audio_file:
        samples = np.frombuffer(audio_file.readframes(audio_file.getnframes()), dtype=np.int16)

    assert len(samples) == 2 + round(0.01 * WEBRTC_SAMPLE_RATE) + 1
    assert samples[:2].tolist() == [100, -100]
    assert np.all(samples[2:-1] == 0)
    assert samples[-1] == 50


def _source_wav(sample_rate: int, channels: int) -> bytes:
    mono = np.tile(np.array([1200, 2400], dtype=np.int16), sample_rate // 20)
    frames = np.repeat(mono[:, None], channels, axis=1)
    if channels == 2:
        frames[:, 1] += 1000
    output = io.BytesIO()
    with wave.open(output, "wb") as audio_file:
        audio_file.setnchannels(channels)
        audio_file.setsampwidth(2)
        audio_file.setframerate(sample_rate)
        audio_file.writeframes(frames.tobytes())
    return output.getvalue()


def test_phone_band_from_wav_resamples_24khz_mono_and_stereo_to_48khz_mono() -> None:
    for channels in (1, 2):
        converted = phone_band_from_wav(_source_wav(24_000, channels))

        with wave.open(io.BytesIO(converted), "rb") as audio_file:
            assert audio_file.getnchannels() == 1
            assert audio_file.getsampwidth() == 2
            assert audio_file.getframerate() == WEBRTC_SAMPLE_RATE
            output = np.frombuffer(audio_file.readframes(audio_file.getnframes()), dtype=np.int16)
        assert len(output) == WEBRTC_SAMPLE_RATE // 10
        assert output.max() > 1000


def test_phone_band_from_wav_reads_oversized_data_chunk_to_eof() -> None:
    source = bytearray(_source_wav(24_000, 1))
    data_offset = source.index(b"data") + 4
    struct.pack_into("<I", source, data_offset, 0xFFFFFFFF)

    converted = phone_band_from_wav(bytes(source))

    with wave.open(io.BytesIO(converted), "rb") as audio_file:
        assert audio_file.getframerate() == WEBRTC_SAMPLE_RATE
        assert audio_file.getnchannels() == 1


@pytest.mark.asyncio
async def test_speechify_requests_are_spaced_at_least_1_1_seconds_apart(monkeypatch) -> None:
    clock, _ = _patch_clock(monkeypatch)
    session = _SynthesisSession([(200, {}), (200, {})], lambda: clock[0])

    results = await asyncio.gather(
        _synthesize_utterance(session, "key", "first", "voice"),
        _synthesize_utterance(session, "key", "second", "voice"),
    )

    assert results == [b"audio", b"audio"]
    assert len(session.starts) == 2
    starts = sorted(session.starts)
    assert starts[1] - starts[0] >= 1.1


@pytest.mark.asyncio
async def test_speechify_retries_429_once_and_respects_retry_after(monkeypatch) -> None:
    clock, sleeps = _patch_clock(monkeypatch)
    session = _SynthesisSession([(429, {"Retry-After": "5"}), (200, {})], lambda: clock[0])

    result = await _synthesize_utterance(session, "key", "hello", "voice")

    assert result == b"audio"
    assert len(session.starts) == 2
    assert session.starts[1] - session.starts[0] >= 5
    assert sleeps == [5.0]


@pytest.mark.asyncio
async def test_speechify_raises_after_four_429_retries(monkeypatch) -> None:
    clock, sleeps = _patch_clock(monkeypatch)
    session = _SynthesisSession([(429, {}) for _ in range(5)], lambda: clock[0])

    with pytest.raises(RuntimeError, match=r"Speechify synthesis failed \(429\)"):
        await _synthesize_utterance(session, "key", "hello", "voice")

    assert len(session.starts) == 5
    assert sleeps == [2, 4, 8, 16]
