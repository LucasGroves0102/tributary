# Build from the repo root: docker build -f services/company-loader/Dockerfile .
FROM python:3.12-slim
WORKDIR /app
COPY libs/sec /libs/sec
RUN pip install --no-cache-dir /libs/sec
COPY services/company-loader/pyproject.toml .
COPY services/company-loader/src ./src
RUN pip install --no-cache-dir .
CMD ["company-loader"]
