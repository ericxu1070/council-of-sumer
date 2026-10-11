"""AGNTCY Observe initialization and explicit OTel spans with W3C propagation."""
from __future__ import annotations

import os
from contextlib import contextmanager

_ready = False


def initialize(service: str = "sumer-application") -> None:
    global _ready
    if _ready or os.getenv("SUMER_OBSERVE", "true").lower() == "false":
        return
    from ioa_observe.sdk import Observe
    Observe.init(
        app_name=service,
        api_endpoint=os.getenv("OTLP_HTTP_ENDPOINT", "http://127.0.0.1:4318"),
        telemetry_enabled=False,
        observe_sync_enabled=False,
    )
    _ready = True


@contextmanager
def span(name: str, *, parent: str = "", **attributes):
    if os.getenv("SUMER_OBSERVE", "true").lower() == "false":
        yield None
        return
    initialize()
    from opentelemetry import trace
    from opentelemetry.propagate import extract
    context = extract({"traceparent": parent}) if parent else None
    attrs = {f"sumer.{k}": v for k, v in attributes.items() if v is not None}
    with trace.get_tracer("sumer").start_as_current_span(name, context=context, attributes=attrs) as s:
        yield s


def context_headers() -> dict:
    if not _ready:
        return {}
    from opentelemetry import trace
    from opentelemetry.propagate import inject
    headers = {}
    inject(headers)
    c = trace.get_current_span().get_span_context()
    if c.is_valid:
        headers.update(trace_id=f"{c.trace_id:032x}", span_id=f"{c.span_id:016x}")
    return headers


def flush() -> None:
    if _ready:
        from opentelemetry import trace
        trace.get_tracer_provider().force_flush(timeout_millis=5000)
