"""AWS Lambda adapter for the lightweight FastAPI control plane."""

from mangum import Mangum

from .main import app

# API Gateway owns the public HTTP boundary. Large media files use the existing
# presigned S3 flow; this handler is intentionally limited to control-plane APIs.
handler = Mangum(app, lifespan="off")
