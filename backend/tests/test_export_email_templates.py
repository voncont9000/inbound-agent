import json
from pathlib import Path

from inbound_api.export_email_templates import build_email_template_responses

MOCKS_DIR = Path(__file__).resolve().parents[2] / "web" / "src" / "mocks"


def test_committed_email_template_mock_matches_core_templates() -> None:
    committed = json.loads((MOCKS_DIR / "email_action_templates.json").read_text(encoding="utf-8"))

    assert committed == build_email_template_responses()
