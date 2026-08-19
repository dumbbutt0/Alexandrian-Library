import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import cover_design


class CoverDesignTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project_path = Path(self.temporary_directory.name)
        self.book_id = "book-test"
        self.book_directory = (
            self.project_path / "data" / "books" / self.book_id
        )
        self.book_directory.mkdir(parents=True)
        self.book_text = (
            "# A Final Test Book\n\n"
            "The visible branch remains recoverable after the boundary.\n"
        )
        book_path = self.book_directory / "book.md"
        book_path.write_text(self.book_text, encoding="utf-8")
        book_hash = hashlib.sha256(book_path.read_bytes()).hexdigest()
        (self.book_directory / "manifest.json").write_text(
            '{"book_id":"book-test","working_title":"A Final Test Book",'
            '"central_question":"What survives the boundary?"}\n',
            encoding="utf-8",
        )
        (self.book_directory / "assembly.json").write_text(
            '{"book_id":"book-test","book_sha256":"' + book_hash + '"}\n',
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def proposal(self) -> dict:
        assembly_path = self.book_directory / "assembly.json"
        return {
            "book_id": self.book_id,
            "assembly_sha256": hashlib.sha256(
                assembly_path.read_bytes()
            ).hexdigest(),
            "core_question": "What survives the boundary?",
            "question_tension": (
                "A decision crosses the boundary, but its provenance must remain "
                "visible enough for another reader to recover."
            ),
            "dominant_symbol": "branch lattice",
            "diversity_statement": (
                "The branch lattice and single boundary beam are specific to this "
                "question and avoid the dominant objects of the recent shelf covers."
            ),
            "quote": "The visible branch remains recoverable after the boundary.",
            "concept": (
                "A visible branch crosses one boundary while its provenance "
                "remains attached and inspectable."
            ),
            "visual_motifs": [
                "branch lattice",
                "boundary beam",
                "archive frame",
            ],
            "palette": ["#05070D", "#D1AD64", "#176BCE", "#E8DDC4"],
            "image_prompt": (
                "Create an original title-free illustrated editorial collage about "
                "a visible decision branch crossing a durable boundary. Use an "
                "archive frame, a single luminous boundary beam, layered torn-paper "
                "fields, filled stained-glass color masses, and a precise branch "
                "lattice whose provenance remains attached. Preserve dark quiet "
                "zones for live HTML typography and a strong central silhouette at "
                "thumbnail size. Use carbon black, brass, cobalt, and parchment. "
                "Include no words, logos, watermarks, copied artwork, eyes, hands, "
                "or embedded book mockups."
            ),
            "fallback_style": "archive_geometry",
        }

    def test_submit_and_verify_cover_design(self) -> None:
        document = cover_design.submit_design(
            self.project_path,
            self.proposal(),
            "codex",
        )
        self.assertEqual(document["asset"]["status"], "fallback")
        self.assertEqual(
            document["asset"]["render_profile"],
            cover_design.GRAPHICAL_FALLBACK_PROFILE,
        )
        fallback = (
            self.project_path / document["asset"]["path"]
        ).read_text(encoding="utf-8")
        self.assertIn(
            f'data-render-profile="{cover_design.GRAPHICAL_FALLBACK_PROFILE}"',
            fallback,
        )
        self.assertIn('fill="url(#vault)"', fallback)
        self.assertTrue(
            (self.project_path / document["asset"]["path"]).is_file()
        )
        verified = cover_design.load_and_verify(
            self.project_path,
            self.book_id,
        )
        self.assertEqual(verified["design_sha256"], document["design_sha256"])
        self.assertEqual(
            verified["core_question"],
            "What survives the boundary?",
        )
        self.assertEqual(verified["topic_terms"], ["boundary", "survives"])
        self.assertIn("boundary", verified["prompt_topic_terms"])

    def test_rejects_a_core_question_not_bound_to_the_manifest(self) -> None:
        proposal = self.proposal()
        proposal["core_question"] = "What would make a prettier cover?"

        with self.assertRaisesRegex(
            cover_design.CoverDesignError,
            "exactly match the final manifest",
        ):
            cover_design.submit_design(self.project_path, proposal, "codex")

    def test_rejects_the_recent_cover_dominant_symbol(self) -> None:
        prior_directory = (
            self.project_path / "data" / "books" / "book-prior" / "cover"
        )
        prior_directory.mkdir(parents=True)
        (prior_directory / "design.json").write_text(
            json.dumps(
                {
                    "book_id": "book-prior",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "dominant_symbol": "branch lattice",
                    "visual_motifs": ["branch lattice"],
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            cover_design.CoverDesignError,
            "four most recent covers",
        ):
            cover_design.submit_design(
                self.project_path,
                self.proposal(),
                "codex",
            )

    def test_rejects_invented_quote(self) -> None:
        proposal = self.proposal()
        proposal["quote"] = "This sentence was never present in the final book."
        with self.assertRaisesRegex(
            cover_design.CoverDesignError,
            "not exact final-book text",
        ):
            cover_design.submit_design(
                self.project_path,
                proposal,
                "codex",
            )

    def test_rejects_generic_prompt_not_bound_to_core_question(self) -> None:
        proposal = self.proposal()
        proposal["image_prompt"] = (
            "Create an original title-free illustrated editorial collage using "
            "dense torn paper, stained glass color masses, archival texture, and "
            "one large filled symbolic object. Keep quiet regions for live HTML "
            "typography and preserve strong value grouping at thumbnail size. "
            "Use an original composition with no words, logos, watermarks, eyes, "
            "hands, copied artwork, or embedded book mockups. Treat the visual "
            "metaphor as presentation rather than evidence."
        )

        with self.assertRaisesRegex(
            cover_design.CoverDesignError,
            "topic-specific term",
        ):
            cover_design.submit_design(
                self.project_path,
                proposal,
                "codex",
            )

    def test_generated_asset_replaces_fallback_with_hash_binding(self) -> None:
        cover_design.submit_design(
            self.project_path,
            self.proposal(),
            "codex",
        )
        generated = self.project_path / "source.jpg"
        generated.write_bytes(b"generated-cover")
        document = cover_design.bind_generated_asset(
            self.project_path,
            self.book_id,
            generated,
            generated_by="test",
        )
        self.assertEqual(document["asset"]["status"], "generated")
        self.assertEqual(
            (self.project_path / document["asset"]["path"]).read_bytes(),
            b"generated-cover",
        )

    def test_rejects_outline_only_art_direction(self) -> None:
        proposal = self.proposal()
        proposal["image_prompt"] += (
            " Render the whole composition as minimal line art."
        )
        with self.assertRaisesRegex(
            cover_design.CoverDesignError,
            "outline-only line treatment",
        ):
            cover_design.submit_design(
                self.project_path,
                proposal,
                "codex",
            )

    def test_accepts_an_explicit_line_art_prohibition(self) -> None:
        proposal = self.proposal()
        proposal["image_prompt"] += " Use dense collage, not line art."

        document = cover_design.submit_design(
            self.project_path,
            proposal,
            "codex",
        )

        self.assertEqual(document["asset"]["status"], "fallback")

    def test_refresh_upgrades_a_verified_legacy_fallback(self) -> None:
        document = cover_design.submit_design(
            self.project_path,
            self.proposal(),
            "codex",
        )
        fallback_path = self.project_path / document["asset"]["path"]
        fallback_path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg"></svg>\n',
            encoding="utf-8",
        )
        document["asset"].pop("render_profile")
        document["asset"]["sha256"] = cover_design.file_sha256(
            fallback_path
        )
        document["design_sha256"] = cover_design.canonical_json_sha256(
            document,
            "design_sha256",
        )
        cover_design.design_path(
            self.project_path,
            self.book_id,
        ).write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        refreshed = cover_design.refresh_fallback_asset(
            self.project_path,
            self.book_id,
        )

        self.assertEqual(
            refreshed["asset"]["render_profile"],
            cover_design.GRAPHICAL_FALLBACK_PROFILE,
        )
        self.assertIn(
            cover_design.GRAPHICAL_FALLBACK_PROFILE,
            fallback_path.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
