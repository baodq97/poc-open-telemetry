import json
import logging
import os
from typing import Dict, Any

import httpx
import pika

from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry import trace
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.propagate import extract
from opentelemetry.trace import SpanKind
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter as OTLPHTTPTraceExporter


configure_azure_monitor()
LoggingInstrumentor().instrument(set_logging_format=True)
HTTPXClientInstrumentor().instrument()


def _configure_otlp_exporter() -> None:
    try:
        endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://apm-server:4318/v1/traces")
        exporter = OTLPHTTPTraceExporter(endpoint=endpoint)
        provider = trace.get_tracer_provider()
        if hasattr(provider, "add_span_processor"):
            provider.add_span_processor(BatchSpanProcessor(exporter))
        logger.info("OTLP exporter configured endpoint=%s", endpoint)
    except Exception as e:  # noqa: BLE001
        logger.exception("Failed to configure OTLP exporter: %s", e)

logger = logging.getLogger("worker")
logger.setLevel(logging.INFO)

tracer = trace.get_tracer(__name__)
_configure_otlp_exporter()


def _normalize_headers(headers: Dict[str, Any] | None) -> Dict[str, str]:
    if not headers:
        return {}
    normalized: Dict[str, str] = {}
    for k, v in headers.items():
        key = k.decode() if isinstance(k, (bytes, bytearray)) else str(k)
        if isinstance(v, (bytes, bytearray)):
            normalized[key] = v.decode(errors="ignore")
        else:
            normalized[key] = str(v)
    return normalized


def main() -> None:
    rabbit_host = os.getenv("RABBITMQ_HOST", "localhost")
    queue_name = os.getenv("RABBITMQ_QUEUE", "chat-jobs")
    dotnet_url = os.getenv("DOTNET_SERVICE_URL", "http://localhost:8080")

    logger.info("Worker connecting to RabbitMQ host=%s queue=%s", rabbit_host, queue_name)

    connection = pika.BlockingConnection(pika.ConnectionParameters(host=rabbit_host))
    channel = connection.channel()
    channel.queue_declare(queue=queue_name, durable=True)
    channel.basic_qos(prefetch_count=10)

    def on_message(ch, method, properties, body):  # type: ignore[no-redef]
        headers = _normalize_headers(getattr(properties, "headers", None))
        context = extract(headers)
        with tracer.start_as_current_span(
            name="rabbitmq.process",
            context=context,
            kind=SpanKind.CONSUMER,
        ) as span:
            span.set_attribute("messaging.system", "rabbitmq")
            span.set_attribute("messaging.destination", queue_name)
            span.set_attribute("messaging.operation", "process")

            try:
                payload = json.loads(body.decode("utf-8"))
                message = payload.get("message", "")
                logger.info("Processing message length=%d", len(message))

                # Call .NET analyze
                with httpx.Client(base_url=dotnet_url, timeout=5.0) as client:
                    resp = client.post("/analyze", json={"text": message})
                    resp.raise_for_status()
                    analysis = resp.json()
                logger.info("Analyze result: %s", analysis)
                ch.basic_ack(delivery_tag=method.delivery_tag)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Processing failed: %s", exc)
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    channel.basic_consume(queue=queue_name, on_message_callback=on_message)
    try:
        logger.info("Worker started consuming")
        channel.start_consuming()
    except KeyboardInterrupt:
        logger.info("Worker interrupted, closing connection...")
    finally:
        channel.close()
        connection.close()


if __name__ == "__main__":
    main()


