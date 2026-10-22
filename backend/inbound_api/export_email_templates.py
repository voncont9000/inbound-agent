import json
from pathlib import Path

from inbound_core.post_actions import EMAIL_TEMPLATES

from inbound_api.schemas import EmailActionTemplateResponse


def build_email_template_responses() -> list[dict]:
    return [
        EmailActionTemplateResponse(**template).model_dump(mode="json")
        for template in EMAIL_TEMPLATES
    ]


def main() -> None:
    output_dir = Path(__file__).resolve().parents[2] / "web" / "src" / "mocks"
    output = output_dir / "email_action_templates.json"
    output.write_text(
        json.dumps(build_email_template_responses(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
