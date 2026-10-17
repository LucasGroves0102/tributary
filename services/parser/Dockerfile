# Build from the repo root: docker build -f services/parser/Dockerfile .
FROM python:3.12-slim
WORKDIR /app
COPY services/parser/pyproject.toml .
COPY services/parser/src ./src
RUN pip install --no-cache-dir .
CMD ["parser"]
