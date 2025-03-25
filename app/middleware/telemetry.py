from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
import atexit
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import FastAPI, Request
from typing import Callable
import time

class TelemetryMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, service_name: str = "backend-service"):
        super().__init__(app)
        self.service_name = service_name
        self.setup_telemetry()

    def setup_telemetry(self):
        # Initialize tracing
        resource = Resource.create({"service.name": self.service_name})

        # Set up trace provider
        tracer_provider = TracerProvider(resource=resource)
        span_processor = BatchSpanProcessor(OTLPSpanExporter())
        tracer_provider.add_span_processor(span_processor)
        trace.set_tracer_provider(tracer_provider)

        # Set up metrics
        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter()
        )
        metric_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
        metrics.set_meter_provider(metric_provider)

        # Create meters for metrics
        self.meter = metrics.get_meter(__name__)

        # Create standard metrics
        self.http_requests_counter = self.meter.create_counter(
            name="http_requests_total",
            description="Total number of HTTP requests",
            unit="1"
        )

        self.http_request_duration = self.meter.create_histogram(
            name="http_request_duration_seconds",
            description="HTTP request duration in seconds",
            unit="s"
        )

        # Register cleanup
        atexit.register(self.cleanup_telemetry)

    async def dispatch(self, request: Request, call_next: Callable):
        start_time = time.time()
        response = await call_next(request)
        duration = time.time() - start_time

        # Record metrics
        self.http_requests_counter.add(1, {
            "path": request.url.path,
            "method": request.method
        })
        self.http_request_duration.record(duration, {
            "path": request.url.path,
            "method": request.method
        })

        return response

    def cleanup_telemetry(self):
        try:
            # Try to flush first
            if hasattr(trace.get_tracer_provider(), "force_flush"):
                trace.get_tracer_provider().force_flush()
            if hasattr(metrics.get_meter_provider(), "force_flush"):
                metrics.get_meter_provider().force_flush()
        except Exception:
            # If flush fails, just shutdown without flushing
            pass

        # Always attempt shutdown
        if hasattr(trace.get_tracer_provider(), "shutdown"):
            trace.get_tracer_provider().shutdown()
        if hasattr(metrics.get_meter_provider(), "shutdown"):
            metrics.get_meter_provider().shutdown()