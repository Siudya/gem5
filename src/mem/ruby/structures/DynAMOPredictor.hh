/*
 * Copyright (c) 2025 DynAMO/Delegato Paper Reproduction
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are
 * met: redistributions of source code must retain the above copyright
 * notice, this list of conditions and the following disclaimer;
 * redistributions in binary form must reproduce the above copyright
 * notice, this list of conditions and the following disclaimer in the
 * documentation and/or other materials provided with the distribution;
 * neither the name of the copyright holders nor the names of its
 * contributors may be used to endorse or promote products derived from
 * this software without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
 * "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 * LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
 * A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
 * OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
 * SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
 * LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
 * DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
 * THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 * (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */

/**
 * @file DynAMOPredictor.hh
 *
 * DynAMO-Reuse-PN predictor for atomic memory operations.
 *
 * Implements the AMT (Atomic Metadata Table) and Reuse-PN prediction logic
 * from "DynAMO: Improving Parallelism Through Dynamic Placement of Atomic
 * Memory Operations" (ISCA '23).
 *
 * Placed per-core alongside the L1D cache controller. Predicts whether an
 * AMO should execute near (locally at L1D) or far (at the HN/directory).
 *
 * The predictor is queried only when the target cache line is NOT in a
 * Unique state (UC/UD). UC/UD lines always execute AMOs locally.
 */

#ifndef __MEM_RUBY_STRUCTURES_DYNAMO_PREDICTOR_HH__
#define __MEM_RUBY_STRUCTURES_DYNAMO_PREDICTOR_HH__

#include <cstdint>
#include <vector>

#include "mem/ruby/common/Address.hh"

namespace gem5
{

namespace ruby
{

class DynAMOPredictor
{
  public:
    /**
     * @param entries Total number of AMT entries (default: 128)
     * @param assoc   Set associativity (default: 4)
     * @param counter_bits Width of reuse confidence counter (default: 5)
     * @param block_size_bits log2(cache line size in bytes)
     */
    DynAMOPredictor(
        int entries, int assoc, int counter_bits, int block_size_bits);

    /**
     * Predict whether an AMO should execute near (true) or far (false).
     *
     * @param addr         Cache-line-aligned address
     * @param l1_state_code  0 = Invalid, 1 = SC, 2 = SD
     *                       (UC/UD never reach here; handled before calling)
     * @return true for near execution, false for far execution
     */
    bool predictNear(Addr addr, int l1_state_code);

    /**
     * Notify that a tracked cache line was hit in L1D by any access
     * (load, store, or subsequent atomic). Sets the reuse_bit.
     */
    void notifyHit(Addr addr);

    /**
     * Notify that an AtomicLoad Unique fetch formed a cache residency.
     * Updates the global denominator for the first-touch heuristic.
     */
    void notifyAmoFetched(Addr addr);

    /**
     * Notify that an AMO-fetched residency was reused by a local access.
     * Updates the global numerator and per-line reuse_bit.
     */
    void notifyAmoResidencyReuse(Addr addr);

    /**
     * Notify that a tracked cache line was evicted or invalidated from L1D.
     * Updates the reuse confidence counter based on reuse_bit.
     */
    void notifyEvictOrInval(Addr addr);

  private:
    struct AMTEntry
    {
        bool valid = false;
        Addr tag = 0;
        int reuse_conf = 0;
        bool reuse_bit = false;
        uint64_t lru_stamp = 0;
    };

    int m_sets;
    int m_assoc;
    int m_counter_max;
    int m_block_size_bits;

    // Global reuse tracking for first-touch heuristic
    int64_t m_total_amo_fetched = 0;
    int64_t m_total_reused = 0;

    // LRU stamp counter
    uint64_t m_lru_counter = 0;

    // AMT storage: m_sets × m_assoc
    std::vector<std::vector<AMTEntry>> m_table;

    int getSet(Addr addr) const;
    Addr getTag(Addr addr) const;
    AMTEntry* lookup(Addr addr);
    AMTEntry* allocate(Addr addr);
    void touchLRU(AMTEntry& entry);
};

} // namespace ruby

} // namespace gem5

#endif // __MEM_RUBY_STRUCTURES_DYNAMO_PREDICTOR_HH__
