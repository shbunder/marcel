"""OpenTelemetry tracing setup for LLM observability via Phoenix."""

from __future__ import annotations

import logging
from functools import lru_cache

from marcel_core.config import settings

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_tracer_provider():
    """Create and return a configured TracerProvider (singleton)."""
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({'service.name': 'marcel'})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=f'{settings.marcel_tracing_endpoint}/v1/traces')
    provider.add_span_processor(BatchSpanProcessor(exporter))
    log.info('tracing: OTLP exporter configured -> %s', settings.marcel_tracing_endpoint)
    return provider


def get_instrumentation_settings():
    """Return InstrumentationSettings if tracing is enabled, else None."""
    if not settings.marcel_tracing_enabled:
        return None

    from pydantic_ai.models.instrumented import InstrumentationSettings

    return InstrumentationSettings(tracer_provider=_get_tracer_provider())
