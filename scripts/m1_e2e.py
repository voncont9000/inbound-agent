"""M1 integration e2e using the voice API client against the real API with fixture call artifacts.

Run with `cd voice && uv run python ../scripts/m1_e2e.py`. The API on :8000 must run with
SESSION_COOKIE_SECURE=false, and the worker and web dev server on :5173 must be running.
"""

import asyncio
import json
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from inbound_core.call_report import CallStart
from inbound_voice.api_client import ApiSettings, VoiceApiClient
from inbound_voice.call_report import build_call_report
from inbound_voice.extraction import build_intake_artifacts, build_post_call_tasks
from inbound_voice.sinks import LocalFilesCallSink

BASE = os.environ.get("E2E_BASE", "http://localhost:5173/api")
ORIGIN = "http://localhost:5173"
TO = "+441234567890"
FROM = "+447700900456"
OUT = Path(os.environ.get("E2E_RUNS_DIR", "./m1-e2e-runs"))
EVIDENCE = Path(os.environ.get("E2E_EVIDENCE", "./m1-e2e-evidence.json"))
evidence = {}


def log(k, v):
    evidence[k] = v
    print(f"## {k}: {json.dumps(v, default=str)[:1500]}")


def web(token=None):
    c = httpx.Client(base_url=BASE, headers={"Origin": ORIGIN}, timeout=20)
    return c


def signup(c, tag):
    r = c.post(
        "/auth/signup",
        json={
            "email": f"{tag}-{uuid.uuid4().hex[:6]}@example.com",
            "password": "correct-horse-battery",
            "display_name": tag,
            "tenant_name": f"{tag} LLP",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


async def call(voice, config_route, n, live_fields, result_fields, callback):
    route = await voice.route(TO, FROM)
    log(
        f"call{n}.route",
        {
            "tenant_id": route.tenant_id,
            "assistant_id": route.assistant_id,
            "context_card": route.context_card,
            "digest_present": route.digest is not None,
        },
    )
    ext = f"CA-e2e-{n}-{uuid.uuid4().hex[:8]}"
    start = CallStart(
        to_number=TO,
        from_number=FROM,
        external_call_id=ext,
        channel="phone",
        started_at=datetime.now(UTC) - timedelta(minutes=2),
    )
    conv_id = await voice.start_call(start)
    sink = LocalFilesCallSink(OUT, ext)
    utter = [
        "Hi there. I think I've been unfairly dismissed.",
        "My name is Siobhan McAllister.",
        (
            "I worked for Brightwell Logistics Limited as a warehouse supervisor "
            "for just over four years."
        ),
        "They dismissed me on the tenth of August.",
        "No, I haven't contacted ACAS yet.",
        "My email is siobhan dot mcallister at example dot com.",
        "A call back tomorrow morning would be great. Bye.",
    ]
    t0 = start.started_at
    for i, u in enumerate(utter):
        sink.append_transcript(
            "assistant",
            "Thanks, could you tell me more?",
            (t0 + timedelta(seconds=10 * i)).isoformat(),
        )
        sink.append_transcript("caller", u, (t0 + timedelta(seconds=10 * i + 5)).isoformat())
    live = {"service": "employment", "fields": live_fields}
    result = {
        "status": "completed",
        "service": "employment",
        "model": "fixture",
        "fields": result_fields,
        "callback_requested": callback,
        "complaint": {"made": False},
    }
    post, merged = build_intake_artifacts(live, result, route.config, caller_id=FROM)
    tasks = build_post_call_tasks(post, merged, route.config, [])
    sink.save_live_intake(live)
    sink.save_post_call_intake(post)
    sink.save_intake(merged)
    for t in tasks:
        sink.append_task(t.pop("type"), t)
    sink.append_task("end_call", {"source": "goodbye_detected"})
    sink.save_metrics({"call_duration_seconds": 95, "estimated_cost": {"total": 0.03}})
    report = build_call_report(sink.directory, ext)
    ok = await voice.post_report(conv_id, start, report)
    log(
        f"call{n}.report_posted",
        {
            "conversation_id": conv_id,
            "accepted": ok,
            "merged_fields": {
                k: {
                    kk: v.get(kk) for kk in ("value", "status", "confidence", "confirmed_by_caller")
                }
                for k, v in merged["fields"].items()
            },
            "tasks": report.tasks,
        },
    )
    return conv_id, route


def wait_job_done(conv_id):
    import subprocess

    for _ in range(40):
        out = subprocess.run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "db",
                "psql",
                "-U",
                "inbound_migrator",
                "-d",
                "inbound",
                "-Atc",
                (
                    "select status||'|'||attempts||'|'||coalesce(error,'') from jobs "
                    "where kind='graph_ingest' "
                    f"and payload->>'conversation_id'='{conv_id}'"
                ),
            ],
            capture_output=True,
            text=True,
            cwd=Path.home() / "repos/inbound-agent",
        ).stdout.strip()
        if out and not out.startswith(("queued", "running", "pending")):
            return out
        time.sleep(1)
    return out


async def main():
    c1 = web()
    a1 = signup(c1, "firm-one")
    t1 = a1["tenants"][0]["id"]
    log("signup1", {"user": a1["user"]["email"], "tenant": t1, "cookies": list(c1.cookies.keys())})
    tpls = c1.get("/templates").json()
    log("templates", [t["name"] for t in tpls])
    law = next(t for t in tpls if "law" in t["name"])
    r = c1.post(
        "/assistants", json={"name": "Reception", "template": law["name"], "config": law["config"]}
    )
    assert r.status_code == 201, r.text
    asst = r.json()
    log("assistant", {"id": asst["id"], "template": asst.get("template")})
    r = c1.post(
        f"/assistants/{asst['id']}/channels",
        json={"kind": "phone", "external_id": "+44 1234 567890"},
    )
    log("channel", {"status": r.status_code, "body": r.json()})
    voice = VoiceApiClient(
        ApiSettings(
            "http://localhost:8000",
            os.environ.get("VOICE_SERVICE_TOKEN", "dev-only-voice-service-token"),
        ),
        spool_dir=OUT / "spool",
    )

    def conf(v, c=0.95):
        return {
            "value": v,
            "confidence": c,
            "evidence": "transcript",
            "confirmed_by_caller": True,
        }

    def unc(v, c=0.9):
        return {
            "value": v,
            "confidence": c,
            "evidence": "transcript",
            "confirmed_by_caller": False,
        }

    conv1, _ = await call(
        voice,
        None,
        1,
        {
            "caller_name": {"value": "Siobhan McAllister", "confirmed": True},
            "email": {"value": "siobhan.mcallister@example.com", "confirmed": True},
        },
        {
            "caller_name": conf("Siobhan McAllister"),
            "email": conf("siobhan.mcallister@example.com"),
            "employer_name": unc("Brightwell Logistics Limited"),
            "job_title": unc("warehouse supervisor"),
            "length_of_service": unc("just over four years"),
            "termination_date": unc("2026-08-10"),
            "acas_early_conciliation_started": unc(False),
            "issue_type": unc("dismissal"),
        },
        {
            "requested": True,
            "reason": "Unfair dismissal advice",
            "preferred_time": "tomorrow morning",
        },
    )
    log("call1.graph_ingest_job", wait_job_done(conv1))
    r = c1.get(f"/conversations/{conv1}")
    assert r.status_code == 200, r.text
    conv = r.json()
    log(
        "call1.conversation",
        {
            "status": conv["status"],
            "person_id": conv["person_id"],
            "needs_review": conv["needs_review"],
            "messages": len(conv["messages"]),
            "intake_versions": [i["version"] for i in conv["intakes"]],
            "tasks": [(t["kind"], t["due_at"]) for t in conv["tasks"]],
            "end_reason": conv["end_reason"],
        },
    )
    contacts = c1.get("/contacts", params={"q": "07700 900456"}).json()
    log("call1.contacts_q_phone", contacts)
    if contacts["items"]:
        pid = contacts["items"][0]["id"]
        d = c1.get(f"/contacts/{pid}").json()
        log(
            "call1.contact",
            {
                "display_name": d["display_name"],
                "identities": [
                    (i.get("type"), i.get("value_normalized") or i.get("value"))
                    for i in d["identities"]
                ],
                "facts": [
                    (f["predicate"], f["value"], f["confidence"], f["status"])
                    for f in d["active_facts"]
                ],
                "open_tasks": [t["kind"] for t in d["open_tasks"]],
                "commitments": d["open_commitments"],
            },
        )
    inbox = c1.get("/conversations").json()
    log("inbox", [(i["id"], i["person_id"], i["status"]) for i in inbox["items"]])
    # second call: caller changes employer name unconfirmed; confirmed name unchanged
    conv2, route2 = await call(
        voice,
        None,
        2,
        {"caller_name": {"value": "Siobhan McAllister", "confirmed": True}},
        {
            "caller_name": unc("Siobhan MacAllister", 0.7),
            "employer_name": unc("Brightwell Logistics Ltd"),
        },
        {"requested": False},
    )
    log("call2.context_card_nonempty", bool(route2.context_card))
    log("call2.graph_ingest_job", wait_job_done(conv2))
    if contacts["items"]:
        d = c1.get(f"/contacts/{pid}").json()
        log(
            "call2.contact",
            {
                "display_name": d["display_name"],
                "conversations": len(d["conversations"]),
                "facts": [
                    (f["predicate"], f["value"], f["confidence"], f["status"])
                    for f in d["active_facts"]
                ],
            },
        )
        await asyncio.to_thread(lambda: None)
    # tenant isolation
    c2 = web()
    signup(c2, "firm-two")
    iso = {
        "t2_conversations": len(c2.get("/conversations").json()["items"]),
        "t2_contacts": len(c2.get("/contacts").json()["items"]),
        "t2_get_t1_conversation": c2.get(f"/conversations/{conv1}").status_code,
        "t2_get_t1_contact": c2.get(f"/contacts/{pid}").status_code if contacts["items"] else None,
        "t2_get_t1_assistant": c2.get(f"/assistants/{asst['id']}").status_code,
        "t2_xtenant_t1_header": c2.get("/conversations", headers={"X-Tenant-Id": t1}).status_code,
        "t2_review_t1_conversation": c2.post(
            f"/conversations/{conv1}/reviews", json={"field": "caller_name", "resolved_value": "X"}
        ).status_code,
        "t2_tasks": len(c2.get("/tasks").json()["items"]),
        "t2_knowledge_gaps": len(c2.get("/knowledge-gaps").json()["items"]),
    }
    a2asst = c2.post(
        "/assistants", json={"name": "Other", "template": law["name"], "config": law["config"]}
    ).json()
    dup = c2.post(f"/assistants/{a2asst['id']}/channels", json={"kind": "phone", "external_id": TO})
    iso["t2_claim_same_number"] = (dup.status_code, dup.text[:200])
    iso["route_after_t2_claim_tenant"] = str((await voice.route(TO, FROM)).tenant_id) == t1
    iso["unauth_conversations"] = httpx.get(BASE + "/conversations").status_code
    iso["bad_service_token_route"] = httpx.get(
        "http://localhost:8000/internal/voice/route",
        params={"to": TO},
        headers={"X-Service-Token": "nope"},
    ).status_code
    log("tenant_isolation", iso)
    EVIDENCE.write_text(json.dumps(evidence, indent=2, default=str))


asyncio.run(main())
