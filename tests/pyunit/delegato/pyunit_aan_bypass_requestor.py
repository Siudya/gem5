#!/usr/bin/env python3
"""
Regression tests for AAN transparent bypass routing.
"""

import pathlib
import re
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
CHI_PROTOCOL_ROOT = REPO_ROOT / "src" / "mem" / "ruby" / "protocol" / "chi"


def read_protocol_file(name):
    return (CHI_PROTOCOL_ROOT / name).read_text(encoding="utf-8")


def action_body(source, action_name):
    match = re.search(
        rf"action\({re.escape(action_name)},[^\n]*\) \{{(.*?)\n\}}",
        source,
        re.S,
    )
    if match is None:
        raise AssertionError(f"action {action_name} not found")
    return match.group(1)


def compact(text):
    return " ".join(text.split())


class AANTransparentBypassTest(unittest.TestCase):
    def test_bat_reject_uses_transparent_forward_only(self):
        body = action_body(read_protocol_file("CHI-cache-actions.sm"),
                           "Initiate_AtomicReturn_Forward")
        # Static regression: extract BAT reject branch anchored by existing comment
        self.assertIn("AAN Miss + BAT Rejects: bypass", body)
        bypass = body.split("AAN Miss + BAT Rejects: bypass", 1)[1]
        bypass = bypass.split("} else {", 1)[0]

        self.assertIn("Event:SendAtomicReturn_AANTransparentBypass", bypass)
        self.assertNotIn("Event:SendAtomicReturn_AANBypass", bypass)
        self.assertNotIn("Event:SendDBIDResp_AR", bypass)
        self.assertNotIn("Event:SendARData", bypass)
        self.assertNotIn("Event:SendCompData_AR", bypass)

    def test_aan_forward_uses_original_requestor_and_real_hnf(self):
        body = compact(action_body(read_protocol_file("CHI-cache-actions.sm"),
                                   "Send_AtomicReturn_AANTransparentBypass"))

        self.assertIn("prepareRequestAtomic(tbe, CHIRequestType:AtomicReturn, out_msg);", body)
        self.assertIn("out_msg.requestor := tbe.requestor;", body)
        self.assertIn("out_msg.fwdRequestor := tbe.requestor;", body)
        self.assertIn("out_msg.Destination.add(mapAddressToDownstreamMachine(tbe.addr));", body)
        self.assertIn("out_msg.allowRetry := true;", body)
        self.assertNotIn("allowRequestRetry(tbe, out_msg)", body)
        self.assertNotIn("aan_bypass", body)

    def test_dbid_responses_retarget_pending_destination(self):
        body = compact(action_body(read_protocol_file("CHI-cache-actions.sm"),
                                   "Receive_ReqResp_CopyDBID"))

        self.assertIn("tbe.txnId := in_msg.dbid;", body)
        self.assertIn("tbe.pendReqDest.clear();", body)
        self.assertIn("tbe.pendReqDest.add(in_msg.responder);", body)

    def test_retry_ack_retargets_before_waiting_destination_is_recorded(self):
        body = compact(action_body(read_protocol_file("CHI-cache-actions.sm"),
                                   "Receive_RetryAck"))

        self.assertIn("tbe.pendReqDest.clear();", body)
        self.assertIn("destsWaitingRetry.addNetDest(tbe.pendReqDest);", body)
        retarget_pos = body.index("tbe.pendReqDest.clear();")
        add_pos = body.index("destsWaitingRetry.addNetDest(tbe.pendReqDest);")
        self.assertLess(retarget_pos, add_pos)
        self.assertIn("tbe.pendReqDest.add(in_msg.responder);", body)

    def test_write_and_atomic_data_use_pending_destination(self):
        actions = read_protocol_file("CHI-cache-actions.sm")

        for action in ("Send_ARData", "Send_ANRData", "Send_WBData", "Send_WUData"):
            body = action_body(actions, action)
            self.assertIn("tbe.pendReqDest.isEmpty() == false", body, action)
            self.assertIn("tbe.pendReqDest.smallestElement()", body, action)

    def test_old_aan_bypass_marker_and_directory_guards_are_removed(self):
        combined = "\n".join(
            read_protocol_file(name)
            for name in (
                "CHI-cache-actions.sm",
                "CHI-cache-funcs.sm",
                "CHI-cache-transitions.sm",
                "CHI-cache.sm",
                "CHI-msg.sm",
            )
        )

        self.assertNotIn("aan_bypass", combined)
        self.assertNotIn("is_aan_bypass", combined)
        self.assertNotIn("pendReqAANBypass", combined)
        self.assertNotIn("SendAtomicReturn_AANBypass", combined)


if __name__ == "__main__":
    unittest.main()
