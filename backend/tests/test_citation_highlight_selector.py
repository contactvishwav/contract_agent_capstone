"""Regression coverage for the citation over-highlighting bug (independent
audit finding #1): chat_evidence_service.py's excerpt is capped at 1200
characters and was being used verbatim as the PDF highlight target, so a
one-sentence answer highlighted up to a full page.

REAL_MSA_EXCERPT below is the actual 1200-character excerpt captured live
from a real Clean_MSA.pdf upload + real Gemini chat turn during the audit
(GET /api/chat/sessions/{id}, citation.excerpt) - not a hand-picked short
fixture. Using the real shape is the point: the previous test suite's
31-character fixtures never exercised this bug at all.
"""

import unittest

from backend.application.services.citation_highlight_selector import select_claim_highlight

REAL_MSA_EXCERPT = (
    "MASTER SERVICES AGREEMENT Between ClientCo (“Client”) and ConsultCorp (“Company”) "
    "1. Scope of Services Company will provide consulting, engineering, and implementation services "
    "as requested by Client from time to time. "
    "2. Payment Terms Client shall pay all invoices within ninety (90) days of receipt. "
    "Payment is subject to Client’s sole discretion and satisfaction with the deliverables. "
    "3. Indemnification Company shall indemnify, defend, and hold harmless Client from any and all "
    "claims, damages, losses, liabilities, and expenses, including those arising from Client’s own "
    "negligence or misuse of the deliverables. "
    "4. Limitation of Liability Company’s liability shall be unlimited and shall include all direct, "
    "indirect, incidental, punitive, consequential, and special damages. "
    "5. Intellectual Property Rights All deliverables, including Company’s tools, templates, "
    "methodologies, and reusable components, shall become the exclusive property of Client upon "
    "creation. "
    "6. Termination Client may terminate this Agreement at any time without notice and without "
    "payment for work performed prior to termination. "
    "7. Data Privacy & Security Company shall comply with all applicable global data protectio"
)

REAL_ANSWER = (
    "The payment terms in this contract state that the Client must pay all invoices within ninety "
    "(90) days of receipt. Additionally, payment is subject to the Client's sole discretion and "
    "satisfaction with the deliverables."
)


class RealisticFullLengthExcerptTests(unittest.TestCase):
    """Exercises the real, reported failure shape - a ~1200-char, 7-section
    retrieved chunk - not the old test suite's short hand-picked strings."""

    def test_narrows_a_full_page_chunk_down_to_the_supporting_sentences(self):
        highlight = select_claim_highlight(REAL_MSA_EXCERPT, REAL_ANSWER)
        self.assertIsNotNone(highlight)
        self.assertLess(
            len(highlight), len(REAL_MSA_EXCERPT) * 0.35,
            "narrowed highlight must be a small fraction of the full 1200-char excerpt, "
            "not most of a page",
        )
        self.assertIn("ninety (90) days", highlight)
        self.assertIn("sole discretion", highlight)
        # The unrelated sections must not be swept into the highlight just
        # because they were part of the same retrieved chunk.
        self.assertNotIn("Indemnification", highlight)
        self.assertNotIn("Limitation of Liability", highlight)
        self.assertNotIn("Intellectual Property", highlight)
        self.assertNotIn("Termination", highlight)

    def test_narrowed_text_is_a_verbatim_contiguous_substring_of_the_excerpt(self):
        """Must remain something locate_unique_page_match can actually find
        as a single exact substring on the real PDF page - never sentences
        stitched together out of order or from non-adjacent parts."""
        highlight = select_claim_highlight(REAL_MSA_EXCERPT, REAL_ANSWER)
        self.assertIn(highlight, REAL_MSA_EXCERPT)

    def test_single_fact_answer_selects_only_its_own_sentence(self):
        highlight = select_claim_highlight(
            REAL_MSA_EXCERPT,
            "The contract states the Client shall pay all invoices within ninety (90) days of receipt.",
        )
        self.assertIsNotNone(highlight)
        self.assertIn("ninety (90) days", highlight)
        self.assertNotIn("sole discretion", highlight)
        self.assertLess(len(highlight), 150)


class FallbackToFullExcerptTests(unittest.TestCase):
    """The conservative half of the design: never guess. If nothing scores
    confidently, return None so the caller keeps today's full-excerpt
    behavior rather than highlighting something unrelated."""

    def test_no_textual_overlap_returns_none(self):
        highlight = select_claim_highlight(
            REAL_MSA_EXCERPT,
            "I could not find any information about training hours in this contract.",
        )
        self.assertIsNone(highlight)

    def test_missing_excerpt_returns_none(self):
        self.assertIsNone(select_claim_highlight(None, REAL_ANSWER))
        self.assertIsNone(select_claim_highlight("", REAL_ANSWER))

    def test_missing_answer_text_returns_none(self):
        self.assertIsNone(select_claim_highlight(REAL_MSA_EXCERPT, None))
        self.assertIsNone(select_claim_highlight(REAL_MSA_EXCERPT, ""))

    def test_single_sentence_excerpt_has_nothing_to_narrow(self):
        short = "Payment is due within 90 days of invoice receipt."
        self.assertIsNone(select_claim_highlight(short, REAL_ANSWER))

    def test_short_generic_sentence_does_not_spuriously_match_a_long_answer(self):
        """A short, near-content-free sentence like a bare section header
        must not "match" purely because a long answer statistically
        contains a couple of its words."""
        excerpt = (
            "3. Indemnification. "
            "Client shall pay all invoices within ninety (90) days of receipt of a valid invoice "
            "from Company for services actually rendered under this agreement."
        )
        highlight = select_claim_highlight(excerpt, "Tell me about the indemnification clause.")
        # "Indemnification." alone shares only one content word with the
        # answer - must not qualify (ratio/absolute-count bar).
        self.assertIsNone(highlight)


class JoinedAdjacentSentencesCapTests(unittest.TestCase):
    def test_join_is_capped_and_never_grows_unbounded(self):
        long_excerpt = " ".join(
            f"Sentence number {i} restates that Client shall pay all invoices within ninety (90) "
            f"days of receipt for services rendered under this agreement."
            for i in range(1, 6)
        )
        answer = "Client shall pay all invoices within ninety (90) days of receipt."
        highlight = select_claim_highlight(long_excerpt, answer)
        self.assertIsNotNone(highlight)
        self.assertLessEqual(len(highlight), 500)


if __name__ == "__main__":
    unittest.main()
