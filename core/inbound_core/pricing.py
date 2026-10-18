from pathlib import Path
from typing import Any


def load_price_table(path: str | Path) -> dict[str, Any]:
    import yaml

    with Path(path).open(encoding="utf-8") as price_file:
        return yaml.safe_load(price_file)


def calculate_cost(metrics: dict[str, Any], price_table: dict[str, Any]) -> dict[str, Any]:
    prices = price_table.get("prices", price_table)
    usage = metrics.get("usage", {})
    providers = metrics.get("providers") or {}
    stt_provider = providers.get("stt", "deepgram")
    tts_provider = providers.get("tts", "deepgram")
    llm_model = providers.get("llm_model", "google/gemini-2.5-flash")
    llm_prices = prices.get("llm_models", {}).get(llm_model, {})
    extraction_model = providers.get("extraction_model")
    extraction_prices = prices.get("llm_models", {}).get(extraction_model, {})
    transport = metrics.get("transport")
    twilio_cost = (
        _priced_cost(
            metrics.get("call_duration_seconds"),
            prices.get("twilio_uk_inbound_per_minute", {}).get("value"),
            divisor=60,
        )
        if transport == "twilio"
        else 0.0
        if transport == "webrtc"
        else None
    )
    components = {
        "stt": _priced_cost(
            metrics.get("call_duration_seconds"),
            prices.get(f"{stt_provider}_stt_per_minute", {}).get("value"),
            divisor=60,
        ),
        "tts": _priced_cost(
            usage.get("tts_characters"),
            prices.get(f"{tts_provider}_tts_per_1000_characters", {}).get("value"),
            divisor=1000,
        ),
        "llm_input": _priced_cost(
            usage.get("llm_input_tokens"),
            llm_prices.get("input_per_1m_tokens", {}).get("value"),
            divisor=1_000_000,
        ),
        "llm_output": _priced_cost(
            usage.get("llm_output_tokens"),
            llm_prices.get("output_per_1m_tokens", {}).get("value"),
            divisor=1_000_000,
        ),
        "twilio": twilio_cost,
    }
    if extraction_model is not None or "extraction_input_tokens" in usage:
        input_tokens = usage.get("extraction_input_tokens", 0)
        output_tokens = usage.get("extraction_output_tokens", 0)
        if not input_tokens and not output_tokens:
            components["extraction"] = 0.0
        else:
            extraction_input = _priced_cost(
                input_tokens,
                extraction_prices.get("input_per_1m_tokens", {}).get("value"),
                divisor=1_000_000,
            )
            extraction_output = _priced_cost(
                output_tokens,
                extraction_prices.get("output_per_1m_tokens", {}).get("value"),
                divisor=1_000_000,
            )
            components["extraction"] = (
                None
                if extraction_input is None or extraction_output is None
                else extraction_input + extraction_output
            )
    return {
        "currency": price_table.get("currency", "USD"),
        "components": components,
        "total": round(sum(value for value in components.values() if value is not None), 8),
        "unknown_components": [name for name, value in components.items() if value is None],
    }


def _priced_cost(quantity: Any, rate: Any, divisor: int) -> float | None:
    if quantity is None or rate is None:
        return None
    return float(quantity) * float(rate) / divisor
