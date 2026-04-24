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

The workflow file is kept as an example at [`ci/deploy-blueos-extension.example.yml`](ci/deploy-blueos-extension.example.yml) because some GitHub OAuth tokens cannot push to `.github/workflows/` without the **`workflow` scope**.

1. Run `gh auth refresh -s workflow` (or use a token with `workflow`), then:
2. `mkdir -p .github/workflows && cp ci/deploy-blueos-extension.example.yml .github/workflows/deploy.yml`
3. Add repo **Secrets**: `DOCKER_USERNAME`, `DOCKER_PASSWORD` (and use **Variables** for author/org if desired, matching the action inputs).

Then push to trigger the BlueOS extension deploy action.

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

- Connection: `udpin:0.0.0.0:14550` (companion listens; autopilot sends to companion).
- Tilt center: `MAV_CMD_DO_GIMBAL_MANAGER_TILTPAN` pitch `0°` (see [camera_tilt_control.md](camera_tilt_control.md)).
- Light: `MAV_CMD_DO_SET_SERVO` on configured channel (default **13**), PWM mapped from brightness %.

Adjust in **Settings** if your vehicle differs.

## License

MIT
