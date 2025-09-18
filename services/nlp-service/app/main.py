import logging
import os
from typing import Dict

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter as OTLPHTTPTraceExporter


configure_azure_monitor()

logger = logging.getLogger("nlp_service")
logger.setLevel(logging.INFO)

tracer = trace.get_tracer(__name__)

app = FastAPI(title="NLP Service", version="0.1.0")
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


class ClassifyRequest(BaseModel):
    text: str


@app.get("/healthz")
async def healthz() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/classify")
async def classify_endpoint(req: ClassifyRequest) -> Dict:
    logger.info("Classify request received")

    dotnet_url = os.getenv("DOTNET_SERVICE_URL", "http://localhost:8080")

    analysis: Dict | None = None
    async with httpx.AsyncClient(base_url=dotnet_url, timeout=5.0) as client:
        try:
            resp = await client.post("/analyze", json={"text": req.text})
            resp.raise_for_status()
            analysis = resp.json()
        except httpx.HTTPError as exc:
            logger.exception(".NET analyze failed: %s", exc)
            raise HTTPException(status_code=502, detail="Analyze service unavailable") from exc

    length = int(analysis.get("length", 0))
    classification = "short" if length < 20 else "long"

    return {"classification": classification, "analysis": analysis}


