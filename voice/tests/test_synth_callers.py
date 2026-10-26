import io
import struct
import wave

import numpy as np

from eval.synth_callers import (
    WEBRTC_SAMPLE_RATE,
    combine_segment_wavs,
    phone_band_from_wav,
    split_paused_utterance,
    utterance_audio_path,
)


def _wav(samples: list[int]) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio_file:
        audio_file.setnchannels(1)
        audio_file.setsampwidth(2)
        audio_file.setframerate(WEBRTC_SAMPLE_RATE)
        audio_file.writeframes(np.asarray(samples, dtype=np.int16).tobytes())
    return output.getvalue()


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
