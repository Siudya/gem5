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
    int entries, int assoc, int block_size_bits)
    : m_assoc(assoc),
      m_block_size_bits(block_size_bits)
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
DelegatoPredictorTable::mapPolicyToAction(int policy_state, bool has_owner)
{
    // Map policy state + directory context to action
    // (Figure 6b + Section 5.3 of paper)
    switch (policy_state) {
      case STATE_CA:
        // Central All: Centralize by default.
        // If has_owner (RU state), Delegate to let owner execute.
        if (has_owner)
            return ACTION_DELEGATE;
        return ACTION_CENTRALIZE;

      case STATE_PO:
        // Pinned Owner: keep line at owner.
        // If has_owner, Delegate; otherwise Centralize.
        if (has_owner)
            return ACTION_DELEGATE;
        return ACTION_CENTRALIZE;

      case STATE_PC:
        // Present Central: owner not reusing → always Centralize.
        return ACTION_CENTRALIZE;

      default:
        return ACTION_CENTRALIZE;
    }
}

int
DelegatoPredictorTable::decideAction(Addr addr, NodeID req_id, bool has_owner)
{
    PTEntry* entry = lookup(addr);

    if (entry == nullptr) {
        // PT miss: allocate new entry with default CA state
        entry = allocate(addr);
        entry->last_req_id = req_id;
        entry->has_last_req = true;
        return mapPolicyToAction(STATE_CA, has_owner);
    }

    touchLRU(*entry);

    int policy = entry->policy_state;

    // Check for same-requester repeat → upgrade to PO (Migrate)
    // Paper Figure 6b: CA/PC + same_req → PO, action = Migrate
    // Guard: Migrate requires an owner to exist (data must come from somewhere);
    // if no owner, fall through to mapPolicyToAction which returns Centralize.
    if (has_owner && entry->has_last_req && entry->last_req_id == req_id &&
        (policy == STATE_CA || policy == STATE_PC)) {
        entry->policy_state = STATE_PO;
        entry->last_req_id = req_id;
        return ACTION_MIGRATE;
    }

    // Update last_req_id for next comparison
    entry->last_req_id = req_id;
    entry->has_last_req = true;

    return mapPolicyToAction(policy, has_owner);
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
