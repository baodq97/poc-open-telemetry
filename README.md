## PoC Microservices with OpenTelemetry (Python + .NET) → Elastic APM (OTLP)

### What this stack shows

- 2 Python services (FastAPI, asyncio): `chat-service`, `nlp-service`
- 1 Python worker using RabbitMQ (`pika`)
- 1 .NET 8 minimal API: `dotnet-service`
- RabbitMQ (management UI)
- Elastic Stack: Elasticsearch + Kibana + APM Server
- OpenTelemetry SDKs export directly to Elastic APM Server via OTLP/HTTP (no OTEL Collector)
- Optional dual-export to Azure Monitor Application Insights (enabled when `APPLICATIONINSIGHTS_CONNECTION_STRING` is set)

### Prerequisites

- Docker & Docker Compose
- `.env` with at least:
  - `ELASTIC_PASSWORD` and `KIBANA_PASSWORD`
  - Other defaults are provided in `docker-compose.yml`

### Environment (.env)

Create a `.env` file in the project root (values below are examples):

```env
# Elastic Stack version
STACK_VERSION=9.1.3

# Credentials
ELASTIC_PASSWORD=changeme
KIBANA_PASSWORD=changeme

# Ports
ES_PORT=9200
KIBANA_PORT=5601

# Elasticsearch cluster
CLUSTER_NAME=elastic-docker-cluster
LICENSE=basic
MEM_LIMIT=2g
```

### Run

```bash
docker compose --env-file .env up -d --build
```

Note: Docker Compose automatically loads a `.env` in the project root. You can omit `--env-file .env` if you keep the file there.

### Endpoints

- Chat API: `http://localhost:8000/docs`
- NLP API: `http://localhost:8001/docs`
- .NET API: `http://localhost:8080/swagger`
- RabbitMQ UI: `http://localhost:15672` (guest/guest)
- Kibana: `http://localhost:5601`
- APM Server (OTLP/HTTP): `http://localhost:8200/v1/traces`
- Elasticsearch: `https://localhost:${ES_PORT}` (TLS, CA configured in the stack)

### Demo flow

- `POST /chat` (chat-service) logs, publishes a message to RabbitMQ, then calls `nlp-service`.
- `worker` consumes the message, continues the distributed trace, and calls `.NET /analyze`.
- `GET /chat-stream` streams mock chunks with trace-aware logging.

### Telemetry notes

- OpenTelemetry SDKs send traces to Elastic APM Server via OTLP/HTTP at `apm-server:8200/v1/traces`.
- Trace context is propagated over HTTP and injected into RabbitMQ headers for end-to-end tracing.
- Optional Azure Monitor export is enabled by `APPLICATIONINSIGHTS_CONNECTION_STRING` in `docker-compose.yml`. Remove it to disable Application Insights.

### Verify in Kibana

1. Open Kibana → Observability → APM → Services. You should see:
   - `chat-service`, `nlp-service`, `worker`, `dotnet-service`.
2. Open Observability → Traces to view end-to-end trace waterfalls.

### Quick test

Trigger a chat request to generate traces:

```bash
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Hello from OTLP to Elastic APM"}'
```

Stream endpoint:

```bash
curl -N http://localhost:8000/chat-stream
```

### Notes

- The OTEL Collector is not used. Services export directly to APM Server (port 8200).
- APM Server is configured to trust the Elastic Stack CA and authenticate to Elasticsearch with the `elastic` user.
