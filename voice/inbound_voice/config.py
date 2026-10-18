from __future__ import annotations

import os
from pathlib import Path

from inbound_core.config import (
    AssistantConfig,
    DateDeadlineUrgencyRule,
    FieldSpec,
    KeywordUrgencyRule,
    KnowledgeConfig,
    KnowledgeFAQ,
    KnowledgeSource,
    OfficeHours,
    PriceItem,
    ServiceConfig,
    UrgencyRule,
    _deep_merge,
)
from inbound_core.config import (
    load_assistant_config as _load_assistant_config,
)

VOICE_ROOT = Path(__file__).resolve().parents[1]


def load_assistant_config(path: str | Path) -> AssistantConfig:
    digest_override = os.environ.get("KNOWLEDGE_DIGEST_PATH", "").strip()
    config_path = Path(path)
    template_dir = config_path.parent.parent / "templates"
    if not template_dir.is_dir():
        template_dir = VOICE_ROOT / "config" / "templates"
    return _load_assistant_config(
        config_path,
        template_dir=template_dir,
        digest_root=VOICE_ROOT,
        digest_path_override=digest_override or None,
    )


__all__ = [
    "AssistantConfig",
    "DateDeadlineUrgencyRule",
    "FieldSpec",
    "KeywordUrgencyRule",
    "KnowledgeConfig",
    "KnowledgeFAQ",
    "KnowledgeSource",
    "OfficeHours",
    "PriceItem",
    "ServiceConfig",
    "UrgencyRule",
    "VOICE_ROOT",
    "_deep_merge",
    "load_assistant_config",
]
