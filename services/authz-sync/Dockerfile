# Build from the repo root: docker build -f services/authz-sync/Dockerfile .
FROM python:3.12-slim
WORKDIR /app
COPY libs/authz /libs/authz
RUN pip install --no-cache-dir /libs/authz
COPY services/authz-sync/pyproject.toml .
COPY services/authz-sync/src ./src
RUN pip install --no-cache-dir .
CMD ["authz-sync"]
