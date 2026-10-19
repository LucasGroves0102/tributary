FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
COPY src ./src
RUN pip install --no-cache-dir .
RUN useradd --system --uid 10001 --no-create-home app
USER 10001
CMD ["bronze-loader"]
