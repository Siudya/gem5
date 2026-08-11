#include <gtest/gtest.h>

#include <vector>

#include "mem/ruby/network/garnet/SerDesVcFlowControl.hh"

namespace gem5
{

namespace ruby
{

namespace garnet
{

TEST(SerDesVcFlowControlTest, InitializesIndependentPerVcState)
{
    SerDesVcFlowControl flow;
    flow.configure(4, {1, 2});

    EXPECT_EQ(flow.occupancy(0), 0);
    EXPECT_EQ(flow.occupancy(1), 0);
    EXPECT_EQ(flow.localCredit(0), 1);
    EXPECT_EQ(flow.localCredit(1), 2);
    EXPECT_EQ(flow.rxState(0), SerDesRxState::Idle);
    EXPECT_EQ(flow.rxState(1), SerDesRxState::Idle);
}

TEST(SerDesVcFlowControlTest, EnforcesPerVcFifoCapacity)
{
    SerDesVcFlowControl flow;
    flow.configure(4, {1});

    EXPECT_TRUE(flow.hasSpace(0, 4));
    flow.enqueue(0, 4);
    EXPECT_EQ(flow.occupancy(0), 4);
    EXPECT_FALSE(flow.hasSpace(0));
}

TEST(SerDesVcFlowControlTest, HoldsNextPacketUntilLocalFree)
{
    SerDesVcFlowControl flow;
    flow.configure(4, {1});
    flow.enqueue(0, 2);

    ASSERT_TRUE(flow.canSend(0, HEAD_TAIL_));
    flow.sent(0, HEAD_TAIL_);
    EXPECT_EQ(flow.occupancy(0), 1);
    EXPECT_EQ(flow.rxState(0), SerDesRxState::WaitFree);
    EXPECT_FALSE(flow.canSend(0, HEAD_TAIL_));

    flow.returnLocalCredit(0, true);
    EXPECT_EQ(flow.rxState(0), SerDesRxState::Idle);
    EXPECT_TRUE(flow.canSend(0, HEAD_TAIL_));
}

TEST(SerDesVcFlowControlTest, StreamsOnePacketThroughAllStates)
{
    SerDesVcFlowControl flow;
    flow.configure(4, {1});
    flow.enqueue(0, 3);

    flow.sent(0, HEAD_);
    EXPECT_EQ(flow.rxState(0), SerDesRxState::Streaming);
    flow.returnLocalCredit(0, false);

    flow.sent(0, BODY_);
    EXPECT_EQ(flow.rxState(0), SerDesRxState::Streaming);
    flow.returnLocalCredit(0, false);

    flow.sent(0, TAIL_);
    EXPECT_EQ(flow.rxState(0), SerDesRxState::WaitFree);
    flow.returnLocalCredit(0, true);
    EXPECT_EQ(flow.rxState(0), SerDesRxState::Idle);
    EXPECT_EQ(flow.occupancy(0), 0);
}

TEST(SerDesVcFlowControlTest, NumericCreditDoesNotReleasePacket)
{
    SerDesVcFlowControl flow;
    flow.configure(4, {2});
    flow.enqueue(0, 2);

    flow.sent(0, HEAD_);
    flow.sent(0, TAIL_);
    ASSERT_EQ(flow.rxState(0), SerDesRxState::WaitFree);

    flow.returnLocalCredit(0, false);
    EXPECT_EQ(flow.rxState(0), SerDesRxState::WaitFree);
    flow.returnLocalCredit(0, true);
    EXPECT_EQ(flow.rxState(0), SerDesRxState::Idle);
}

TEST(SerDesVcFlowControlTest, LocalCreditsGateEgress)
{
    SerDesVcFlowControl flow;
    flow.configure(4, {1});
    flow.enqueue(0, 2);

    flow.sent(0, HEAD_);
    EXPECT_FALSE(flow.canSend(0, BODY_));

    flow.returnLocalCredit(0, false);
    EXPECT_TRUE(flow.canSend(0, BODY_));
}

TEST(SerDesVcFlowControlTest, WaitingVcDoesNotBlockAnotherVc)
{
    SerDesVcFlowControl flow;
    flow.configure(4, {1, 1});
    flow.enqueue(0);
    flow.enqueue(1);
    const std::vector<flit_type> types = {HEAD_TAIL_, HEAD_TAIL_};
    const std::vector<bool> ready = {true, true};

    ASSERT_EQ(flow.select(types, ready), 0);
    flow.sent(0, HEAD_TAIL_);
    EXPECT_EQ(flow.select(types, ready), 1);
}

TEST(SerDesVcFlowControlTest, RoundRobinAdvancesOnlyAfterSend)
{
    SerDesVcFlowControl flow;
    flow.configure(4, {1, 1, 1});
    flow.enqueue(0);
    flow.enqueue(1);
    flow.enqueue(2);
    const std::vector<flit_type> types(3, HEAD_TAIL_);
    const std::vector<bool> ready(3, true);

    EXPECT_EQ(flow.select(types, ready), 0);
    EXPECT_EQ(flow.select(types, ready), 0);
    flow.sent(0, HEAD_TAIL_);
    EXPECT_EQ(flow.select(types, ready), 1);
    flow.sent(1, HEAD_TAIL_);
    EXPECT_EQ(flow.select(types, ready), 2);
}

} // namespace garnet
} // namespace ruby
} // namespace gem5
