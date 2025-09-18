import asyncio
import json
import logging
import os
from typing import AsyncIterator, Dict

import httpx
import pika
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.propagate import inject
from opentelemetry.trace import SpanKind
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter as OTLPHTTPTraceExporter


# Configure Azure Monitor OpenTelemetry (uses APPLICATIONINSIGHTS_CONNECTION_STRING)
configure_azure_monitor()

logger = logging.getLogger("chat_service")
logger.setLevel(logging.INFO)

tracer = trace.get_tracer(__name__)

app = FastAPI(title="Chat Service", version="0.1.0")

# Instrument FastAPI, httpx, logging
FastAPIInstrumentor.instrument_app(app)
HTTPXClientInstrumentor().instrument()
LoggingInstrumentor().instrument(set_logging_format=True)


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
_configure_otlp_exporter()


class ChatRequest(BaseModel):
    message: str


def _publish_to_rabbitmq_sync(queue_name: str, payload: Dict, headers: Dict[str, str]) -> None:
    connection = pika.BlockingConnection(
        pika.ConnectionParameters(host=os.getenv("RABBITMQ_HOST", "localhost"))
    )
    try:
        channel = connection.channel()
        channel.queue_declare(queue=queue_name, durable=True)
        body = json.dumps(payload).encode("utf-8")
        properties = pika.BasicProperties(
            content_type="application/json",
            delivery_mode=2,
            headers=headers or {},
        )
        channel.basic_publish(exchange="", routing_key=queue_name, body=body, properties=properties)
        channel.close()
    finally:
        connection.close()


async def publish_to_rabbitmq(queue_name: str, payload: Dict) -> None:
    # Inject current trace context into carrier for RabbitMQ headers
    carrier: Dict[str, str] = {}
    inject(carrier)
    await asyncio.to_thread(_publish_to_rabbitmq_sync, queue_name, payload, carrier)


@app.get("/healthz")
async def healthz() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/chat")
async def chat_endpoint(req: ChatRequest) -> Dict:
    logger.info("Received chat request")

    nlp_url = os.getenv("NLP_SERVICE_URL", "http://localhost:8001")
    rabbitmq_queue = os.getenv("RABBITMQ_QUEUE", "chat-jobs")

    # Publish to RabbitMQ with tracing
    with tracer.start_as_current_span("publish_to_rabbitmq", kind=SpanKind.PRODUCER) as span:
        span.set_attribute("messaging.destination", rabbitmq_queue)
        span.set_attribute("chat.message_length", len(req.message))
        await publish_to_rabbitmq(rabbitmq_queue, {"message": req.message})

    # Call NLP service
    classification: Dict | None = None
    with tracer.start_as_current_span("call_nlp_service"):
        async with httpx.AsyncClient(base_url=nlp_url, timeout=5.0) as client:
            try:
                response = await client.post("/classify", json={"text": req.message})
                response.raise_for_status()
                classification = response.json()
            except httpx.HTTPError as exc:
                logger.exception("NLP service call failed: %s", exc)
                raise HTTPException(status_code=502, detail="NLP service unavailable") from exc

    return {"ok": True, "classification": classification}


@app.get("/chat-stream")
async def chat_stream() -> StreamingResponse:
    logger.info("Starting chat-stream")

    async def stream_generator() -> AsyncIterator[bytes]:
        for index in range(5):
            chunk = f"chunk-{index}\n"
            logger.info("stream_chunk_emitted index=%d", index)
            yield chunk.encode("utf-8")
            await asyncio.sleep(0.5)

    return StreamingResponse(stream_generator(), media_type="text/plain")


