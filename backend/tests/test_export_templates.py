import json
from pathlib import Path

from inbound_api.export_templates import build_template_responses, build_template_samples

MOCKS_DIR = Path(__file__).resolve().parents[2] / "web" / "src" / "mocks"


def test_committed_template_mock_matches_current_templates() -> None:
    committed = json.loads((MOCKS_DIR / "templates.json").read_text(encoding="utf-8"))

    assert committed == build_template_responses()


def test_committed_template_samples_mock_matches_current_templates() -> None:
    committed = json.loads((MOCKS_DIR / "template_samples.json").read_text(encoding="utf-8"))

    assert committed == build_template_samples()
