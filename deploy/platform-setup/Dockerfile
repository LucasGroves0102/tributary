# Build from the repo root: docker build -f deploy/platform-setup/Dockerfile .
FROM openfga/cli:v0.6.3@sha256:4f3bf5ec0e79e20eb6e400f98431d23eda259faf71dfaf72b2f196d7823a3ef9 AS fga

FROM python:3.12-alpine
COPY --from=fga /fga /usr/local/bin/fga
WORKDIR /app
COPY authz/model.fga authz/
COPY streaming/debezium/app-connector.json streaming/debezium/
COPY deploy/platform-setup/setup.py .
USER 10001:10001
ENTRYPOINT ["python", "setup.py"]
