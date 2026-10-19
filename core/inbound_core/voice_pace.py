import json
from functools import cache
from importlib.resources import files


@cache
def _speechify_voice_pace() -> tuple[int, dict[str, int]]:
    data = json.loads(
        files("inbound_core")
        .joinpath("data")
        .joinpath("speechify_voice_pace.json")
        .read_text(encoding="utf-8")
    )
    return int(data["target_wpm"]), {
        str(voice_id): int(wpm) for voice_id, wpm in data["voices"].items()
    }


def speechify_voice_wpm(voice_id: str) -> int | None:
    _, voices = _speechify_voice_pace()
    return voices.get(voice_id)


def speechify_rate_percent(voice_id: str, speed: float) -> int:
    target_wpm, voices = _speechify_voice_pace()
    wpm = voices.get(voice_id)
    factor = max(1.0, target_wpm / wpm) if wpm is not None else 1.0
    factor = min(1.6, max(0.7, factor * speed))
    return round((factor - 1) * 100)
