# Build from the repo root: docker build -f services/poller/Dockerfile .
FROM python:3.12-slim
WORKDIR /app
COPY libs/sec /libs/sec
RUN pip install --no-cache-dir /libs/sec
COPY services/poller/pyproject.toml .
COPY services/poller/src ./src
RUN pip install --no-cache-dir .
CMD ["poller"]
