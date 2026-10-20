# Build from the repo root: docker build -f services/parser/Dockerfile .
FROM python:3.12-slim
WORKDIR /app
COPY libs/telemetry /libs/telemetry
RUN pip install --no-cache-dir /libs/telemetry
COPY services/parser/pyproject.toml .
COPY services/parser/src ./src
RUN pip install --no-cache-dir .
RUN useradd --system --uid 10001 --no-create-home app
USER 10001
CMD ["parser"]
