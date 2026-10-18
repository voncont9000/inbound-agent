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
