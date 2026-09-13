#!/usr/bin/env python3
# Copyright (c) 2026 The Doichain developers
# Distributed under the MIT/X11 software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.

# Tests that name_doi operations are tracked by the name mempool exactly like
# name_update ones.
#
# The legacy 0.20 tree kept DOI operations in a separate map (mapNameDois) that
# addUnchecked filled but removeExpireConflicts never cleaned.  The nc31 fork
# dissolved that map: DOI now goes into the same `updates` map as name_update,
# guarded by `isNameUpdate () || isNameDoi ()` in addUnchecked, remove and
# check.  Everything derived from that map therefore covers DOI too:
#
#   a) pendingChainLength counts pending DOI operations, so -limitnamechains
#      applies to them;
#   b) lastNameOutput finds the previous pending DOI output, so a second
#      operation chains onto the first instead of conflicting with it;
#   c) removeExpireConflicts evicts pending DOI operations when the name
#      expires.
#
# (a) and (b) are covered below.  (c) follows from the same map -- the function
# iterates `updates` and removes recursively -- but is not exercised here: it
# only triggers on a chain reorg, and constructing one in this harness proved
# brittle for reasons unrelated to the property under test (deterministic mining
# reproduces an invalidated block byte for byte, and wallet resubmission of
# previously rejected transactions perturbs the mempool assertions).  Rather
# than ship a test that is green because it checks little, the property is left
# to review of removeExpireConflicts itself.
#
# The test further exercises the name_doi branch of name_pending, which is why
# it lives next to the core_io / rpc/names fix.

from test_framework.names import NameTestFramework
from test_framework.util import assert_equal, assert_raises_rpc_error

CHAIN_LIMIT = 2


class NameDoiMempoolTest (NameTestFramework):

  def set_test_params (self):
    self.setup_clean_chain = True
    self.setup_name_test ([["-debug", "-namehistory", "-allowexpired",
                            "-limitnamechains=%d" % CHAIN_LIMIT]])

  def nameOutput (self, txid):
    """
    Returns the vout index of the single name output of the given transaction.
    """

    data = self.node.getrawtransaction (txid, 1)
    found = [out['n'] for out in data['vout']
               if 'nameOp' in out['scriptPubKey']]
    assert_equal (len (found), 1)
    return found[0]

  def run_test (self):
    self.node = self.nodes[0]
    self.generate (self.node, 200)

    self.test_chain_on_confirmed_name ()
    self.test_d_namespace_is_not_reserved ()

    # Runs last on purpose: it leaves a rejected transaction behind in the
    # wallet, which becomes valid once its parent confirms -- the name then
    # exists, so the update path applies instead of the free-name one -- and is
    # resubmitted by the wallet.  That would perturb the empty-mempool
    # assertions of any section running after it.
    self.test_pending_registration_not_chainable ()

  def test_chain_on_confirmed_name (self):
    """
    a) the pending-chain limit counts name_doi operations, and
    b) a second operation chains onto the first one's output.
    """

    node = self.node
    name = "e/chain"

    # Register and confirm, so the name exists in the name database.
    node.name_doi (name, "one")
    self.generate (node, 1)
    self.checkName (0, name, "one", None, False)

    # Two operations on the confirmed name; the second has to chain.
    first = node.name_doi (name, "two")
    second = node.name_doi (name, "three")
    assert_equal (set (node.getrawmempool ()), {first, second})

    # name_pending has to report both, with the DOI operation name.  Without
    # the OP_NAME_DOI branch in rpc/names.cpp this aborts the node instead.
    pending = node.name_pending (name)
    assert_equal (len (pending), 2)
    assert_equal ([p['op'] for p in pending], ["name_doi", "name_doi"])
    assert_equal (sorted (p['value'] for p in pending), ["three", "two"])

    # The actual proof of chaining: the second transaction spends the name
    # output of the first one.  That only happens if lastNameOutput() found it,
    # which in turn requires the DOI entry to be in the `updates` map.
    prevOut = self.nameOutput (first)
    spent = [(vin['txid'], vin['vout'])
               for vin in node.getrawtransaction (second, 1)['vin']]
    assert (first, prevOut) in spent

    # Two operations are pending, which is the configured limit, so a third one
    # has to be refused.  This is pendingChainLength() counting DOI entries; if
    # it returned 0 for them, this call would succeed.
    assert_raises_rpc_error (None, "too many pending operations",
                             node.name_doi, name, "four")

    self.generate (node, 1)
    assert_equal (node.getrawmempool (), [])
    self.checkName (0, name, "three", None, False)
    values = [h['value'] for h in node.name_history (name)]
    assert_equal (values, ["one", "two", "three"])

  def test_pending_registration_not_chainable (self):
    """
    Pins down a real restriction, inherited from the legacy tree: chaining onto
    an unconfirmed one-step registration is rejected by consensus.  The mempool
    bookkeeping is not at fault -- the wallet RPC does find the pending output
    and builds the chained transaction -- but the strict ownership branch of
    CheckNameTransaction sees the name as still free, because `doiExists` is
    queried against the confirmed name database, and a registration of a free
    name must not spend a name input.  The guard for pending inputs further
    down (`inHeight == MEMPOOL_HEIGHT`) is unreachable in that case.

    With the default -limitnamechains=1 this is invisible, since the RPC
    refuses a second pending operation anyway.
    """

    node = self.node
    name = "e/fresh"

    pendingReg = node.name_doi (name, "one")
    assert_equal (node.getrawmempool (), [pendingReg])

    # The RPC succeeds and returns a txid -- CommitTransaction does not throw
    # when the transaction cannot be broadcast -- but it is not accepted.
    chained = node.name_doi (name, "two")
    assert chained not in node.getrawmempool ()

    # Spell out why, so a future change of this behaviour trips the test.
    hexstr = node.gettransaction (chained)['hex']
    res = node.testmempoolaccept ([hexstr])
    assert_equal (res[0]['allowed'], False)
    assert_equal (res[0]['reject-reason'], "tx-namedoi-freename-with-input")

    self.generate (node, 1)
    self.checkName (0, name, "one", None, False)


  def test_d_namespace_is_not_reserved (self):
    """
    Characterisation: a name_doi on a d/ name is accepted today.

    The legacy 0.20 tree refused it, in CNameMemPool::checkTx:

        case OP_NAME_DOI:
          if (EncodeNameForMessage (name).rfind ("d/", 0) == 0)
            return false;

    That was mempool *policy*, not consensus -- such a transaction was never
    invalid, it was merely not accepted or relayed by that node, and a miner
    could include it in a block regardless.  The nc31 fork groups OP_NAME_DOI
    with OP_NAME_UPDATE in checkTx, so the guard is gone and the d/ namespace
    carries no special meaning anywhere in validation.

    Whether to restore the separation is a policy decision, not a security one.
    This test records today's behaviour so that changing it is visible rather
    than silent.
    """

    node = self.node
    name = "d/audit"

    txid = node.name_doi (name, "value")
    assert_equal (node.getrawmempool (), [txid])
    self.generate (node, 1)
    self.checkName (0, name, "value", None, False)

    # Not covered here: how a classic name_update interacts with a name held by
    # a NAME_DOI output.  isAnyUpdate() is true for OP_NAME_DOI, so that may
    # well be allowed; it is a separate question from the namespace one and is
    # left untested rather than asserted from reading.


if __name__ == '__main__':
  NameDoiMempoolTest (__file__).main ()
