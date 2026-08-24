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
#include <unordered_map>
#include <vector>

#include "base/statistics.hh"
#include "base/types.hh"
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
 * Entry lifetime (leaky bucket, optional): each entry carries a 5-bit reuse
 * saturating counter and a lifetime window of `lifetimeTicks`.  Conceptually
 * the lifetime counts down every cycle; when it expires, an entry with
 * reuse > 0 pays one reuse credit and restarts its window, while an entry
 * with reuse == 0 dies.  A new entry starts with reuse = 0 (one window to
 * prove itself); every admit recharges reuse by one.  Long-idle candidates
 * therefore expire in one window, while repeatedly readmitted hot lines
 * accumulate credits and survive proportionally longer.  The implementation
 * settles lazily in O(1) at lookup/allocate time: k = elapsed/W full windows
 * are charged against the reuse counter, and the entry dies if k exceeds it.
 * lifetimeTicks == 0 disables lifetimes (capacity-only eviction).
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
        : aanStats(parent),
          m_assoc(assoc),
          m_blockSizeBits(blockSizeBits)
    {
        assert(entries > 0 && assoc > 0);
        assert(blockSizeBits > 0);
        assert(entries % assoc == 0);
        m_sets = entries / assoc;
        m_table.resize(m_sets, std::vector<Entry>(m_assoc));
    }

    // now/lifetimeTicks drive the lazy lifetime settlement; the caller
    // supplies both each call (lifetime from a Cycles parameter via
    // cyclesToTicks) so the table needs no clock of its own.
    // lifetimeTicks == 0 disables lifetimes.
    bool
    shouldAdmit(Addr addr, bool noFilter, Tick now = 0, Tick lifetimeTicks = 0)
    {
        ++aanStats.batLookups;

        if (noFilter) {
            ++aanStats.batAdmits;
            return true;
        }

        Entry* entry = lookup(addr);
        if (entry != nullptr && !settle(*entry, now, lifetimeTicks)) {
            // Lifetime expired with no reuse credit left: the candidate
            // record is gone; this touch restarts it as a first touch.
            ++aanStats.batExpires;
            entry = nullptr;
        }
        if (entry != nullptr) {
            if (entry->reuse < REUSE_MAX) {
                ++entry->reuse;
            }
            if (!entry->retouchCounted) {
                // This candidate's first-touch rejection is now proven wrong
                // in hindsight: the line did come back while the record
                // lived. Counted once per allocated record.
                ++aanStats.rejectsRetouched;
                entry->retouchCounted = true;
            }
            touchLRU(*entry);
            ++aanStats.batAdmits;
            return true;
        }

        Entry* allocated = allocate(addr, now, lifetimeTicks);
        allocated->reuse = 0;
        allocated->base = now;
        ++aanStats.batRejects;
        return false;
    }

    void incDispatch()  { ++aanStats.dispatch; }
    void incReceived()  { ++aanStats.received; }
    void incBypass()    { ++aanStats.bypass; }
    void incAMOExec()   { ++aanStats.amoExec; }

    // Admission-quality tracking: each admitted fill is classified when its
    // line leaves the AAN (eviction or snoop recall) by whether it served at
    // least one local hit while resident. Lines still resident at the stats
    // dump sit in neither bucket, so fill >= fillsRepaid + fillsUnrepaid by
    // at most the AAN array capacity.
    void
    noteFill(Addr addr)
    {
        ++aanStats.fill;
        auto [it, inserted] = m_residentFills.emplace(getTag(addr), false);
        if (!inserted) {
            // A second fill without an observed drop: classify the stale
            // record and restart tracking for the new residency.
            classifyFill(it->second);
            it->second = false;
        }
    }

    void
    noteHit(Addr addr)
    {
        ++aanStats.localHit;
        auto it = m_residentFills.find(getTag(addr));
        if (it != m_residentFills.end()) {
            it->second = true;
        }
    }

    // Called on every cache-block deallocation of the owning controller;
    // no-op unless the block is a tracked AAN admission.
    void
    noteDrop(Addr addr)
    {
        auto it = m_residentFills.find(getTag(addr));
        if (it == m_residentFills.end()) {
            return;
        }
        classifyFill(it->second);
        m_residentFills.erase(it);
    }

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
              ADD_STAT(batRejects, "AAN BAT: first-touch rejections"),
              ADD_STAT(batExpires, "AAN BAT: entries found dead at lookup (lifetime spent)"),
              ADD_STAT(fillsRepaid,
                       "AAN: fills that served >=1 local hit before the line "
                       "left the AAN (useful admissions)"),
              ADD_STAT(fillsUnrepaid,
                       "AAN: fills whose line left the AAN with zero local "
                       "hits (false admissions)"),
              ADD_STAT(rejectsRetouched,
                       "AAN BAT: first-touch rejections whose line was "
                       "touched again while its record lived (false bypasses)")
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
        statistics::Scalar batExpires;
        statistics::Scalar fillsRepaid;
        statistics::Scalar fillsUnrepaid;
        statistics::Scalar rejectsRetouched;
    } aanStats;

    static constexpr uint8_t REUSE_MAX = 31;  // 5-bit saturating

    struct Entry
    {
        bool valid = false;
        Addr tag = 0;
        uint64_t lruStamp = 0;
        uint8_t reuse = 0;   // lifetime credits (5-bit saturating)
        Tick base = 0;       // start of the current lifetime window
        bool retouchCounted = false;  // rejectsRetouched taken for this record
    };

    int m_sets = 0;
    int m_assoc;
    int m_blockSizeBits;
    uint64_t m_lruCounter = 0;

    std::vector<std::vector<Entry>> m_table;

    // Tag -> "served a local hit since its fill", for every admitted line
    // currently resident in the AAN. Bounded by the AAN array capacity.
    std::unordered_map<Addr, bool> m_residentFills;

    void
    classifyFill(bool repaid)
    {
        if (repaid) {
            ++aanStats.fillsRepaid;
        } else {
            ++aanStats.fillsUnrepaid;
        }
    }

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

    // Lazily apply lifetime windows elapsed since entry.base. Returns false
    // (and invalidates the entry) if the lifetime is spent; true if the
    // entry is still alive (reuse charged, base advanced). No-op when
    // lifetimes are disabled.
    bool
    settle(Entry& entry, Tick now, Tick lifetimeTicks)
    {
        if (lifetimeTicks == 0) {
            return true;
        }
        const uint64_t elapsed = now - entry.base;
        const uint64_t windows = elapsed / lifetimeTicks;
        if (windows == 0) {
            return true;
        }
        if (windows > entry.reuse) {
            entry.valid = false;
            return false;
        }
        entry.reuse -= windows;
        entry.base += windows * lifetimeTicks;
        return true;
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
    allocate(Addr addr, Tick now, Tick lifetimeTicks)
    {
        const int set = getSet(addr);
        const Addr tag = getTag(addr);

        // Settle lifetimes across the set first so spent entries free
        // their slots instead of competing with live candidates for LRU.
        if (lifetimeTicks != 0) {
            for (auto& entry : m_table[set]) {
                if (entry.valid) {
                    settle(entry, now, lifetimeTicks);
                }
            }
        }

        for (auto& entry : m_table[set]) {
            if (!entry.valid) {
                entry.valid = true;
                entry.tag = tag;
                entry.retouchCounted = false;
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
        victim->retouchCounted = false;
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
