FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      cups cups-client cups-filters \
      ghostscript poppler-utils \
      libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
      fonts-dejavu-core \
      tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml constraints.txt README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir -c constraints.txt .

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV DATA_DIR=/data
EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]
