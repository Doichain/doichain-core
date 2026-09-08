#!/usr/bin/env bash
# Runs INSIDE the libguestfs builder container (see build-rootfs.sh).
# Debian 12 genericcloud qcow2 -> doichaind + miner + systemd units baked in ->
# sparsified/compressed qcow2 ready for `aleph file upload`.
set -euo pipefail

WS="${WS:-/workspace}"                     # stage-b directory, bind-mounted
OUT="${WS}/dist"
GUEST="${WS}/rootfs/guest"
BASE_URL="${BASE_URL:-https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2}"
BASE="${OUT}/debian-12-genericcloud-amd64.qcow2"
WORK="${OUT}/aleph-doichain-regtest.work.qcow2"
FINAL="${OUT}/aleph-doichain-regtest.qcow2"
SIZE="${ROOTFS_IMAGE_SIZE:-20G}"

for c in curl qemu-img virt-customize virt-sparsify; do
  command -v "$c" >/dev/null || { echo "missing: $c" >&2; exit 1; }
done
for f in "${OUT}/doichaind" "${OUT}/doichain-cli" "${WS}/miner.py"; do
  [ -f "$f" ] || { echo "missing build input: $f (run build-linux-binaries.sh first)" >&2; exit 1; }
done

if [ ! -f "${BASE}" ]; then
  echo "downloading base image"
  curl --fail --location --retry 5 --retry-delay 5 --connect-timeout 20 -o "${BASE}" "${BASE_URL}"
fi

rm -f "${WORK}" "${FINAL}"
cp "${BASE}" "${WORK}"
qemu-img resize "${WORK}" "${SIZE}"

echo "customizing image"
virt-customize -a "${WORK}" \
  --mkdir /etc/doichain --mkdir /var/lib/doichain --mkdir /usr/local/lib/doichain \
  --copy-in "${OUT}/doichaind:/usr/local/bin" \
  --copy-in "${OUT}/doichain-cli:/usr/local/bin" \
  --upload "${WS}/miner.py:/usr/local/lib/doichain/miner.py" \
  --upload "${GUEST}/doichaind.service:/etc/systemd/system/doichaind.service" \
  --upload "${GUEST}/doichain-miner.service:/etc/systemd/system/doichain-miner.service" \
  --upload "${GUEST}/doichain.env:/etc/default/doichain" \
  --upload "${GUEST}/doichain-miner.env:/etc/default/doichain-miner" \
  --upload "${GUEST}/doichain-guest-setup.sh:/usr/local/sbin/doichain-guest-setup.sh" \
  --upload "${GUEST}/doichain-status:/usr/local/bin/doichain-status" \
  --run-command "chmod 0755 /usr/local/bin/doichaind /usr/local/bin/doichain-cli /usr/local/bin/doichain-status /usr/local/sbin/doichain-guest-setup.sh /usr/local/lib/doichain/miner.py" \
  --run-command "/usr/local/sbin/doichain-guest-setup.sh" \
  --run-command "systemctl enable doichaind.service doichain-miner.service" \
  --truncate /etc/machine-id

echo "sparsifying + compressing"
virt-sparsify --compress "${WORK}" "${FINAL}"
rm -f "${WORK}"
qemu-img info "${FINAL}" | grep -E 'virtual size|disk size|file format'
[ -n "${HOST_UID:-}" ] && chown -R "${HOST_UID}:${HOST_GID:-${HOST_UID}}" "${OUT}" || true
echo "rootfs ready: ${FINAL}"
