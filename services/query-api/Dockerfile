# Build from the repo root: docker build -f services/query-api/Dockerfile .
FROM python:3.12-slim
WORKDIR /app
COPY libs/authz /libs/authz
RUN pip install --no-cache-dir /libs/authz
COPY services/query-api/pyproject.toml .
COPY services/query-api/src ./src
RUN pip install --no-cache-dir .
EXPOSE 8000
CMD ["query-api"]
