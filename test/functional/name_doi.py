#!/usr/bin/env python3
# Copyright (c) 2026 The Doichain developers
# Distributed under the MIT/X11 software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.

# RPC test for the Doichain-specific name_doi operation (OP_NAME_DOI).
#
# The point of this test is that a transaction carrying a name_doi output can
# be rendered as JSON.  OP_NAME_DOI was added to the script, consensus and
# mempool code, but two switches that turn a name operation into RPC output
# were never extended and fell through to their "default: assert (false)"
# branch, aborting the node:
#
#   Assertion failed: false (core_io.cpp: NameOpToUniv: 581)
#
# That made every RPC converting such a transaction to JSON fatal --
# decoderawtransaction, getrawtransaction with verbosity, getblock with
# verbosity 2 -- and name_pending as well, whose input comes from the mempool
# and therefore from the P2P network.  Since name_doi is what the Doichain
# dApp writes, this aborted nodes on ordinary mainnet traffic.

from test_framework.names import NameTestFramework
from test_framework.util import *

NAME = "e/regression"
VALUE = '{"dataHash":"4ea5c508","doiTimestamp":"2024-09-10T09:13:49.426Z"}'
NEW_VALUE = '{"dataHash":"0123abcd","doiTimestamp":"2024-09-10T09:18:56.204Z"}'


class NameDoiTest (NameTestFramework):

  def set_test_params (self):
    # -txindex mirrors the configuration the fleet runs, where getrawtransaction
    # is what the block explorer calls and where the abort was first observed.
    self.setup_name_test ([["-txindex"]] * 2)

  def run_test (self):
    node = self.nodes[0]

    # Register the name in a single step.  While the transaction sits in the
    # mempool, name_pending has to report it instead of aborting the node.
    txid = node.name_doi (NAME, VALUE)
    self.checkPendingDoi (node, txid, VALUE)

    self.generate (node, 1)
    assert_equal (node.name_pending (), [])

    # Decoding the mined transaction is the path a block explorer takes.
    self.checkDecodedDoi (0, txid, VALUE)

    # getblock at verbosity 2 runs every transaction of the block through the
    # very same conversion.
    block = node.getblock (node.getbestblockhash (), 2)
    ops = [out['scriptPubKey']['nameOp']
             for tx in block['tx'] for out in tx['vout']
             if 'nameOp' in out['scriptPubKey']]
    assert_equal (len (ops), 1)
    assert_equal (ops[0]['op'], "name_doi")
    assert_equal (ops[0]['name'], NAME)
    assert_equal (ops[0]['value'], VALUE)

    # Updating an existing name spends its previous name_doi output; the
    # update has to decode just as well as the registration.
    txid = node.name_doi (NAME, NEW_VALUE)
    self.checkPendingDoi (node, txid, NEW_VALUE)
    self.generate (node, 1)
    self.checkDecodedDoi (0, txid, NEW_VALUE)

    # Finally, the name index has to agree with what the raw transaction said.
    data = node.name_show (NAME)
    assert_equal (data['name'], NAME)
    assert_equal (data['value'], NEW_VALUE)

  def checkPendingDoi (self, node, txid, value):
    """
    Verifies that name_pending reports the unconfirmed name_doi transaction,
    both unfiltered and filtered by name.
    """

    assert txid in node.getrawmempool ()

    for pending in [node.name_pending (), node.name_pending (NAME)]:
      assert_equal (len (pending), 1)
      assert_equal (pending[0]['op'], "name_doi")
      assert_equal (pending[0]['name'], NAME)
      assert_equal (pending[0]['value'], value)
      assert_equal (pending[0]['txid'], txid)

  def checkDecodedDoi (self, ind, txid, value):
    """
    Decodes the given transaction and verifies its single name output, once
    through decoderawtransaction and once through getrawtransaction.
    """

    txHex = self.nodes[ind].gettransaction (txid)['hex']
    data = self.nodes[ind].decoderawtransaction (txHex)

    res = None
    for out in data['vout']:
      if 'nameOp' in out['scriptPubKey']:
        assert res is None
        res = out['scriptPubKey']['nameOp']

    assert res is not None
    assert_equal (res['op'], "name_doi")
    assert_equal (res['name'], NAME)
    assert_equal (res['value'], value)

    # The same has to come back through getrawtransaction, which is what the
    # block explorer calls.
    verbose = self.nodes[ind].getrawtransaction (txid, 1)
    ops = [out['scriptPubKey']['nameOp'] for out in verbose['vout']
             if 'nameOp' in out['scriptPubKey']]
    assert_equal (ops, [res])


if __name__ == '__main__':
  NameDoiTest (__file__).main ()
