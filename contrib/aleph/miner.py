#!/usr/bin/env python3
"""
Throttled CPU miner / observer for the Doichain DAA rehearsal (stage B).

Instead of building blocks itself, it asks the node to try a bounded number of
nonces per tick on a fresh template:

    generatetoaddress 1 <address> <maxtries>

so the effective hashrate is exactly  maxtries / tick  — settable per role
(honest ~1 kH/s, attacker ~47 kH/s) — against the node's real difficulty, with
real proof of work and real block times.  Hashing is memoryless, so restarting
the search on a new template every tick does not change the statistics.

With --hashrate 0 it mines nothing and only logs the chain (observer role).
Every tick writes one CSV line: unix time, height, nBits, difficulty, found.

  miner.py --datadir /var/lib/doichain --hashrate 1000 --name honest
  miner.py --rpc http://127.0.0.1:18443 --cookie /var/lib/doichain/regtest/.cookie ...
  touch /run/doichain/miner.stop      # graceful stop (attacker leaves)
"""
import argparse, base64, json, os, sys, time, urllib.request


class Rpc:
    def __init__(self, url, cookie=None, user=None, password=None):
        self.url = url
        if cookie:
            with open(cookie) as f:
                user, password = f.read().strip().split(":", 1)
        self.auth = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.id = 0

    def __call__(self, method, *params):
        self.id += 1
        body = json.dumps({"jsonrpc": "1.0", "id": self.id, "method": method, "params": list(params)}).encode()
        req = urllib.request.Request(self.url, data=body, headers={
            "Content-Type": "application/json", "Authorization": f"Basic {self.auth}"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            out = json.loads(resp.read())
        if out.get("error"):
            raise RuntimeError(out["error"])
        return out["result"]


def wait_for_node(rpc, log):
    for i in range(600):
        try:
            rpc("getblockcount")
            return
        except Exception as e:                       # noqa: BLE001 - node still starting
            if i % 10 == 0:
                log(f"waiting for node: {e}")
            time.sleep(2)
    sys.exit("node did not come up")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rpc", default="http://127.0.0.1:18443", help="node RPC URL")
    ap.add_argument("--datadir", help="derive cookie from <datadir>/regtest/.cookie")
    ap.add_argument("--cookie", help="explicit cookie file")
    ap.add_argument("--rpcuser"); ap.add_argument("--rpcpassword")
    ap.add_argument("--address", help="payout address (default: node wallet getnewaddress)")
    ap.add_argument("--hashrate", type=float, default=1000.0, help="hashes per second; 0 = observe only")
    ap.add_argument("--tick", type=float, default=1.0, help="seconds per mining attempt")
    ap.add_argument("--name", default="miner", help="role label for logs")
    ap.add_argument("--stop-file", default="/run/doichain/miner.stop", help="stop mining when this file exists")
    ap.add_argument("--csv", default=None, help="CSV log path (default: stdout only)")
    args = ap.parse_args()

    def log(msg):
        print(f"{time.strftime('%H:%M:%S')} [{args.name}] {msg}", flush=True)

    cookie = args.cookie or (os.path.join(args.datadir, "regtest", ".cookie") if args.datadir else None)
    rpc = Rpc(args.rpc, cookie=cookie, user=args.rpcuser, password=args.rpcpassword)
    wait_for_node(rpc, log)

    address = args.address
    if args.hashrate > 0 and not address:
        try:
            rpc("createwallet", "miner", False, False, "", False, True, True)
        except Exception:                             # noqa: BLE001 - already exists
            try:
                rpc("loadwallet", "miner")
            except Exception:                         # noqa: BLE001 - already loaded
                pass
        address = rpc("getnewaddress")
        log(f"payout address {address}")

    maxtries = max(1, int(round(args.hashrate * args.tick)))
    csv = open(args.csv, "a") if args.csv else None
    log(f"start: hashrate={args.hashrate:.0f} H/s -> maxtries={maxtries}/tick ({args.tick}s), stop-file={args.stop_file}")

    mining = args.hashrate > 0
    next_tick = time.monotonic()
    last_height = -1
    while True:
        next_tick += args.tick
        found = ""
        if mining and os.path.exists(args.stop_file):
            log("stop-file present: leaving the network (no more hashing)")
            mining = False
        if mining:
            try:
                res = rpc("generatetoaddress", 1, address, maxtries)
                if res:
                    found = res[0]
            except Exception as e:                    # noqa: BLE001
                log(f"rpc error: {e}")
                time.sleep(2)
        try:
            info = rpc("getblockchaininfo")
            height, diff = info["blocks"], info["difficulty"]
            bits = rpc("getblockheader", info["bestblockhash"])["bits"]
        except Exception as e:                        # noqa: BLE001
            log(f"rpc error: {e}"); time.sleep(2); continue
        if found or height != last_height:
            log(f"height={height} bits={bits} difficulty={diff:.6g}" + (f" FOUND {found[:16]}…" if found else ""))
            last_height = height
        if csv:
            csv.write(f"{int(time.time())},{height},{bits},{diff:.6g},{1 if found else 0}\n"); csv.flush()
        delay = next_tick - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:                                          # hashing took longer than a tick: re-anchor, don't spiral
            next_tick = time.monotonic()


if __name__ == "__main__":
    main()
