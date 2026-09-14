Doichain Core integration/staging tree
=======================================

> [!IMPORTANT]
> **Security fork at block 431017 (11 September 2026).** Doichain activated a security fork to restart the stalled chain and to close a serious security hole: the old software never checked a block's difficulty (`nBits`). All node operators, pools and exchanges should run [v31.1.5](https://github.com/Doichain/doichain-core/releases/tag/v31.1.5). Check your node: `doichain-cli getblockhash 431018` must return `71d50ff1…`. Details and instructions: [#16](https://github.com/Doichain/doichain-core/issues/16).

[![Lean CI](https://github.com/Doichain/doichain-core/actions/workflows/ci-lean.yml/badge.svg?branch=master)](https://github.com/Doichain/doichain-core/actions/workflows/ci-lean.yml)

https://doichain.org

What is Doichain?
-----------------

Doichain is a decentralized name and data registration blockchain. It is a fork
of [Namecoin](https://namecoin.org) (which in turn derives from Bitcoin) and
therefore inherits Namecoin's naming system and merged mining, while adding the
`name_doi` operation for a simpler, ownership-controlled way to register names.

What does it do?
----------------

* Securely record and transfer arbitrary names (keys).
* Attach a value (data) to a name (up to 520 bytes).
* Register a name in a single step with `name_doi` (see below).
* Transact the Doichain currency (DOI).
* Merged mining and decentralized DNS, inherited from Namecoin.

Differences from Namecoin (upstream)
------------------------------------

Doichain is built on top of Namecoin Core, so most of the code, RPCs and tooling
are identical. The Doichain-specific parts are:

* **Own network.** Doichain is a separate chain with its own genesis block,
  network magic, default ports (P2P 8338 / RPC 8339), address prefixes,
  `dc…` bech32 addresses and AuxPoW chain id `0x0002`.
* **`name_doi` — one-step, UTXO-owned names.** In addition to Namecoin's classic
  two-step registration (`name_new` → wait → `name_firstupdate`, a commit/reveal
  that protects against front-running), Doichain adds the `OP_NAME_DOI` operation:
  a name is **registered in a single transaction**, and afterwards it can only be
  changed by whoever controls it — proven by spending the name's previous
  `name_doi` output (the same UTXO-ownership principle Namecoin uses for
  `name_update`). Classic `d/` Namecoin names keep working unchanged alongside it.

### A simple example

Register a name and a value in one step, read it back, then update it as the
owner (this spends the previous output, so only the owner can do it):

```sh
# one-step registration
doichain-cli name_doi "id/alice" "hello from doichain"

# ...mine/confirm a block, then read it back
doichain-cli name_show "id/alice"

# owner update (fails for anyone who does not control the name)
doichain-cli name_doi "id/alice" "updated value"
```

License
-------

Doichain Core is released under the terms of the MIT license. See [COPYING](COPYING)
for more information or see https://opensource.org/license/MIT.

Development Process
-------------------

The `master` branch is regularly built (see doc/build-*.md for instructions) and
tested, but is not guaranteed to be completely stable.
[Tags](https://github.com/Doichain/doichain-core/tags) are created regularly to
indicate new official, stable release versions of Doichain Core.

Doichain Core tracks [Namecoin Core](https://github.com/namecoin/namecoin-core)
and [Bitcoin Core](https://github.com/bitcoin/bitcoin) upstream; changes that are
not Doichain-specific should ideally be contributed there. The contribution
workflow is described in [CONTRIBUTING.md](CONTRIBUTING.md) and useful hints for
developers can be found in [doc/developer-notes.md](doc/developer-notes.md).

This repository is based on the Namecoin Core release tagged **`nc31.1`**
(Bitcoin Core 31), and the full upstream history is preserved. The complete set
of Doichain-specific changes on top of that base can therefore be reviewed with:

```sh
git fetch --tags
git diff nc31.1..master
```

or on GitHub via
[`nc31.1...master`](https://github.com/Doichain/doichain-core/compare/nc31.1...master).

Please discuss complicated or controversial changes as a GitHub issue before
working on a patch set.

Testing
-------

Testing and code review is the bottleneck for development; please help out by
testing other people's pull requests, and remember this is a security-critical
project where any mistake might cost people lots of money.

### Automated Testing

Developers are strongly encouraged to write [unit tests](src/test/README.md) for
new code, and to submit new unit tests for old code. Unit tests can be compiled
and run (assuming they weren't disabled during the generation of the build system)
with: `ctest`. Further details on running and extending unit tests can be found in
[/src/test/README.md](/src/test/README.md).

There are also [regression and integration tests](/test), written in Python.
These tests can be run (if the [test dependencies](/test) are installed) with:
`build/test/functional/test_runner.py` (assuming `build` is your build directory).

Continuous Integration runs via **GitHub Actions**
([.github/workflows/ci.yml](.github/workflows/ci.yml)) on every push and pull
request, building and testing on Linux, macOS and Windows and running the linters.

### Manual Quality Assurance (QA) Testing

Changes should be tested by somebody other than the developer who wrote the code.
This is especially important for large or high-risk changes. It is useful to add a
test plan to the pull request description if testing the changes is not
straightforward.

Translations
------------

**Translation workflow is not yet set up for Doichain Core. For strings which are
common to Bitcoin Core, see below.**

Changes to translations as well as new translations can be submitted to
[Bitcoin Core's Transifex page](https://explore.transifex.com/bitcoin/bitcoin/).

Translations are periodically pulled from Transifex and merged into the git
repository. See the [translation process](doc/translation_process.md) for details
on how this works.

**Important**: We do not accept translation changes as GitHub pull requests because
the next pull from Transifex would automatically overwrite them again.
