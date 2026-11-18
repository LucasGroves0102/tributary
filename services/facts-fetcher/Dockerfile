# Build from the repo root: docker build -f services/facts-fetcher/Dockerfile .
FROM python:3.12-slim
WORKDIR /app
COPY libs/telemetry /libs/telemetry
COPY libs/sec /libs/sec
COPY libs/bodies /libs/bodies
RUN pip install --no-cache-dir /libs/telemetry /libs/sec /libs/bodies
COPY services/facts-fetcher/pyproject.toml .
COPY services/facts-fetcher/src ./src
RUN pip install --no-cache-dir .
RUN useradd --system --uid 10001 --no-create-home app && install -d -o app /bodies
USER 10001
CMD ["facts-fetcher"]
