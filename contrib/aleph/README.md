# Doichain nodes on Aleph.im

Launch one or more `doichaind` VMs on [Aleph.im](https://aleph.im) from the command line,
using the Rust `aleph` CLI and SSH — no CI pipeline involved. Built for the DigiShield
difficulty-adjustment rehearsal (three regtest nodes: honest miner, attacker, observer),
usable for any single node.

## Prerequisites

- `aleph` CLI 0.18+ (`cargo install aleph-cli`) with a funded account: `aleph account list`,
  `aleph account use <name>` (or pass `ALEPH_ACCOUNT=<name>`). Credits are charged per hour
  per instance and per pinned image — see `aleph instance price --size 1vcpu-2gb`.
- an SSH key pair (`~/.ssh/id_ed25519` or `~/.ssh/id_rsa`; override with `SSH_KEY=`).
- `jq`, `ssh`, `scp`.
- **Only for building an image:** Docker (Docker Desktop on macOS, x86_64 host). On macOS,
  if `docker pull` hangs and dies with `error getting credentials`, run with a
  credential-free config: `export DOCKER_CONFIG=$(mktemp -d); echo '{}' > "$DOCKER_CONFIG/config.json"`.

## Path A — the reference image is already published

`reference-rootfs.json` records the item hash of the last published image (commit, size,
date). Nothing to build:

```bash
cd contrib/aleph
NODE_COUNT=1 ./aleph-testnet.sh up      # one node (default NODE_COUNT=3)
./aleph-testnet.sh wait                 # ipv4 + mapped ports + ssh
./aleph-testnet.sh status               # height, nBits, difficulty, peers, tip age
./aleph-testnet.sh ssh 1                # root shell; `doichain-status getpeerinfo` etc.
./aleph-testnet.sh down                 # FORGET the instances — this is what stops billing
```

Use a different image with `ROOTFS_HASH=<item hash> ./aleph-testnet.sh up`.

## Path B — build and publish your own image

```bash
cd contrib/aleph
./aleph-testnet.sh build     # 1) doichaind + doichain-cli for Debian 12 amd64 (Docker)
                             # 2) rootfs: Debian 12 genericcloud + virt-customize + sparsify
./aleph-testnet.sh upload    # aleph file upload -> IPFS + STORE pin; rewrites reference-rootfs.json
git add reference-rootfs.json && git commit -m "contrib/aleph: publish rootfs <hash>"
```

The binary is compiled inside a `debian:12` container on purpose: the guest image is
Debian 12, and a binary from a newer distro fails there on glibc/libevent versions.

## The rehearsal (three nodes)

Re-run the whole difficulty-attack rehearsal — attacker pumps the difficulty, leaves,
DigiShield recovers — with a single command. The VMs are always torn down at the end
(even on error or Ctrl-C), so a failed run never keeps billing:

```bash
./aleph-testnet.sh rehearse         # up → wire → roles → attack → leave → recover → collect → down
PUMP_MIN=45 REC_MIN=180 ./aleph-testnet.sh rehearse   # tune the phase lengths (defaults shown)
```

It leaves the measured data in `runs/<timestamp>/` and evaluates it with `analyze-run.py`
(recovery curve, valve events, time-to-recover). The reference run (2026-09-08) recovered
a 48× attack in ~2.5 h time-lapse (≈ 25 h mainnet scale) and then held ~60-second blocks.

Or drive the phases by hand:

```bash
./aleph-testnet.sh up && ./aleph-testnet.sh wait
./aleph-testnet.sh wire      # -addnode mesh over SSH, restart nodes, verify 2 peers each
./aleph-testnet.sh roles     # vm1 honest @ HONEST_HR (1000 H/s), vm2 attacker @ ATTACK_HR (47000 H/s), vm3 observer
# ... let the attacker pump the difficulty (watch `status`), then:
./aleph-testnet.sh leave     # attacker stops hashing; DigiShield must recover with vm1 alone
./aleph-testnet.sh status    # repeat; tip age and difficulty tell the story
./aleph-testnet.sh collect   # miner.csv + debug.log from every VM -> runs/<timestamp>/
./aleph-testnet.sh down
```

The guests run regtest with `-digishieldheight=20 -digishieldresetbits=1f7fffff
-digishieldstrict=1 -powtargetspacing=60` (see `rootfs/guest/doichain.env`): a 60-second
block target compresses the DigiShield dynamics tenfold, so a ~28-hour mainnet-scale
recovery plays out in about three hours. The miner is a throttled RPC loop
(`generatetoaddress 1 <addr> <maxtries>` per tick), so the hashrate ratio comes from the
`*_HR` settings, not from machine size; a `1vcpu-2gb` instance is plenty.

## What is in the image

| Guest path | Purpose |
|---|---|
| `/usr/local/bin/doichaind`, `doichain-cli` | node binaries (Debian 12 build) |
| `/etc/default/doichain` | `DOICHAIND_OPTS` (consensus switches, identical on every VM) and `DOICHAIND_PEERS` (written by `wire`) |
| `/etc/default/doichain-miner` | `MINER_HASHRATE`, `MINER_TICK`, `MINER_NAME` (written by `roles`) |
| `doichaind.service`, `doichain-miner.service` | start at boot; `RuntimeDirectory=/run/doichain`; the attacker leaves when `/run/doichain/miner.stop` exists |
| `/usr/local/bin/doichain-status` | one-line status, or any `doichain-cli` call |
| `/var/lib/doichain/miner.csv` | per-tick log: time, height, nBits, difficulty, block found |

RPC stays on localhost (cookie auth); everything is driven over SSH (`root@<host ipv4> -p <mapped 22>`).

## Files

| File | Role |
|---|---|
| `aleph-testnet.sh` | orchestrator: `build · upload · up · wait · wire · roles · leave · status · collect · down · ssh` |
| `build-linux-binaries.sh` | Debian-12 build of `doichaind`/`doichain-cli` in Docker → `dist/` |
| `rootfs/build-rootfs.sh` | host wrapper: builds the libguestfs builder image and runs the recipe (privileged, amd64) |
| `rootfs/build-rootfs-image.sh` | the recipe (runs inside the builder): genericcloud qcow2 → customize → `virt-sparsify --compress` |
| `rootfs/guest/*` | systemd units, env files, guest setup, status helper |
| `miner.py` | throttled miner / observer (also usable against any local regtest node) |
| `reference-rootfs.json` | item hash of the published reference image (Path A) |

## Aleph specifics worth knowing

- Every deploy is a **new instance**: new host IPv4, new mapped ports. Peers are wired
  after the fact, which is what `wait` + `wire` do.
- `aleph instance show <hash> --verbose --json` exposes `.networking.ipv4` and a flat
  `.mapped_ports {"22": 64859, "18444": 64860}`; the CRN's own execution record uses
  `{"22": {"host": …}}` — the orchestrator accepts both.
- Instances bill until `aleph instance delete`. `down` is not optional.
- Other networks: change `DOICHAIND_OPTS` in `rootfs/guest/doichain.env` and rebuild, or
  edit `/etc/default/doichain` over SSH and `systemctl restart doichaind` (a mainnet node
  needs more disk than `1vcpu-2gb`'s 20 GiB over time).
