from fastapi import FastAPI

from .domain import JobState
from .providers import provider_registry

app = FastAPI(title="LingoWave API", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "api"}


@app.get("/v1/providers")
def providers() -> dict[str, str]:
    return provider_registry()


@app.get("/v1/job-states")
def job_states() -> list[str]:
    return [state.value for state in JobState]
