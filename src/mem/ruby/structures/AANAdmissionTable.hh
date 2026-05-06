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

#ifndef __MEM_RUBY_STRUCTURES_AAN_ADMISSION_TABLE_HH__
#define __MEM_RUBY_STRUCTURES_AAN_ADMISSION_TABLE_HH__

#include <cassert>
#include <cstdint>
#include <vector>

#include "base/statistics.hh"
#include "mem/ruby/common/Address.hh"

namespace gem5
{

namespace ruby
{

/**
 * AAN bypass/admission table (BAT) with integrated protocol stats.
 *
 * For filtered AAN, the first lookup of a cache line records it and rejects
 * admission; later lookups of the same resident BAT tag admit.  For no-filter
 * AAN, every lookup admits without allocating a BAT entry.
 *
 * Stats registered here appear under the owning cache controller's stats
 * namespace (same pattern as TBEStorage).
 */
class AANAdmissionTable
{
  public:
    // parent is the owning cache controller (statistics::Group).
    AANAdmissionTable(statistics::Group *parent,
                      int entries, int assoc, int blockSizeBits)
        : m_assoc(assoc),
          m_blockSizeBits(blockSizeBits),
          aanStats(parent)
    {
        assert(entries > 0 && assoc > 0);
        assert(blockSizeBits > 0);
        assert(entries % assoc == 0);
        m_sets = entries / assoc;
        m_table.resize(m_sets, std::vector<Entry>(m_assoc));
    }

    bool
    shouldAdmit(Addr addr, bool noFilter)
    {
        ++aanStats.batLookups;

        if (noFilter) {
            ++aanStats.batAdmits;
            return true;
        }

        Entry* entry = lookup(addr);
        if (entry != nullptr) {
            touchLRU(*entry);
            ++aanStats.batAdmits;
            return true;
        }

        allocate(addr);
        ++aanStats.batRejects;
        return false;
    }

    void incDispatch()  { ++aanStats.dispatch; }
    void incReceived()  { ++aanStats.received; }
    void incLocalHit()  { ++aanStats.localHit; }
    void incBypass()    { ++aanStats.bypass; }
    void incFill()      { ++aanStats.fill; }
    void incAMOExec()   { ++aanStats.amoExec; }

  private:
    struct AANStatsGroup : public statistics::Group
    {
        AANStatsGroup(statistics::Group *parent)
            : statistics::Group(parent, "aanBAT"),
              ADD_STAT(dispatch,   "Requester->AAN AtomicReturn dispatches"),
              ADD_STAT(received,   "AAN: AtomicReturn requests received"),
              ADD_STAT(localHit,   "AAN: local hit (line resident with unique ownership)"),
              ADD_STAT(bypass,     "AAN: miss bypassed to HNF (BAT reject)"),
              ADD_STAT(fill,       "AAN: miss admitted by BAT (fetch + exec pending)"),
              ADD_STAT(amoExec,    "AAN: local AMO executions after NCBWrData"),
              ADD_STAT(batLookups, "AAN BAT: total shouldAdmit calls"),
              ADD_STAT(batAdmits,  "AAN BAT: admissions (nofilter + seen-again)"),
              ADD_STAT(batRejects, "AAN BAT: first-touch rejections")
        {}

        statistics::Scalar dispatch;
        statistics::Scalar received;
        statistics::Scalar localHit;
        statistics::Scalar bypass;
        statistics::Scalar fill;
        statistics::Scalar amoExec;
        statistics::Scalar batLookups;
        statistics::Scalar batAdmits;
        statistics::Scalar batRejects;
    } aanStats;

    struct Entry
    {
        bool valid = false;
        Addr tag = 0;
        uint64_t lruStamp = 0;
    };

    int m_sets = 0;
    int m_assoc;
    int m_blockSizeBits;
    uint64_t m_lruCounter = 0;

    std::vector<std::vector<Entry>> m_table;

    int
    getSet(Addr addr) const
    {
        return static_cast<int>(getTag(addr) % m_sets);
    }

    Addr
    getTag(Addr addr) const
    {
        return addr >> m_blockSizeBits;
    }

    Entry*
    lookup(Addr addr)
    {
        const int set = getSet(addr);
        const Addr tag = getTag(addr);
        for (auto& entry : m_table[set]) {
            if (entry.valid && entry.tag == tag) {
                return &entry;
            }
        }
        return nullptr;
    }

    Entry*
    allocate(Addr addr)
    {
        const int set = getSet(addr);
        const Addr tag = getTag(addr);

        for (auto& entry : m_table[set]) {
            if (!entry.valid) {
                entry.valid = true;
                entry.tag = tag;
                touchLRU(entry);
                return &entry;
            }
        }

        Entry* victim = &m_table[set][0];
        for (auto& entry : m_table[set]) {
            if (entry.lruStamp < victim->lruStamp) {
                victim = &entry;
            }
        }
        victim->valid = true;
        victim->tag = tag;
        touchLRU(*victim);
        return victim;
    }

    void
    touchLRU(Entry& entry)
    {
        entry.lruStamp = ++m_lruCounter;
    }
};

} // namespace ruby

} // namespace gem5

#endif // __MEM_RUBY_STRUCTURES_AAN_ADMISSION_TABLE_HH__
