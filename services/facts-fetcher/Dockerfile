# Build from the repo root: docker build -f services/facts-fetcher/Dockerfile .
FROM python:3.12-slim
WORKDIR /app
COPY libs/sec /libs/sec
RUN pip install --no-cache-dir /libs/sec
COPY services/facts-fetcher/pyproject.toml .
COPY services/facts-fetcher/src ./src
RUN pip install --no-cache-dir .
CMD ["facts-fetcher"]
