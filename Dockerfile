FROM debian:bookworm-slim AS native-build

RUN apt-get update \
    && apt-get install -y --no-install-recommends cmake g++ ninja-build \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY native/CMakeLists.txt native/CMakeLists.txt
COPY native/src native/src
COPY native/tests native/tests
RUN cmake -S native -B out -G Ninja -DCMAKE_BUILD_TYPE=Release \
    && cmake --build out --target benchforge_workloads --parallel 2 \
    && mkdir -p /native-bin \
    && cp out/benchforge_workloads /native-bin/

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BENCHFORGE_DATABASE=/data/benchforge.sqlite \
    BENCHFORGE_DATA_DIR=/data/public_datasets/processed \
    BENCHFORGE_HOST=0.0.0.0 \
    BENCHFORGE_PORT=8765

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install --no-cache-dir --no-compile . \
    && useradd --system --uid 10001 --home-dir /nonexistent \
        --shell /usr/sbin/nologin benchforge \
    && mkdir -p /data/public_datasets/processed /opt/benchforge/bin \
    && chown -R benchforge:benchforge /data
COPY --from=native-build /native-bin/benchforge_workloads /opt/benchforge/bin/benchforge_workloads

USER benchforge
VOLUME ["/data"]
EXPOSE 8765
STOPSIGNAL SIGINT
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=2).read()"

ENTRYPOINT ["benchforge"]
CMD ["dashboard", "--database", "/data/benchforge.sqlite", "--data-dir", "/data/public_datasets/processed", "--host", "0.0.0.0", "--allow-remote", "--port", "8765"]
