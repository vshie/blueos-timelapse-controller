# syntax=docker/dockerfile:1
FROM node:20-bookworm-slim AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV TIMELAPSE_DATA_DIR=/data

# Build deps only for pip wheels that compile on linux/arm/v7 (pymavlink -> lxml); removed after install.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    python3-dev \
    libxml2-dev \
    libxslt1-dev \
    zlib1g-dev \
    pkg-config \
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-libav \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONPATH=/app

COPY backend/pyproject.toml ./
# uvicorn[standard] pulls uvloop+httptools (needs a C toolchain on arm/v7 QEMU builds). Plain uvicorn is fine.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --prefer-binary \
    "fastapi>=0.109.0" \
    "uvicorn>=0.27.0" \
    "pydantic>=2.5.0" \
    "pydantic-settings>=2.1.0" \
    "pymavlink>=2.4.41" \
    && apt-get update \
    && apt-get install -y --no-install-recommends libxml2 libxslt1.1 \
    && apt-get purge -y --auto-remove build-essential gcc g++ python3-dev libxml2-dev libxslt1-dev zlib1g-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY backend/app ./app
COPY --from=frontend /src/dist ./app/static

EXPOSE 9876/tcp

LABEL version="0.1.0"
LABEL permissions='\
{\
 "ExposedPorts": {\
   "9876/tcp": {}\
 },\
 "HostConfig": {\
   "Binds": [\
     "/usr/blueos/extensions/timelapse-controller:/data"\
   ],\
   "ExtraHosts": ["host.docker.internal:host-gateway"],\
   "PortBindings": {\
     "9876/tcp": [\
       {\
         "HostPort": ""\
       }\
     ]\
   }\
 }\
}'
LABEL authors='[\
 {\
   "name": "Tony White",\
   "email": "tonywhite@bluerobotics.com"\
 }\
]'
LABEL company='\
{\
 "about": "Scheduled RTSP capture with MAVLink controls",\
 "name": "vshie",\
 "email": ""\
 }'
LABEL type="tool"
LABEL readme="https://raw.githubusercontent.com/vshie/blueos-timelapse-controller/main/README.md"
LABEL links='\
{\
 "source": "https://github.com/vshie/blueos-timelapse-controller"\
 }'
LABEL requirements="core >= 1.1"

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
