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

    resource = Resource.create(
        {
            'service.name': 'marcel',
            'openinference.project.name': settings.marcel_tracing_project,
        }
    )
    provider = TracerProvider(resource=resource)

    # OpenInference span processor — converts pydantic-ai's gen_ai.* attributes
    # to OpenInference format for rich Phoenix rendering (tabbed messages, etc.)
    # Must be added BEFORE the exporter so spans are transformed before export.
    try:
        from openinference.instrumentation.pydantic_ai import OpenInferenceSpanProcessor

        provider.add_span_processor(OpenInferenceSpanProcessor())
        log.info('tracing: OpenInference pydantic-ai span processor registered')
    except Exception:
        log.debug('tracing: OpenInference pydantic-ai span processor not available', exc_info=True)

    exporter = OTLPSpanExporter(endpoint=f'{settings.marcel_tracing_endpoint}/v1/traces')
    provider.add_span_processor(BatchSpanProcessor(exporter))
    log.info('tracing: OTLP exporter configured -> %s', settings.marcel_tracing_endpoint)
    return provider


def get_instrumentation_settings():
    """Return InstrumentationSettings if tracing is enabled, else None."""
    if not settings.marcel_tracing_enabled:
        return None

    from pydantic_ai.models.instrumented import InstrumentationSettings

    return InstrumentationSettings(tracer_provider=_get_tracer_provider(), version=5)
