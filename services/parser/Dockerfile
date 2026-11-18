# Build from the repo root: docker build -f services/parser/Dockerfile .
FROM python:3.12-slim
WORKDIR /app
COPY libs/kafka /libs/kafka
COPY libs/telemetry /libs/telemetry
COPY libs/bodies /libs/bodies
RUN pip install --no-cache-dir /libs/kafka /libs/telemetry /libs/bodies
COPY services/parser/pyproject.toml .
COPY services/parser/src ./src
RUN pip install --no-cache-dir .
RUN useradd --system --uid 10001 --no-create-home app && install -d -o app /bodies
USER 10001
CMD ["parser"]
