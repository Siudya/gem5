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

#ifndef __MEM_RUBY_STRUCTURES_AMO_STATS_HH__
#define __MEM_RUBY_STRUCTURES_AMO_STATS_HH__

#include "base/statistics.hh"

namespace gem5
{

namespace ruby
{

class AMOStats
{
  public:
    AMOStats(statistics::Group *parent)
        : stats(parent)
    {}

    void incCoreAtomicLoad() { ++stats.coreAtomicLoad; }
    void incNearL1AMO() { ++stats.nearL1AMO; }
    void incNearL2AMO() { ++stats.nearL2AMO; }
    void incAANHit() { ++stats.aanHit; }
    void incAANMiss() { ++stats.aanMiss; }
    void incAANFill() { ++stats.aanFill; }
    void incAANBypass() { ++stats.aanBypass; }
    void incAANAMO() { ++stats.aanAMO; }
    void incDelegate() { ++stats.delegate; }
    void incMigrate() { ++stats.migrate; }
    void incCentralize() { ++stats.centralize; }
    void incAmoFetch() { ++stats.amoFetch; }
    void incLocalReuse() { ++stats.localReuse; }

  private:
    struct AMOStatsGroup : public statistics::Group
    {
        AMOStatsGroup(statistics::Group *parent)
            : statistics::Group(parent, "amoStats"),
              ADD_STAT(coreAtomicLoad, "Core AtomicLoad requests"),
              ADD_STAT(nearL1AMO, "Near AMOs executed at L1D with Unique permission"),
              ADD_STAT(nearL2AMO, "Near AMOs executed at L2 with Unique permission"),
              ADD_STAT(aanHit, "AAN AtomicReturn local hits"),
              ADD_STAT(aanMiss, "AAN AtomicReturn misses"),
              ADD_STAT(aanFill, "AAN AtomicReturn misses admitted and filled"),
              ADD_STAT(aanBypass, "AAN AtomicReturn misses bypassed to HN-F"),
              ADD_STAT(aanAMO, "AAN local AMO executions"),
              ADD_STAT(delegate, "AMOs delegated by HN-F"),
              ADD_STAT(migrate, "AMOs migrated by HN-F"),
              ADD_STAT(centralize, "AMOs centralized at HN-F"),
              ADD_STAT(amoFetch, "Cacheline residencies created by AMO Unique data fetch"),
              ADD_STAT(localReuse, "AMO-fetched cacheline residencies reused by a later local access"),
              ADD_STAT(placementTotal, "AMOs classified by execution placement",
                       nearL1AMO + nearL2AMO + centralize + migrate +
                       delegate + aanAMO)
        {}

        statistics::Scalar coreAtomicLoad;
        statistics::Scalar nearL1AMO;
        statistics::Scalar nearL2AMO;
        statistics::Scalar aanHit;
        statistics::Scalar aanMiss;
        statistics::Scalar aanFill;
        statistics::Scalar aanBypass;
        statistics::Scalar aanAMO;
        statistics::Scalar delegate;
        statistics::Scalar migrate;
        statistics::Scalar centralize;
        statistics::Scalar amoFetch;
        statistics::Scalar localReuse;
        statistics::Formula placementTotal;
    } stats;
};

} // namespace ruby

} // namespace gem5

#endif // __MEM_RUBY_STRUCTURES_AMO_STATS_HH__
