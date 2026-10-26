import asyncio
import base64
import importlib
import uuid
from types import SimpleNamespace

import pytest
from inbound_core.call_report import RouteResponse
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pipecat.frames.frames import EndFrame, LLMRunFrame, UserStartedSpeakingFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

import inbound_voice.tracing as tracing


@pytest.fixture
def reset_tracing_setup_cache():
    previous = tracing._tracing_setup_attempted, tracing._tracing_setup_result
    tracing._tracing_setup_attempted = False
    tracing._tracing_setup_result = False
    yield
    tracing._tracing_setup_attempted, tracing._tracing_setup_result = previous


def test_disabled_tracing_does_not_call_setup(monkeypatch, reset_tracing_setup_cache) -> None:
    setup_module = importlib.import_module("pipecat.utils.tracing.setup")

    def unexpected_setup(**_kwargs):
        raise AssertionError("setup should not be called")

    monkeypatch.setattr(setup_module, "setup_tracing", unexpected_setup)

    assert tracing.configure_tracing({}) is False
    assert tracing.configure_tracing({"OTEL_EXPORTER_OTLP_ENDPOINT": "  "}) is False
    assert tracing.build_span_exporter({}) is None


def test_traces_endpoint_supports_langfuse_default_and_override() -> None:
    assert (
        tracing.traces_endpoint(
            {"OTEL_EXPORTER_OTLP_ENDPOINT": "https://cloud.langfuse.com/api/public/otel"}
        )
        == "https://cloud.langfuse.com/api/public/otel/v1/traces"
    )
    assert (
        tracing.traces_endpoint(
            {"OTEL_EXPORTER_OTLP_ENDPOINT": "https://cloud.langfuse.com/api/public/otel///"}
        )
        == "https://cloud.langfuse.com/api/public/otel/v1/traces"
    )
    assert (
        tracing.traces_endpoint(
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": "https://cloud.langfuse.com/api/public/otel",
                "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "https://collector.example/traces",
            }
        )
        == "https://collector.example/traces"
    )


def test_otlp_headers_require_both_langfuse_keys() -> None:
    expected = "Basic " + base64.b64encode(b"pk:sk").decode()
    assert tracing.otlp_headers({"LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk"}) == {
        "Authorization": expected
    }
    assert tracing.otlp_headers({"LANGFUSE_PUBLIC_KEY": "pk"}) is None
    assert tracing.otlp_headers({"LANGFUSE_SECRET_KEY": "sk"}) is None


def test_build_span_exporter_wraps_otlp_exporter() -> None:
    exporter = tracing.build_span_exporter(
        {
            "OTEL_EXPORTER_OTLP_ENDPOINT": "https://cloud.langfuse.com/api/public/otel",
            "LANGFUSE_PUBLIC_KEY": "pk",
            "LANGFUSE_SECRET_KEY": "sk",
        }
    )

    assert isinstance(exporter, tracing.RedactingSpanExporter)
    assert isinstance(exporter._exporter, OTLPSpanExporter)
    assert exporter._exporter._endpoint == ("https://cloud.langfuse.com/api/public/otel/v1/traces")
    expected = "Basic " + base64.b64encode(b"pk:sk").decode()
    assert exporter._exporter._client._headers["authorization"] == expected


def test_configure_tracing_calls_setup_once(monkeypatch, reset_tracing_setup_cache) -> None:
    setup_module = importlib.import_module("pipecat.utils.tracing.setup")
    calls = []

    def setup(**kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(setup_module, "setup_tracing", setup)
    env = {"OTEL_EXPORTER_OTLP_ENDPOINT": "https://trace.example/otel"}

    assert tracing.configure_tracing(env) is True
    assert tracing.configure_tracing(env) is True
    assert len(calls) == 1
    assert calls[0]["service_name"] == "inbound-voice"
    assert isinstance(calls[0]["exporter"], tracing.RedactingSpanExporter)


def test_configure_tracing_returns_false_when_setup_raises(
    monkeypatch, reset_tracing_setup_cache
) -> None:
    setup_module = importlib.import_module("pipecat.utils.tracing.setup")

    def fail(**_kwargs):
        raise ValueError("do not include this message in logs")

    monkeypatch.setattr(setup_module, "setup_tracing", fail)

    assert (
        tracing.configure_tracing({"OTEL_EXPORTER_OTLP_ENDPOINT": "https://trace.example"}) is False
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Email jane.doe+call@example.com", "Email [email]"),
        ("Call +44 7700 900123", "Call [phone]"),
        ("Call 07700900123", "Call [phone]"),
        ("Call (020) 7946 0958", "Call [phone]"),
        ("2026-10-24", "2026-10-24"),
        ("2026-10-24T13:08:00Z", "2026-10-24T13:08:00Z"),
        ("f47ac10b-58cc-4372-a567-0e02b2c3d479", "f47ac10b-58cc-4372-a567-0e02b2c3d479"),
        ("13:08", "13:08"),
        ("£120", "£120"),
        ("Call 3 times", "Call 3 times"),
    ],
)
def test_redact_text(value: str, expected: str) -> None:
    assert tracing.redact_text(value) == expected


def test_redacting_span_exporter_redacts_span_and_event_attributes() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(tracing.RedactingSpanExporter(exporter)))
    tracer = provider.get_tracer("redaction-test")
    tenant_id = str(uuid.uuid4())

    with tracer.start_as_current_span(
        "call",
        attributes={
            "input": "call me on +447700900123 or jane@example.com",
            "tenant_id": tenant_id,
            "metrics.ttfb": 0.2,
            "list": ("x jane@example.com",),
        },
    ) as span:
        span.add_event("input", {"email": "jane@example.com"})

    exported = exporter.get_finished_spans()[0]
    assert exported.name == "call"
    assert exported.context.trace_id == span.get_span_context().trace_id
    assert exported.attributes["input"] == "call me on [phone] or [email]"
    assert exported.attributes["tenant_id"] == tenant_id
    assert exported.attributes["metrics.ttfb"] == 0.2
    assert exported.attributes["list"] == ("x [email]",)
    assert exported.events[0].attributes["email"] == "[email]"
    provider.shutdown()


class PassthroughProcessor(FrameProcessor):
    async def process_frame(self, frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


@pytest.fixture(scope="module")
def pipecat_in_memory_exporter():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(tracing.RedactingSpanExporter(exporter)))
    trace.set_tracer_provider(provider)
    yield exporter
    provider.shutdown()


def test_call_trace_observer_records_conversation_trace_id(pipecat_in_memory_exporter) -> None:
    recorded: list[str] = []
    observer = tracing.CallTraceObserver(recorded.append)
    tenant_id = uuid.uuid4()
    assistant_id = uuid.uuid4()
    route = RouteResponse.model_construct(tenant_id=tenant_id, assistant_id=assistant_id)
    conversation_id = uuid.uuid4()

    async def run_pipeline() -> None:
        task = PipelineTask(
            Pipeline([PassthroughProcessor()]),
            enable_tracing=True,
            conversation_id="call-1",
            additional_span_attributes=tracing.call_span_attributes(channel="phone", route=route),
            observers=[observer],
        )
        observer.task = task
        runner = asyncio.create_task(PipelineRunner(handle_sigint=False).run(task))
        await asyncio.sleep(0.05)
        await task.queue_frame(LLMRunFrame())
        await task.queue_frame(UserStartedSpeakingFrame())
        observer.set_conversation_id(conversation_id)
        await task.queue_frame(EndFrame())
        await runner

    asyncio.run(run_pipeline())

    exported = pipecat_in_memory_exporter.get_finished_spans()
    conversation = next(span for span in exported if span.name == "conversation")
    assert len(recorded) == 1
    assert len(recorded[0]) == 32
    assert recorded[0] == recorded[0].lower()
    assert recorded[0] == f"{conversation.context.trace_id:032x}"
    assert conversation.attributes["channel"] == "phone"
    assert conversation.attributes["tenant_id"] == str(tenant_id)
    assert conversation.attributes["assistant_id"] == str(assistant_id)
    assert conversation.attributes["conversation_id"] == str(conversation_id)
    assert conversation.attributes["conversation.id"] == "call-1"


def test_call_trace_helpers_degrade_without_pipecat_span() -> None:
    assert tracing.conversation_trace_id(SimpleNamespace()) is None
    assert (
        tracing.set_conversation_span_attribute(SimpleNamespace(), "conversation_id", "id") is False
    )
    assert tracing.call_span_attributes(channel="web", route=None) == {"channel": "web"}
