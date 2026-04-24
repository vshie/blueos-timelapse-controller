#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TAG="${1:-dev}"
IMAGE="${IMAGE:-vshie/blueos-timelapse-controller:${TAG}}"
OUT_DIR="${ROOT}/dist"
mkdir -p "${OUT_DIR}"
docker build -t "${IMAGE}" "${ROOT}"
docker save "${IMAGE}" -o "${OUT_DIR}/timelapse-controller-${TAG}.tar"
echo "Wrote ${OUT_DIR}/timelapse-controller-${TAG}.tar"
