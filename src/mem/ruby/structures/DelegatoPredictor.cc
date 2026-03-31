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

#include "mem/ruby/structures/DelegatoPredictor.hh"

#include <cassert>

#include "base/logging.hh"

namespace gem5
{

namespace ruby
{

// ===== DelegatoReuseTable =====

DelegatoReuseTable::DelegatoReuseTable(
    int entries, int assoc, int block_size_bits)
    : m_assoc(assoc),
      m_block_size_bits(block_size_bits)
{
    assert(entries > 0 && assoc > 0);
    assert(block_size_bits > 0);
    assert(entries % assoc == 0);
    m_sets = entries / assoc;
    m_table.resize(m_sets, std::vector<RTEntry>(m_assoc));
}

int
DelegatoReuseTable::getSet(Addr addr) const
{
    return (int)((addr >> m_block_size_bits) % m_sets);
}

Addr
DelegatoReuseTable::getTag(Addr addr) const
{
    return addr >> m_block_size_bits;
}

DelegatoReuseTable::RTEntry*
DelegatoReuseTable::lookup(Addr addr)
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

DelegatoReuseTable::RTEntry*
DelegatoReuseTable::allocateOrFind(Addr addr)
{
    // Try to find existing entry first
    RTEntry* existing = lookup(addr);
    if (existing != nullptr) {
        touchLRU(*existing);
        return existing;
    }

    int set = getSet(addr);
    Addr tag = getTag(addr);

    // Find an invalid entry
    for (auto& entry : m_table[set]) {
        if (!entry.valid) {
            entry.valid = true;
            entry.tag = tag;
            entry.reuse = false;
            touchLRU(entry);
            return &entry;
        }
    }

    // Evict LRU entry
    RTEntry* victim = &m_table[set][0];
    for (auto& entry : m_table[set]) {
        if (entry.lru_stamp < victim->lru_stamp) {
            victim = &entry;
        }
    }
    victim->valid = true;
    victim->tag = tag;
    victim->reuse = false;
    touchLRU(*victim);
    return victim;
}

void
DelegatoReuseTable::touchLRU(RTEntry& entry)
{
    entry.lru_stamp = ++m_lru_counter;
}

void
DelegatoReuseTable::notifyLocalUniqueAmo(Addr addr)
{
    RTEntry* entry = allocateOrFind(addr);
    entry->reuse = true;
}

bool
DelegatoReuseTable::queryAndResetReuse(Addr addr, bool upstream_unique)
{
    assert(!upstream_unique);
    RTEntry* entry = lookup(addr);

    // RT eviction-spill query: reuse_bit from previously-evicted reuse info.
    // upstream_unique is always false here (evaluated in SLICC directly).
    bool reused = upstream_unique;
    if (entry != nullptr) {
        reused = reused || entry->reuse;
        // Reset reuse after query (between-two-delegates tracking)
        entry->reuse = false;
    }
    return reused;
}


// ===== DelegatoPredictorTable =====

DelegatoPredictorTable::DelegatoPredictorTable(
    int entries, int assoc, int block_size_bits,
    int cores_per_chiplet, int hnf_chiplet_id)
    : m_assoc(assoc),
      m_block_size_bits(block_size_bits),
      m_cores_per_chiplet(cores_per_chiplet),
      m_hnf_chiplet_id(hnf_chiplet_id)
{
    assert(entries > 0 && assoc > 0);
    assert(block_size_bits > 0);
    assert(entries % assoc == 0);
    m_sets = entries / assoc;
    m_table.resize(m_sets, std::vector<PTEntry>(m_assoc));
}

int
DelegatoPredictorTable::getSet(Addr addr) const
{
    return (int)((addr >> m_block_size_bits) % m_sets);
}

Addr
DelegatoPredictorTable::getTag(Addr addr) const
{
    return addr >> m_block_size_bits;
}

DelegatoPredictorTable::PTEntry*
DelegatoPredictorTable::lookup(Addr addr)
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

DelegatoPredictorTable::PTEntry*
DelegatoPredictorTable::allocate(Addr addr)
{
    int set = getSet(addr);
    Addr tag = getTag(addr);

    // Find an invalid entry first
    for (auto& entry : m_table[set]) {
        if (!entry.valid) {
            entry.valid = true;
            entry.tag = tag;
            entry.policy_state = STATE_CA;
            entry.last_req_id = -1;
            touchLRU(entry);
            return &entry;
        }
    }

    // Evict LRU entry
    PTEntry* victim = &m_table[set][0];
    for (auto& entry : m_table[set]) {
        if (entry.lru_stamp < victim->lru_stamp) {
            victim = &entry;
        }
    }
    victim->valid = true;
    victim->tag = tag;
    victim->policy_state = STATE_CA;
    victim->last_req_id = -1;
    touchLRU(*victim);
    return victim;
}

void
DelegatoPredictorTable::touchLRU(PTEntry& entry)
{
    entry.lru_stamp = ++m_lru_counter;
}

int
DelegatoPredictorTable::getChiplet(NodeID nodeID) const
{
    if (m_cores_per_chiplet <= 0)
        return 0;
    int num_cpus = 2 * m_cores_per_chiplet;
    return ((int)(nodeID % num_cpus)) / m_cores_per_chiplet;
}

bool
DelegatoPredictorTable::isLocal(NodeID nodeID) const
{
    return getChiplet(nodeID) == m_hnf_chiplet_id;
}

int
DelegatoPredictorTable::mapPolicyToAction(
    int policy_state, int dir_case, bool req_local, bool owner_local)
{
    // Full strategy table from Delegato paper (Figure 6b / Section 5.3).
    //
    // | Policy | UC/UD | RSC/RSD | RU,Own.local | RU,Own.remote | I,Req.local | I,Req.remote |
    // |--------|-------|---------|--------------|---------------|-------------|--------------|
    // |  CA    |   C   |    C    |      D       |       C       |      M      |      C       |
    // |  PC    |   C   |    C    |      C       |       C       |      M      |      M       |
    // |  PO    |   M   |    M    |      D       |       D       |      M      |      M       |
    switch (policy_state) {
      case STATE_CA:
        switch (dir_case) {
          case DIR_UC_UD:   return ACTION_CENTRALIZE;
          case DIR_RSC_RSD: return ACTION_CENTRALIZE;
          case DIR_RU:
            return owner_local ? ACTION_DELEGATE : ACTION_CENTRALIZE;
          case DIR_I:
            return req_local ? ACTION_MIGRATE : ACTION_CENTRALIZE;
          default:          return ACTION_CENTRALIZE;
        }

      case STATE_PC:
        switch (dir_case) {
          case DIR_UC_UD:   return ACTION_CENTRALIZE;
          case DIR_RSC_RSD: return ACTION_CENTRALIZE;
          case DIR_RU:      return ACTION_CENTRALIZE;
          case DIR_I:       return ACTION_MIGRATE;
          default:          return ACTION_CENTRALIZE;
        }

      case STATE_PO:
        switch (dir_case) {
          case DIR_UC_UD:   return ACTION_MIGRATE;
          case DIR_RSC_RSD: return ACTION_MIGRATE;
          case DIR_RU:      return ACTION_DELEGATE;
          case DIR_I:       return ACTION_MIGRATE;
          default:          return ACTION_MIGRATE;
        }

      default:
        return ACTION_CENTRALIZE;
    }
}

int
DelegatoPredictorTable::decideAction(
    Addr addr, NodeID req_id, int dir_case, NodeID owner_id)
{
    bool req_local = isLocal(req_id);
    bool owner_local = isLocal(owner_id); // only meaningful for DIR_RU

    PTEntry* entry = lookup(addr);

    if (entry == nullptr) {
        // PT miss: allocate new entry with default CA state
        entry = allocate(addr);
        entry->last_req_id = req_id;
        entry->has_last_req = true;
        return mapPolicyToAction(STATE_CA, dir_case, req_local, owner_local);
    }

    touchLRU(*entry);

    int policy = entry->policy_state;

    // Same-requester repeat → upgrade CA/PC to PO, action = Migrate.
    // Paper Figure 6b: CA/PC + same_req → PO.
    // No has_owner guard: whether Migrate is physically executable
    // is decided by the HN-F action layer, not the predictor.
    if (entry->has_last_req && entry->last_req_id == req_id &&
        (policy == STATE_CA || policy == STATE_PC)) {
        entry->policy_state = STATE_PO;
        entry->last_req_id = req_id;
        return ACTION_MIGRATE;
    }

    // Update last_req_id for next comparison
    entry->last_req_id = req_id;
    entry->has_last_req = true;

    return mapPolicyToAction(policy, dir_case, req_local, owner_local);
}

void
DelegatoPredictorTable::updateReuseFeedback(Addr addr, bool reuse_bit)
{
    PTEntry* entry = lookup(addr);
    if (entry == nullptr)
        return;

    // Paper Figure 6b: reuse_bit==0 from Delegate → push toward PC
    if (!reuse_bit) {
        if (entry->policy_state == STATE_CA ||
            entry->policy_state == STATE_PO) {
            entry->policy_state = STATE_PC;
        }
    }
    // reuse_bit==1: stay in current state (no transition)
}

int
DelegatoPredictorTable::getState(Addr addr)
{
    PTEntry* entry = lookup(addr);
    if (entry == nullptr)
        return -1;
    return entry->policy_state;
}

} // namespace ruby

} // namespace gem5
