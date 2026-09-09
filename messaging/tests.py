from django.test import TestCase
from unittest.mock import MagicMock

from .utils import mask_contact_info, mask_conversation_messages, _PLACEHOLDER


class MaskContactInfoTests(TestCase):
    """Single-message masking tests."""

    # ── Phone numbers ──────────────────────────────────────────────────

    def test_saudi_phone_masked(self):
        text = "call me at 0551234567"
        masked, flagged = mask_contact_info(text)
        self.assertTrue(flagged)
        self.assertNotIn("0551234567", masked)
        self.assertIn(_PLACEHOLDER, masked)

    def test_international_phone_masked(self):
        text = "my number is +966551234567"
        masked, flagged = mask_contact_info(text)
        self.assertTrue(flagged)
        self.assertNotIn("+966551234567", masked)

    def test_spaced_phone_masked(self):
        text = "055 123 4567"
        masked, flagged = mask_contact_info(text)
        self.assertTrue(flagged)

    # ── Spelled-out digits (English) ───────────────────────────────────

    def test_english_spelled_digits(self):
        text = "zero five five zero six three one one three two one"
        masked, flagged = mask_contact_info(text)
        self.assertTrue(flagged)
        self.assertEqual(masked, _PLACEHOLDER)

    def test_english_misspelled_digits(self):
        text = "zero five five zero six thre one one thre two one"
        masked, flagged = mask_contact_info(text)
        self.assertTrue(flagged)
        self.assertEqual(masked, _PLACEHOLDER)

    def test_english_oh_for_zero(self):
        text = "oh five five oh six three one one"
        masked, flagged = mask_contact_info(text)
        self.assertTrue(flagged)

    # ── Spelled-out digits (Arabic) ────────────────────────────────────

    def test_arabic_spelled_digits(self):
        text = "صفر خمسة خمسة صفر ستة ثلاثة واحد واحد ثلاثة اثنين واحد"
        masked, flagged = mask_contact_info(text)
        self.assertTrue(flagged)
        self.assertEqual(masked, _PLACEHOLDER)

    def test_arabic_colloquial_digits(self):
        text = "صفر خمسه خمسه صفر سته تلاته واحد واحد"
        masked, flagged = mask_contact_info(text)
        self.assertTrue(flagged)

    # ── Arabic-Indic numerals ──────────────────────────────────────────

    def test_arabic_indic_numerals(self):
        text = "٠٥٥٠٦٣١١٣٢١"
        masked, flagged = mask_contact_info(text)
        self.assertTrue(flagged)

    # ── Emails ─────────────────────────────────────────────────────────

    def test_email_masked(self):
        masked, flagged = mask_contact_info("reach me at user@gmail.com")
        self.assertTrue(flagged)
        self.assertNotIn("user@gmail.com", masked)

    # ── URLs ───────────────────────────────────────────────────────────

    def test_url_masked(self):
        masked, flagged = mask_contact_info("check https://example.com/path")
        self.assertTrue(flagged)

    def test_whatsapp_link_masked(self):
        masked, flagged = mask_contact_info("msg me wa.me/966551234567")
        self.assertTrue(flagged)

    # ── Keywords ───────────────────────────────────────────────────────

    def test_whatsapp_keyword_masked(self):
        masked, flagged = mask_contact_info("add me on whatsapp")
        self.assertTrue(flagged)

    def test_arabic_keyword_masked(self):
        masked, flagged = mask_contact_info("تواصل معي واتساب")
        self.assertTrue(flagged)

    def test_snap_keyword_masked(self):
        masked, flagged = mask_contact_info("my snap is @coolcar")
        self.assertTrue(flagged)

    # ── Social handles ─────────────────────────────────────────────────

    def test_handle_masked(self):
        masked, flagged = mask_contact_info("follow me @mycarshop")
        self.assertTrue(flagged)

    # ── False positives — MUST NOT mask ────────────────────────────────

    def test_sar_price_not_masked(self):
        text = "The price is SAR 245,000"
        masked, flagged = mask_contact_info(text)
        self.assertFalse(flagged)
        self.assertIn("245,000", masked)

    def test_riyal_price_not_masked(self):
        text = "السعر 360000 ريال"
        masked, flagged = mask_contact_info(text)
        self.assertFalse(flagged)
        self.assertIn("360000", masked)

    def test_year_not_masked(self):
        text = "Looking at a 2024 Toyota Camry"
        masked, flagged = mask_contact_info(text)
        self.assertFalse(flagged)
        self.assertIn("2024", masked)

    def test_short_number_not_masked(self):
        text = "mileage is 64000 km"
        masked, flagged = mask_contact_info(text)
        self.assertFalse(flagged)
        self.assertIn("64000", masked)

    def test_normal_text_not_masked(self):
        text = "I'm interested in the car, is it still available?"
        masked, flagged = mask_contact_info(text)
        self.assertFalse(flagged)
        self.assertEqual(masked, text)

    def test_empty_text(self):
        masked, flagged = mask_contact_info("")
        self.assertFalse(flagged)
        self.assertEqual(masked, "")

    def test_none_text(self):
        masked, flagged = mask_contact_info(None)
        self.assertFalse(flagged)
        self.assertIsNone(masked)


class CrossMessageMaskingTests(TestCase):
    """Split-number detection across consecutive messages."""

    def _make_msg(self, content, sender_id=1):
        m = MagicMock()
        m.content = content
        m.sender_id = sender_id
        return m

    def test_split_phone_across_two_messages(self):
        msgs = [
            self._make_msg("0500037"),
            self._make_msg("883"),
        ]
        masked = mask_conversation_messages(msgs, reader_id=2)
        self.assertEqual(masked[0], _PLACEHOLDER)
        self.assertEqual(masked[1], _PLACEHOLDER)

    def test_split_phone_across_three_messages(self):
        msgs = [
            self._make_msg("055"),
            self._make_msg("0631"),
            self._make_msg("1321"),
        ]
        masked = mask_conversation_messages(msgs, reader_id=2)
        self.assertTrue(all(m == _PLACEHOLDER for m in masked))

    def test_split_from_different_senders_not_masked(self):
        msgs = [
            self._make_msg("0500037", sender_id=1),
            self._make_msg("883", sender_id=2),
        ]
        masked = mask_conversation_messages(msgs, reader_id=3)
        # Different senders — should NOT aggregate
        self.assertNotEqual(masked[0], _PLACEHOLDER)
        self.assertNotEqual(masked[1], _PLACEHOLDER)

    def test_normal_short_messages_not_masked(self):
        msgs = [
            self._make_msg("ok"),
            self._make_msg("sounds good"),
            self._make_msg("let me check"),
        ]
        masked = mask_conversation_messages(msgs, reader_id=2)
        self.assertEqual(masked[0], "ok")
        self.assertEqual(masked[1], "sounds good")

    def test_price_in_conversation_not_masked(self):
        msgs = [
            self._make_msg("The total is SAR 245,000"),
            self._make_msg("That includes shipping"),
        ]
        masked = mask_conversation_messages(msgs, reader_id=2)
        self.assertIn("245,000", masked[0])

    def test_allow_contact_bypasses_masking(self):
        msgs = [
            self._make_msg("call me at 0551234567"),
        ]
        masked = mask_conversation_messages(msgs, reader_id=2, allow_contact=True)
        self.assertIn("0551234567", masked[0])

    def test_admin_bypasses_masking(self):
        msgs = [
            self._make_msg("call me at 0551234567"),
        ]
        masked = mask_conversation_messages(msgs, reader_id=2, is_admin=True)
        self.assertIn("0551234567", masked[0])
