import unittest
from pathlib import Path

import book_synthesis


PROJECT_PATH = Path(__file__).resolve().parents[1]
STANDARD_PATH = (
    PROJECT_PATH / "docs" / "alexandrian-narrative-research-standard.md"
)
SKILL_PATHS = (
    PROJECT_PATH / "skills" / "alexandrian-reviewer" / "SKILL.md",
    PROJECT_PATH / "skills" / "alexandrian-book-planner" / "SKILL.md",
    PROJECT_PATH / "skills" / "alexandrian-chapter-developer" / "SKILL.md",
    PROJECT_PATH / "skills" / "alexandrian-chapter-evaluator" / "SKILL.md",
    PROJECT_PATH / "skills" / "alexandrian-book-synthesizer" / "SKILL.md",
    PROJECT_PATH / "skills" / "alexandrian-claim-auditor" / "SKILL.md",
)


class NarrativeResearchStandardTests(unittest.TestCase):
    def test_new_syntheses_record_the_narrative_profile(self) -> None:
        self.assertEqual(
            book_synthesis.PROFILE_NAME,
            "Alexandrian Narrative Research Book",
        )
        self.assertEqual(book_synthesis.PROFILE_VERSION, "1.0")

    def test_canonical_standard_preserves_reading_and_evidence_layers(self) -> None:
        standard = STANDARD_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "Read like a compelling book. Inspect like a research artifact.",
            standard,
        )
        self.assertIn("## Evidence architecture", standard)
        self.assertIn("## Technical clarity boundary", standard)

    def test_every_book_stage_names_the_narrative_standard(self) -> None:
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name):
                skill = skill_path.read_text(encoding="utf-8")
                normalized_skill = " ".join(skill.split())
                self.assertIn(
                    "Alexandrian Narrative Research Book",
                    normalized_skill,
                )

    def test_synthesizer_uses_the_governing_principle(self) -> None:
        synthesizer = SKILL_PATHS[4].read_text(encoding="utf-8")
        self.assertIn(
            "Read like a compelling book. Inspect like a research artifact.",
            synthesizer,
        )
        self.assertNotIn(
            "## Proposed Alexandrian accessibility profile",
            synthesizer,
        )

    def test_readme_links_the_canonical_standard(self) -> None:
        readme = (PROJECT_PATH / "README.md").read_text(encoding="utf-8")
        self.assertIn(
            "docs/alexandrian-narrative-research-standard.md",
            readme,
        )


if __name__ == "__main__":
    unittest.main()
