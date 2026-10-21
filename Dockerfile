# SmartPrint server (API + print worker). The PySide6 desktop clients run on the shop PCs, not here.
FROM python:3.13-slim
ARG WITH_LIBREOFFICE=0          # docker build --build-arg WITH_LIBREOFFICE=1 .   (accept Word/Excel/PowerPoint uploads)
RUN apt-get update \
 && apt-get install -y --no-install-recommends cups-client curl \
 && if [ "$WITH_LIBREOFFICE" = "1" ]; then apt-get install -y --no-install-recommends libreoffice-writer libreoffice-calc libreoffice-impress; fi \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml alembic.ini ./
COPY alembic ./alembic
COPY src ./src
RUN pip install --no-cache-dir ".[server]"
ENV SMARTPRINT_DATA_DIR=/data SMARTPRINT_ENVIRONMENT=prod
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s CMD curl -fs http://localhost:8000/health || exit 1
# migrate on every start (idempotent), then serve
CMD ["sh", "-c", "smartprint-server migrate && smartprint-server serve --host 0.0.0.0 --port 8000"]
