#!/usr/bin/env python3
# Copyright (c) 2026 The Doichain Core developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""Doichain DigiShield-v3 difficulty adjustment, end to end across nodes.

Exercises the regtest-only DAA switches (-digishieldheight, -digishieldresetbits,
-digishieldstrict) on a real multi-node chain:

  1. reset window   : the first 28 blocks from activation carry exactly the reset
                      target, whatever the pre-fork chain looked like
  2. continuation   : DigiShield then holds ~the reset target at neutral spacing
  3. bounded valve  : after a gap longer than the valve, the next block is 4x
                      easier -- and never powLimit -- and the blocks after it do
                      not collapse (no block storm)
  4. block relay    : a connected peer accepts every per-block difficulty change
  5. headers presync: a fresh node with -minimumchainwork pulls the >2000-block
                      DigiShield stretch through the low-work headers sync, where
                      PermittedDifficultyTransition is checked for every header.
                      Without the DigiShield branch in that function this sync
                      would stall (old_nbits != new_nbits off the 2016-boundary).

-digishieldstrict disables regtest's min-difficulty exception, which otherwise
short-circuits PermittedDifficultyTransition and would give this test no teeth.
Block times are driven with setmocktime, so the run is deterministic.
"""

import time

from test_framework.test_framework import BitcoinTestFramework
from test_framework.util import (
    assert_equal,
    assert_greater_than_or_equal,
)

ACTIVATION = 20              # -digishieldheight
RESET_BITS = "1f7fffff"      # -digishieldresetbits: 256x harder than regtest min, still instant
REGTEST_MIN_BITS = "207fffff"
AVERAGING_WINDOW = 17        # nPowAveragingWindow
MEDIAN_TIME_SPAN = 11        # CBlockIndex::nMedianTimeSpan
RESET_WINDOW = AVERAGING_WINDOW + MEDIAN_TIME_SPAN
SPACING = 600                # nPowTargetSpacing
VALVE_GAP = 6 * SPACING      # nDoiMinDifficultyGap on regtest (set by -digishieldheight)
VALVE_FACTOR = 4             # nDoiMinDifficultyValveFactor
MAX_HEADERS_RESULTS = 2000   # a full headers message is what triggers low-work headers sync
DIGISHIELD_BLOCKS = MAX_HEADERS_RESULTS + 40   # DigiShield blocks after the reset window


def target_of(bits_hex):
    """Decode compact nBits (hex string) to the 256-bit target."""
    bits = int(bits_hex, 16)
    exponent, mantissa = bits >> 24, bits & 0x007fffff
    return mantissa << (8 * (exponent - 3)) if exponent > 3 else mantissa >> (8 * (3 - exponent))


class DigishieldTest(BitcoinTestFramework):
    def set_test_params(self):
        self.setup_clean_chain = True
        self.num_nodes = 3
        daa = [f"-digishieldheight={ACTIVATION}", f"-digishieldresetbits={RESET_BITS}", "-digishieldstrict=1"]
        # node2 stays disconnected and empty until the presync test; -debug=net exposes
        # the headers-sync log lines we assert on.
        self.extra_args = [daa, daa, daa + ["-debug=net"]]

    def setup_network(self):
        self.setup_nodes()
        self.connect_nodes(0, 1)

    def bits_at(self, node, height):
        return node.getblockheader(node.getblockhash(height))["bits"]

    def mine_at(self, t):
        """Mine one block on node0 with block time t (deterministic via mocktime)."""
        self.nodes[0].setmocktime(t)
        return self.generate(self.nodes[0], 1, sync_fun=self.no_op)[0]

    def run_test(self):
        miner, peer, fresh = self.nodes
        t = int(time.time()) - 40 * 24 * 3600          # start 40 days back: the whole run stays in the past
        # node1 receives blocks by relay as they are mined; give it a clock that is
        # ahead of every block time so nothing is rejected as too far in the future.
        peer.setmocktime(t + (ACTIVATION + RESET_WINDOW + DIGISHIELD_BLOCKS + 10) * SPACING + VALVE_GAP)

        self.log.info("Pre-fork: old rules keep the regtest minimum difficulty")
        for _ in range(ACTIVATION - 1):
            t += SPACING
            self.mine_at(t)
        assert_equal(miner.getblockcount(), ACTIVATION - 1)
        assert_equal(self.bits_at(miner, ACTIVATION - 1), REGTEST_MIN_BITS)

        self.log.info(f"Reset window: blocks {ACTIVATION}..{ACTIVATION + RESET_WINDOW - 1} carry the reset target")
        for _ in range(RESET_WINDOW):
            t += SPACING
            self.mine_at(t)
        for h in range(ACTIVATION, ACTIVATION + RESET_WINDOW):
            assert_equal(self.bits_at(miner, h), RESET_BITS)

        self.log.info("Continuation: DigiShield holds the reset target at neutral spacing")
        reset_target = target_of(RESET_BITS)
        for _ in range(DIGISHIELD_BLOCKS):
            t += SPACING
            self.mine_at(t)
        tip_height = miner.getblockcount()
        assert_equal(tip_height, ACTIVATION + RESET_WINDOW + DIGISHIELD_BLOCKS - 1)
        first_ds = target_of(self.bits_at(miner, ACTIVATION + RESET_WINDOW))
        assert_greater_than_or_equal(reset_target, first_ds)                    # never easier than the reset
        assert_greater_than_or_equal(first_ds, reset_target * 84 // 100)       # at most one clamp step harder
        tip_target = target_of(self.bits_at(miner, tip_height))
        assert_greater_than_or_equal(reset_target * 110 // 100, tip_target)   # still ~reset after 2000 blocks
        assert_greater_than_or_equal(tip_target, reset_target * 90 // 100)

        self.log.info("Bounded valve: a block after a >1h gap is 4x easier, never powLimit")
        prev_target = tip_target
        t += VALVE_GAP + 1
        self.mine_at(t)
        valve_height = miner.getblockcount()
        valve_bits = self.bits_at(miner, valve_height)
        valve_target = target_of(valve_bits)
        assert valve_bits != REGTEST_MIN_BITS
        expected = prev_target * VALVE_FACTOR
        # compact encoding truncates the mantissa: allow the last-byte rounding
        assert_greater_than_or_equal(expected, valve_target)
        assert_greater_than_or_equal(valve_target, expected * 999 // 1000)

        self.log.info("After the valve: the window absorbs the easier block without a storm")
        for _ in range(6):
            t += SPACING
            self.mine_at(t)
        for h in range(valve_height + 1, miner.getblockcount() + 1):
            tgt = target_of(self.bits_at(miner, h))
            assert_greater_than_or_equal(reset_target * 150 // 100, tgt)     # never more than 1.5x the reset target
            assert_greater_than_or_equal(tgt, reset_target)                  # and no tighter than it

        self.log.info("Block relay: the connected peer followed every difficulty change")
        self.sync_blocks(self.nodes[:2], timeout=120)
        assert_equal(peer.getbestblockhash(), miner.getbestblockhash())
        assert_equal(self.bits_at(peer, valve_height), valve_bits)

        self.log.info("Headers presync: a fresh node with -minimumchainwork syncs the DigiShield stretch")
        tip = miner.getblockcount()
        min_work = miner.getblockheader(miner.getblockhash(tip - 3))["chainwork"]
        self.restart_node(2, extra_args=self.extra_args[2] + [f"-minimumchainwork=0x{min_work}"])
        fresh.setmocktime(t + VALVE_GAP)
        assert_equal(fresh.getblockcount(), 0)
        # "invalid difficulty transition" is what PermittedDifficultyTransition logs when it
        # rejects a header -- the failure mode the DigiShield branch exists to prevent.
        with fresh.assert_debug_log(["Initial headers sync started", "Initial headers sync complete"],
                                    unexpected_msgs=["invalid difficulty transition"], timeout=120):
            self.connect_nodes(2, 0)
            self.sync_blocks(self.nodes, timeout=180)
        assert_equal(fresh.getbestblockhash(), miner.getbestblockhash())
        assert_equal(self.bits_at(fresh, ACTIVATION + RESET_WINDOW - 1), RESET_BITS)
        assert_equal(self.bits_at(fresh, valve_height), valve_bits)


if __name__ == "__main__":
    DigishieldTest(__file__).main()
