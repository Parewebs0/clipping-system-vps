# --- Stage 1: Mission Control frontend (React + Vite + shadcn/ui, issue #8) ---
# Output goes to /build/app/static/mission-control (vite outDir ../app/static/...).
FROM node:22-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- Stage 2: API ---
FROM python:3.12-slim
WORKDIR /opt/clipping-system
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# gog CLI (Google Drive listing for paso 3b drive_resolver_tick).
# Downloaded from the official release (github.com/openclaw/gogcli, formerly
# steipete/gogcli) and verified against the sha256 published in its checksums.txt.
# Config/keyring stay bind-mounted from ./gog/config and ./gog/share (see compose).
# To bump: change GOG_VERSION and both GOG_SHA256_* from the release checksums.txt.
ARG GOG_VERSION=0.40.0
ARG GOG_SHA256_AMD64=5f73815950f30de4165b7b767103ca45c4950e84a1da601eda5294e9ff94f767
ARG GOG_SHA256_ARM64=21ca9757f67a573115b517854184561cef6b3b73c21e0f60c72229522c7198ac
ARG TARGETARCH
COPY docker/fetch_gog.py /tmp/fetch_gog.py
RUN set -eu; \
    arch="${TARGETARCH:-$(dpkg --print-architecture)}"; \
    case "$arch" in \
      amd64) sha="$GOG_SHA256_AMD64" ;; \
      arm64) sha="$GOG_SHA256_ARM64" ;; \
      *) echo "unsupported arch for gog: $arch" >&2; exit 1 ;; \
    esac; \
    python /tmp/fetch_gog.py \
      "https://github.com/openclaw/gogcli/releases/download/v${GOG_VERSION}/gogcli_${GOG_VERSION}_linux_${arch}.tar.gz" \
      "$sha" /usr/local/bin/gog; \
    chmod 0755 /usr/local/bin/gog; \
    rm /tmp/fetch_gog.py; \
    gog --version

COPY . .
COPY --from=frontend /build/app/static/mission-control ./app/static/mission-control
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
