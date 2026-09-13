#!/usr/bin/env python3
# Copyright (c) 2026 The Doichain developers
# Distributed under the MIT/X11 software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.

# RPC test for the Doichain-specific name_doi operation (OP_NAME_DOI).
#
# Part one covers the JSON rendering.  OP_NAME_DOI was added to the script,
# consensus and mempool code, but two switches that turn a name operation into
# RPC output were never extended and fell through to their
# "default: assert (false)" branch, aborting the node:
#
#   Assertion failed: false (core_io.cpp: NameOpToUniv: 581)
#
# That made every RPC converting such a transaction to JSON fatal --
# decoderawtransaction, getrawtransaction with verbosity, getblock with
# verbosity 2 -- and name_pending as well, whose input comes from the mempool
# and therefore from the P2P network.  Since name_doi is what the Doichain
# dApp writes, this aborted nodes on ordinary mainnet traffic.
#
# Part two covers the operation's behaviour: registration, update, the
# Doichain-specific one-step registration without a name input, re-registration
# of an expired name by a third party, and survival of a chain reorganisation.  Those flows come from the parallel test written by
# David Reband; they are adopted here rather than kept in a second file with the
# same name.
#
# Part three pins down that fee bumping refuses a name transaction: bumpfee
# rebuilt the replacement without the name prefix and turned a mainnet
# registration into a plain payment.
#
# Deliberately NOT adopted from that file, because they encode decisions taken
# the other way (see the audit review):
#
#   * d/ names being refused for name_doi -- today they are accepted, and
#     test/functional/name_doi_mempool.py pins that down.  The legacy guard
#     never worked (it compared EncodeNameForMessage(), which quotes the name),
#     so restoring it would introduce a rule, not restore one.
#   * a chain of two pending DOI operations on an *unconfirmed* registration --
#     that requires relaxing CheckNameTransaction, which is live consensus since
#     DoiOwnershipHeight = 431017 and therefore needs its own activation height.
#     name_doi_mempool.py pins down today's behaviour instead.

from test_framework.names import NameTestFramework
from test_framework.util import *

NAME = "e/regression"
VALUE = '{"dataHash":"4ea5c508","doiTimestamp":"2024-09-10T09:13:49.426Z"}'
NEW_VALUE = '{"dataHash":"0123abcd","doiTimestamp":"2024-09-10T09:18:56.204Z"}'


class NameDoiTest (NameTestFramework):

  def set_test_params (self):
    # -txindex mirrors the configuration the fleet runs, where getrawtransaction
    # is what the block explorer calls and where the abort was first observed.
    # -allowexpired lets name_show report an expired name instead of raising;
    # it is an RPC convenience and does not change what is valid.
    self.setup_name_test ([["-txindex", "-namehistory", "-allowexpired"]] * 2)

  def generateToOther (self, n):
    """
    Generates n blocks paying to the second node, so the coins of the first one
    stay predictable across sections.
    """

    addr = self.nodes[1].getnewaddress ()
    self.generatetoaddress (self.nodes[0], n, addr)

  def run_test (self):
    self.node = self.nodes[0]

    self.test_json_rendering ()
    self.test_registration ()
    self.test_update ()
    self.test_registration_without_name_input ()
    self.test_reregistration_after_expiry ()
    self.test_bumpfee_refused ()

    # Runs last: it disconnects the two nodes and rebuilds the chain.
    self.test_reorg ()

  def test_json_rendering (self):
    """
    Every RPC path that turns a name_doi output into JSON has to render it
    instead of aborting the node.
    """

    node = self.node

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

    # The asm rendering has to show the name and value as hex like the other
    # name operations, not as a decimal number.  Without OP_NAME_DOI in the
    # ScriptToAsmStr condition a short name comes out as an integer.
    asm = [out['scriptPubKey']['asm'] for tx in block['tx']
             for out in tx['vout'] if 'nameOp' in out['scriptPubKey']]
    assert_equal (len (asm), 1)
    assert "OP_NAME_DOI" in asm[0]

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

  def test_registration (self):
    """A name_doi on an unused name registers it in a single step."""

    self.log.info ("registering a DOI")
    node = self.node

    txid = node.name_doi ("e/first", "value one")
    assert txid in node.getrawmempool ()

    pending = [p for p in node.name_pending () if p["name"] == "e/first"]
    assert_equal (len (pending), 1)
    assert_equal (pending[0]["op"], "name_doi")

    self.generate (node, 1)
    data = node.name_show ("e/first")
    assert_equal (data["name"], "e/first")
    assert_equal (data["value"], "value one")
    assert_equal (data["expired"], False)

    names = [n["name"] for n in node.name_list ()]
    assert "e/first" in names

  def test_update (self):
    """A name_doi spending the previous DOI output updates the name."""

    self.log.info ("updating a DOI")
    node = self.node

    node.name_doi ("e/first", "value two")
    self.generate (node, 1)
    assert_equal (node.name_show ("e/first")["value"], "value two")

  def test_registration_without_name_input (self):
    """
    A DOI may be registered without spending a name input.  This is where
    Doichain diverges from Namecoin: mainnet block 29966 carries three such
    registrations, which is why the historic rule below DoiOwnershipHeight has
    to keep accepting them.
    """

    self.log.info ("registering a DOI without a name input")
    node = self.node

    txid = node.name_doi ("e/no-input", "fresh")
    raw = node.getrawtransaction (txid, True)

    nameIns = 0
    for vin in raw["vin"]:
      prev = node.getrawtransaction (vin["txid"], True)
      spent = prev["vout"][vin["vout"]]["scriptPubKey"]
      if "nameOp" in spent:
        nameIns += 1
    assert_equal (nameIns, 0)

    self.generate (node, 1)
    assert_equal (node.name_show ("e/no-input")["value"], "fresh")

  def test_reregistration_after_expiry (self):
    """
    An expired name is free again, and *anybody* may take it -- not just its
    previous owner.  Both registration paths allow it; this covers the name_doi
    one, which no test touched before: name_expiration.py and
    name_allowexpired.py only exercise name_new / name_firstupdate.

    This is deliberately a characterisation test.  Switching name expiry off is
    under discussion (it is implemented on a branch but activated nowhere), and
    it would change exactly this behaviour.  Pinning it down means that change
    has to be made openly rather than slipping through.

    Scale of the question on mainnet: 57 699 of 57 740 names are expired, so
    almost every name ever registered is currently free for anyone to take.
    """

    self.log.info ("re-registering an expired name from another wallet")
    node, other = self.nodes[0], self.nodes[1]
    name = "e/expired-doi"

    node.name_doi (name, "belongs to node 0")
    self.generate (node, 1)
    data = node.name_show (name)
    assert_equal (data['value'], "belongs to node 0")
    assert_equal (data['expired'], False)
    assert_equal (data['ismine'], True)

    # Mine past the expiration depth -- 30 blocks on regtest -- paying the
    # second node, so its wallet can fund the takeover.  Coinbase outputs need
    # 100 confirmations, hence the larger count.
    self.generateToOther (110)

    data = node.name_show (name)
    assert_equal (data['expired'], True)
    assert data['expires_in'] < 0

    # The other wallet registers the same name in one step.  It must not spend
    # a name input: to consensus the name counts as free again, and spending
    # one would be rejected as tx-namedoi-freename-with-input.
    txid = other.name_doi (name, "taken over by node 1")
    assert txid in other.getrawmempool ()

    raw = other.getrawtransaction (txid, True)
    for vin in raw['vin']:
      prev = other.getrawtransaction (vin['txid'], True)
      assert 'nameOp' not in prev['vout'][vin['vout']]['scriptPubKey']

    self.generate (other, 1)

    data = other.name_show (name)
    assert_equal (data['value'], "taken over by node 1")
    assert_equal (data['expired'], False)
    assert_equal (data['ismine'], True)

    # And it really left the first wallet.
    assert_equal (node.name_show (name)['ismine'], False)

  def test_bumpfee_refused (self):
    """
    Fee bumping has to refuse a name transaction.  bumpfee and psbtbumpfee
    rebuild the replacement from the plain destinations of the original
    outputs, which strips the name prefix.  For a name_doi whose name output
    and change both pay to the wallet, both even count as change and are merged
    into a single output: the replacement is a valid plain payment that relays
    and confirms, and the registration is gone.  That happened on mainnet with a
    v31.1.4 test registration whose fee had been too low to relay.
    """

    self.log.info ("refusing to bump the fee of a name transaction")
    node = self.node
    name = "e/bumped"

    # A registration without a name input, as in the mainnet case.
    txid = node.name_doi (name, "registration")
    for bump in [node.bumpfee, node.psbtbumpfee]:
      assert_raises_rpc_error (-4, "Transaction contains a name operation",
                               bump, txid)

    # The original is untouched: still pending, still carrying the name.
    pending = [p for p in node.name_pending () if p["name"] == name]
    assert_equal (len (pending), 1)
    assert_equal (pending[0]["txid"], txid)
    assert_equal (pending[0]["op"], "name_doi")
    self.generate (node, 1)
    assert_equal (node.name_show (name)["value"], "registration")

    # An update spending the name output is refused just the same.
    txid = node.name_doi (name, "update")
    for bump in [node.bumpfee, node.psbtbumpfee]:
      assert_raises_rpc_error (-4, "Transaction contains a name operation",
                               bump, txid)
    self.generate (node, 1)
    assert_equal (node.name_show (name)["value"], "update")

  def test_reorg (self):
    """
    A DOI registration has to survive being reorganised out and back in, and
    the name database has to stay consistent across it.
    """

    self.log.info ("reorganising a block with a name_doi")
    node = self.node
    other = self.nodes[1]

    self.disconnect_nodes (0, 1)
    addrOther = other.getnewaddress ()

    node.name_doi ("e/reorged", "before")
    self.generatetoaddress (node, 1, addrOther, sync_fun=self.no_op)
    assert_equal (node.name_show ("e/reorged")["value"], "before")

    # The other node builds a longer chain without our transaction.
    self.generatetoaddress (other, 3, addrOther, sync_fun=self.no_op)

    self.connect_nodes (0, 1)
    self.sync_blocks ()

    # The name is gone from the chain but back in node 0's mempool, so mining
    # on node 0 brings it back.
    assert_raises_rpc_error (-4, "name never existed",
                             node.name_show, "e/reorged")
    self.generatetoaddress (node, 1, addrOther, sync_fun=self.no_op)
    self.sync_blocks ()
    assert_equal (node.name_show ("e/reorged")["value"], "before")

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
