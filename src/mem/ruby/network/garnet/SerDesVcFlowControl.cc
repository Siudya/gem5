#include "mem/ruby/network/garnet/SerDesVcFlowControl.hh"

#include <cassert>

namespace gem5
{

namespace ruby
{

namespace garnet
{

void
SerDesVcFlowControl::configure(
    unsigned fifo_depth, const std::vector<unsigned> &local_credits)
{
    assert(fifo_depth > 0);
    assert(!local_credits.empty());
    for (const auto credits : local_credits) {
        assert(credits > 0);
    }

    m_fifoDepth = fifo_depth;
    m_nextVc = 0;
    m_occupancy.assign(local_credits.size(), 0);
    m_localCredits = local_credits;
    m_maxLocalCredits = local_credits;
    m_rxStates.assign(local_credits.size(), SerDesRxState::Idle);
}

bool
SerDesVcFlowControl::validVc(int vc) const
{
    return vc >= 0 && static_cast<unsigned>(vc) < m_occupancy.size();
}

bool
SerDesVcFlowControl::hasSpace(int vc, unsigned count) const
{
    assert(validVc(vc));
    assert(count > 0);
    return count <= m_fifoDepth - m_occupancy[vc];
}

void
SerDesVcFlowControl::enqueue(int vc, unsigned count)
{
    assert(hasSpace(vc, count));
    m_occupancy[vc] += count;
}

bool
SerDesVcFlowControl::canSend(int vc, flit_type type) const
{
    assert(validVc(vc));

    if (m_occupancy[vc] == 0 || m_localCredits[vc] == 0) {
        return false;
    }

    switch (m_rxStates[vc]) {
      case SerDesRxState::Idle:
        return type == HEAD_ || type == HEAD_TAIL_;
      case SerDesRxState::Streaming:
        return type == BODY_ || type == TAIL_;
      case SerDesRxState::WaitFree:
        return false;
    }

    return false;
}

void
SerDesVcFlowControl::sent(int vc, flit_type type)
{
    assert(canSend(vc, type));

    --m_occupancy[vc];
    --m_localCredits[vc];

    if (type == HEAD_) {
        m_rxStates[vc] = SerDesRxState::Streaming;
    } else if (type == TAIL_ || type == HEAD_TAIL_) {
        m_rxStates[vc] = SerDesRxState::WaitFree;
    }

    m_nextVc = (vc + 1) % m_occupancy.size();
}

void
SerDesVcFlowControl::returnLocalCredit(int vc, bool free_signal)
{
    assert(validVc(vc));
    assert(m_rxStates[vc] != SerDesRxState::Idle);
    assert(m_localCredits[vc] < m_maxLocalCredits[vc]);
    assert(!free_signal || m_rxStates[vc] == SerDesRxState::WaitFree);

    ++m_localCredits[vc];
    if (free_signal) {
        m_rxStates[vc] = SerDesRxState::Idle;
    }
}

int
SerDesVcFlowControl::select(
    const std::vector<flit_type> &head_types,
    const std::vector<bool> &has_head) const
{
    assert(head_types.size() == m_occupancy.size());
    assert(has_head.size() == m_occupancy.size());

    for (unsigned offset = 0; offset < m_occupancy.size(); ++offset) {
        const unsigned vc = (m_nextVc + offset) % m_occupancy.size();
        if (has_head[vc] && canSend(vc, head_types[vc])) {
            return vc;
        }
    }

    return -1;
}

unsigned
SerDesVcFlowControl::occupancy(int vc) const
{
    assert(validVc(vc));
    return m_occupancy[vc];
}

unsigned
SerDesVcFlowControl::localCredit(int vc) const
{
    assert(validVc(vc));
    return m_localCredits[vc];
}

SerDesRxState
SerDesVcFlowControl::rxState(int vc) const
{
    assert(validVc(vc));
    return m_rxStates[vc];
}

} // namespace garnet
} // namespace ruby
} // namespace gem5
