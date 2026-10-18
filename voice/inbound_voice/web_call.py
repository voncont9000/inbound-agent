from __future__ import annotations

from pipecat.frames.frames import Frame, InputAudioRawFrame, InterruptionFrame, OutputAudioRawFrame
from pipecat.serializers.base_serializer import FrameSerializer


class PcmWebSocketSerializer(FrameSerializer):
    async def deserialize(self, data: str | bytes) -> Frame | None:
        if not isinstance(data, bytes) or len(data) % 2:
            return None
        return InputAudioRawFrame(audio=data, sample_rate=16000, num_channels=1)

    async def serialize(self, frame: Frame) -> str | bytes | None:
        if isinstance(frame, OutputAudioRawFrame):
            return frame.audio
        if isinstance(frame, InterruptionFrame):
            return '{"type":"clear"}'
        return None
