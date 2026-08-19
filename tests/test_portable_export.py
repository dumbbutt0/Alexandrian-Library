import json
import unittest
import zipfile
from io import BytesIO

import portable_export


class PortableExportTests(unittest.TestCase):
    def test_evidence_manifest_preserves_book_evidence_and_policy_identity(self) -> None:
        manifest = portable_export.evidence_manifest(
            book={
                "book_id": "book-test",
                "title": "A Verified Book",
                "edition": 1,
                "chapter_count": 2,
                "verified_sha256": "a" * 64,
                "assembly": {"assembled_at": "2026-01-01T00:00:00+00:00"},
            },
            provenance={
                "source_count": 1,
                "sources": [{"receipt_id": "source-one"}],
                "evidence_status_count": 0,
                "evidence_statuses": [],
                "evidence_gaps": [],
            },
            run_receipt={
                "status": "complete",
                "workflow_policy_version": "policy-v1",
                "finish_guardrails": {"enabled": True},
            },
        )
        self.assertEqual(manifest["schema_version"], "alexandrian-portable-evidence.v1")
        self.assertEqual(manifest["evidence"]["sources"][0]["receipt_id"], "source-one")
        self.assertEqual(manifest["workflow"]["workflow_policy_version"], "policy-v1")
        self.assertEqual(manifest["generated_at"], "2026-01-01T00:00:00+00:00")

    def test_epub_contains_book_navigation_and_portable_evidence(self) -> None:
        evidence = {
            "schema_version": "alexandrian-portable-evidence.v1",
            "generated_at": "2026-01-01T00:00:00+00:00",
            "evidence": {"source_count": 1},
        }
        payload = portable_export.epub_bytes(
            book_id="book-test",
            title="A Verified Book",
            markdown="# A Verified Book\n\nEvidence **remains inspectable**.",
            evidence=evidence,
        )
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            self.assertEqual(archive.namelist()[0], "mimetype")
            self.assertEqual(
                archive.read("mimetype"),
                b"application/epub+zip",
            )
            self.assertIn(
                b"Evidence <strong>remains inspectable</strong>",
                archive.read("EPUB/book.xhtml"),
            )
            exported_evidence = archive.read("EPUB/evidence.xhtml").decode("utf-8")
            self.assertIn(json.dumps("alexandrian-portable-evidence.v1"), exported_evidence)

    def test_safe_export_name_has_no_path_components(self) -> None:
        self.assertEqual(
            portable_export.safe_export_name("A Book: Why / How?", "epub"),
            "a-book-why-how.epub",
        )


if __name__ == "__main__":
    unittest.main()
