# Build from the repo root: docker build -f services/mcp-server/Dockerfile .
FROM python:3.12-slim
WORKDIR /app
COPY libs/telemetry /libs/telemetry
RUN pip install --no-cache-dir /libs/telemetry
# verify_figures runs the agent's verify.py, which needs only the standard library: the agent
# package without its dependencies, so no Anthropic SDK (and no model key) is in this image.
# tests/test_verify_tool.py fails if verify.py ever imports beyond the standard library.
COPY services/agent/pyproject.toml /services/agent/pyproject.toml
COPY services/agent/src /services/agent/src
RUN pip install --no-cache-dir --no-deps /services/agent
COPY services/mcp-server/pyproject.toml .
COPY services/mcp-server/src ./src
RUN pip install --no-cache-dir .
EXPOSE 8002
RUN useradd --system --uid 10001 --no-create-home app
USER 10001
CMD ["mcp-server"]
