import json
from pathlib import Path

from inbound_core.templates import list_templates

from inbound_api.schemas import TemplateResponse
from inbound_api.settings import get_settings


def build_template_responses() -> list[dict]:
    templates_dir = get_settings().templates_dir
    return [
        TemplateResponse(
            name=template.name,
            label=template.meta.label,
            description=template.meta.description,
            sets_up=template.meta.sets_up,
            sample_business_name=(
                template.meta.sample.business_name if template.meta.sample else None
            ),
            config=template.config,
        ).model_dump(mode="json")
        for template in list_templates(templates_dir)
    ]


def build_template_samples() -> dict[str, dict]:
    return {
        template.name: template.meta.sample.model_dump(mode="json")
        for template in list_templates(get_settings().templates_dir)
        if template.meta.sample is not None
    }


def main() -> None:
    output_dir = Path(__file__).resolve().parents[2] / "web" / "src" / "mocks"
    output = output_dir / "templates.json"
    output.write_text(
        json.dumps(build_template_responses(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "template_samples.json").write_text(
        json.dumps(build_template_samples(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
