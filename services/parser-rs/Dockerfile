# Build from the repo root: docker build -f services/parser-rs/Dockerfile .
FROM rust:1.99.1-slim-trixie@sha256:4df2ae8cfdfebdfc13a65da7f253b73d68983dc0339f2719bc17a279c913b13d AS build
ARG CARGO_BUILD_JOBS=4
WORKDIR /src
COPY services/parser-rs/Cargo.toml services/parser-rs/Cargo.lock ./
COPY services/parser-rs/src ./src
RUN cargo build --release --locked

FROM gcr.io/distroless/cc-debian13@sha256:159783207c2cd44c2aa5715961d13c8612368ac9bd450f887e3f08fc8ea461e3
COPY --from=build /src/target/release/parser-rs /usr/local/bin/parser-rs
USER 10001
ENTRYPOINT ["parser-rs"]
