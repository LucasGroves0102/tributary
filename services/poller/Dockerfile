# Build from the repo root: docker build -f services/poller/Dockerfile .
FROM python:3.12-slim
WORKDIR /app
COPY libs/sec /libs/sec
RUN pip install --no-cache-dir /libs/sec
COPY services/poller/pyproject.toml .
COPY services/poller/src ./src
RUN pip install --no-cache-dir .
RUN useradd --system --uid 10001 --no-create-home app
USER 10001
CMD ["poller"]
