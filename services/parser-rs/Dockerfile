# Build from the repo root: docker build -f services/parser-rs/Dockerfile .
FROM rust:1.99.1-slim-trixie@sha256:4df2ae8cfdfebdfc13a65da7f253b73d68983dc0339f2719bc17a279c913b13d AS build
ARG CARGO_BUILD_JOBS=4
# librdkafka's configure needs make and a C++ compiler
RUN apt-get update && apt-get install -y --no-install-recommends make g++ \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY services/parser-rs/Cargo.toml services/parser-rs/Cargo.lock ./
COPY services/parser-rs/src ./src
RUN cargo build --release --locked

FROM gcr.io/distroless/cc-debian13@sha256:159783207c2cd44c2aa5715961d13c8612368ac9bd450f887e3f08fc8ea461e3
COPY --from=build /src/target/release/parser /src/target/release/parser-rs /usr/local/bin/
USER 10001
# The service, as in services/parser's image; parser-rs is the stdin CLI.
CMD ["parser"]
