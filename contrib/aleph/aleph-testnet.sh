#!/usr/bin/env bash
# Doichain DAA rehearsal on Aleph.im — three regtest VMs driven by the local Rust
# `aleph` CLI plus SSH.  No relay tooling involved: the rootfs is self-starting.
#
#   ./aleph-testnet.sh build        Linux binaries + rootfs image (Docker)
#   ./aleph-testnet.sh upload       aleph file upload -> state/rootfs.json      (PAID: STORE pin)
#   ./aleph-testnet.sh up           3 instances + port forwards -> state/vms.json (PAID: credits/hour)
#   ./aleph-testnet.sh wait         wait for ipv4 + mapped ports + ssh on all VMs -> state/net.json
#   ./aleph-testnet.sh wire         -addnode mesh, restart nodes, verify 2 peers each
#   ./aleph-testnet.sh roles        vm1 honest (HONEST_HR), vm2 attacker (ATTACK_HR), vm3 observer
#   ./aleph-testnet.sh leave        attacker stops hashing (stop-file) -> recovery begins
#   ./aleph-testnet.sh status       one line per VM (height, bits, difficulty, peers, tip age)
#   ./aleph-testnet.sh collect      miner.csv + debug.log from every VM -> runs/<timestamp>/
#   ./aleph-testnet.sh down         FORGET all instances (stops billing)
#   ./aleph-testnet.sh watchdog 12  detached timer: collect + down after N hours
#   ./aleph-testnet.sh ssh <n> [cmd]
#
# Env: ALEPH_ACCOUNT (default: active account), SSH_KEY (~/.ssh/id_ed25519),
#      VM_SIZE (1vcpu-2gb), HONEST_HR (1000), ATTACK_HR (47000)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE="${HERE}/state"; mkdir -p "${STATE}"
SSH_KEY="${SSH_KEY:-}"
if [ -z "${SSH_KEY}" ]; then
  for k in "$HOME/.ssh/id_ed25519" "$HOME/.ssh/id_rsa"; do [ -f "$k.pub" ] && { SSH_KEY="$k"; break; }; done
fi
[ -n "${SSH_KEY}" ] && [ -f "${SSH_KEY}.pub" ] || { echo "no SSH key pair found; set SSH_KEY=/path/to/key" >&2; exit 1; }
VM_SIZE="${VM_SIZE:-1vcpu-2gb}"
HONEST_HR="${HONEST_HR:-1000}"; ATTACK_HR="${ATTACK_HR:-47000}"
NODE_COUNT="${NODE_COUNT:-3}"                       # 1 = a single node, 3 = the rehearsal
NAME_PREFIX="${NAME_PREFIX:-doichain-regtest}"
NAMES=(); for i in $(seq 1 "${NODE_COUNT}"); do NAMES+=("${NAME_PREFIX}-${i}"); done
ACCT=(); [ -n "${ALEPH_ACCOUNT:-}" ] && ACCT=(--account "${ALEPH_ACCOUNT}")
REFERENCE="${HERE}/reference-rootfs.json"         # committed pointer to the published image

need() { command -v "$1" >/dev/null || { echo "missing: $1" >&2; exit 1; }; }
need aleph; need jq; need ssh; need scp
log() { echo "$(date +%H:%M:%S) $*"; }
hex64() { grep -oE '[0-9a-f]{64}' | head -1; }

# --- state helpers ---------------------------------------------------------
vm_hash() { jq -r ".[$(( $1 - 1 ))].item_hash" "${STATE}/vms.json"; }
net_get() { jq -r ".[$(( $1 - 1 ))].$2" "${STATE}/net.json"; }
vssh() {  # vssh <n> <cmd...>
  local n=$1; shift
  ssh -i "${SSH_KEY}" -p "$(net_get "$n" ssh_port)" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      -o ConnectTimeout=15 -o LogLevel=ERROR "root@$(net_get "$n" ipv4)" "$@"
}

cmd_build() {
  "${HERE}/build-linux-binaries.sh"
  "${HERE}/rootfs/build-rootfs.sh"
}

cmd_upload() {
  local img="${HERE}/dist/aleph-doichain-regtest.qcow2"
  [ -f "$img" ] || { echo "no image: $img (run build)"; exit 1; }
  log "uploading $(du -h "$img" | cut -f1) rootfs (STORE pin, paid in credits)"
  aleph file upload "$img" --json "${ACCT[@]}" | tee "${STATE}/upload.json"
  local h; h=$(jq -r '.item_hash // .itemHash // empty' "${STATE}/upload.json"); [ -n "$h" ] || h=$(hex64 < "${STATE}/upload.json")
  local commit; commit=$(git -C "${HERE}" rev-parse --short HEAD 2>/dev/null || echo unknown)
  jq -n --arg h "$h" --arg t "$(date -u +%FT%TZ)" --arg c "$commit" --argjson s "$(stat -f %z "$img" 2>/dev/null || stat -c %s "$img")" \
     '{item_hash:$h, uploaded_at:$t, source_commit:$c, size_bytes:$s, image:"aleph-doichain-regtest.qcow2"}' \
     | tee "${STATE}/rootfs.json" > "${REFERENCE}"
  log "rootfs item hash: $h  (recorded in ${REFERENCE##*/} — commit it so others can launch without building)"
}

resolve_rootfs() {  # ROOTFS_HASH env > state/rootfs.json > committed reference-rootfs.json
  if [ -n "${ROOTFS_HASH:-}" ]; then echo "${ROOTFS_HASH}"; return; fi
  for f in "${STATE}/rootfs.json" "${REFERENCE}"; do
    [ -f "$f" ] && { jq -r '.item_hash // empty' "$f"; return; }
  done
  echo "no rootfs hash: set ROOTFS_HASH, run 'upload', or commit reference-rootfs.json" >&2; exit 1
}

pfw() {  # pfw <hash> <port> <name>: the CLI resolves the VM through the scheduler, which
         # learns about a new INSTANCE message with a delay — retry until it does.
  local out="${STATE}/pfw-$3-$2.json"
  for attempt in $(seq 1 30); do
    if aleph instance port-forward create "$1" "$2" --tcp true --json "${ACCT[@]}" > "$out" 2>&1; then
      log "  $3: port-forward $2 requested"; return 0
    fi
    grep -q 'not found in the scheduler' "$out" || { cat "$out" >&2; return 1; }
    [ $((attempt % 6)) -eq 0 ] && log "  $3: scheduler has not picked up the instance yet ($((attempt*10))s)"
    sleep 10
  done
  echo "$3: scheduler never picked up the instance for port $2" >&2; return 1
}

cmd_up() {  # resumable: names already in state/vms.json are reused, not recreated
  local rootfs; rootfs=$(resolve_rootfs)
  [ -f "${STATE}/vms.json" ] || echo "[]" > "${STATE}/vms.json"
  for name in "${NAMES[@]}"; do
    if jq -e --arg n "$name" '.[] | select(.name==$n)' "${STATE}/vms.json" >/dev/null 2>&1; then
      log "$name exists ($(jq -r --arg n "$name" '.[] | select(.name==$n) | .item_hash[0:12]' "${STATE}/vms.json")) — reusing"; continue
    fi
    log "creating $name ($VM_SIZE) from $rootfs"
    aleph instance create "$name" --image "$rootfs" --size "$VM_SIZE" --ssh-pubkey-file "${SSH_KEY}.pub" --json "${ACCT[@]}" \
      | tee "${STATE}/create-${name}.json" >/dev/null
    local h; h=$(jq -r '.item_hash // .itemHash // .hash // empty' "${STATE}/create-${name}.json"); [ -n "$h" ] || h=$(hex64 < "${STATE}/create-${name}.json")
    [ -n "$h" ] || { echo "no item hash in the create output for $name" >&2; exit 1; }
    jq --arg n "$name" --arg h "$h" '. + [{name:$n, item_hash:$h}]' "${STATE}/vms.json" > "${STATE}/vms.tmp" && mv "${STATE}/vms.tmp" "${STATE}/vms.json"
  done
  log "instances: $(jq -c '[.[].item_hash[0:12]]' "${STATE}/vms.json")"
  for i in $(seq 1 "${NODE_COUNT}"); do
    local h; h=$(vm_hash "$i")
    for port in 22 18444; do pfw "$h" "$port" "${NAMES[$((i-1))]}" || exit 1; done
  done
}

cmd_wait() {
  echo "[]" > "${STATE}/net.json"
  for i in $(seq 1 "${NODE_COUNT}"); do
    local h; h=$(vm_hash "$i"); local ipv4="" sshp="" p2pp=""
    for attempt in $(seq 1 60); do
      # read-only command: takes no --account (that flag is for signing commands only)
      aleph instance show "$h" --verbose --json > "${STATE}/show-$i.json" 2>/dev/null || true
      # `aleph instance show --verbose --json`: .networking.ipv4 and a flat
      # .mapped_ports {"22": 64859, ...}; tolerate the CRN record shape {"22": {"host": ...}} too.
      ipv4=$(jq -r '.networking.ipv4 // .ipv4 // empty' "${STATE}/show-$i.json")
      sshp=$(jq -r '(.mapped_ports["22"] | if type=="object" then .host else . end) // empty' "${STATE}/show-$i.json")
      p2pp=$(jq -r '(.mapped_ports["18444"] | if type=="object" then .host else . end) // empty' "${STATE}/show-$i.json")
      if [ -n "$ipv4" ] && [ -n "$sshp" ] && [ -n "$p2pp" ]; then
        if ssh -i "${SSH_KEY}" -p "$sshp" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 -o LogLevel=ERROR "root@$ipv4" true 2>/dev/null; then break; fi
      fi
      [ $((attempt % 6)) -eq 0 ] && log "vm$i: waiting (ipv4=${ipv4:-?} ssh=${sshp:-?} p2p=${p2pp:-?}) $((attempt*10))s"
      ipv4=""; sleep 10
    done
    [ -n "$ipv4" ] || { echo "vm$i not reachable"; exit 1; }
    jq --arg n "${NAMES[$((i-1))]}" --arg h "$h" --arg ip "$ipv4" --argjson s "$sshp" --argjson p "$p2pp" \
       '. + [{name:$n, item_hash:$h, ipv4:$ip, ssh_port:$s, p2p_port:$p}]' "${STATE}/net.json" > "${STATE}/net.tmp" && mv "${STATE}/net.tmp" "${STATE}/net.json"
    log "vm$i ready: ssh root@$ipv4 -p $sshp   p2p $ipv4:$p2pp"
  done
}

peer_addr() {  # peer_addr <j>: the VM's public IPv6 on the internal port — works across CRNs
               # and avoids hairpin NAT (a VM cannot reach its own host's public IPv4:mapped
               # port); PEER_MODE=ipv4 forces host-ipv4:mapped-port instead.
  local v6; v6=$(jq -r '.networking.ipv6 // empty' "${STATE}/show-$1.json" 2>/dev/null)
  if [ "${PEER_MODE:-auto}" != "ipv4" ] && [ -n "$v6" ]; then echo "[$v6]:18444"; else echo "$(net_get "$1" ipv4):$(net_get "$1" p2p_port)"; fi
}

cmd_wire() {
  for i in $(seq 1 "${NODE_COUNT}"); do
    local peers=""
    for j in $(seq 1 "${NODE_COUNT}"); do [ "$j" != "$i" ] && peers="$peers -addnode=$(peer_addr "$j")"; done
    peers="${peers# }"
    # listen on both stacks (an explicit -bind=0.0.0.0 would silence IPv6), then wire the peers
    vssh "$i" "sed -i -e 's| -bind=0.0.0.0:18444||' -e 's|^DOICHAIND_PEERS=.*|DOICHAIND_PEERS=$peers|' /etc/default/doichain && systemctl restart doichaind"
    log "vm$i peers: $peers"
  done
  sleep 20
  for i in $(seq 1 "${NODE_COUNT}"); do log "vm$i connections: $(vssh "$i" doichain-status getconnectioncount)"; done
}

set_role() {  # set_role <n> <hashrate> <name>
  vssh "$1" "printf 'MINER_HASHRATE=%s\nMINER_TICK=1\nMINER_NAME=%s\n' '$2' '$3' > /etc/default/doichain-miner && systemctl restart doichain-miner"
  log "vm$1 -> $3 @ $2 H/s"
}
cmd_roles() {
  set_role 1 "$HONEST_HR" honest
  [ "${NODE_COUNT}" -ge 2 ] && set_role 2 "$ATTACK_HR" attacker
  for i in $(seq 3 "${NODE_COUNT}"); do set_role "$i" 0 observer; done
}
cmd_leave() { [ "${NODE_COUNT}" -ge 2 ] || { echo "no attacker with NODE_COUNT=1"; exit 1; }; vssh 2 "touch /run/doichain/miner.stop"; log "attacker (vm2) told to leave — recovery starts now ($(date -u +%FT%TZ))"; }
cmd_status() { for i in $(seq 1 "${NODE_COUNT}"); do echo "vm$i $(vssh "$i" doichain-status 2>&1)"; done; }

cmd_collect() {
  local dir="${HERE}/runs/$(date +%Y%m%d-%H%M%S)"; mkdir -p "$dir"
  for i in $(seq 1 "${NODE_COUNT}"); do
    scp -q -i "${SSH_KEY}" -P "$(net_get "$i" ssh_port)" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR \
      "root@$(net_get "$i" ipv4):/var/lib/doichain/miner.csv" "$dir/vm$i-miner.csv" 2>/dev/null || true
    scp -q -i "${SSH_KEY}" -P "$(net_get "$i" ssh_port)" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR \
      "root@$(net_get "$i" ipv4):/var/lib/doichain/regtest/debug.log" "$dir/vm$i-debug.log" 2>/dev/null || true
  done
  cp "${STATE}"/*.json "$dir/" 2>/dev/null || true
  log "collected into $dir"; ls -la "$dir"
}

cmd_watchdog() {  # watchdog <hours>: detached timer that collects, then FORGETs everything
  local hours="${1:-12}"; local secs=$(( hours * 3600 ))
  local deadline; deadline=$(date -u -v+"${hours}"H +%FT%TZ 2>/dev/null || date -u -d "+${hours} hours" +%FT%TZ)
  nohup bash -c "sleep ${secs}; cd '${HERE}'; ./aleph-testnet.sh collect; ./aleph-testnet.sh down" \
    > "${STATE}/watchdog.log" 2>&1 &
  echo $! > "${STATE}/watchdog.pid"
  log "watchdog pid $! will collect + down at ${deadline} UTC  (cancel: ./aleph-testnet.sh watchdog-cancel)"
}
cmd_watchdog_cancel() { kill "$(cat "${STATE}/watchdog.pid")" 2>/dev/null && log "watchdog cancelled" || log "no watchdog running"; }
cmd_at() {  # at <minutes> <subcommand>: detached timer, e.g. `at 45 leave`
  local mins="$1" sub="$2"; local when; when=$(date -u -v+"${mins}"M +%FT%TZ 2>/dev/null || date -u -d "+${mins} minutes" +%FT%TZ)
  nohup bash -c "sleep $(( mins * 60 )); cd '${HERE}'; ./aleph-testnet.sh ${sub}" > "${STATE}/at-${sub}.log" 2>&1 &
  echo $! > "${STATE}/at-${sub}.pid"
  log "'${sub}' scheduled for ${when} UTC (pid $!)"
}

cmd_down() {
  for i in $(seq 1 "${NODE_COUNT}"); do
    local h; h=$(vm_hash "$i")
    aleph instance delete "$h" --json "${ACCT[@]}" >/dev/null && log "forgot ${NAMES[$((i-1))]} ($h)" || log "delete failed for $h"
  done
}

case "${1:-}" in
  build)   cmd_build ;;
  upload)  cmd_upload ;;
  up)      cmd_up ;;
  wait)    cmd_wait ;;
  wire)    cmd_wire ;;
  roles)   cmd_roles ;;
  leave)   cmd_leave ;;
  status)  cmd_status ;;
  collect) cmd_collect ;;
  down)    cmd_down ;;
  watchdog)        cmd_watchdog "${2:-12}" ;;
  watchdog-cancel) cmd_watchdog_cancel ;;
  at)      cmd_at "$2" "$3" ;;
  ssh)     n=$2; shift 2; vssh "$n" "$@" ;;
  *) sed -n '2,20p' "$0" ;;
esac
