#!/usr/bin/env bash
# Runs once inside the image at build time (virt-customize --run-command).
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# runtime libs for the Debian-12-built doichaind (ldd: libevent_core, libevent_extra,
# libevent_pthreads, libsqlite3), python3 for the miner, jq for doichain-status
apt-get install -y -qq --no-install-recommends \
  libevent-core-2.1-7 libevent-extra-2.1-7 libevent-pthreads-2.1-7 libsqlite3-0 python3 jq >/dev/null
apt-get clean
rm -rf /var/lib/apt/lists/*
id doichain >/dev/null 2>&1 || useradd --system --home-dir /var/lib/doichain --shell /usr/sbin/nologin doichain
chown -R doichain:doichain /var/lib/doichain /etc/doichain
ln -sf /usr/local/lib/doichain/miner.py /usr/local/bin/doichain-miner
echo "doichain guest setup done"
