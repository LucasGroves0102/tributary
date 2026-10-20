# Build from the repo root: docker build -f services/company-loader/Dockerfile .
FROM python:3.12-slim
WORKDIR /app
COPY libs/telemetry /libs/telemetry
COPY libs/sec /libs/sec
RUN pip install --no-cache-dir /libs/telemetry /libs/sec
COPY services/company-loader/pyproject.toml .
COPY services/company-loader/src ./src
RUN pip install --no-cache-dir .
RUN useradd --system --uid 10001 --no-create-home app
USER 10001
CMD ["company-loader"]
