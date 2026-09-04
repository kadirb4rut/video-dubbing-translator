FROM python:3.12-slim
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt .
RUN python -m pip install --no-cache-dir --upgrade "pip>=26.2" "setuptools>=83" \
    && python -m pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./alembic.ini
COPY config ./config
RUN addgroup --system lingowave && adduser --system --ingroup lingowave lingowave \
    && mkdir -p /app/data \
    && chown -R lingowave:lingowave /app
USER lingowave
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
