FROM ubuntu:24.04 AS builder
ARG FEX_VERSION=latest
ARG BUILD_JOBS=2
ENV DEBIAN_FRONTEND=noninteractive
RUN test "$(uname -m)" = aarch64
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git clang-18 clang-tools-18 lld cmake ninja-build pkg-config python3 \
    libssl-dev libstdc++-14-dev squashfs-tools erofs-utils \
    && rm -rf /var/lib/apt/lists/*
COPY docker/build_fex.py /tmp/build_fex.py
RUN python3 /tmp/build_fex.py "$FEX_VERSION" "$BUILD_JOBS"
# FEX appends "RootFS/" directly to this override, so retain the trailing slash.
ENV HOME=/tmp/fex-home FEX_APP_DATA_LOCATION=/tmp/fex-home/.local/share/fex-emu/
RUN mkdir -p "$HOME" "$FEX_APP_DATA_LOCATION" \
    && FEXRootFSFetcher --assume-yes --extract --distro-name ubuntu --distro-version 24.04 --force-ui tty
COPY docker/collect_rootfs.py /tmp/collect_rootfs.py
RUN python3 /tmp/collect_rootfs.py

FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    HOME=/data/home \
    PATH=/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv ca-certificates curl libssl3t64 libstdc++6 tzdata \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 valheim \
    && useradd --uid 10001 --gid 10001 --home-dir /data/home valheim \
    && mkdir /data && chown 10001:10001 /data
RUN python3 -m venv /opt/venv
COPY --from=builder /usr/local/ /usr/local/
COPY --from=builder /opt/rootfs /opt/rootfs
COPY --from=builder /opt/build-info.json /opt/build-info.json
RUN ldconfig
WORKDIR /opt/valheim
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
USER 10001:10001
EXPOSE 8080 2456/udp 2457/udp
ENTRYPOINT ["/opt/venv/bin/python", "-m", "app.cli"]
CMD ["serve"]
