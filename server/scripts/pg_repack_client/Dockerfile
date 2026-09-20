# Build the pg_repack 1.5.2 CLIENT for the battlestats droplet.
#
# Why a container: the droplet is Ubuntu 24.04 and the dev box is 26.04, so a
# native build here links against a newer glibc than production has. Building
# in a 24.04 image yields a binary that runs there, and keeps a compiler
# toolchain off the production host.
#
# Why 1.5.2 exactly: the managed cluster offers extension 1.5.2 and pg_repack
# refuses to run when client and extension versions differ. apt only carries
# 1.5.3.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg lsb-release git build-essential \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
        https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt noble-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        postgresql-server-dev-18 libpq-dev \
        libssl-dev zlib1g-dev libreadline-dev libzstd-dev liblz4-dev \
        libkrb5-dev libselinux1-dev libpam0g-dev libxslt1-dev libnuma-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone --depth 1 --branch ver_1.5.2 https://github.com/reorg/pg_repack.git
WORKDIR /src/pg_repack
RUN make PG_CONFIG=/usr/lib/postgresql/18/bin/pg_config

# Only the client binary leaves this image; the server-side library is the
# cluster's own.
RUN mkdir /out && cp bin/pg_repack /out/pg_repack \
    && /out/pg_repack --version \
    && (ldd /out/pg_repack | tee /out/ldd.txt)
