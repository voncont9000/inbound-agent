from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from inbound_api.db import session_factory
from inbound_api.routers import (
    assistants,
    auth,
    calendar,
    contacts,
    conversations,
    gmail,
    internal_voice,
    knowledge,
    post_actions,
    tasks,
    webhooks,
)
from inbound_api.schemas import KnowledgeSourceCreate

app = FastAPI(title="Inbound API", version="0.1.0")


@app.get("/healthz", include_in_schema=False)
async def healthz() -> JSONResponse:
    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(status_code=503, content={"ok": False})
    return JSONResponse(content={"ok": True})


for router in (
    auth.router,
    assistants.router,
    knowledge.router,
    conversations.router,
    tasks.router,
    contacts.router,
    internal_voice.router,
    webhooks.router,
    gmail.router,
    calendar.router,
    post_actions.router,
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
