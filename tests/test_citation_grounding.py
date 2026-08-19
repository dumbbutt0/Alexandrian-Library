import unittest

import citation_grounding


class CitationGroundingTests(unittest.TestCase):
    def test_chapter_requires_distinct_markers_for_sourced_claims(self) -> None:
        claims = [
            {"claim_id": "C1", "source_receipt_ids": ["R1"]},
            {"claim_id": "C2", "source_receipt_ids": ["R1", "R2"]},
            {"claim_id": "C3", "source_receipt_ids": []},
        ]
        packets = [{"receipt_id": "R1"}, {"receipt_id": "R2"}]
        result = citation_grounding.validate_chapter_markdown(
            "First claim. [Evidence: R1]\n\nSecond claim. [Evidence: R2]",
            claims,
            packets,
            [],
        )
        self.assertEqual(result["marker_count"], 2)
        self.assertEqual(result["grounded_claim_count"], 2)
        self.assertEqual(result["cited_receipt_ids"], ["R1", "R2"])

        legacy = citation_grounding.parse_evidence_markers(
            "Legacy marker. [Evidence: `R1`, `R2`]"
        )
        self.assertEqual(legacy[0].receipt_ids, ("R1", "R2"))

        with self.assertRaisesRegex(
            citation_grounding.CitationGroundingError,
            "Missing: C2",
        ):
            citation_grounding.validate_chapter_markdown(
                "Both claims share one marker. [Evidence: R1]",
                claims,
                packets,
                [],
            )

    def test_chapter_rejects_unknown_receipt_and_hidden_contradiction(self) -> None:
        with self.assertRaisesRegex(
            citation_grounding.CitationGroundingError,
            "outside the approved context",
        ):
            citation_grounding.validate_chapter_markdown(
                "Unsupported. [Evidence: UNKNOWN]",
                [],
                [{"receipt_id": "R1"}],
                [],
            )

        with self.assertRaisesRegex(
            citation_grounding.CitationGroundingError,
            "visibly marked",
        ):
            citation_grounding.validate_chapter_markdown(
                "The sources disagree.",
                [],
                [],
                [{"claim_ids": ["C1"]}],
            )

        result = citation_grounding.validate_chapter_markdown(
            "> [!CONTRADICTION] The sources disagree.",
            [],
            [],
            [{"claim_ids": ["C1"]}],
        )
        self.assertEqual(result["contradiction_callout_count"], 1)

    def test_synthesis_requires_citation_beside_exact_mapping(self) -> None:
        claims = [
            {
                "claim_id": "C1",
                "source_receipt_ids": ["R1"],
                "resolution_status": "established",
            }
        ]
        mappings = [
            {
                "claim_id": "C1",
                "edition_excerpt": "The measured effect was bounded.",
                "edition_location": "Chapter 1",
            }
        ]
        result = citation_grounding.validate_synthesis_markdown(
            "The measured effect was bounded. [Evidence: R1]",
            claims,
            mappings,
        )
        self.assertEqual(result["grounded_claim_count"], 1)

        with self.assertRaisesRegex(
            citation_grounding.CitationGroundingError,
            "same paragraph",
        ):
            citation_grounding.validate_synthesis_markdown(
                "The measured effect was bounded.\n\n[Evidence: R1]",
                claims,
                mappings,
            )

    def test_synthesis_accepts_inventory_evidence_receipt_field(self) -> None:
        claims = [
            {
                "claim_id": "SC1",
                "evidence_receipt_ids": ["R1"],
                "resolution_status": "established",
            }
        ]
        mappings = [
            {
                "claim_id": "SC1",
                "edition_excerpt": "The accepted chapter claim remains bounded.",
                "edition_location": "Chapter 1",
            }
        ]

        result = citation_grounding.validate_synthesis_markdown(
            "The accepted chapter claim remains bounded. [Evidence: R1]",
            claims,
            mappings,
        )

        self.assertEqual(result["grounded_claim_count"], 1)

    def test_contradicted_synthesis_claim_stays_visible(self) -> None:
        claims = [
            {
                "claim_id": "C1",
                "source_receipt_ids": ["R1"],
                "resolution_status": "contradicted",
            }
        ]
        mappings = [
            {
                "claim_id": "C1",
                "edition_excerpt": "The original explanation did not hold.",
                "edition_location": "Chapter 1",
            }
        ]
        result = citation_grounding.validate_synthesis_markdown(
            "> [!CONTRADICTION] The original explanation did not hold. "
            "[Evidence: R1]",
            claims,
            mappings,
        )
        self.assertEqual(result["contradiction_callout_count"], 1)


if __name__ == "__main__":
    unittest.main()
