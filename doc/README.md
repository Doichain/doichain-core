Doichain Core
==============

Setup
---------------------
[Doichain Core](http://doichain.org/) is the official Doichain client and it builds the backbone of the network. However, it downloads and stores the entire history of Doichain transactions (which is currently several GBs); depending on the speed of your computer and network connection, the synchronization process can take anywhere from a few hours to a day or more.

To download Doichain Core, visit [doichain.org](https://doichain.org/download/).

Running
---------------------
The following are some helpful notes on how to run Doichain Core on your native platform.

### Unix

Unpack the files into a directory and run:

- `bin/doichain-qt` (GUI) or
- `bin/doichaind` (headless)

### Windows

Unpack the files into a directory, and then run doichain-qt.exe.

### macOS

Drag Doichain-Qt to your applications folder, and then run Doichain-Qt.

### Need Help?

* See the documentation at the [Doichain Site](https://doichain.org)
for help and more information.
* Ask for help on [#doichain](http://webchat.freenode.net?channels=doichain) on Freenode. If you don't have an IRC client use [webchat here](http://webchat.freenode.net?channels=doichain).
* Ask for help on the [Doichain forums](https://forum.doichain.info/index.php), in the [Technical Support board](https://forum.doichain.info/viewforum.php?f=7).

Building
---------------------
The following are developer notes on how to build Bitcoin Core on your native platform. They are not complete guides, but include notes on the necessary libraries, compile flags, etc.

- [Dependencies](dependencies.md)
- [macOS Build Notes](build-osx.md)
- [Unix Build Notes](build-unix.md)
- [Windows Build Notes](build-windows.md)
- [FreeBSD Build Notes](build-freebsd.md)
- [OpenBSD Build Notes](build-openbsd.md)
- [NetBSD Build Notes](build-netbsd.md)
- [Android Build Notes](build-android.md)

Development
---------------------
The Doichain repo's [root README](https://github.com/doichain/namecore/blob/master/README.md) contains relevant information on the development process and automated testing.

- [Developer Notes](developer-notes.md)
- [Productivity Notes](productivity.md)
- [Release Notes](release-notes.md)
- [Release Process](release-process.md)
- [Source Code Documentation (External Link)](https://doxygen.bitcoincore.org/)
- [Translation Process](translation_process.md)
- [Translation Strings Policy](translation_strings_policy.md)
- [JSON-RPC Interface](JSON-RPC-interface.md)
- [Unauthenticated REST Interface](REST-interface.md)
- [Shared Libraries](shared-libraries.md)
- [BIPS](bips.md)
- [Dnsseed Policy](dnsseed-policy.md)
- [Benchmarking](benchmarking.md)

### Resources
* Discuss on the [Doichain forums](https://forum.doichain.info/), in the [Development & Technical Discussion board](https://forum.doichain.info/viewforum.php?f=8).
* Discuss on [#doichain-dev](http://webchat.freenode.net/?channels=doichain-dev) on Freenode. If you don't have an IRC client use [webchat here](http://webchat.freenode.net/?channels=doichain-dev).

### Miscellaneous
- [Assets Attribution](assets-attribution.md)
- [Assumeutxo design](assumeutxo.md)
- [bitcoin.conf Configuration File](bitcoin-conf.md)
- [Files](files.md)
- [Fuzz-testing](fuzzing.md)
- [I2P Support](i2p.md)
- [Init Scripts (systemd/upstart/openrc)](init.md)
- [Managing Wallets](managing-wallets.md)
- [PSBT support](psbt.md)
- [Reduce Memory](reduce-memory.md)
- [Reduce Traffic](reduce-traffic.md)
- [Tor Support](tor.md)
- [ZMQ](zmq.md)

License
---------------------
Distributed under the [MIT software license](/COPYING).
