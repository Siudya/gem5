/*
 * Copyright (c) 2020 Advanced Micro Devices, Inc.
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice,
 * this list of conditions and the following disclaimer.
 *
 * 2. Redistributions in binary form must reproduce the above copyright notice,
 * this list of conditions and the following disclaimer in the documentation
 * and/or other materials provided with the distribution.
 *
 * 3. Neither the name of the copyright holder nor the names of its
 * contributors may be used to endorse or promote products derived from this
 * software without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
 * AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 * IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
 * ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
 * LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
 * CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 * SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 * INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 * ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 * POSSIBILITY OF SUCH DAMAGE.
 */


#include "mem/ruby/network/garnet/NetworkBridge.hh"

#include <algorithm>
#include <cmath>

#include "debug/RubyNetwork.hh"
#include "mem/ruby/network/garnet/Credit.hh"
#include "mem/ruby/network/garnet/GarnetNetwork.hh"
#include "params/GarnetIntLink.hh"

namespace gem5
{

namespace ruby
{

namespace garnet
{

NetworkBridge::NetworkBridge(const Params &p)
    : CreditLink(p), coBridge(nullptr), nLink(p.link), enCdc(true),
      enSerDes(true), mType(p.vtype), cdcLatency(p.cdc_latency),
      serDesLatency(p.serdes_latency), lastScheduledAt(0),
      lastD2DReadyAt(0), network(nullptr), vcsPerVnet(0),
      d2dBuffersConfigured(false),
      bridgeTrafficStats(this, m_virt_nets)
{
    if (mType == enums::LINK_OBJECT) {
        nLink->setLinkConsumer(this);
        setSourceQueue(nLink->getBuffer(), nLink);
    } else if (mType == enums::OBJECT_LINK) {
        nLink->setSourceQueue(&linkBuffer, this);
        setLinkConsumer(nLink);
    } else {
        // CDC type must be set
        panic("CDC type must be set");
    }
}

void
NetworkBridge::setVcsPerVnet(uint32_t consumerVcs)
{
    DPRINTF(RubyNetwork, "VcsPerVnet VC: %d\n", consumerVcs);
    NetworkLink::setVcsPerVnet(consumerVcs);
    vcsPerVnet = consumerVcs;
    lenBuffer.resize(consumerVcs * m_virt_nets);
    sizeSent.resize(consumerVcs * m_virt_nets);
    flitsSent.resize(consumerVcs * m_virt_nets);
    extraCredit.resize(consumerVcs * m_virt_nets);

    nLink->setVcsPerVnet(consumerVcs);
    configureD2DBuffers();
}

void
NetworkBridge::initBridge(NetworkBridge *coBrid, bool cdc_en, bool serdes_en)
{
    coBridge = coBrid;
    enCdc = cdc_en;
    enSerDes = serdes_en;
    configureD2DBuffers();
}

void
NetworkBridge::setNetwork(GarnetNetwork *network_ptr)
{
    network = network_ptr;
    configureD2DBuffers();
}

bool
NetworkBridge::decouplesVc(int vnet) const
{
    return bufferDepth > 0 && enSerDes && network != nullptr &&
        vnet >= 0 && static_cast<uint32_t>(vnet) < m_virt_nets &&
        !network->isVNetOrdered(vnet);
}

void
NetworkBridge::configureD2DBuffers()
{
    if (d2dBuffersConfigured || mType != enums::LINK_OBJECT ||
        bufferDepth == 0 || !enSerDes || network == nullptr ||
        vcsPerVnet == 0) {
        return;
    }

    const unsigned total_vcs = vcsPerVnet * m_virt_nets;
    std::vector<unsigned> local_credits(total_vcs);
    for (unsigned vnet = 0; vnet < m_virt_nets; ++vnet) {
        const unsigned credits =
            network->get_vnet_type(vnet) == DATA_VNET_ ?
            network->getBuffersPerDataVC() :
            network->getBuffersPerCtrlVC();
        for (unsigned offset = 0; offset < vcsPerVnet; ++offset) {
            local_credits[vnet * vcsPerVnet + offset] = credits;
        }
    }

    perVcBuffers.resize(total_vcs);
    for (auto &buffer : perVcBuffers) {
        buffer.setMaxSize(bufferDepth);
    }
    d2dHeadTypes.assign(total_vcs, CREDIT_);
    d2dHasHead.assign(total_vcs, false);
    d2dFlow.configure(bufferDepth, local_credits);
    d2dBuffersConfigured = true;
}

NetworkBridge::~NetworkBridge()
{
}

NetworkBridge::BridgeTrafficStats::BridgeTrafficStats(
    statistics::Group *parent, uint32_t vnets)
    : statistics::Group(parent, "traffic"),
      ADD_STAT(d2dMsgsTotal, "Messages sent toward the link, per vnet"),
      ADD_STAT(d2dBytesTotal,
               "Protocol bytes sent toward the link, per vnet"),
      ADD_STAT(d2dAmoMsgs, "AMO-tagged messages sent toward the link, "
               "per vnet"),
      ADD_STAT(d2dAmoBytes, "AMO-tagged protocol bytes sent toward the "
               "link, per vnet")
{
    d2dMsgsTotal.init(vnets).flags(statistics::nozero);
    d2dBytesTotal.init(vnets).flags(statistics::nozero);
    d2dAmoMsgs.init(vnets).flags(statistics::nozero);
    d2dAmoBytes.init(vnets).flags(statistics::nozero);
}

void
NetworkBridge::recordD2DMsg(flit *t_flit)
{
    const int vnet = t_flit->get_vnet();
    if (vnet < 0 || static_cast<uint32_t>(vnet) >= m_virt_nets) {
        return;
    }
    const int bytes = t_flit->msgSize;
    bridgeTrafficStats.d2dMsgsTotal[vnet]++;
    bridgeTrafficStats.d2dBytesTotal[vnet] += bytes;
    const MsgPtr &msg = t_flit->get_msg_ptr();
    if (msg && msg->getAmoTagged()) {
        bridgeTrafficStats.d2dAmoMsgs[vnet]++;
        bridgeTrafficStats.d2dAmoBytes[vnet] += bytes;
    }
}

void
NetworkBridge::scheduleFlit(flit *t_flit, Cycles latency)
{
    Cycles totLatency = latency;

    if (enCdc) {
        // Add the CDC latency
        totLatency = latency + cdcLatency;
    }

    Tick sendTime = link_consumer->getObject()->clockEdge(totLatency);
    Tick nextAvailTick = lastScheduledAt + link_consumer->getObject()->\
            cyclesToTicks(Cycles(1));
    sendTime = std::max(nextAvailTick, sendTime);
    t_flit->set_time(sendTime);
    lastScheduledAt = sendTime;
    linkBuffer.insert(t_flit);
    link_consumer->scheduleEventAbsolute(sendTime);
}

void
NetworkBridge::scheduleD2DFlit(flit *t_flit, Cycles latency)
{
    assert(d2dBuffersConfigured);
    const int vc = t_flit->get_vc();
    assert(decouplesVc(t_flit->get_vnet()));
    assert(d2dFlow.hasSpace(vc));

    Tick ready_time = clockEdge(latency);
    const Tick next_ready = lastD2DReadyAt + cyclesToTicks(Cycles(1));
    ready_time = std::max(next_ready, ready_time);
    lastD2DReadyAt = ready_time;

    t_flit->set_time(ready_time);
    perVcBuffers[vc].insert(t_flit);
    d2dFlow.enqueue(vc);
    scheduleEventAbsolute(ready_time);
}

void
NetworkBridge::acceptLocalCredit(Credit *credit)
{
    assert(d2dBuffersConfigured);
    const int vc = credit->get_vc();
    const int vnet = vc / vcsPerVnet;
    assert(decouplesVc(vnet));

    d2dFlow.returnLocalCredit(vc, credit->is_free_signal());
    scheduleEvent(Cycles(1));
}

void
NetworkBridge::enqueueD2DCredit(int vc)
{
    assert(coBridge != nullptr);
    assert(vcsPerVnet > 0);
    assert(vc >= 0 && static_cast<unsigned>(vc) < extraCredit.size());
    assert(coBridge->decouplesVc(vc / vcsPerVnet));
    assert(!extraCredit[vc].empty());
    flitisizeAndSend(new Credit(vc, false, curTick()));
}

bool
NetworkBridge::sendFromD2DBuffer()
{
    if (!d2dBuffersConfigured) {
        return false;
    }

    std::fill(d2dHeadTypes.begin(), d2dHeadTypes.end(), CREDIT_);
    std::fill(d2dHasHead.begin(), d2dHasHead.end(), false);
    for (unsigned vc = 0; vc < perVcBuffers.size(); ++vc) {
        if (perVcBuffers[vc].isReady(curTick())) {
            flit *head = perVcBuffers[vc].peekTopFlit();
            if (decouplesVc(head->get_vnet())) {
                d2dHeadTypes[vc] = head->get_type();
                d2dHasHead[vc] = true;
            }
        }
    }

    const int vc = d2dFlow.select(d2dHeadTypes, d2dHasHead);
    if (vc < 0) {
        return false;
    }

    flit *t_flit = perVcBuffers[vc].getTopFlit();
    d2dFlow.sent(vc, t_flit->get_type());
    scheduleFlit(t_flit, Cycles(0));
    assert(coBridge != nullptr);
    coBridge->enqueueD2DCredit(vc);
    return true;
}

void
NetworkBridge::neutralize(int vc, int eCredit)
{
    assert(vc >= 0 && static_cast<unsigned>(vc) < extraCredit.size());
    assert(eCredit > 0);
    extraCredit[vc].push(eCredit);
}

void
NetworkBridge::flitisizeAndSend(flit *t_flit)
{
    // Traffic accounting: one count per message (head flit), taken at the
    // source-side bridge before any width conversion, so the counts are
    // messages and protocol bytes regardless of the link's flit width.
    if (mType == enums::OBJECT_LINK &&
        (t_flit->get_type() == HEAD_ || t_flit->get_type() == HEAD_TAIL_)) {
        recordD2DMsg(t_flit);
    }

    // Serialize-Deserialize only if it is enabled
    if (enSerDes) {
        // Calculate the target-width
        int target_width = bitWidth;
        int cur_width = nLink->bitWidth;
        if (mType == enums::OBJECT_LINK) {
            target_width = nLink->bitWidth;
            cur_width = bitWidth;
        }

        DPRINTF(RubyNetwork, "Target width: %d Current: %d\n",
            target_width, cur_width);

        int vc = t_flit->get_vc();

        // An equal-width SerDes still models the bridge pipeline, traffic
        // accounting, and long-link flow control; it only skips conversion.
        if (target_width == cur_width) {
            if (mType == enums::LINK_OBJECT &&
                decouplesVc(t_flit->get_vnet())) {
                if (t_flit->get_type() != CREDIT_) {
                    coBridge->neutralize(vc, 1);
                }
                scheduleD2DFlit(t_flit, serDesLatency);
            } else {
                scheduleFlit(t_flit, serDesLatency);
            }
            return;
        }

        if (target_width > cur_width) {
            // Deserialize
            // This deserializer combines flits from the
            // same message together
            int num_flits = 0;
            int flitPossible = 0;
            if (t_flit->get_type() == CREDIT_) {
                lenBuffer[vc]++;
                assert(extraCredit[vc].front());
                if (lenBuffer[vc] == extraCredit[vc].front()) {
                    flitPossible = 1;
                    extraCredit[vc].pop();
                    lenBuffer[vc] = 0;
                }
            } else if (t_flit->get_type() == TAIL_ ||
                       t_flit->get_type() == HEAD_TAIL_) {
                // If its the end of packet, then send whatever
                // is available.
                int sizeAvail = (t_flit->msgSize - sizeSent[vc]);
                flitPossible = ceil((float)sizeAvail/(float)target_width);
                assert (flitPossible < 2);
                num_flits = (t_flit->get_id() + 1) - flitsSent[vc];
                // Stop tracking the packet.
                flitsSent[vc] = 0;
                sizeSent[vc] = 0;
            } else {
                // If we are yet to receive the complete packet
                // track the size recieved and flits deserialized.
                int sizeAvail =
                    ((t_flit->get_id() + 1)*cur_width) - sizeSent[vc];
                flitPossible = floor((float)sizeAvail/(float)target_width);
                assert (flitPossible < 2);
                num_flits = (t_flit->get_id() + 1) - flitsSent[vc];
                if (flitPossible) {
                    sizeSent[vc] += target_width;
                    flitsSent[vc] = t_flit->get_id() + 1;
                }
            }

            DPRINTF(RubyNetwork, "Deserialize :%dB -----> %dB "
                " vc:%d\n", cur_width, target_width, vc);

            flit *fl = NULL;
            if (flitPossible) {
                fl = t_flit->deserialize(lenBuffer[vc], num_flits,
                    target_width);
            }

            // Inform the credit serializer about the number
            // of flits that were generated.
            if (t_flit->get_type() != CREDIT_ && fl) {
                coBridge->neutralize(vc, num_flits);
            }

            // Schedule only if we are done deserializing
            if (fl) {
                DPRINTF(RubyNetwork, "Scheduling a flit\n");
                lenBuffer[vc] = 0;
                if (mType == enums::LINK_OBJECT &&
                    decouplesVc(fl->get_vnet())) {
                    scheduleD2DFlit(fl, serDesLatency);
                } else {
                    scheduleFlit(fl, serDesLatency);
                }
            }
            // Delete this flit, new flit is sent in any case
            delete t_flit;
        } else {
            // Serialize
            DPRINTF(RubyNetwork, "Serializing flit :%d -----> %d "
            "(vc:%d, Original Message Size: %d)\n",
                cur_width, target_width, vc, t_flit->msgSize);

            int flitPossible = 0;
            if (t_flit->get_type() == CREDIT_) {
                // We store the deserialization ratio and then
                // access it when serializing credits in the
                // oppposite direction.
                assert(extraCredit[vc].front());
                flitPossible = extraCredit[vc].front();
                extraCredit[vc].pop();
            } else if (t_flit->get_type() == HEAD_ ||
                    t_flit->get_type() == BODY_) {
                int sizeAvail =
                    ((t_flit->get_id() + 1)*cur_width) - sizeSent[vc];
                flitPossible = floor((float)sizeAvail/(float)target_width);
                if (flitPossible) {
                    sizeSent[vc] += flitPossible*target_width;
                    flitsSent[vc] += flitPossible;
                }
            } else {
                int sizeAvail = t_flit->msgSize - sizeSent[vc];
                flitPossible = ceil((float)sizeAvail/(float)target_width);
                sizeSent[vc] = 0;
                flitsSent[vc] = 0;
            }
            assert(flitPossible > 0);

            // Schedule all the flits
            // num_flits could be zero for credits
            for (int i = 0; i < flitPossible; i++) {
                // Ignore neutralized credits
                flit *fl = t_flit->serialize(i, flitPossible, target_width);
                if (mType == enums::LINK_OBJECT &&
                    decouplesVc(fl->get_vnet())) {
                    scheduleD2DFlit(fl, serDesLatency);
                } else {
                    scheduleFlit(fl, serDesLatency);
                }
                DPRINTF(RubyNetwork, "Serialized to flit[%d of %d parts]:"
                " %s\n", i+1, flitPossible, *fl);
            }

            if (t_flit->get_type() != CREDIT_) {
                coBridge->neutralize(vc, flitPossible);
            }
            // Delete this flit, new flit is sent in any case
            delete t_flit;
        }
        return;
    }

    // If only CDC is enabled schedule it
    scheduleFlit(t_flit, Cycles(0));
}
void
NetworkBridge::wakeup()
{
    flit *t_flit;

    if (link_srcQueue->isReady(curTick())) {
        t_flit = link_srcQueue->getTopFlit();
        DPRINTF(RubyNetwork, "Recieved flit %s\n", *t_flit);
        const bool is_local_credit =
            t_flit->get_type() == CREDIT_ &&
            mType == enums::OBJECT_LINK && coBridge != nullptr &&
            vcsPerVnet > 0 &&
            coBridge->decouplesVc(t_flit->get_vc() / vcsPerVnet);
        if (is_local_credit) {
            coBridge->acceptLocalCredit(static_cast<Credit *>(t_flit));
            delete t_flit;
        } else {
            flitisizeAndSend(t_flit);
        }
    }

    if (sendFromD2DBuffer()) {
        scheduleEvent(Cycles(1));
    }

    // Reschedule in case there is a waiting flit.
    if (!link_srcQueue->isEmpty()) {
        scheduleEvent(Cycles(1));
    }
}

bool
NetworkBridge::functionalRead(Packet *pkt, WriteMask &mask)
{
    bool read = NetworkLink::functionalRead(pkt, mask);
    for (auto &buffer : perVcBuffers) {
        if (buffer.functionalRead(pkt, mask)) {
            read = true;
        }
    }
    return read;
}

uint32_t
NetworkBridge::functionalWrite(Packet *pkt)
{
    uint32_t writes = NetworkLink::functionalWrite(pkt);
    for (auto &buffer : perVcBuffers) {
        writes += buffer.functionalWrite(pkt);
    }
    return writes;
}

} // namespace garnet
} // namespace ruby
} // namespace gem5
