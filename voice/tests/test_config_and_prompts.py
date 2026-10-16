from pathlib import Path

from inbound_voice.config import load_firm_config
from inbound_voice.prompts import render_system_prompt

ROOT = Path(__file__).resolve().parents[1]


def test_demo_firm_config_and_prompt_rules() -> None:
    firm = load_firm_config(ROOT / "config" / "demo_firm.yaml")
    prompt = render_system_prompt(firm)

    assert firm.firm_name == "Demo & Co Solicitors"
    assert "AI assistant" in prompt
    assert "must never give legal advice" in prompt
    assert "Never confirm or deny" in prompt
    assert "Never estimate, invent or promise a quote" in prompt
    assert "log_complaint" in prompt
    assert "call 999" in prompt
