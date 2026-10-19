# Build from the repo root: docker build -f db/Dockerfile .
FROM alpine:3.22
RUN apk add --no-cache bash postgresql16-client
WORKDIR /app
COPY scripts/migrate.sh scripts/
COPY db/migrations db/migrations
COPY db/app db/app
USER 10001:10001
CMD ["sh", "-c", "scripts/migrate.sh db/migrations \"$DATABASE_URL\" && scripts/migrate.sh db/app/migrations \"$APP_DATABASE_URL\" && if [ -n \"$SEED_DEMO_USERS\" ]; then psql \"$APP_DATABASE_URL\" -v ON_ERROR_STOP=1 -q -f db/app/seed.sql && echo 'demo users seeded'; fi"]
