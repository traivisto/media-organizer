# A viewing copy of Kuva-arkisto, for a NAS (spec 8.3).
#
# Built from the repository root -- or, on the NAS, from the folder `mediaidx
# publish` copies the same files into -- so that `mediaidx/` is beside
# `deploy/`:
#
#   docker build -f deploy/nas/Dockerfile -t kuva-arkisto:viewer .
#
# It serves what it is given at /data (the published database, thumbnails,
# text model and config.toml) and the originals at /photos, both read-only.

FROM python:3.12-slim

# Dates are shown, and date searches resolved, in local time: a container left
# on UTC would put a photograph taken at 00:30 on the day before. The zone data
# is not in the slim image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*
ENV TZ=Europe/Helsinki \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY deploy/nas/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY mediaidx /app/mediaidx

EXPOSE 8710
CMD ["python", "-m", "mediaidx", "--config", "/data/config.toml", \
     "serve", "--read-only", "--host", "0.0.0.0", "--port", "8710"]
