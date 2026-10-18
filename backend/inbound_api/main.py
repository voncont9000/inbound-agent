from fastapi import FastAPI

from inbound_api.routers import (
    assistants,
    auth,
    contacts,
    conversations,
    internal_voice,
    knowledge,
    tasks,
)
from inbound_api.schemas import KnowledgeSourceCreate

app = FastAPI(title="Inbound API", version="0.1.0")

for router in (
    auth.router,
    assistants.router,
    knowledge.router,
    conversations.router,
    tasks.router,
    contacts.router,
    internal_voice.router,
):
    app.include_router(router)

_original_openapi = app.openapi


def _openapi():
    if app.openapi_schema is not None:
        return app.openapi_schema
    schema = _original_openapi()
    schemas = schema.setdefault("components", {}).setdefault("schemas", {})
    if "KnowledgeSourceCreate" not in schemas:
        knowledge_source_schema = KnowledgeSourceCreate.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
        for name, definition in knowledge_source_schema.pop("$defs", {}).items():
            schemas.setdefault(name, definition)
        schemas["KnowledgeSourceCreate"] = knowledge_source_schema
    app.openapi_schema = schema
    return schema


app.openapi = _openapi
