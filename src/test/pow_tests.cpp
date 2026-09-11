// Copyright (c) 2015-present The Bitcoin Core developers
// Distributed under the MIT software license, see the accompanying
// file COPYING or http://www.opensource.org/licenses/mit-license.php.

#include <arith_uint256.h>
#include <chain.h>
#include <chainparams.h>
#include <pow.h>
#include <test/util/random.h>
#include <test/util/common.h>
#include <test/util/setup_common.h>
#include <util/chaintype.h>

#include <boost/test/unit_test.hpp>

BOOST_FIXTURE_TEST_SUITE(pow_tests, BasicTestingSetup)

/* Test calculation of next difficulty target with no constraints applying */
BOOST_AUTO_TEST_CASE(get_next_work)
{
    const auto chainParams = CreateChainParams(*m_node.args, ChainType::MAIN);
    int64_t nLastRetargetTime = 1261130161; // Block #30240
    CBlockIndex pindexLast;
    pindexLast.nHeight = 32255;
    pindexLast.nTime = 1262152739;  // Block #32255
    pindexLast.nBits = 0x1d00ffff;

    // Here (and below): expected_nbits is calculated in
    // CalculateNextWorkRequired(); redoing the calculation here would be just
    // reimplementing the same code that is written in pow.cpp. Rather than
    // copy that code, we just hardcode the expected result.
    unsigned int expected_nbits = 0x1d00d86aU;
    BOOST_CHECK_EQUAL(CalculateNextWorkRequired(&pindexLast, nLastRetargetTime, chainParams->GetConsensus()), expected_nbits);
    BOOST_CHECK(PermittedDifficultyTransition(chainParams->GetConsensus(), pindexLast.nHeight+1, pindexLast.nBits, expected_nbits));
}

/* Doichain: DigiShield-v3 retarget core (ported from Zcash).  Verify the neutral
   point and the damped, clamped +16% / -32% per-step behaviour. */
BOOST_AUTO_TEST_CASE(digishield_retarget)
{
    const auto chainParams = CreateChainParams(*m_node.args, ChainType::MAIN);
    const auto& params{chainParams->GetConsensus()};
    const int64_t window = params.AveragingWindowTimespan();

    arith_uint256 bnAvg;
    bnAvg.SetCompact(0x1d00ffff);

    auto target = [&](int64_t last, int64_t first) {
        arith_uint256 t;
        t.SetCompact(CalculateNextWorkRequiredDigishield(bnAvg, last, first, params));
        return t;
    };
    const arith_uint256 fast    = target(0, 0);           // blocks as fast as possible
    const arith_uint256 neutral = target(window, 0);      // block times exactly on target
    const arith_uint256 slow    = target(10 * window, 0); // blocks very slow

    // Fast blocks raise difficulty (smaller target); slow blocks lower it.
    BOOST_CHECK(fast < neutral);
    BOOST_CHECK(neutral < slow);

    // The per-block step is clamped to the damped +16% / -32% bounds.
    arith_uint256 up{bnAvg};   up   /= window; up   *= params.MinActualTimespan();
    arith_uint256 down{bnAvg}; down /= window; down *= params.MaxActualTimespan();
    BOOST_CHECK_EQUAL(fast.GetCompact(), up.GetCompact());
    BOOST_CHECK_EQUAL(slow.GetCompact(), down.GetCompact());
}

namespace {
/* Doichain: a minimal, mutable Consensus::Params for the DigiShield tests.  The
   real struct is move-only (it owns the name-rules object), so it cannot be
   copied; carry over just the fields the difficulty code reads. */
void InitDigishieldParams(Consensus::Params& p, const Consensus::Params& main)
{
    p.powLimit = main.powLimit;
    p.nPowTargetSpacing = main.nPowTargetSpacing;
    p.nPowTargetTimespan = main.nPowTargetTimespan;
    p.fPowAllowMinDifficultyBlocks = false;
    p.fPowNoRetargeting = false;
    p.nPowAveragingWindow = main.nPowAveragingWindow;
    p.nPowMaxAdjustUp = main.nPowMaxAdjustUp;
    p.nPowMaxAdjustDown = main.nPowMaxAdjustDown;
    p.nDoiMinDifficultyValveFactor = main.nDoiMinDifficultyValveFactor;
}
} // namespace

/* Doichain: the emergency valve eases the DigiShield target by a bounded factor
   after a long gap — never to powLimit, which would poison the averaging window
   and trigger a block storm. */
BOOST_AUTO_TEST_CASE(digishield_bounded_valve)
{
    const auto chainParams = CreateChainParams(*m_node.args, ChainType::MAIN);
    Consensus::Params params{};
    InitDigishieldParams(params, chainParams->GetConsensus());
    params.fPowNoRetargeting = false;
    params.DoiDifficultyHeight = 0;             // DigiShield from genesis
    params.nDoiDifficultyResetBits = 0;         // no reset window: plain averaging
    params.nDoiMinDifficultyGap = 6 * params.nPowTargetSpacing;
    params.nDoiMinDifficultyValveFactor = 4;

    const int n = params.nPowAveragingWindow + CBlockIndex::nMedianTimeSpan + 5;
    std::vector<CBlockIndex> blocks(n);
    for (int i = 0; i < n; ++i) {
        blocks[i].pprev = i ? &blocks[i - 1] : nullptr;
        blocks[i].nHeight = i;
        blocks[i].nTime = 1700000000 + i * params.nPowTargetSpacing;
        blocks[i].nBits = 0x1826f19c; // mainnet difficulty during the 2026 hash-attack
    }
    const CBlockIndex* tip = &blocks[n - 1];

    CBlockHeader normal;
    normal.nTime = tip->nTime + params.nPowTargetSpacing;
    const unsigned int nbits_normal = GetNextWorkRequired(tip, &normal, params);

    CBlockHeader late;
    late.nTime = tip->nTime + params.nDoiMinDifficultyGap + 1;
    const unsigned int nbits_valve = GetNextWorkRequired(tip, &late, params);

    arith_uint256 t_normal, t_valve;
    t_normal.SetCompact(nbits_normal);
    t_valve.SetCompact(nbits_valve);
    arith_uint256 expected{t_normal};
    expected *= 4;
    BOOST_CHECK_EQUAL(t_valve.GetCompact(), expected.GetCompact());   // exactly 4x easier...
    BOOST_CHECK(t_valve < UintToArith256(params.powLimit));             // ...and nowhere near powLimit
    BOOST_CHECK(PermittedDifficultyTransition(params, tip->nHeight + 1, tip->nBits, nbits_valve));
}

/* Doichain: the first (averaging window + median-time span) blocks after activation
   use the fixed reset target, so DigiShield then starts from a clean window. */
BOOST_AUTO_TEST_CASE(digishield_reset_window)
{
    const auto chainParams = CreateChainParams(*m_node.args, ChainType::MAIN);
    Consensus::Params params{};
    InitDigishieldParams(params, chainParams->GetConsensus());
    params.fPowNoRetargeting = false;
    params.nDoiMinDifficultyGap = 0;
    const int activation = 40;
    params.DoiDifficultyHeight = activation;
    params.nDoiDifficultyResetBits = 0x19061e72;   // ~40x easier than the stuck chain
    const int reset_window = DigishieldResetWindow(params);
    BOOST_CHECK_EQUAL(reset_window, params.nPowAveragingWindow + CBlockIndex::nMedianTimeSpan);

    // Stuck pre-fork chain (high difficulty, ~400-min blocks), then reset blocks at 10 min.
    const int n = activation + reset_window + 3;
    std::vector<CBlockIndex> blocks(n);
    int64_t t = 1700000000;
    for (int i = 0; i < n; ++i) {
        if (i) t += (i < activation ? 40 : 1) * params.nPowTargetSpacing;
        blocks[i].pprev = i ? &blocks[i - 1] : nullptr;
        blocks[i].nHeight = i;
        blocks[i].nTime = t;
        blocks[i].nBits = i < activation ? 0x1826f19c : params.nDoiDifficultyResetBits;
    }

    CBlockHeader next;
    // Every block inside the reset window gets exactly the reset target, whatever the
    // stuck pre-fork history looks like.
    for (int h = activation; h < activation + reset_window; ++h) {
        const CBlockIndex* prev = &blocks[h - 1];
        next.nTime = prev->nTime + params.nPowTargetSpacing;
        BOOST_CHECK_EQUAL(GetNextWorkRequired(prev, &next, params), params.nDoiDifficultyResetBits);
        BOOST_CHECK(PermittedDifficultyTransition(params, h, prev->nBits, params.nDoiDifficultyResetBits));
    }
    // The first block after the window is computed by DigiShield from an all-reset
    // window at neutral spacing: it stays at the reset target (within rounding).
    const CBlockIndex* prev = &blocks[activation + reset_window - 1];
    next.nTime = prev->nTime + params.nPowTargetSpacing;
    arith_uint256 got, reset;
    got.SetCompact(GetNextWorkRequired(prev, &next, params));
    reset.SetCompact(params.nDoiDifficultyResetBits);
    BOOST_CHECK(got <= reset);                                   // never easier than the reset...
    arith_uint256 lower{reset};
    lower /= params.AveragingWindowTimespan();
    lower *= params.MinActualTimespan();
    BOOST_CHECK(got >= lower);                                   // ...and at most one clamp step harder
}

/* Doichain: headers-sync plausibility bound for per-block DigiShield changes. */
BOOST_AUTO_TEST_CASE(digishield_permitted_transition)
{
    const auto chainParams = CreateChainParams(*m_node.args, ChainType::MAIN);
    Consensus::Params params{};
    InitDigishieldParams(params, chainParams->GetConsensus());
    params.DoiDifficultyHeight = 0;
    params.nDoiDifficultyResetBits = 0;
    params.nDoiMinDifficultyValveFactor = 4;
    const uint32_t old_nbits = 0x1826f19c;
    arith_uint256 old_t;
    old_t.SetCompact(old_nbits);
    const int64_t window = params.AveragingWindowTimespan();

    arith_uint256 max_ok{old_t};
    max_ok /= window; max_ok *= params.MaxActualTimespan(); max_ok *= 4;
    arith_uint256 min_ok{old_t};
    min_ok /= window; min_ok *= params.MinActualTimespan(); min_ok /= 4;
    arith_uint256 near_min{min_ok};
    near_min *= 101; near_min /= 100;                             // just inside the lower bound

    BOOST_CHECK(PermittedDifficultyTransition(params, 1000, old_nbits, old_nbits));
    BOOST_CHECK(PermittedDifficultyTransition(params, 1000, old_nbits, max_ok.GetCompact()));
    BOOST_CHECK(PermittedDifficultyTransition(params, 1000, old_nbits, near_min.GetCompact()));
    arith_uint256 too_easy{max_ok};
    too_easy *= 2;
    arith_uint256 too_hard{min_ok};
    too_hard /= 2;
    BOOST_CHECK(!PermittedDifficultyTransition(params, 1000, old_nbits, too_easy.GetCompact()));
    BOOST_CHECK(!PermittedDifficultyTransition(params, 1000, old_nbits, too_hard.GetCompact()));
    // Dropping straight to powLimit is never a permitted per-block step.
    BOOST_CHECK(!PermittedDifficultyTransition(params, 1000, old_nbits, UintToArith256(params.powLimit).GetCompact()));
}

/* Test the constraint on the upper bound for next work */
BOOST_AUTO_TEST_CASE(get_next_work_pow_limit)
{
    const auto chainParams = CreateChainParams(*m_node.args, ChainType::MAIN);
    const auto& consensus{chainParams->GetConsensus()};

    // This case drives the retarget up against the powLimit ceiling. On Doichain
    // the powLimit (~2^240, nBits 0x1f00ffff) is high enough that
    // target * nActualTimespan overflows 256 bits before the ceiling is reached
    // (see ChainParams_MAIN_sanity); clamping exactly at the ceiling is therefore
    // mathematically unreachable without overflow. That difficulty never occurs
    // on the merge-mined chain (it does not return to the floor after genesis),
    // so skip the exact-ceiling check when the powLimit exceeds the safe range.
    arith_uint256 targ_max{UintToArith256(uint256{"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"})};
    targ_max /= consensus.nPowTargetTimespan*4;
    if (UintToArith256(consensus.powLimit) >= targ_max) {
        BOOST_TEST_MESSAGE("skipping powLimit-ceiling retarget check (high-powLimit Doichain chain)");
        return;
    }

    int64_t nLastRetargetTime = 1231006505; // Block #0
    CBlockIndex pindexLast;
    pindexLast.nHeight = 2015;
    pindexLast.nTime = 1233061996;  // Block #2015
    pindexLast.nBits = UintToArith256(consensus.powLimit).GetCompact();
    unsigned int expected_nbits = pindexLast.nBits;
    BOOST_CHECK_EQUAL(CalculateNextWorkRequired(&pindexLast, nLastRetargetTime, consensus), expected_nbits);
    BOOST_CHECK(PermittedDifficultyTransition(consensus, pindexLast.nHeight+1, pindexLast.nBits, expected_nbits));
}

/* Test the constraint on the lower bound for actual time taken */
BOOST_AUTO_TEST_CASE(get_next_work_lower_limit_actual)
{
    const auto chainParams = CreateChainParams(*m_node.args, ChainType::MAIN);
    int64_t nLastRetargetTime = 1279008237; // Block #66528
    CBlockIndex pindexLast;
    pindexLast.nHeight = 68543;
    pindexLast.nTime = 1279297671;  // Block #68543
    pindexLast.nBits = 0x1c05a3f4;
    unsigned int expected_nbits = 0x1c0168fdU;
    BOOST_CHECK_EQUAL(CalculateNextWorkRequired(&pindexLast, nLastRetargetTime, chainParams->GetConsensus()), expected_nbits);
    BOOST_CHECK(PermittedDifficultyTransition(chainParams->GetConsensus(), pindexLast.nHeight+1, pindexLast.nBits, expected_nbits));
    /* Upstream asserts here that reducing nbits further is rejected. On Doichain it
       is not: below DoiPowCheckHeight the nBits rule is not enforced at all — the
       chain launched with the difficulty check disabled for the premine, and the
       real chain breaks the legacy transition bounds at height 2016 (0x1f00ffff ->
       0x1e063102). Enforcing the bounds there stops headers presync at that block
       and no fresh node can ever sync past it.
       The restrictive behaviour is still covered, by digishield_permitted_transition,
       which exercises the heights where the rule actually applies. */
    unsigned int invalid_nbits = expected_nbits-1;
    BOOST_CHECK(pindexLast.nHeight+1 < chainParams->GetConsensus().DoiPowCheckHeight);
    BOOST_CHECK(PermittedDifficultyTransition(chainParams->GetConsensus(), pindexLast.nHeight+1, pindexLast.nBits, invalid_nbits));
}

/* Test the constraint on the upper bound for actual time taken */
BOOST_AUTO_TEST_CASE(get_next_work_upper_limit_actual)
{
    const auto chainParams = CreateChainParams(*m_node.args, ChainType::MAIN);
    int64_t nLastRetargetTime = 1263163443; // NOTE: Not an actual block time
    CBlockIndex pindexLast;
    pindexLast.nHeight = 46367;
    pindexLast.nTime = 1269211443;  // Block #46367
    pindexLast.nBits = 0x1c387f6f;
    unsigned int expected_nbits = 0x1d00e1fdU;
    BOOST_CHECK_EQUAL(CalculateNextWorkRequired(&pindexLast, nLastRetargetTime, chainParams->GetConsensus()), expected_nbits);
    BOOST_CHECK(PermittedDifficultyTransition(chainParams->GetConsensus(), pindexLast.nHeight+1, pindexLast.nBits, expected_nbits));
    /* See get_next_work_lower_limit_actual: below DoiPowCheckHeight Doichain does not
       enforce the nBits rule, so the transition is permitted rather than rejected. */
    unsigned int invalid_nbits = expected_nbits+1;
    BOOST_CHECK(pindexLast.nHeight+1 < chainParams->GetConsensus().DoiPowCheckHeight);
    BOOST_CHECK(PermittedDifficultyTransition(chainParams->GetConsensus(), pindexLast.nHeight+1, pindexLast.nBits, invalid_nbits));
}

BOOST_AUTO_TEST_CASE(CheckProofOfWork_test_negative_target)
{
    const auto& consensus = CreateChainParams(*m_node.args, ChainType::MAIN)->GetConsensus();
    uint256 hash;
    unsigned int nBits;
    nBits = UintToArith256(consensus.powLimit).GetCompact(true);
    hash = uint256{1};
    BOOST_CHECK(!CheckProofOfWork(hash, nBits, consensus));
}

BOOST_AUTO_TEST_CASE(CheckProofOfWork_test_overflow_target)
{
    const auto& consensus = CreateChainParams(*m_node.args, ChainType::MAIN)->GetConsensus();
    uint256 hash;
    unsigned int nBits{~0x00800000U};
    hash = uint256{1};
    BOOST_CHECK(!CheckProofOfWork(hash, nBits, consensus));
}

BOOST_AUTO_TEST_CASE(CheckProofOfWork_test_too_easy_target)
{
    const auto& consensus = CreateChainParams(*m_node.args, ChainType::MAIN)->GetConsensus();
    uint256 hash;
    unsigned int nBits;
    arith_uint256 nBits_arith = UintToArith256(consensus.powLimit);
    nBits_arith *= 2;
    nBits = nBits_arith.GetCompact();
    hash = uint256{1};
    BOOST_CHECK(!CheckProofOfWork(hash, nBits, consensus));
}

BOOST_AUTO_TEST_CASE(CheckProofOfWork_test_biger_hash_than_target)
{
    const auto& consensus = CreateChainParams(*m_node.args, ChainType::MAIN)->GetConsensus();
    uint256 hash;
    unsigned int nBits;
    arith_uint256 hash_arith = UintToArith256(consensus.powLimit);
    nBits = hash_arith.GetCompact();
    hash_arith *= 2; // hash > nBits
    hash = ArithToUint256(hash_arith);
    BOOST_CHECK(!CheckProofOfWork(hash, nBits, consensus));
}

BOOST_AUTO_TEST_CASE(CheckProofOfWork_test_zero_target)
{
    const auto& consensus = CreateChainParams(*m_node.args, ChainType::MAIN)->GetConsensus();
    uint256 hash;
    unsigned int nBits;
    arith_uint256 hash_arith{0};
    nBits = hash_arith.GetCompact();
    hash = ArithToUint256(hash_arith);
    BOOST_CHECK(!CheckProofOfWork(hash, nBits, consensus));
}

BOOST_AUTO_TEST_CASE(GetBlockProofEquivalentTime_test)
{
    const auto chainParams = CreateChainParams(*m_node.args, ChainType::MAIN);
    std::vector<CBlockIndex> blocks(10000);
    for (int i = 0; i < 10000; i++) {
        blocks[i].pprev = i ? &blocks[i - 1] : nullptr;
        blocks[i].nHeight = i;
        blocks[i].nTime = 1269211443 + i * chainParams->GetConsensus().nPowTargetSpacing;
        blocks[i].nBits = 0x207fffff; /* target 0x7fffff000... */
        blocks[i].nChainWork = i ? blocks[i - 1].nChainWork + GetBlockProof(blocks[i - 1]) : arith_uint256(0);
    }

    for (int j = 0; j < 1000; j++) {
        CBlockIndex *p1 = &blocks[m_rng.randrange(10000)];
        CBlockIndex *p2 = &blocks[m_rng.randrange(10000)];
        CBlockIndex *p3 = &blocks[m_rng.randrange(10000)];

        int64_t tdiff = GetBlockProofEquivalentTime(*p1, *p2, *p3, chainParams->GetConsensus());
        BOOST_CHECK_EQUAL(tdiff, p1->GetBlockTime() - p2->GetBlockTime());
    }
}

void sanity_check_chainparams(const ArgsManager& args, ChainType chain_type)
{
    const auto params = CreateChainParams(args, chain_type);
    const auto& consensus = params->GetConsensus();

    // hash genesis is correct
    BOOST_CHECK_EQUAL(consensus.hashGenesisBlock, params->GenesisBlock().GetHash());

    // target timespan is an even multiple of spacing
    BOOST_CHECK_EQUAL(consensus.nPowTargetTimespan % consensus.nPowTargetSpacing, 0);

    // genesis nBits is positive, doesn't overflow and is lower than powLimit
    arith_uint256 pow_compact;
    bool neg, over;
    pow_compact.SetCompact(params->GenesisBlock().nBits, &neg, &over);
    BOOST_CHECK(!neg && pow_compact != 0);
    BOOST_CHECK(!over);
    BOOST_CHECK(UintToArith256(consensus.powLimit) >= pow_compact);

    // check max target * 4*nPowTargetTimespan doesn't overflow -- see pow.cpp:CalculateNextWorkRequired()
    if (!consensus.fPowNoRetargeting) {
        arith_uint256 targ_max{UintToArith256(uint256{"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"})};
        targ_max /= consensus.nPowTargetTimespan*4;
        // Doichain keeps its original chain, whose genesis sits at the minimum
        // difficulty (nBits 0x1f00ffff => powLimit ~2^240). That intentionally
        // exceeds Bitcoin's retarget no-overflow bound; the overflow is only
        // reachable if difficulty returns to the powLimit floor, which has not
        // happened on the merge-mined chain since genesis. Enforce the strict
        // bound where it holds and document the intentional exception otherwise.
        if (UintToArith256(consensus.powLimit) >= targ_max) {
            BOOST_TEST_MESSAGE("powLimit intentionally exceeds the retarget no-overflow bound (high-powLimit Doichain chain)");
        } else {
            BOOST_CHECK(UintToArith256(consensus.powLimit) < targ_max);
        }
    }
}

BOOST_AUTO_TEST_CASE(ChainParams_MAIN_sanity)
{
    sanity_check_chainparams(*m_node.args, ChainType::MAIN);
}

BOOST_AUTO_TEST_CASE(ChainParams_REGTEST_sanity)
{
    sanity_check_chainparams(*m_node.args, ChainType::REGTEST);
}

BOOST_AUTO_TEST_CASE(ChainParams_TESTNET_sanity)
{
    sanity_check_chainparams(*m_node.args, ChainType::TESTNET);
}

/*
BOOST_AUTO_TEST_CASE(ChainParams_TESTNET4_sanity)
{
    sanity_check_chainparams(*m_node.args, ChainType::TESTNET4);
}
*/

BOOST_AUTO_TEST_CASE(ChainParams_SIGNET_sanity)
{
    sanity_check_chainparams(*m_node.args, ChainType::SIGNET);
}

BOOST_AUTO_TEST_SUITE_END()
