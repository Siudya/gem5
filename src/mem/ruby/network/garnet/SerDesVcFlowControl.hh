#ifndef __MEM_RUBY_NETWORK_GARNET_SERDES_VC_FLOW_CONTROL_HH__
#define __MEM_RUBY_NETWORK_GARNET_SERDES_VC_FLOW_CONTROL_HH__

#include <vector>

#include "mem/ruby/network/garnet/CommonTypes.hh"

namespace gem5
{

namespace ruby
{

namespace garnet
{

enum class SerDesRxState
{
    Idle,
    Streaming,
    WaitFree
};

class SerDesVcFlowControl
{
  public:
    void configure(unsigned fifo_depth,
                   const std::vector<unsigned> &local_credits);

    bool hasSpace(int vc, unsigned count = 1) const;
    void enqueue(int vc, unsigned count = 1);

    bool canSend(int vc, flit_type type) const;
    void sent(int vc, flit_type type);
    void returnLocalCredit(int vc, bool free_signal);

    int select(const std::vector<flit_type> &head_types,
               const std::vector<bool> &has_head) const;

    unsigned occupancy(int vc) const;
    unsigned localCredit(int vc) const;
    SerDesRxState rxState(int vc) const;

  private:
    bool validVc(int vc) const;

    unsigned m_fifoDepth = 0;
    unsigned m_nextVc = 0;
    std::vector<unsigned> m_occupancy;
    std::vector<unsigned> m_localCredits;
    std::vector<unsigned> m_maxLocalCredits;
    std::vector<SerDesRxState> m_rxStates;
};

} // namespace garnet
} // namespace ruby
} // namespace gem5

#endif // __MEM_RUBY_NETWORK_GARNET_SERDES_VC_FLOW_CONTROL_HH__
