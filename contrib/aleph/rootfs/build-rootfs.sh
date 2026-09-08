#!/usr/bin/env bash
# Host wrapper: build the libguestfs builder image, then run build-rootfs-image.sh
# inside it (privileged, amd64).  Needs Docker (Docker Desktop on macOS).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAGE="$(cd "${HERE}/.." && pwd)"
IMG=doichain-rootfs-builder:local

docker build --platform linux/amd64 -t "${IMG}" -f "${HERE}/Dockerfile.rootfs" "${HERE}"
docker run --rm --privileged --platform linux/amd64 \
  -e LIBGUESTFS_BACKEND=direct \
  -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
  -e ROOTFS_IMAGE_SIZE="${ROOTFS_IMAGE_SIZE:-20G}" \
  -v "${STAGE}:/workspace" \
  "${IMG}" bash /workspace/rootfs/build-rootfs-image.sh
