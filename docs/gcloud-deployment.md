# Google Cloud Deployment Guide -- espresso-service

This document describes everything needed to deploy espresso-service to Google Cloud Run.
It covers infrastructure setup, code changes, CI/CD, cost optimization, and verification.

---

## Table of Contents

1. [Why Cloud Run](#1-why-cloud-run)
2. [Prerequisites](#2-prerequisites)
3. [Infrastructure Setup](#3-infrastructure-setup)
4. [Code Changes Required](#4-code-changes-required)
5. [CI/CD Pipeline](#5-cicd-pipeline)
6. [Cost Optimization](#6-cost-optimization)
7. [Verification Checklist](#7-verification-checklist)

---

## 1. Why Cloud Run

Cloud Run is the best fit for this workload. espresso-service is a stateless FastAPI
microservice with bursty traffic and long idle periods. Cloud Run provides:

- Scale to zero (no cost when idle)
- Per-request billing
- Sub-second autoscaling
- Zero Kubernetes ops overhead
- Built-in managed load balancer, TLS, and health-check routing
- CPU-only (no GPU needed) fits Cloud Run's resource model

Comparison with alternatives:

| Criterion         | Cloud Run        | GKE Autopilot      | Compute Engine MIG   |
|-------------------|------------------|---------------------|----------------------|
| Scale to zero     | Yes (native)     | No (min 1 pod)      | No (min 1 instance)  |
| Ops overhead      | Near-zero        | Medium (k8s)        | High (OS patching)   |
| Idle cost         | $0 at zero       | ~$30-50/mo min      | ~$25+/mo min         |
| Burst scaling     | Seconds          | Minutes             | Minutes              |
| Cold start tools  | Startup probe, min-instances, CPU boost | Pod readiness probes | N/A |

When you would outgrow it: >8 vCPUs per instance, gRPC bidirectional streaming,
or many microservices sharing a cluster.

---

## 2. Prerequisites

Before starting, ensure the following are in place:

- A Google Cloud project with billing enabled
- `gcloud` CLI installed and authenticated
- APIs enabled:
  ```bash
  gcloud services enable \
    run.googleapis.com \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com \
    cloudscheduler.googleapis.com \
    secretmanager.googleapis.com
  ```
- An Artifact Registry Docker repository:
  ```bash
  gcloud artifacts repositories create espresso-repo \
    --repository-format=docker \
    --location=europe-west1 \
    --description="espresso-service container images"
  ```

---

## 3. Infrastructure Setup

### 3.1 Secret Manager

Store all secrets in Google Secret Manager. Cloud Run will inject them as environment
variables at runtime. `app/config.py` (Pydantic Settings) requires zero changes -- it
reads from env vars regardless of source.

```bash
# Create each secret
echo -n "your-api-key" | gcloud secrets create espresso-api-key --data-file=-
echo -n "nim-key1,nim-key2" | gcloud secrets create espresso-nim-keys --data-file=-
echo -n "https://your-project.supabase.co" | gcloud secrets create espresso-supabase-url --data-file=-
echo -n "your-supabase-service-key" | gcloud secrets create espresso-supabase-service-key --data-file=-
```

### 3.2 IAM Service Accounts

Two service accounts are needed:

1. **Cloud Run runtime** (auto-created, or create a dedicated one):
   - Needs `secretmanager.secretAccessor` on the secrets above
2. **Avelero caller** (for authenticated invocation):
   ```bash
   gcloud iam service-accounts create avelero-caller \
     --display-name="Avelero espresso-service caller"

   gcloud run services add-iam-policy-binding espresso-service \
     --region=europe-west1 \
     --member="serviceAccount:avelero-caller@$PROJECT_ID.iam.gserviceaccount.com" \
     --role="roles/run.invoker"
   ```

### 3.3 Deploy Command

```bash
gcloud run deploy espresso-service \
  --image europe-west1-docker.pkg.dev/$PROJECT_ID/espresso-repo/espresso-service:$TAG \
  --region europe-west1 \
  --platform managed \
  --execution-environment gen2 \
  --cpu 4 \
  --memory 4Gi \
  --timeout 300s \
  --concurrency 4 \
  --min-instances 1 \
  --max-instances 10 \
  --cpu-boost \
  --no-cpu-throttling \
  --port 8000 \
  --set-secrets "API_KEY=espresso-api-key:latest,NIM_API_KEYS=espresso-nim-keys:latest,SUPABASE_URL=espresso-supabase-url:latest,SUPABASE_SERVICE_KEY=espresso-supabase-service-key:latest" \
  --no-allow-unauthenticated
```

Flag rationale:

| Flag                  | Value          | Rationale                                                        |
|-----------------------|----------------|------------------------------------------------------------------|
| `--cpu 4`             | 4 vCPU         | LightGBM uses OpenMP parallelism; models B+C run in parallel     |
| `--memory 4Gi`        | 4 GiB          | Three models (~150-450MB loaded) plus batch processing headroom  |
| `--concurrency 4`     | 4 req/instance | Each batch is CPU-heavy; more causes OpenMP thread contention    |
| `--min-instances 1`   | 1 warm         | Eliminates cold starts for first request; costs ~$9/mo idle      |
| `--max-instances 10`  | 10             | 10 x 4 concurrency = 40 simultaneous batch requests              |
| `--cpu-boost`         | On             | Doubles CPU during startup, cuts model deserialization 30-50%    |
| `--no-cpu-throttling` | Always-on CPU  | Keeps NIM key cooldown timers and cache TTL eviction running     |
| `--timeout 300s`      | 5 min          | Matches 300s timeout in batch_predict.py for worst-case batches  |

### 3.4 Startup and Liveness Probes

After deploying, configure probes pointing at the new `/ready` endpoint (see code changes):

```bash
gcloud run services update espresso-service \
  --region europe-west1 \
  --startup-probe-path=/api/v1/ready \
  --startup-probe-initial-delay=10 \
  --startup-probe-period=5 \
  --startup-probe-failure-threshold=24 \
  --liveness-probe-path=/api/v1/health \
  --liveness-probe-period=30
```

### 3.5 Autoscaling Behavior

```
Concurrent requests    Active instances
0                      1 (min-instances)
1-4                    1
5-8                    2
9-12                   3
...
37-40                  10 (max)
41+                    Queued / 503
```

### 3.6 Traffic Routing and Authentication

Avelero calls a single stable Cloud Run URL. Cloud Run handles all routing, health
checking, TLS termination, and instance lifecycle. No load balancer configuration needed.

Authentication is two layers:

1. **IAM layer**: Only `avelero-caller` service account can invoke the service
2. **Application layer**: Bearer token via existing `verify_api_key` middleware

Architecture:

```
Avelero (apps/api)
    |
    | HTTPS + IAM token + Bearer API_KEY
    v
Cloud Run managed LB (automatic TLS, anycast)
    |-- Instance 1 (warm, min-instances=1)
    |-- Instance 2..N (autoscaled on demand)
    |
    |--> Supabase PostgREST (external HTTPS)
    |--> NVIDIA NIM API (external HTTPS)
```

Avelero-side recommendations:
- Send batch requests sequentially (not parallel) to avoid overwhelming the service
- Use 300s timeout matching the service timeout
- Implement exponential backoff on 503 responses
- Optionally use a Trigger.dev job for async prediction requests

---

## 4. Code Changes Required

### 4.1 Dockerfile -- Rewrite for Cloud Run

**File:** `Dockerfile`

Changes from current:
- Remove `HEALTHCHECK` directive (Cloud Run ignores it, uses its own probes)
- Use `$PORT` env var (Cloud Run injects this)
- Add `--timeout-keep-alive 75` (prevents 502 errors; Cloud Run LB keepalive is 70s)
- Add `--workers 1` (horizontal scaling via instances, not workers -- multiple workers
  would duplicate model memory)

New Dockerfile:

```dockerfile
FROM python:3.12-slim AS base
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml .
RUN pip install --no-cache-dir .

FROM base AS production
COPY app/ app/
COPY espresso_models/ espresso_models/

ENV PORT=8000
EXPOSE ${PORT}

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --timeout-keep-alive 75
```

### 4.2 Parallel Model Loading

**File:** `app/models/loader.py`

Currently models load sequentially (total startup = sum of all three load times).
Change to parallel loading with ThreadPoolExecutor (total = max of the three).
Pickle deserialization involves I/O and C-level operations that release the GIL,
so true parallelism is achieved.

Replace the `load_all` method:

```python
import concurrent.futures

def load_all(self) -> None:
    from espresso_models.model_a.model import CarbonFootprintModel
    from espresso_models.model_b.model import CarbonFootprintModelB
    from espresso_models.model_c.model import CarbonFootprintModelC

    loaders = {
        "A": (CarbonFootprintModel.load, self._paths["A"]),
        "B": (CarbonFootprintModelB.load, self._paths["B"]),
        "C": (CarbonFootprintModelC.load, self._paths["C"]),
    }

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = {}
        for name, (load_fn, path) in loaders.items():
            if path.exists():
                logger.info("Loading Model %s from %s", name, path)
                futures[name] = pool.submit(load_fn, path)
            else:
                logger.warning("Model %s artifact not found at %s", name, path)

        for name, future in futures.items():
            try:
                self._models[name] = future.result()
                logger.info("Model %s loaded successfully", name)
            except Exception:
                logger.exception("Failed to load Model %s", name)

    self._loaded = True
```

### 4.3 Readiness Endpoint

**File:** `app/api/v1/health.py`

The current `/health` endpoint calls `nim_client.health_check()` (external HTTP) --
too slow and unreliable for a startup probe. Add a lightweight `/ready` endpoint that
only checks whether models are loaded.

Add this to the existing file:

```python
@router.get("/ready")
async def readiness_check(request: Request) -> dict:
    model_loader = request.app.state.model_loader
    if not model_loader.is_loaded:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=503,
            content={"status": "starting", "models": model_loader.status()},
        )
    return {"status": "ready", "models": model_loader.status()}
```

Note: With the current lifespan approach, `model_loader.is_loaded` will always be True
by the time requests arrive (lifespan blocks until loading completes). If you later move
to background loading for faster startup-probe response, the 503 path becomes relevant.

### 4.4 Structured JSON Logging

**File:** `app/main.py`

Cloud Logging parses structured JSON automatically. Add a `configure_logging()` function
and call it at the top of `create_app()`:

```python
import logging
import structlog

def configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )

def create_app() -> FastAPI:
    configure_logging()
    # ... rest of create_app unchanged
```

### 4.5 Summary of File Changes

| File                   | Change                                              | Priority |
|------------------------|-----------------------------------------------------|----------|
| `Dockerfile`           | Rewrite: remove HEALTHCHECK, $PORT, keepalive, 1 worker | Required |
| `app/models/loader.py` | Parallel model loading with ThreadPoolExecutor      | High     |
| `app/api/v1/health.py` | Add `/ready` endpoint (no external HTTP calls)      | High     |
| `app/main.py`          | Add structlog JSON config for Cloud Logging         | Medium   |
| `cloudbuild.yaml`      | New file: CI/CD pipeline (see section 5)            | Medium   |

`app/config.py` requires zero changes. Pydantic Settings reads from env vars, which
Secret Manager provides transparently.

---

## 5. CI/CD Pipeline

### 5.1 cloudbuild.yaml

Create `cloudbuild.yaml` in the repository root:

```yaml
steps:
  # Run tests
  - name: "python:3.12-slim"
    entrypoint: bash
    args:
      - "-c"
      - |
        pip install -e ".[dev]" && pytest tests/ -x -q

  # Build container image
  - name: "gcr.io/cloud-builders/docker"
    args:
      - "build"
      - "-t"
      - "europe-west1-docker.pkg.dev/$PROJECT_ID/espresso-repo/espresso-service:$SHORT_SHA"
      - "-t"
      - "europe-west1-docker.pkg.dev/$PROJECT_ID/espresso-repo/espresso-service:latest"
      - "."

  # Push to Artifact Registry
  - name: "gcr.io/cloud-builders/docker"
    args:
      - "push"
      - "--all-tags"
      - "europe-west1-docker.pkg.dev/$PROJECT_ID/espresso-repo/espresso-service"

  # Deploy to Cloud Run
  - name: "gcr.io/cloud-builders/gcloud"
    args:
      - "run"
      - "deploy"
      - "espresso-service"
      - "--image"
      - "europe-west1-docker.pkg.dev/$PROJECT_ID/espresso-repo/espresso-service:$SHORT_SHA"
      - "--region"
      - "europe-west1"
      - "--platform"
      - "managed"

images:
  - "europe-west1-docker.pkg.dev/$PROJECT_ID/espresso-repo/espresso-service:$SHORT_SHA"
  - "europe-west1-docker.pkg.dev/$PROJECT_ID/espresso-repo/espresso-service:latest"

options:
  logging: CLOUD_LOGGING_ONLY
```

### 5.2 Trigger Setup

```bash
gcloud builds triggers create github \
  --repo-name=espresso-service \
  --repo-owner=YOUR_ORG \
  --branch-pattern="^main$" \
  --build-config=cloudbuild.yaml \
  --description="Deploy espresso-service on push to main"
```

---

## 6. Cost Optimization

### 6.1 Cost Estimates

| Scenario                              | min-instances | Monthly cost     |
|---------------------------------------|---------------|------------------|
| Scale to zero, pay only per-request   | 0             | ~$10/mo (100 req/day, 30s avg) |
| Always-warm single instance           | 1             | ~$275/mo         |
| Scheduled: warm business hours only   | 0 nights, 1 days | ~$100-120/mo |

The scheduled approach is recommended. Instant response during business hours,
cold start latency accepted during off-hours when traffic is minimal.

### 6.2 Scheduled Scaling

Use Cloud Scheduler to toggle min-instances. Requires a service account with
`run.services.update` permission.

```bash
# Create the scheduler service account
gcloud iam service-accounts create espresso-scheduler \
  --display-name="espresso-service Cloud Scheduler"

# Grant it permission to update the Cloud Run service
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:espresso-scheduler@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/run.developer"

# Scale down at 22:00 UTC weekdays
gcloud scheduler jobs create http espresso-scale-down \
  --schedule="0 22 * * 1-5" \
  --uri="https://run.googleapis.com/v2/projects/$PROJECT_ID/locations/europe-west1/services/espresso-service" \
  --http-method=PATCH \
  --message-body='{"scaling":{"minInstanceCount":0}}' \
  --oauth-service-account-email="espresso-scheduler@$PROJECT_ID.iam.gserviceaccount.com"

# Scale up at 07:00 UTC weekdays
gcloud scheduler jobs create http espresso-scale-up \
  --schedule="0 7 * * 1-5" \
  --uri="https://run.googleapis.com/v2/projects/$PROJECT_ID/locations/europe-west1/services/espresso-service" \
  --http-method=PATCH \
  --message-body='{"scaling":{"minInstanceCount":1}}' \
  --oauth-service-account-email="espresso-scheduler@$PROJECT_ID.iam.gserviceaccount.com"
```

---

## 7. Verification Checklist

Run through these steps after implementing the code changes and deploying:

### Local Verification

- [ ] `docker build -t espresso-test .` succeeds
- [ ] `docker run -p 8000:8000 --env-file .env espresso-test` starts successfully
- [ ] `curl localhost:8000/api/v1/ready` returns 200 with `{"status": "ready", ...}`
- [ ] `curl localhost:8000/api/v1/health` returns 200 with full health status
- [ ] Logs show parallel "Loading Model X" timestamps (overlapping, not sequential)
- [ ] Logs are valid JSON (one JSON object per line)
- [ ] `pytest tests/` passes with no regressions

### Cloud Run Verification

- [ ] `gcloud run deploy` completes without errors
- [ ] Service URL is accessible with IAM token + API key
- [ ] Unauthenticated requests are rejected (403)
- [ ] Set min-instances=0, wait for scale-down, send a request -- measure cold start
- [ ] Send concurrent requests, verify instance count increases in Cloud Console
- [ ] Trigger Cloud Scheduler jobs manually, verify min-instances changes
- [ ] Check Cloud Logging for structured JSON log entries
- [ ] Startup probe logs show `/api/v1/ready` being polled during cold start

### Avelero Integration

- [ ] Avelero API can obtain IAM identity token for `avelero-caller` service account
- [ ] Batch prediction requests complete successfully end-to-end
- [ ] 503 responses during scale-up are handled with exponential backoff
- [ ] Request timeout set to 300s on the Avelero side
