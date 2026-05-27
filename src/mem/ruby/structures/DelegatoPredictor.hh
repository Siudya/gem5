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
 * @file DelegatoPredictor.hh
 *
 * Delegato predictor hardware structures for locality-aware AMO placement
 * on chiplet architectures.
 *
 * Implements the two key tables from "Delegato: Locality-Aware Atomic
 * Memory Operations on Chiplets" (MICRO '25):
 *
 * 1. DelegatoReuseTable — instantiated in every cache controller.
 *    Training: at L1D when dynamic Delegato RT is enabled via
 *    Callback_AtomicHit, and at L2
 *    via eviction spill (Profile_Eviction) when amoReuseCount >= 2.
 *    Query: at the owner L2 on SnpAMO arrival — queryAndResetReuse()
 *    returns the eviction-spill signal; upstream_unique is now always
 *    passed as false because fix_2/fix_3 compute it externally in SLICC.
 *    128 entries, 2-way set-associative.
 *
 * 2. DelegatoPredictorTable — placed per HN-F/LLC slice, maintains a
 *    CA/PC/PO state machine per address to choose between Centralize,
 *    Delegate, and Migrate actions for far AMOs.
 *    128 entries, 2-way set-associative.
 */

#ifndef __MEM_RUBY_STRUCTURES_DELEGATO_PREDICTOR_HH__
#define __MEM_RUBY_STRUCTURES_DELEGATO_PREDICTOR_HH__

#include <cstdint>
#include <vector>

#include "mem/ruby/common/Address.hh"
#include "mem/ruby/common/TypeDefines.hh"

namespace gem5
{

namespace ruby
{

/**
 * Reuse Table: instantiated in every CHI cache controller (same SLICC
 * template).  Training occurs at:
 *   - L1D with dynamic Delegato RT enabled: Callback_AtomicHit on local-Unique AMO.
 *   - L2 eviction: Profile_Eviction spills amoReuseCount >= 2 to RT.
 * The owner L2 queries its RT on SnpAMO arrival; the RT signal serves as
 * the "eviction spill" component of the three-signal reuse_bit formula:
 *   reuse_bit = upstream_unique || (amoReuseCount >= 2) || rt_spill_reuse
 * upstream_unique and amoReuseCount are evaluated directly in SLICC —
 * queryAndResetReuse() is called with upstream_unique=false so it returns
 * only the RT-local eviction-spill signal.
 *
 * Paper spec: 128 entries, 2-way, 50 bits/entry (49-bit tag + 1-bit reuse).
 */
class DelegatoReuseTable
{
  public:
    /**
     * @param entries Total number of entries (default: 128)
     * @param assoc   Set associativity (default: 2)
     * @param block_size_bits log2(cache line size in bytes)
     */
    DelegatoReuseTable(int entries, int assoc, int block_size_bits);

    /**
     * Notify that the core executed a local AMO on a Unique (UC/UD) line.
     * Sets reuse=1 for this address (allocates entry if miss).
     */
    void notifyLocalUniqueAmo(Addr addr);

    /**
     * Query the RT eviction-spill bit for a given address and reset it.
     * Called when a Delegate snoop (SnpAMO) arrives at the owner L2.
     *
     * The upstream_unique parameter is retained for interface compatibility
     * but current callers always pass false — upstream_unique is evaluated
     * directly in SLICC (tbe.dir_ownerExists && tbe.dir_ownerIsExcl).
     *
     * @param addr            Cache-line-aligned address
     * @param upstream_unique Always false in current usage (kept for API)
     * @return true if this RT entry had eviction-spill reuse set
     */
    bool queryAndResetReuse(Addr addr, bool upstream_unique);

  private:
    struct RTEntry
    {
        bool valid = false;
        Addr tag = 0;
        bool reuse = false;
        uint64_t lru_stamp = 0;
    };

    int m_sets;
    int m_assoc;
    int m_block_size_bits;
    uint64_t m_lru_counter = 0;

    std::vector<std::vector<RTEntry>> m_table;

    int getSet(Addr addr) const;
    Addr getTag(Addr addr) const;
    RTEntry* lookup(Addr addr);
    RTEntry* allocateOrFind(Addr addr);
    void touchLRU(RTEntry& entry);
};


/**
 * Predictor Table: per HN-F/LLC slice structure. Implements the CA/PC/PO
 * state machine from Delegato Figure 6b.
 *
 * Paper spec: 128 entries, 2-way, 56 bits/entry
 *   (49-bit tag + 5-bit last_req_id + 2-bit policy_state).
 */
class DelegatoPredictorTable
{
  public:
    // Policy states (Figure 6b)
    enum PolicyState : int {
        STATE_CA = 0,   // Chiplet-Aware (default)
        STATE_PC = 1,   // Present Central (owner not reusing)
        STATE_PO = 2    // Pinned Owner (same requester repeats)
    };

    // Action outputs
    enum Action : int {
        ACTION_CENTRALIZE = 0,
        ACTION_DELEGATE   = 1,
        ACTION_MIGRATE    = 2
    };

    // Directory state categories for policy table lookup
    enum DirCase : int {
        DIR_UC_UD   = 0,   // Line cached locally at HN-F, no upstream
        DIR_RSC_RSD = 1,   // Shared/owned upstream (not exclusive)
        DIR_RU      = 2,   // Single exclusive owner upstream
        DIR_I       = 3    // Not cached anywhere
    };

    /**
     * @param entries Total number of entries (default: 128)
     * @param assoc   Set associativity (default: 2)
     * @param block_size_bits log2(cache line size in bytes)
     * @param cores_per_chiplet Number of cores per chiplet (for locality)
     * @param hnf_chiplet_id    This HN-F's chiplet (0 or 1)
     */
    DelegatoPredictorTable(int entries, int assoc, int block_size_bits,
                           int cores_per_chiplet, int hnf_chiplet_id);

    /**
     * Look up the predictor for a far AMO and decide the action.
     * Also updates last_req_id and may trigger state transitions.
     *
     * @param addr      Cache-line-aligned address
     * @param req_id    Requester NodeID (global version)
     * @param dir_case  Directory state category (DirCase enum value)
     * @param owner_id  Owner NodeID (only meaningful when dir_case==DIR_RU)
     * @return Action to take (Centralize/Delegate/Migrate)
     */
    int decideAction(Addr addr, NodeID req_id, int dir_case,
                     NodeID owner_id);

    /**
     * Stateless policy lookup for fixed CA/PC/PO HN-F policies.
     *
     * Unlike decideAction(), this method does not allocate, update LRU,
     * remember the last requester, or transition policy state.
     */
    int staticAction(int policy_state, NodeID req_id, int dir_case,
                     NodeID owner_id);

    /**
     * Update the predictor based on reuse_bit feedback from a Delegate
     * SnpResp. Called only after a Delegate transaction completes.
     *
     * @param addr      Cache-line-aligned address
     * @param reuse_bit True if owner reported reuse since last Delegate
     */
    void updateReuseFeedback(Addr addr, bool reuse_bit);

    /**
     * Get the current policy state for an address (for stats/debug).
     * Returns -1 if address is not in the table.
     */
    int getState(Addr addr);

  private:
    struct PTEntry
    {
        bool valid = false;
        Addr tag = 0;
        int policy_state = STATE_CA;
        // UINT_MAX means "no previous requester"
        NodeID last_req_id = static_cast<NodeID>(-1);
        bool has_last_req = false;
        uint64_t lru_stamp = 0;
    };

    int m_sets;
    int m_assoc;
    int m_block_size_bits;
    uint64_t m_lru_counter = 0;

    std::vector<std::vector<PTEntry>> m_table;

    int m_cores_per_chiplet;
    int m_hnf_chiplet_id;

    int getSet(Addr addr) const;
    Addr getTag(Addr addr) const;
    PTEntry* lookup(Addr addr);
    PTEntry* allocate(Addr addr);
    void touchLRU(PTEntry& entry);

    /** Determine chiplet index from a global controller NodeID. */
    int getChiplet(NodeID nodeID) const;

    /** True if the given NodeID is on the same chiplet as this HN-F. */
    bool isLocal(NodeID nodeID) const;

    /**
     * Map policy state + directory case + chiplet locality to action.
     * Implements the full strategy table from Delegato paper Figure 6b.
     */
    int mapPolicyToAction(int policy_state, int dir_case,
                          bool req_local, bool owner_local);
};

} // namespace ruby

} // namespace gem5

#endif // __MEM_RUBY_STRUCTURES_DELEGATO_PREDICTOR_HH__
