"""Reply to recipients the user edited; answer a meeting with a comment."""
import unittest
from unittest import mock

from harness import FakeBackend, make_acct, webapp  # noqa: F401
from outlook_activesync_mcp.commands import mail_write as mw, people
import email
import email.policy

from outlook_activesync_mcp.errors import EasStatusError
from outlook_activesync_mcp.models import pack_item_id
from outlook_activesync_mcp.wbxml import find, text_of
from outlook_activesync_mcp.wbxml import el as _el


IID = pack_item_id("14", "7")
CAL = pack_item_id("3", "9")


class _Client:
    def __init__(self, status="1"):
        self.cmds, self.status = [], status
        self.s = type("S", (), {"max_attachment_bytes": 10_000_000})()

    def ensure_provisioned(self):
        pass

    def command(self, cmd, node, **kw):
        self.cmds.append((cmd, node))
        if cmd == "MeetingResponse":
            return _el("MeetingResponse", "MeetingResponse",
                       _el("MeetingResponse", "Result", _el("MeetingResponse", "Status", text=self.status)))
        return None


def _mime(node):
    m = find(node, "ComposeMail", "Mime")
    raw = next(getattr(m, k) for k in ("data", "opaque", "value") if isinstance(getattr(m, k, None), (bytes, bytearray)))
    return email.message_from_bytes(bytes(raw), policy=email.policy.default)


class ReplyTo(unittest.TestCase):
    def test_edited_recipients_go_in_to_and_cc(self):
        c = _Client()
        with mock.patch.object(mw, "_fetch_headers", return_value={"subject": "Бюджет"}), \
             mock.patch.object(people, "resolve_for_send", side_effect=lambda cl, l: l), \
             mock.patch.object(mw, "_self_address", return_value="me@bank.test"):
            r = webapp._reply_to(c, item_id=IID, body="Спасибо", to=["a@bank.test"], cc=["b@bank.test", "c@bank.test"])
        cmd, node = c.cmds[-1]
        self.assertEqual(cmd, "SmartReply", "the server still quotes the original")
        msg = _mime(node)
        self.assertEqual(msg["To"], "a@bank.test")
        self.assertEqual(msg["Cc"], "b@bank.test, c@bank.test")
        self.assertEqual(msg["Subject"], "Re: Бюджет")
        self.assertEqual(r["items"][0]["cc"], ["b@bank.test", "c@bank.test"])

    def test_empty_to_is_refused(self):
        with self.assertRaises(Exception):
            webapp._reply_to(_Client(), item_id=IID, to=[], body="x")


class RsvpNote(unittest.TestCase):
    def setUp(self):
        self.a = make_acct()

    def test_note_rides_in_the_meeting_response(self):
        c = _Client()
        self.assertTrue(webapp._meeting_response(self.a, c, CAL, "accept", "Опоздаю на 10 минут"))
        cmd, node = c.cmds[-1]
        self.assertEqual(cmd, "MeetingResponse")
        sr = find(node, "MeetingResponse", "SendResponse")
        self.assertEqual(text_of(find(sr, "AirSyncBase", "Data")), "Опоздаю на 10 минут")
        self.assertEqual(text_of(find(node, "MeetingResponse", "UserResponse")), "1")

    def test_server_refuses_the_body_plain_answer_then_note_by_mail(self):
        c = _Client(status="2")
        with mock.patch("outlook_activesync_mcp.commands.calendar.handle") as plain:
            noted = webapp._meeting_response(self.a, c, CAL, "decline", "Буду в отпуске")
        self.assertFalse(noted, "caller must mail the note")
        plain.assert_called_once()
        self.assertEqual(plain.call_args.kwargs["response"], "decline")

    def test_note_mail_goes_to_the_organizer(self):
        sent = []
        with mock.patch.object(webapp, "_send_mime", side_effect=lambda a, m: sent.append(m)):
            webapp._send_rsvp_note(self.a, "boss@bank.test", "Синк", "tentative", "Возможно")
        msg = email.message_from_bytes(sent[0], policy=email.policy.default)
        self.assertEqual(msg["To"], "boss@bank.test")
        self.assertEqual(msg["Subject"], "Под вопросом: Синк")
        self.assertIn("Возможно", msg.get_content())

    def test_without_a_note_nothing_changes(self):
        with mock.patch("outlook_activesync_mcp.commands.calendar.handle") as plain:
            self.assertTrue(webapp._meeting_response(self.a, _Client(), CAL, "accept", ""))
        plain.assert_called_once()

    def test_unreachable_is_not_turned_into_a_plain_answer(self):
        class Down(_Client):
            def command(self, cmd, node, **kw):
                raise webapp.NotAuthenticated("EAS endpoint недоступен (reset)") if hasattr(webapp, "NotAuthenticated") \
                    else __import__("outlook_activesync_mcp.errors", fromlist=["x"]).NotAuthenticated("EAS endpoint недоступен (reset)")
        with mock.patch("outlook_activesync_mcp.commands.calendar.handle") as plain, self.assertRaises(Exception):
            webapp._meeting_response(self.a, Down(), CAL, "accept", "текст")
        plain.assert_not_called()

if __name__ == "__main__":
    unittest.main()
