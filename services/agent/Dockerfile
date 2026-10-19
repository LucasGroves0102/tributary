# Build from the repo root: docker build -f services/agent/Dockerfile .
FROM python:3.12-slim
WORKDIR /app
COPY services/agent/pyproject.toml .
COPY services/agent/src ./src
RUN pip install --no-cache-dir .
EXPOSE 8001
RUN useradd --system --uid 10001 --no-create-home app
USER 10001
CMD ["agent-api"]
