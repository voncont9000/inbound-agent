from __future__ import annotations

import numpy as np

from eval.twilio_sim import (
    _parse_stream_twiml,
    frame_mulaw,
    mulaw_to_pcm16,
    pcm16_to_mulaw,
)


def test_mulaw_round_trip_stays_within_quantization_tolerance() -> None:
    samples = (np.sin(np.linspace(0, 18 * np.pi, 8000, endpoint=False)) * 12000).astype(np.int16)

    decoded = mulaw_to_pcm16(pcm16_to_mulaw(samples))

    assert decoded.dtype == np.int16
    assert np.mean(np.abs(decoded.astype(np.int32) - samples.astype(np.int32))) < 500


def test_mulaw_framing_uses_twenty_ms_frames_and_pads_with_silence() -> None:
    payload = bytes(range(161))

    frames = frame_mulaw(payload)

    assert [len(frame) for frame in frames] == [160, 160]
    assert frames[0] == payload[:160]
    assert frames[1] == payload[160:] + b"\xff" * 159


def test_twiml_parse_returns_stream_url_and_parameters() -> None:
    url, parameters = _parse_stream_twiml(
        '<Response><Connect><Stream url="wss://example.test/twilio/ws">'
        '<Parameter name="To" value="+441"/><Parameter name="From" value="+442"/>'
        "</Stream></Connect></Response>"
    )

    assert url == "wss://example.test/twilio/ws"
    assert parameters == {"To": "+441", "From": "+442"}
