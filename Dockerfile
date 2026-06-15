# python:3.12 — stabile Wheels für spätere GRIB2-Deps (cfgrib/eccodes)
FROM python:3.12-slim

WORKDIR /app

# eccodes-Systembibliothek wird erst in Phase 3 benötigt:
# RUN apt-get update && apt-get install -y --no-install-recommends libeccodes0 \
#     && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
