#!/usr/bin/env bash
# Compile doichaind + doichain-cli for Debian 12 amd64 inside Docker — the same
# distro as the Aleph guest image, so the dynamically linked binaries run there.
# Output: dist/doichaind, dist/doichain-cli (stripped).  The CMake build dir lives
# in a named Docker volume so re-runs are incremental.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Source tree: the enclosing git checkout by default (contrib/aleph/ lives inside it).
SRC="${DOICHAIN_SRC:-$(git -C "${HERE}" rev-parse --show-toplevel 2>/dev/null || echo "${HERE}/../..")}"
OUT="${HERE}/dist"
mkdir -p "${OUT}"

echo "source : ${SRC} ($(git -C "${SRC}" rev-parse --short HEAD 2>/dev/null || echo '?'))"
echo "output : ${OUT}"

docker run --rm --platform linux/amd64 \
  -v "${SRC}:/src:ro" \
  -v "${OUT}:/out" \
  -v doichain-linux-build:/build \
  -v doichain-apt-cache:/var/cache/apt \
  debian:12 bash -euo pipefail -c '
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends \
      build-essential cmake pkg-config git ca-certificates python3 \
      libevent-dev libboost-dev libsqlite3-dev >/dev/null
    # parallelism bounded by memory: heavy C++ TUs need ~2 GiB each
    MEM_GB=$(awk "/MemTotal/ {print int(\$2/1024/1024)}" /proc/meminfo)
    J=$(( MEM_GB / 2 )); [ "$J" -lt 1 ] && J=1; [ "$J" -gt "$(nproc)" ] && J=$(nproc)
    echo "building with -j${J} (mem ${MEM_GB} GiB, $(nproc) cpus)"
    cmake -S /src -B /build -DCMAKE_BUILD_TYPE=Release \
      -DBUILD_TESTS=OFF -DBUILD_BENCH=OFF -DBUILD_GUI=OFF -DENABLE_IPC=OFF \
      -DWITH_ZMQ=OFF -DBUILD_FUZZ_BINARY=OFF -DENABLE_WALLET=ON >/dev/null
    cmake --build /build --target doichaind doichain-cli -j"${J}" 2>&1 | grep -E "error|Built target|\[100%\]" || true
    test -x /build/bin/doichaind
    cp /build/bin/doichaind /build/bin/doichain-cli /out/
    strip /out/doichaind /out/doichain-cli
    /out/doichaind --version | head -1
    ldd /out/doichaind | grep -E "libevent|sqlite|libstdc" '
ls -la "${OUT}/doichaind" "${OUT}/doichain-cli"
