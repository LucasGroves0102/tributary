# Build from the repo root: docker build -f services/doc-fetcher/Dockerfile .
FROM python:3.12-slim
WORKDIR /app
COPY libs/sec /libs/sec
RUN pip install --no-cache-dir /libs/sec
COPY services/doc-fetcher/pyproject.toml .
COPY services/doc-fetcher/src ./src
RUN pip install --no-cache-dir .
CMD ["doc-fetcher"]
