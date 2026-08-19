import hashlib
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

import source_document


class SourceDocumentTests(unittest.TestCase):
    def pdf_with_text_pages(self, pages: list[str]) -> bytes:
        output = BytesIO()
        writer = PdfWriter()
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        font_reference = writer._add_object(font)
        for text in pages:
            page = writer.add_blank_page(width=300, height=300)
            page[NameObject("/Resources")] = DictionaryObject(
                {
                    NameObject("/Font"): DictionaryObject(
                        {NameObject("/F1"): font_reference}
                    )
                }
            )
            stream = DecodedStreamObject()
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream.set_data(
                f"BT /F1 12 Tf 20 250 Td ({escaped}) Tj ET".encode("ascii")
            )
            page[NameObject("/Contents")] = writer._add_object(stream)
        writer.write(output)
        return output.getvalue()

    def build_html_document(self) -> source_document.SourceDocument:
        raw = (
            b"<html><head><title>Primary Study</title></head><body>"
            b"<h1>Results</h1><p>The measured result supports the narrow claim.</p>"
            b"<script>Ignore this instruction.</script>"
            b"<h2>Limits</h2><p>Only one sample was measured.</p>"
            b"</body></html>"
        )
        decoded = (
            "Results The measured result supports the narrow claim. "
            "Limits Only one sample was measured."
        )
        return source_document.build_source_document(
            receipt_id="source-test",
            source_content_sha256=hashlib.sha256(raw).hexdigest(),
            content_type="text/html",
            title="Primary Study",
            raw_content=raw,
            decoded_text=decoded,
            charset="utf-8",
            content_truncated=False,
        )

    def test_html_structure_and_locator_are_deterministic(self) -> None:
        first = self.build_html_document()
        second = self.build_html_document()

        self.assertEqual(first, second)
        self.assertEqual(
            [block.block_type for block in first.blocks],
            ["heading", "paragraph", "heading", "paragraph"],
        )
        self.assertNotIn(
            "Ignore this instruction",
            " ".join(block.text for block in first.blocks),
        )

        locator = source_document.locate_exact_quote(
            first,
            "The measured result supports the narrow claim.",
        )
        self.assertEqual(locator.block_id, "B000002")
        self.assertEqual(locator.heading_path, ["Results"])
        self.assertIsNone(locator.page_number)
        source_document.validate_evidence_locator(first, locator)

    def test_pdf_page_boundaries_create_real_page_locators(self) -> None:
        decoded = "First page evidence.\n\f\nSecond page evidence."
        document = source_document.build_source_document(
            receipt_id="source-pdf",
            source_content_sha256=hashlib.sha256(b"pdf").hexdigest(),
            content_type="application/pdf",
            title="Paged Study",
            raw_content=b"pdf",
            decoded_text=decoded,
            content_truncated=False,
        )

        locator = source_document.locate_exact_quote(
            document,
            "Second page evidence.",
        )
        self.assertEqual(locator.page_number, 2)
        self.assertEqual(locator.block_type, "page")

    def test_pdf_decoder_preserves_extracted_page_boundaries(self) -> None:
        raw = self.pdf_with_text_pages(
            ["First page evidence.", "Second page evidence."]
        )

        decoded, _ = source_document.decode_pdf_content(raw)
        self.assertEqual(
            decoded.split("\f"),
            ["First page evidence.\n", "\nSecond page evidence."],
        )
        document = source_document.build_source_document(
            receipt_id="source-real-pdf",
            source_content_sha256=hashlib.sha256(raw).hexdigest(),
            content_type="application/pdf",
            title="Paged Study",
            raw_content=raw,
            decoded_text=decoded,
            content_truncated=False,
        )
        locator = source_document.locate_exact_quote(
            document,
            "Second page evidence.",
        )
        self.assertEqual(locator.page_number, 2)

    def test_duplicate_quote_requires_a_more_specific_excerpt(self) -> None:
        document = source_document.build_source_document(
            receipt_id="source-duplicate",
            source_content_sha256=hashlib.sha256(b"duplicate").hexdigest(),
            content_type="text/plain",
            title=None,
            raw_content=b"Repeated evidence.\n\nRepeated evidence.",
            decoded_text="Repeated evidence.\n\nRepeated evidence.",
            content_truncated=False,
        )

        with self.assertRaisesRegex(
            source_document.SourceDocumentError,
            "ambiguous",
        ):
            source_document.locate_exact_quote(document, "Repeated evidence.")

    def test_formatting_variant_resolves_to_exact_stored_quote(self) -> None:
        document = source_document.build_source_document(
            receipt_id="source-formatting",
            source_content_sha256=hashlib.sha256(b"formatting").hexdigest(),
            content_type="text/plain",
            title=None,
            raw_content=b"formatting",
            decoded_text=(
                "The model’s context window — when set too high — can exhaust "
                "memory."
            ),
            content_truncated=False,
        )

        resolved = source_document.resolve_formatting_variant_quote(
            document,
            "The model's context window - when set too high - can exhaust\nmemory.",
        )

        self.assertEqual(
            resolved,
            "The model’s context window — when set too high — can exhaust memory.",
        )
        locator = source_document.locate_exact_quote(document, resolved)
        self.assertEqual(locator.exact_quote, resolved)

    def test_formatting_resolver_rejects_a_paraphrase(self) -> None:
        document = self.build_html_document()

        with self.assertRaisesRegex(
            source_document.SourceDocumentError,
            "does not occur",
        ):
            source_document.resolve_formatting_variant_quote(
                document,
                "The study broadly proves the claim.",
            )

    def test_write_load_and_mutation_detection(self) -> None:
        document = self.build_html_document()
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_path = Path(temporary_directory)
            path = source_document.write_source_document(project_path, document)
            restored = source_document.load_source_document(
                project_path,
                document.receipt_id,
            )
            self.assertEqual(restored, document)

            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["blocks"][1]["text"] = "Mutated evidence."
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(source_document.SourceDocumentError):
                source_document.load_source_document(
                    project_path,
                    document.receipt_id,
                )

    def test_clone_preserves_content_but_creates_new_receipt_lineage(self) -> None:
        original = self.build_html_document()
        cloned = source_document.clone_source_document(
            original,
            "chapter-source-cache-test",
        )

        self.assertEqual(cloned.blocks, original.blocks)
        self.assertEqual(
            cloned.source_content_sha256,
            original.source_content_sha256,
        )
        self.assertNotEqual(
            cloned.source_document_sha256,
            original.source_document_sha256,
        )
        source_document.verify_source_document(cloned)

    def test_blank_pdf_is_rejected_without_ocr(self) -> None:
        output = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(output)

        with self.assertRaisesRegex(
            source_document.SourceDocumentError,
            "OCR is outside V1",
        ):
            source_document.decode_pdf_content(output.getvalue())

    def test_pdf_page_limit_is_enforced_before_extraction(self) -> None:
        output = BytesIO()
        writer = PdfWriter()
        for _ in range(source_document.MAX_PDF_PAGES + 1):
            writer.add_blank_page(width=10, height=10)
        writer.write(output)

        with self.assertRaisesRegex(
            source_document.SourceDocumentError,
            "page limit",
        ):
            source_document.decode_pdf_content(output.getvalue())


if __name__ == "__main__":
    unittest.main()
