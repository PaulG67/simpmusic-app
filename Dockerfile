FROM python:3.12-slim

# Deno is required by yt-dlp to solve YouTube's JavaScript challenges.
# Without it only degraded/incomplete formats are available.
ARG DENO_VERSION=latest

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DENO_DIR=/tmp/deno-cache

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
        curl \
        unzip \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
        amd64) target="x86_64-unknown-linux-gnu" ;; \
        arm64) target="aarch64-unknown-linux-gnu" ;; \
        *) echo "unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    if [ "$DENO_VERSION" = "latest" ]; then \
        url="https://github.com/denoland/deno/releases/latest/download/deno-${target}.zip"; \
    else \
        url="https://github.com/denoland/deno/releases/download/${DENO_VERSION}/deno-${target}.zip"; \
    fi; \
    curl -fsSL "$url" -o /tmp/deno.zip; \
    unzip -q /tmp/deno.zip -d /usr/local/bin; \
    rm /tmp/deno.zip; \
    chmod +x /usr/local/bin/deno; \
    deno --version

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN python -m app.core.icons

VOLUME ["/config", "/cache", "/logs"]

ENV PORT=5080
EXPOSE 5080

COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN sed -i 's/\r$//' /docker-entrypoint.sh && chmod +x /docker-entrypoint.sh

HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request,sys; p=os.environ.get('PORT','5080'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{p}/health', timeout=5).status == 200 else 1)"

ENTRYPOINT ["/docker-entrypoint.sh"]
