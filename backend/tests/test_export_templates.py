import json
from pathlib import Path

from inbound_api.export_templates import build_template_responses

TEMPLATES_PATH = Path(__file__).resolve().parents[2] / "web" / "src" / "mocks" / "templates.json"


def test_committed_template_mock_matches_current_templates() -> None:
    committed = json.loads(TEMPLATES_PATH.read_text(encoding="utf-8"))

    assert committed == build_template_responses()
