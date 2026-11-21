"""Tests for the writing pipeline gates. Run: python3 -m unittest discover -s tools/writing -v"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline as P  # noqa: E402

DRAFT = ("Accountancy (Code 055) is a cornerstone of Class XII CBSE. The theory paper carries 80 marks, "
         "and Remembering and Understanding accounts for 32 marks, which is 40 percent.")


class PreservationGate(unittest.TestCase):
    def test_faithful_rewrite_passes(self):
        final = ("Accountancy (Code 055) is a core subject in Class XII CBSE. The theory paper carries 80 marks. "
                 "Remembering and Understanding accounts for 32 marks, which is 40 percent.")
        self.assertTrue(P.check_preservation(DRAFT, final)["pass"])

    def test_dropped_number_fails(self):
        final = "Accountancy (Code 055) is a core subject in Class XII CBSE. The theory paper carries many marks."
        res = P.check_preservation(DRAFT, final)
        self.assertFalse(res["pass"])
        self.assertIn("missing_numbers", res["fail"])

    def test_invented_name_fails(self):
        final = DRAFT + " Priya Sharma recommends it."
        res = P.check_preservation(DRAFT, final)
        self.assertFalse(res["pass"])
        self.assertIn("added_names", res["fail"])

    def test_changed_figure_fails(self):
        final = DRAFT.replace("80 marks", "90 marks")
        res = P.check_preservation(DRAFT, final)
        self.assertFalse(res["pass"])
        self.assertIn("missing_numbers", res["fail"])
        self.assertIn("added_numbers", res["fail"])

    def test_dropped_protected_term_fails(self):
        final = DRAFT.replace("CBSE", "the board")
        self.assertIn("missing_terms", P.check_preservation(DRAFT, final)["fail"])

    def test_dropped_url_fails(self):
        d = "See https://cbseacademic.nic.in for the syllabus."
        self.assertIn("missing_urls", P.check_preservation(d, "See the site for the syllabus.")["fail"])


class ClaimExtraction(unittest.TestCase):
    def test_greetings_and_signatures_are_not_claims(self):
        text = "Dear Sir/Madam,\n\nThe exam begins on 15/11/2026.\n\nYours sincerely,\nPrincipal, [School Name]"
        claims = P.split_claims(text)
        self.assertEqual(claims, ["The exam begins on 15/11/2026."])

    def test_plain_sentence_without_facts_is_not_a_claim(self):
        self.assertEqual(P.split_claims("Teachers can plan their time well."), [])


class Profiles(unittest.TestCase):
    def test_every_content_type_maps_to_valid_detector_context(self):
        valid = {"general", "technical", "marketing", "personal"}
        for name, prof in P.PROFILES["content_types"].items():
            self.assertIn(prof["detector_context"], valid, name)
            self.assertGreater(prof["max_score"], 0, name)

    def test_detector_is_vendored_and_runs(self):
        self.assertTrue(P.DETECTOR.is_file())
        res = P.run_detector(Path(__file__).resolve().parent / "samples" / "conversational_draft.md", "personal")
        self.assertIsInstance(res["score"], int)


if __name__ == "__main__":
    unittest.main()
