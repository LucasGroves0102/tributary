# Build from the repo root: docker build -f services/authz-sync/Dockerfile .
FROM python:3.12-slim
WORKDIR /app
COPY libs/authz /libs/authz
RUN pip install --no-cache-dir /libs/authz
COPY services/authz-sync/pyproject.toml .
COPY services/authz-sync/src ./src
RUN pip install --no-cache-dir .
RUN useradd --system --uid 10001 --no-create-home app
USER 10001
CMD ["authz-sync"]
