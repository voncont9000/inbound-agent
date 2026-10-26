from __future__ import annotations

import base64
import re
import threading
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit
from uuid import UUID

from inbound_core.call_report import RouteResponse
from loguru import logger
from pipecat.observers.base_observer import BaseObserver

if TYPE_CHECKING:
    from opentelemetry.sdk.trace.export import SpanExporter


_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_PATTERN = re.compile(r"(?<![\w-])\(?\+?\d[\d ().-]{5,}\d(?![\w-])")
_ISO_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
_tracing_setup_attempted = False
_tracing_setup_result = False
_tracing_setup_lock = threading.Lock()
_redacting_span_exporter_type: type | None = None


def tracing_enabled(env: Mapping[str, str]) -> bool:
    return bool(env.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip())


def traces_endpoint(env: Mapping[str, str]) -> str:
    override = env.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    if override and override.strip():
        return override
    endpoint = env.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip().rstrip("/")
    return f"{endpoint}/v1/traces"


def otlp_headers(env: Mapping[str, str]) -> dict[str, str] | None:
    public_key = env.get("LANGFUSE_PUBLIC_KEY")
    secret_key = env.get("LANGFUSE_SECRET_KEY")
    if not public_key or not secret_key:
        return None
    credentials = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    return {"Authorization": f"Basic {credentials}"}


def build_span_exporter(env: Mapping[str, str]) -> SpanExporter | None:
    if not tracing_enabled(env):
        return None

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    exporter = OTLPSpanExporter(endpoint=traces_endpoint(env), headers=otlp_headers(env))
    return _redacting_span_exporter_class()(exporter)


def configure_tracing(env: Mapping[str, str]) -> bool:
    global _tracing_setup_attempted, _tracing_setup_result

    if not tracing_enabled(env):
        return False

    with _tracing_setup_lock:
        if _tracing_setup_attempted:
            return _tracing_setup_result
        _tracing_setup_attempted = True
        try:
            from pipecat.utils.tracing.setup import setup_tracing

            endpoint = traces_endpoint(env)
            result = setup_tracing(
                service_name=env.get("OTEL_SERVICE_NAME") or "inbound-voice",
                exporter=build_span_exporter(env),
            )
            _tracing_setup_result = bool(result)
            if _tracing_setup_result:
                host = urlsplit(endpoint).hostname or ""
                logger.info("OpenTelemetry tracing enabled for {}", host)
        except Exception as error:
            logger.warning("OpenTelemetry tracing setup failed: {}", type(error).__name__)
            _tracing_setup_result = False
        return _tracing_setup_result


def redact_text(text: str) -> str:
    redacted = _EMAIL_PATTERN.sub("[email]", text)

    def redact_phone(match: re.Match[str]) -> str:
        value = match.group()
        digits = sum(character.isdigit() for character in value)
        if not 7 <= digits <= 15 or _ISO_DATE_PATTERN.fullmatch(value):
            return value
        return "[phone]"

    return _PHONE_PATTERN.sub(redact_phone, redacted)


def _redact_attributes(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    if attributes is None:
        return {}
    redacted = {}
    for key, value in attributes.items():
        if isinstance(value, str):
            redacted[key] = redact_text(value)
        elif isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
            redacted[key] = type(value)(redact_text(item) for item in value)
        else:
            redacted[key] = value
    return redacted


def _redacting_span_exporter_class() -> type:
    global _redacting_span_exporter_type
    if _redacting_span_exporter_type is None:
        from opentelemetry.sdk.trace import Event, ReadableSpan
        from opentelemetry.sdk.trace.export import SpanExporter

        class RedactingSpanExporter(SpanExporter):
            def __init__(self, exporter: SpanExporter) -> None:
                self._exporter = exporter

            def export(self, spans: Sequence[ReadableSpan]) -> Any:
                redacted_spans = [
                    ReadableSpan(
                        name=span.name,
                        context=span.context,
                        parent=span.parent,
                        resource=span.resource,
                        attributes=_redact_attributes(span.attributes),
                        events=[
                            Event(
                                name=event.name,
                                attributes=_redact_attributes(event.attributes),
                                timestamp=event.timestamp,
                            )
                            for event in span.events
                        ],
                        links=span.links,
                        kind=span.kind,
                        status=span.status,
                        start_time=span.start_time,
                        end_time=span.end_time,
                        instrumentation_scope=span.instrumentation_scope,
                    )
                    for span in spans
                ]
                return self._exporter.export(redacted_spans)

            def shutdown(self) -> None:
                self._exporter.shutdown()

            def force_flush(self, timeout_millis: int = 30000) -> bool:
                return self._exporter.force_flush(timeout_millis)

        _redacting_span_exporter_type = RedactingSpanExporter
    return _redacting_span_exporter_type


def __getattr__(name: str) -> Any:
    if name == "RedactingSpanExporter":
        return _redacting_span_exporter_class()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def call_span_attributes(*, channel: str, route: RouteResponse | None) -> dict[str, str]:
    attributes = {"channel": channel}
    if route is not None:
        for name in ("tenant_id", "assistant_id"):
            value = getattr(route, name, None)
            if value is not None:
                attributes[name] = str(value)
    return attributes


def _conversation_span(task: Any) -> Any:
    turn_trace_observer = getattr(task, "turn_trace_observer", None)
    return getattr(turn_trace_observer, "_conversation_span", None)


def conversation_trace_id(task: Any) -> str | None:
    span = _conversation_span(task)
    get_span_context = getattr(span, "get_span_context", None)
    if not callable(get_span_context):
        return None
    context = get_span_context()
    if not getattr(context, "is_valid", False):
        return None
    trace_id = getattr(context, "trace_id", None)
    return f"{trace_id:032x}" if isinstance(trace_id, int) and trace_id else None


def set_conversation_span_attribute(task: Any, name: str, value: Any) -> bool:
    span = _conversation_span(task)
    is_recording = getattr(span, "is_recording", None)
    set_attribute = getattr(span, "set_attribute", None)
    if not callable(is_recording) or not is_recording() or not callable(set_attribute):
        return False
    set_attribute(name, value)
    return True


class CallTraceObserver(BaseObserver):
    def __init__(self, on_trace_id: Callable[[str], None]) -> None:
        super().__init__(observe_every_push=True)
        self.on_trace_id = on_trace_id
        self.task: Any = None
        self._trace_id_recorded = False
        self._conversation_id: UUID | str | None = None
        self._conversation_id_set = False

    def set_conversation_id(self, conversation_id: UUID | str) -> None:
        self._conversation_id = conversation_id
        self._conversation_id_set = set_conversation_span_attribute(
            self.task, "conversation_id", str(conversation_id)
        )

    async def on_push_frame(self, data: Any) -> None:
        if self._trace_id_recorded and (self._conversation_id is None or self._conversation_id_set):
            return
        if self.task is None:
            return
        if not self._trace_id_recorded:
            trace_id = conversation_trace_id(self.task)
            if trace_id is not None:
                self.on_trace_id(trace_id)
                self._trace_id_recorded = True
        if self._conversation_id is not None and not self._conversation_id_set:
            self._conversation_id_set = set_conversation_span_attribute(
                self.task, "conversation_id", str(self._conversation_id)
            )
