# Timelapse Controller (BlueOS Extension)

Scheduled **recipes** run at configured times on selected days of the week: optionally center camera tilt (MAVLink mount), set Lumen light PWM via `MAV_CMD_DO_SET_SERVO`, and capture **snapshots** or **timed video** from an **RTSP** stream using **GStreamer**—without taking exclusive control of the USB camera from BlueOS Video Manager (Cockpit can keep viewing the same stream).

## Features

- Vue 3 + Vite UI; FastAPI backend
- Per-recipe: weekdays, multiple daily times, tilt center, light brightness %, snapshot, record duration
- Global settings: default RTSP URL, MAVLink connection string, light servo channel, PWM range
- Manual test actions from the UI
- BlueOS sidebar registration via `register_service`

## Data layout (on vehicle)

Bind mount host path (see Dockerfile `permissions`):

| Host | Container |
|------|-----------|
| `/usr/blueos/extensions/timelapse-controller` | `/data` |

- `/data/config.json` — settings
- `/data/recipes/*.json` — recipes
- `/data/captures/` — JPEG / MPEG-TS (and optional MP4) output

## Local development

```bash
cd frontend && npm install && npm run build
rm -rf ../backend/app/static && mkdir -p ../backend/app/static && cp -r dist/* ../backend/app/static/
cd ../backend && python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
export TIMELAPSE_DATA_DIR="$(pwd)/../data"
mkdir -p "$TIMELAPSE_DATA_DIR"
uvicorn app.main:app --reload --host 0.0.0.0 --port 8080
```

- **Snapshots** use **ffmpeg** (reliable single-frame RTSP). **GStreamer** is used for **RTSP probe** and **MPEG-TS recording** (H.264 passthrough).
- Set `TIMELAPSE_DATA_DIR` for persistence (default `/data` in Docker).

## Automated image export

```bash
chmod +x scripts/export-image-tar.sh
./scripts/export-image-tar.sh dev
```

## GitHub Actions (Docker Hub)

CI matches [BlueOS_videorecorder](https://github.com/vshie/BlueOS_videorecorder/blob/main/.github/workflows/deploy.yml): [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) runs on every **push** using [Deploy-BlueOS-Extension](https://github.com/BlueOS-community/Deploy-BlueOS-Extension).

**Secrets:** `DOCKER_USERNAME`, `DOCKER_PASSWORD`  
**Variables (repository):** `MY_NAME`, `MY_EMAIL`, `ORG_NAME`, `ORG_EMAIL` (same pattern as the videorecorder repo)

Published image on Docker Hub: `YOUR_DOCKER_USER/blueos-timelapse-controller`. The deploy action sets `docker_image = username + (image-prefix default "blueos-") + image-name`; this repo uses **image-name** `timelapse-controller` so the name is not doubled (see [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml)).

Multi-arch in CI: `linux/arm/v7`, `linux/arm64/v8`.

## Docker image

```bash
docker build -t vshie/blueos-timelapse-controller:dev .
docker save vshie/blueos-timelapse-controller:dev -o dist/timelapse-controller-dev.tar
```

## BlueOS manual install

1. Build/push image (or use CI artifact from GitHub Actions).
2. Extensions Manager → custom extension → image `vshie/blueos-timelapse-controller:<tag>`.
3. Paste **Custom settings** from the Dockerfile `permissions` `HostConfig` (JSON), or install from store once published.

## MAVLink defaults

- The extension Docker image requests **`NetworkMode: host`** so it shares the companion’s UDP stack; **`udpin:0.0.0.0:14550`** then receives the same MAVLink as BlueOS. If you disabled host networking, set **MAVLink connection** in Settings to a reachable endpoint (e.g. vehicle UDP port forwarded into the container).
- **Tilt:** `MAV_CMD_DO_GIMBAL_MANAGER_TILTPAN` with pitch in degrees (**0° = center**). Default range in Settings is **−70°…+70°** (see [camera_tilt_control.md](camera_tilt_control.md)).
- **Light (manual tab):** `MAV_CMD_DO_SET_SERVO` on the configured channel (default **13**): **0% → 1100 µs (off)**, **100% → 1900 µs (full)**. Recipes still use **Settings → light PWM min/max** for brightness %.
- Commands are sent to **`MAV_COMP_ID_AUTOPILOT1`**, not the heartbeat source component (avoids GCS/router-only heartbeats).

Adjust in **Settings** if your vehicle differs.

## License

MIT
