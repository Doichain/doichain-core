// Copyright (c) 2009-2010 Satoshi Nakamoto
// Copyright (c) 2009-present The Bitcoin Core developers
// Distributed under the MIT software license, see the accompanying
// file COPYING or http://www.opensource.org/licenses/mit-license.php.

#include <pow.h>

#include <arith_uint256.h>
#include <chain.h>
#include <primitives/block.h>
#include <uint256.h>
#include <util/check.h>

#include <limits>

/* DigiShield-v3 difficulty adjustment, ported from Zcash's src/pow.cpp
 * (GetNextWorkRequired / CalculateNextWorkRequired):
 *   https://github.com/zcash/zcash  @ 558f686599586f55def3db86955d74d3be44605e
 * Adapted to Doichain: uses Doichain's target spacing and is activated only from
 * Consensus::Params::DoiDifficultyHeight onward.  Below that height Doichain keeps
 * the original 2016-block retarget so the pre-existing chain stays valid. */
unsigned int CalculateNextWorkRequiredDigishield(arith_uint256 bnAvg,
        int64_t nLastBlockTime, int64_t nFirstBlockTime, const Consensus::Params& params)
{
    const int64_t averagingWindowTimespan = params.AveragingWindowTimespan();
    const int64_t minActualTimespan = params.MinActualTimespan();
    const int64_t maxActualTimespan = params.MaxActualTimespan();

    // Median times prevent time-warp; damp the deviation to 1/4 of the window.
    int64_t nActualTimespan = nLastBlockTime - nFirstBlockTime;
    nActualTimespan = averagingWindowTimespan + (nActualTimespan - averagingWindowTimespan) / 4;
    if (nActualTimespan < minActualTimespan) nActualTimespan = minActualTimespan;
    if (nActualTimespan > maxActualTimespan) nActualTimespan = maxActualTimespan;

    const arith_uint256 bnPowLimit = UintToArith256(params.powLimit);
    arith_uint256 bnNew{bnAvg};
    bnNew /= averagingWindowTimespan;
    bnNew *= nActualTimespan;
    if (bnNew > bnPowLimit) bnNew = bnPowLimit;
    return bnNew.GetCompact();
}

/* Doichain: length of the one-time reset window at DoiDifficultyHeight.  It covers
 * DigiShield's full lookback (averaging window + median-time span), so once it ends
 * every target and timestamp the algorithm looks at is post-fork. */
int DigishieldResetWindow(const Consensus::Params& params)
{
    return params.nPowAveragingWindow + CBlockIndex::nMedianTimeSpan;
}

namespace {

unsigned int GetNextWorkRequiredDigishield(const CBlockIndex* pindexLast,
        const CBlockHeader* pblock, const Consensus::Params& params)
{
    const arith_uint256 bnPowLimit = UintToArith256(params.powLimit);
    const unsigned int nProofOfWorkLimit = bnPowLimit.GetCompact();

    if (params.fPowNoRetargeting)
        return pindexLast->nBits;

    const int nHeight = pindexLast->nHeight + 1;
    arith_uint256 bnNew;

    if (params.nDoiDifficultyResetBits != 0
        && nHeight < params.DoiDifficultyHeight + DigishieldResetWindow(params)) {
        // Reset window: a fixed target for the first blocks after activation, so
        // DigiShield later averages a clean post-fork window.  Averaging the stuck
        // pre-fork targets/timestamps instead takes days to work off, and a
        // single-block reset is pulled straight back up by the window.
        bnNew.SetCompact(params.nDoiDifficultyResetBits);
    } else {
        // Average the target over the last nPowAveragingWindow blocks.
        const CBlockIndex* pindexFirst = pindexLast;
        arith_uint256 bnTot{0};
        for (int i = 0; pindexFirst != nullptr && i < params.nPowAveragingWindow; ++i) {
            arith_uint256 bnTmp;
            bnTmp.SetCompact(pindexFirst->nBits);
            bnTot += bnTmp;
            pindexFirst = pindexFirst->pprev;
        }
        if (pindexFirst == nullptr)
            return nProofOfWorkLimit; // not enough blocks in the window yet

        const arith_uint256 bnAvg{bnTot / params.nPowAveragingWindow};
        bnNew.SetCompact(CalculateNextWorkRequiredDigishield(bnAvg,
            pindexLast->GetMedianTimePast(), pindexFirst->GetMedianTimePast(), params));
    }

    // Emergency valve, bounded: after nDoiMinDifficultyGap seconds without a block,
    // the next block may be at most nDoiMinDifficultyValveFactor times easier than
    // the computed target.  Never powLimit: on Doichain that is ~2^50 easier than
    // the working target, so one min-difficulty block would dominate the averaging
    // window and collapse difficulty into a storm of ~1500 near-instant blocks.
    if (params.nDoiMinDifficultyGap > 0 && params.nDoiMinDifficultyValveFactor > 1
        && pblock != nullptr
        && pblock->GetBlockTime() > pindexLast->GetBlockTime() + params.nDoiMinDifficultyGap)
        bnNew *= static_cast<uint32_t>(params.nDoiMinDifficultyValveFactor);

    if (bnNew > bnPowLimit) bnNew = bnPowLimit;
    return bnNew.GetCompact();
}

} // anonymous namespace

unsigned int GetNextWorkRequired(const CBlockIndex* pindexLast, const CBlockHeader *pblock, const Consensus::Params& params)
{
    assert(pindexLast != nullptr);
    unsigned int nProofOfWorkLimit = UintToArith256(params.powLimit).GetCompact();

    // Doichain: from DoiDifficultyHeight on, use the DigiShield-v3 per-block
    // adjustment (ported from Zcash) instead of the legacy 2016-block retarget.
    if (pindexLast->nHeight + 1 >= params.DoiDifficultyHeight)
        return GetNextWorkRequiredDigishield(pindexLast, pblock, params);

    // Only change once per difficulty adjustment interval
    if ((pindexLast->nHeight+1) % params.DifficultyAdjustmentInterval() != 0)
    {
        if (params.AllowMinDifficultyBlocks(pblock->GetBlockTime()))
        {
            /* khal's port of this code from Bitcoin to the old doichaind
               has a bug:  Comparison of block times is done by an unsigned
               difference.  Consequently, the minimum difficulty is also
               applied if the block's timestamp is earlier than the preceding
               block's.  Reproduce this.  */
            if (pblock->GetBlockTime() < pindexLast->GetBlockTime())
                return nProofOfWorkLimit;

            // Special difficulty rule for testnet:
            // If the new block's timestamp is more than 2* 10 minutes
            // then it MUST be a min-difficulty block.
            if (pblock->GetBlockTime() > pindexLast->GetBlockTime() + params.nPowTargetSpacing*2)
                return nProofOfWorkLimit;
            else
            {
                // Return the last non-special-min-difficulty-rules-block
                const CBlockIndex* pindex = pindexLast;
                while (pindex->pprev && pindex->nHeight % params.DifficultyAdjustmentInterval() != 0 && pindex->nBits == nProofOfWorkLimit)
                    pindex = pindex->pprev;
                return pindex->nBits;
            }
        }
        return pindexLast->nBits;
    }

    /* Adapt the retargeting interval after merge-mining start
       according to the changed Namecoin rules.  */
    int nBlocksBack = params.DifficultyAdjustmentInterval() - 1;
    if (pindexLast->nHeight >= params.nAuxpowStartHeight
        && (pindexLast->nHeight + 1 > params.DifficultyAdjustmentInterval()))
        nBlocksBack = params.DifficultyAdjustmentInterval();

    // Go back by what we want to be 14 days worth of blocks
    int nHeightFirst = pindexLast->nHeight - nBlocksBack;
    assert(nHeightFirst >= 0);
    const CBlockIndex* pindexFirst = pindexLast->GetAncestor(nHeightFirst);
    assert(pindexFirst);

    return CalculateNextWorkRequired(pindexLast, pindexFirst->GetBlockTime(), params);
}

unsigned int CalculateNextWorkRequired(const CBlockIndex* pindexLast, int64_t nFirstBlockTime, const Consensus::Params& params)
{
    if (params.fPowNoRetargeting)
        return pindexLast->nBits;

    // Limit adjustment step
    int64_t nActualTimespan = pindexLast->GetBlockTime() - nFirstBlockTime;
    if (nActualTimespan < params.nPowTargetTimespan/4)
        nActualTimespan = params.nPowTargetTimespan/4;
    if (nActualTimespan > params.nPowTargetTimespan*4)
        nActualTimespan = params.nPowTargetTimespan*4;

    // Retarget
    const arith_uint256 bnPowLimit = UintToArith256(params.powLimit);
    arith_uint256 bnNew;

    // Special difficulty rule for Testnet4
    if (params.enforce_BIP94) {
        // Here we use the first block of the difficulty period. This way
        // the real difficulty is always preserved in the first block as
        // it is not allowed to use the min-difficulty exception.
        int nHeightFirst = pindexLast->nHeight - (params.DifficultyAdjustmentInterval()-1);
        const CBlockIndex* pindexFirst = pindexLast->GetAncestor(nHeightFirst);
        bnNew.SetCompact(pindexFirst->nBits);
    } else {
        bnNew.SetCompact(pindexLast->nBits);
    }

    bnNew *= nActualTimespan;
    bnNew /= params.nPowTargetTimespan;

    if (bnNew > bnPowLimit)
        bnNew = bnPowLimit;

    return bnNew.GetCompact();
}

// Check that on difficulty adjustments, the new difficulty does not increase
// or decrease beyond the permitted limits.
bool PermittedDifficultyTransition(const Consensus::Params& params, int64_t height, uint32_t old_nbits, uint32_t new_nbits)
{
    if (params.fPowAllowMinDifficultyBlocks) return true;

    // Doichain: the nBits rule was not enforced historically -- the chain was
    // launched with the difficulty check disabled for the premine, so blocks
    // arrived in seconds and e.g. height 2016 moves the target by ~41x, far
    // outside the legacy 4x clamp that the generic branch below assumes.
    // ContextualCheckBlockHeader therefore only enforces nBits from
    // DoiPowCheckHeight onward (see "Doichain historically did not enforce this
    // rule" there), and this anti-DoS heuristic has to mirror that: otherwise
    // headers presync rejects the real chain at height 2016 with "invalid
    // difficulty transition" and a fresh node can never sync past it.
    //
    // Work accounting is unaffected -- CheckProofOfWork still verifies every
    // header's hash against its own claimed target at every height, so a peer
    // cannot fabricate work here.
    if (height < params.DoiPowCheckHeight) return true;

    // Doichain: from DoiDifficultyHeight on the target moves every block.  Bound the
    // per-block step by DigiShield's clamp, widened by the emergency-valve factor
    // (timestamps are not available here, so a valve block cannot be told apart).
    // Inside the reset window the target may jump to the reset target from anywhere.
    // Exact nBits are enforced separately in ContextualCheckBlockHeader.
    if (height >= params.DoiDifficultyHeight) {
        const arith_uint256 pow_limit = UintToArith256(params.powLimit);
        arith_uint256 observed_new_target;
        observed_new_target.SetCompact(new_nbits);
        if (observed_new_target == 0 || observed_new_target > pow_limit) return false;

        const int64_t valve = params.nDoiMinDifficultyValveFactor > 1 ? params.nDoiMinDifficultyValveFactor : 1;
        if (params.nDoiDifficultyResetBits != 0
            && height < params.DoiDifficultyHeight + DigishieldResetWindow(params)) {
            arith_uint256 reset_target;
            reset_target.SetCompact(params.nDoiDifficultyResetBits);
            arith_uint256 max_target{reset_target};
            max_target *= valve;
            if (max_target > pow_limit) max_target = pow_limit;
            return observed_new_target >= reset_target && observed_new_target <= max_target;
        }

        arith_uint256 old_target;
        old_target.SetCompact(old_nbits);
        const int64_t window = params.AveragingWindowTimespan();
        arith_uint256 max_target{old_target};
        max_target /= window; max_target *= params.MaxActualTimespan(); max_target *= valve;
        if (max_target > pow_limit) max_target = pow_limit;
        arith_uint256 min_target{old_target};
        min_target /= window; min_target *= params.MinActualTimespan(); min_target /= valve;
        return observed_new_target >= min_target && observed_new_target <= max_target;
    }

    if (height % params.DifficultyAdjustmentInterval() == 0) {
        int64_t smallest_timespan = params.nPowTargetTimespan/4;
        int64_t largest_timespan = params.nPowTargetTimespan*4;

        const arith_uint256 pow_limit = UintToArith256(params.powLimit);
        arith_uint256 observed_new_target;
        observed_new_target.SetCompact(new_nbits);

        // Calculate the largest difficulty value possible:
        arith_uint256 largest_difficulty_target;
        largest_difficulty_target.SetCompact(old_nbits);
        largest_difficulty_target *= largest_timespan;
        largest_difficulty_target /= params.nPowTargetTimespan;

        if (largest_difficulty_target > pow_limit) {
            largest_difficulty_target = pow_limit;
        }

        // Round and then compare this new calculated value to what is
        // observed.
        arith_uint256 maximum_new_target;
        maximum_new_target.SetCompact(largest_difficulty_target.GetCompact());
        if (maximum_new_target < observed_new_target) return false;

        // Calculate the smallest difficulty value possible:
        arith_uint256 smallest_difficulty_target;
        smallest_difficulty_target.SetCompact(old_nbits);
        smallest_difficulty_target *= smallest_timespan;
        smallest_difficulty_target /= params.nPowTargetTimespan;

        if (smallest_difficulty_target > pow_limit) {
            smallest_difficulty_target = pow_limit;
        }

        // Round and then compare this new calculated value to what is
        // observed.
        arith_uint256 minimum_new_target;
        minimum_new_target.SetCompact(smallest_difficulty_target.GetCompact());
        if (minimum_new_target > observed_new_target) return false;
    } else if (old_nbits != new_nbits) {
        return false;
    }
    return true;
}

// Bypasses the actual proof of work check during fuzz testing with a simplified validation checking whether
// the most significant bit of the last byte of the hash is set.
bool CheckProofOfWork(uint256 hash, unsigned int nBits, const Consensus::Params& params)
{
    if (EnableFuzzDeterminism()) return (hash.data()[31] & 0x80) == 0;
    return CheckProofOfWorkImpl(hash, nBits, params);
}

std::optional<arith_uint256> DeriveTarget(unsigned int nBits, const uint256 pow_limit)
{
    bool fNegative;
    bool fOverflow;
    arith_uint256 bnTarget;

    bnTarget.SetCompact(nBits, &fNegative, &fOverflow);

    // Check range
    if (fNegative || bnTarget == 0 || fOverflow || bnTarget > UintToArith256(pow_limit))
        return {};

    return bnTarget;
}

bool CheckProofOfWorkImpl(uint256 hash, unsigned int nBits, const Consensus::Params& params)
{
    auto bnTarget{DeriveTarget(nBits, params.powLimit)};
    if (!bnTarget) return false;

    // Check proof of work matches claimed amount
    if (UintToArith256(hash) > bnTarget)
        return false;

    return true;
}
