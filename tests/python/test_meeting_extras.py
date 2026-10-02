"""Meetings beyond the basics: Seller cancellations by mail, a proposed new time,
private / response options and attachments on calendar items."""
import base64
import email
import email.policy
import unittest
from datetime import datetime
from unittest import mock

from harness import make_acct, webapp  # noqa: F401
from outlook_activesync_mcp.models import pack_item_id
from outlook_activesync_mcp.wbxml import el, find, find_all, text_of
from outlook_activesync_mcp.wbxml.encoder import encode

from test_reply_rsvp import _Client

CAL = pack_item_id("3", "9")
OCC = pack_item_id("3", "9", instance="20261005T070000Z")
EV = {"item_id": CAL, "uid": "u-1@x", "subject": "План", "start": "2026-10-05 10:00", "end": "2026-10-05 11:00",
      "response_type": "organizer", "organizer": {"address": "me@bank.test"},
      "attendees": [{"address": "me@bank.test"}, {"address": "kate@x.test"}, {"address": "Kate@x.test"}]}


class SellerCancelTest(unittest.TestCase):
    def test_target_only_for_my_seller_meeting_with_others(self):
        seller, bank = make_acct("seller"), make_acct("main")
        with mock.patch.object(webapp, "_cal_item", return_value=EV):
            self.assertEqual(webapp.cancel_notice_target(seller, CAL), (EV, ["kate@x.test"]))
            self.assertIsNone(webapp.cancel_notice_target(bank, CAL))  # Exchange notifies by itself
        theirs = {**EV, "response_type": "accepted", "organizer": {"address": "boss@x.test"}}
        with mock.patch.object(webapp, "_cal_item", return_value=theirs):
            self.assertIsNone(webapp.cancel_notice_target(seller, CAL))

    def test_cancel_mail_is_an_itip_cancel_of_the_occurrence(self):
        a, sent = make_acct("seller"), []
        with mock.patch.object(webapp, "_send_mime", side_effect=lambda a, m: sent.append(m)):
            webapp._send_cancel_bg(a, (EV, ["kate@x.test"]), OCC)
        msg = email.message_from_bytes(sent[0], policy=email.policy.default)
        self.assertTrue(msg["Subject"].startswith("Отменено: "))
        ics = next(p for p in msg.walk() if p.get_content_type() == "text/calendar").get_payload(decode=True).decode()
        for line in ("METHOD:CANCEL", "STATUS:CANCELLED", "UID:u-1@x", "RECURRENCE-ID:20261005T070000Z", "RSVP=FALSE"):
            self.assertIn(line, ics)
        self.assertNotIn("RRULE", ics)


class ProposeTimeTest(unittest.TestCase):
    S, E = datetime(2026, 10, 5, 12, 0).astimezone(), datetime(2026, 10, 5, 13, 0).astimezone()

    def test_proposal_rides_in_send_response(self):
        c = _Client()
        webapp._respond_with_note(c, CAL, "tentative", "", (self.S, self.E))
        send = find(c.cmds[0][1], "MeetingResponse", "SendResponse")
        self.assertIsNone(find(send, "AirSyncBase", "Body"))
        self.assertTrue(text_of(find(send, "MeetingResponse", "ProposedStartTime")).endswith(".000Z"))
        self.assertIsNotNone(find(send, "MeetingResponse", "ProposedEndTime"))

    def test_params(self):
        self.assertEqual(webapp._proposal({"propose_start": "2026-10-05T12:00", "propose_end": "2026-10-05T13:00"}),
                         (self.S, self.E))
        self.assertIsNone(webapp._proposal({"propose_start": "2026-10-05T13:00", "propose_end": "2026-10-05T12:00"}))

    def test_seller_gets_the_time_as_text(self):
        a = make_acct("seller")
        a.backend.client = _Client()
        r = webapp.respond_event(a, {"item_id": CAL, "response": "tentative",
                                     "propose_start": "2026-10-05T12:00", "propose_end": "2026-10-05T13:00"})
        self.assertTrue(r["ok"])
        sends = [find(n, "MeetingResponse", "SendResponse") for _, n in a.backend.client.cmds]
        self.assertFalse(any(find(s, "MeetingResponse", "ProposedStartTime") for s in sends if s))
        self.assertIn("Предлагаю другое время: 05.10 12:00–13:00",
                      text_of(find(sends[0], "AirSyncBase", "Data")))

    def test_accept_cannot_propose(self):
        r = webapp.respond_event(make_acct(), {"item_id": CAL, "response": "accept",
                                               "propose_start": "2026-10-05T12:00", "propose_end": "2026-10-05T13:00"})
        self.assertFalse(r["ok"])


class CalendarExtrasTest(unittest.TestCase):
    def tearDown(self):
        webapp._ATT.extra = {}

    def test_elements_encode_with_opaque_content(self):
        webapp._ATT.extra = {"response_requested": False, "disallow_counter": True,
                             "attachments": [{"name": "plan.txt", "content_base64": base64.b64encode(b"hi").decode()}]}
        els = webapp._cal_extra_els(_Client())
        ad = el("AirSync", "ApplicationData", *els)
        self.assertEqual(text_of(find(ad, "Calendar", "ResponseRequested")), "0")
        self.assertEqual(text_of(find(ad, "Calendar", "DisallowNewTimeProposal")), "1")
        self.assertEqual(find(ad, "AirSyncBase", "Content").data, b"hi")
        self.assertEqual(text_of(find(ad, "AirSyncBase", "DisplayName")), "plan.txt")
        encode(ad)  # every element is in the WBXML tables

    def test_parse_own_fields_not_exceptions(self):
        node = el("AirSync", "ApplicationData",
                  el("Calendar", "Sensitivity", text="2"),
                  el("Calendar", "ResponseRequested", text="0"),
                  el("AirSyncBase", "Attachments",
                     el("AirSyncBase", "Attachment", el("AirSyncBase", "DisplayName", text="a.pdf"),
                        el("AirSyncBase", "FileReference", text="ref1"),
                        el("AirSyncBase", "EstimatedDataSize", text="42")),
                     el("AirSyncBase", "Attachment", el("AirSyncBase", "FileReference", text="logo"),
                        el("AirSyncBase", "IsInline", text="1"))),
                  el("Calendar", "Exceptions", el("Calendar", "Exception", el("Calendar", "Sensitivity", text="0"))))
        x = webapp._cal_extras(node)
        self.assertEqual(x["sensitivity"], "private")
        self.assertFalse(x["response_requested"])
        self.assertNotIn("disallow_counter", x)
        self.assertEqual(x["attachments"], [{"ref": "ref1", "name": "a.pdf", "size": 42}])

    def test_seller_refuses_attachments(self):
        r = webapp.call(make_acct("seller"), "events", {"action": "create", "attachments": [{"name": "a", "content_base64": ""}]})
        self.assertFalse(r["ok"])


if __name__ == "__main__":
    unittest.main()
