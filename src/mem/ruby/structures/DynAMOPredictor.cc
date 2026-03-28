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

#include "mem/ruby/structures/DynAMOPredictor.hh"

#include <cassert>

#include "base/logging.hh"

namespace gem5
{

namespace ruby
{

DynAMOPredictor::DynAMOPredictor(
    int entries, int assoc, int counter_bits, int block_size_bits)
    : m_assoc(assoc),
      m_counter_max((1 << counter_bits) - 1),
      m_block_size_bits(block_size_bits)
{
    assert(entries > 0 && assoc > 0 && counter_bits > 0);
    assert(block_size_bits > 0);
    assert(entries % assoc == 0);
    m_sets = entries / assoc;
    m_table.resize(m_sets, std::vector<AMTEntry>(m_assoc));
}

int
DynAMOPredictor::getSet(Addr addr) const
{
    return (int)((addr >> m_block_size_bits) % m_sets);
}

Addr
DynAMOPredictor::getTag(Addr addr) const
{
    return addr >> m_block_size_bits;
}

DynAMOPredictor::AMTEntry*
DynAMOPredictor::lookup(Addr addr)
{
    int set = getSet(addr);
    Addr tag = getTag(addr);
    for (auto& entry : m_table[set]) {
        if (entry.valid && entry.tag == tag) {
            return &entry;
        }
    }
    return nullptr;
}

DynAMOPredictor::AMTEntry*
DynAMOPredictor::allocate(Addr addr)
{
    int set = getSet(addr);
    Addr tag = getTag(addr);

    // Find an invalid entry first
    for (auto& entry : m_table[set]) {
        if (!entry.valid) {
            entry.valid = true;
            entry.tag = tag;
            entry.reuse_conf = m_counter_max;
            entry.reuse_bit = false;
            touchLRU(entry);
            return &entry;
        }
    }

    // Evict LRU entry
    AMTEntry* victim = &m_table[set][0];
    for (auto& entry : m_table[set]) {
        if (entry.lru_stamp < victim->lru_stamp) {
            victim = &entry;
        }
    }
    victim->valid = true;
    victim->tag = tag;
    victim->reuse_conf = m_counter_max;
    victim->reuse_bit = false;
    touchLRU(*victim);
    return victim;
}

void
DynAMOPredictor::touchLRU(AMTEntry& entry)
{
    entry.lru_stamp = ++m_lru_counter;
}

bool
DynAMOPredictor::predictNear(Addr addr, int l1_state_code)
{
    AMTEntry* entry = lookup(addr);

    if (entry == nullptr) {
        // AMT miss: use global reuse ratio for first-touch decision
        // Default to NEAR if no history yet
        bool global_near = (m_total_amo_fetched == 0) ||
            (m_total_reused * 2 >= m_total_amo_fetched);

        // Always allocate entry on miss, with max confidence
        allocate(addr);

        if (global_near) {
            m_total_amo_fetched++;
            return true;   // NEAR
        } else {
            return false;  // FAR
        }
    }

    // AMT hit
    touchLRU(*entry);

    if (entry->reuse_conf > 0) {
        // High confidence: NEAR — reset reuse_bit for new observation
        entry->reuse_bit = false;
        m_total_amo_fetched++;
        return true;
    }

    // Zero confidence: Reuse-PN semantics
    //   l1_state_code: 0=I, 1=SC, 2=SD
    if (l1_state_code == 0) {
        // I state → FAR
        return false;
    } else {
        // SC/SD (present in L1D) → NEAR
        entry->reuse_bit = false;
        m_total_amo_fetched++;
        return true;
    }
}

void
DynAMOPredictor::notifyHit(Addr addr)
{
    AMTEntry* entry = lookup(addr);
    if (entry != nullptr) {
        entry->reuse_bit = true;
    }
}

void
DynAMOPredictor::notifyEvictOrInval(Addr addr)
{
    AMTEntry* entry = lookup(addr);
    if (entry == nullptr)
        return;

    if (entry->reuse_bit) {
        if (entry->reuse_conf < m_counter_max)
            entry->reuse_conf++;
        m_total_reused++;
    } else {
        if (entry->reuse_conf > 0)
            entry->reuse_conf--;
    }
    entry->reuse_bit = false;
}

} // namespace ruby

} // namespace gem5
