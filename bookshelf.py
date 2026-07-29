import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class BookshelfError(RuntimeError):
    """Reject a corrupt final book or an invalid bookshelf projection."""


def current_utc_time() -> str:
    """Return an ISO 8601 timestamp in UTC."""
    return datetime.now(timezone.utc).isoformat()


def file_sha256(path: Path) -> str:
    """Hash one required bookshelf input."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise BookshelfError(f"Could not hash required file: {path}") from error


def read_json_object(path: Path, label: str) -> dict:
    """Read one required JSON object."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise BookshelfError(f"{label} could not be read: {path}") from error
    if not isinstance(document, dict):
        raise BookshelfError(f"{label} must contain a JSON object.")
    return document


def required_text(value: Any, label: str) -> str:
    """Return one required non-empty string."""
    if not isinstance(value, str) or not value.strip():
        raise BookshelfError(f"{label} must be a non-empty string.")
    return value.strip()


def required_string_list(value: Any, label: str) -> list[str]:
    """Return one required list of unique non-empty strings."""
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise BookshelfError(f"{label} must contain non-empty strings.")
    normalized = [item.strip() for item in value]
    if len(normalized) != len(set(normalized)):
        raise BookshelfError(f"{label} contains duplicate values.")
    return normalized


def resolve_recorded_file(
    project_path: Path,
    recorded_path: Any,
    required_parent: Path,
    label: str,
) -> Path:
    """Resolve a recorded project path without permitting directory escape."""
    relative_path = required_text(recorded_path, f"{label} path")
    path = (project_path / relative_path).resolve()
    try:
        path.relative_to(required_parent.resolve())
    except ValueError as error:
        raise BookshelfError(
            f"{label} is outside {required_parent}: {relative_path}"
        ) from error
    if not path.is_file():
        raise BookshelfError(f"{label} does not exist: {path}")
    return path


def verify_recorded_hash(path: Path, expected_hash: Any, label: str) -> None:
    """Require a recorded SHA-256 digest to match the current file."""
    digest = required_text(expected_hash, f"{label} SHA-256")
    if len(digest) != 64 or file_sha256(path) != digest:
        raise BookshelfError(f"{label} hash does not match: {path}")


def load_knowledge_graph(knowledge_path: Path) -> dict:
    """Load and validate the derived graph used for deterministic linkage."""
    graph_path = knowledge_path / "graph.json"
    graph = read_json_object(graph_path, "Knowledge graph")
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        raise BookshelfError("Knowledge graph has no valid node list.")

    node_ids = []
    for node in nodes:
        if not isinstance(node, dict):
            raise BookshelfError("Knowledge graph contains an invalid node.")
        node_id = required_text(node.get("id"), "Knowledge node ID")
        source_review_ids = node.get("source_review_ids")
        if not isinstance(source_review_ids, list) or any(
            not isinstance(review_id, str) or not review_id.strip()
            for review_id in source_review_ids
        ):
            raise BookshelfError(
                f"Knowledge node {node_id} has invalid review provenance."
            )
        node_ids.append(node_id)
    if len(node_ids) != len(set(node_ids)):
        raise BookshelfError("Knowledge graph contains duplicate node IDs.")
    return graph


def manifest_input(candidate_assembly: dict) -> dict:
    """Return the one hash-bound manifest input from a candidate assembly."""
    inputs = candidate_assembly.get("inputs")
    if not isinstance(inputs, list):
        raise BookshelfError("Candidate assembly has no valid input ledger.")
    matches = [
        item
        for item in inputs
        if isinstance(item, dict) and item.get("role") == "manifest"
    ]
    if len(matches) != 1:
        raise BookshelfError(
            "Candidate assembly must reference exactly one manifest."
        )
    return matches[0]


def validate_chapters(assembly: dict) -> list[dict]:
    """Return ordered frontend metadata for the final assembled chapters."""
    chapters = assembly.get("chapters")
    chapter_count = assembly.get("chapter_count")
    if (
        not isinstance(chapters, list)
        or not chapters
        or chapter_count != len(chapters)
    ):
        raise BookshelfError("Final assembly has an invalid chapter ledger.")

    projected = []
    chapter_ids = []
    for position, chapter in enumerate(chapters, start=1):
        if not isinstance(chapter, dict):
            raise BookshelfError("Final assembly contains an invalid chapter.")
        chapter_id = required_text(chapter.get("chapter_id"), "Chapter ID")
        chapter_ids.append(chapter_id)
        projected.append(
            {
                "chapter_id": chapter_id,
                "position": position,
                "title": required_text(
                    chapter.get("working_title"),
                    f"{chapter_id} title",
                ),
                "material_disposition": required_text(
                    chapter.get("material_disposition"),
                    f"{chapter_id} material disposition",
                ),
                "draft_id": required_text(
                    chapter.get("draft_id"),
                    f"{chapter_id} draft ID",
                ),
                "assessment_id": required_text(
                    chapter.get("assessment_id"),
                    f"{chapter_id} assessment ID",
                ),
            }
        )
    if len(chapter_ids) != len(set(chapter_ids)):
        raise BookshelfError("Final assembly contains duplicate chapter IDs.")
    return projected


def project_final_book(
    project_path: Path,
    books_path: Path,
    assembly_path: Path,
    knowledge_graph: dict,
) -> dict:
    """Validate one final assembly and create its bookshelf record."""
    book_directory = assembly_path.parent.resolve()
    try:
        book_directory.relative_to(books_path.resolve())
    except ValueError as error:
        raise BookshelfError(
            f"Assembly is outside the books directory: {assembly_path}"
        ) from error

    assembly = read_json_object(assembly_path, "Final assembly")
    book_id = required_text(assembly.get("book_id"), "Book ID")
    if (
        assembly.get("lifecycle_status") != "final"
        or book_directory.name != book_id
    ):
        raise BookshelfError(
            f"Assembly is not a matching final book: {assembly_path}"
        )

    book_path = resolve_recorded_file(
        project_path,
        assembly.get("book_path"),
        book_directory,
        "Final book",
    )
    verify_recorded_hash(
        book_path,
        assembly.get("book_sha256"),
        "Final book",
    )

    candidate_assembly_path = resolve_recorded_file(
        project_path,
        assembly.get("candidate_assembly_path"),
        book_directory,
        "Candidate assembly",
    )
    verify_recorded_hash(
        candidate_assembly_path,
        assembly.get("candidate_assembly_sha256"),
        "Candidate assembly",
    )
    candidate_assembly = read_json_object(
        candidate_assembly_path,
        "Candidate assembly",
    )
    if (
        candidate_assembly.get("book_id") != book_id
        or candidate_assembly.get("lifecycle_status") != "candidate"
        or candidate_assembly.get("candidate_sha256")
        != assembly.get("candidate_sha256")
        or candidate_assembly.get("chapters") != assembly.get("chapters")
    ):
        raise BookshelfError(
            f"Candidate and final assembly disagree for {book_id}."
        )

    candidate_path = resolve_recorded_file(
        project_path,
        candidate_assembly.get("candidate_path"),
        book_directory,
        "Candidate book",
    )
    verify_recorded_hash(
        candidate_path,
        candidate_assembly.get("candidate_sha256"),
        "Candidate book",
    )
    if (
        assembly.get("candidate_path")
        != candidate_assembly.get("candidate_path")
        or assembly.get("book_sha256")
        != candidate_assembly.get("candidate_sha256")
    ):
        raise BookshelfError(
            f"Final book does not match its approved candidate for {book_id}."
        )

    editorial_path = resolve_recorded_file(
        project_path,
        assembly.get("editorial_review_path"),
        book_directory,
        "Editorial review",
    )
    verify_recorded_hash(
        editorial_path,
        assembly.get("editorial_review_sha256"),
        "Editorial review",
    )
    editorial = read_json_object(editorial_path, "Editorial review")
    if (
        editorial.get("book_id") != book_id
        or editorial.get("decision") != "approved_for_finalization"
        or editorial.get("candidate_sha256") != assembly.get("candidate_sha256")
        or editorial.get("candidate_assembly_sha256")
        != assembly.get("candidate_assembly_sha256")
    ):
        raise BookshelfError(
            f"Final assembly lacks matching human approval for {book_id}."
        )

    manifest_record = manifest_input(candidate_assembly)
    manifest_path = resolve_recorded_file(
        project_path,
        manifest_record.get("path"),
        book_directory,
        "Book manifest",
    )
    verify_recorded_hash(
        manifest_path,
        manifest_record.get("sha256"),
        "Book manifest",
    )
    manifest = read_json_object(manifest_path, "Book manifest")
    if manifest.get("book_id") != book_id:
        raise BookshelfError(f"Manifest book ID does not match {book_id}.")
    review_ids = required_string_list(
        manifest.get("input_review_ids"),
        f"{book_id} input review IDs",
    )

    knowledge_nodes = knowledge_graph["nodes"]
    knowledge_node_ids = sorted(
        required_text(node.get("id"), "Knowledge node ID")
        for node in knowledge_nodes
        if set(node.get("source_review_ids", [])).intersection(review_ids)
    )
    if not knowledge_node_ids:
        raise BookshelfError(
            f"No promoted knowledge nodes match the reviews for {book_id}."
        )

    chapters = validate_chapters(assembly)
    source_receipt_ids = required_string_list(
        assembly.get("source_receipt_ids"),
        f"{book_id} source receipt IDs",
    )
    if assembly.get("source_count") != len(source_receipt_ids):
        raise BookshelfError(f"Source count does not match for {book_id}.")

    relative_assembly_path = assembly_path.relative_to(project_path).as_posix()
    relative_book_path = book_path.relative_to(project_path).as_posix()
    return {
        "work_id": book_id,
        "book_id": book_id,
        "edition": 1,
        "assembly_id": required_text(
            assembly.get("assembly_id"),
            f"{book_id} assembly ID",
        ),
        "artifact_status": "final",
        "shelf_status": "internal_smoke_test",
        "title": required_text(
            manifest.get("working_title"),
            f"{book_id} title",
        ),
        "central_question": required_text(
            manifest.get("central_question"),
            f"{book_id} central question",
        ),
        "book_mode": required_text(
            manifest.get("book_mode"),
            f"{book_id} mode",
        ),
        "book_path": relative_book_path,
        "book_sha256": required_text(
            assembly.get("book_sha256"),
            f"{book_id} book SHA-256",
        ),
        "assembly_path": relative_assembly_path,
        "assembly_sha256": file_sha256(assembly_path),
        "finalized_at": required_text(
            assembly.get("assembled_at"),
            f"{book_id} finalization time",
        ),
        "chapter_count": len(chapters),
        "chapters": chapters,
        "source_count": len(source_receipt_ids),
        "review_ids": review_ids,
        "knowledge_node_ids": knowledge_node_ids,
    }


def render_index(catalog: dict) -> str:
    """Render the human-readable bookshelf from a validated catalog."""
    lines = [
        "# Alexandrian Bookshelf",
        "",
        f"Generated at: `{catalog['generated_at']}`",
        "",
        "This bookshelf is derived from immutable final book assemblies. "
        "The catalog under `catalog.json` is the frontend-facing view.",
        "",
    ]
    for book in catalog["books"]:
        book_link = "../" + book["book_path"].removeprefix("data/")
        lines.extend(
            [
                f"## {book['title']}",
                "",
                f"- Shelf status: `{book['shelf_status']}`",
                f"- Artifact status: `{book['artifact_status']}`",
                f"- Mode: `{book['book_mode']}`",
                f"- Edition: `{book['edition']}`",
                f"- Chapters: `{book['chapter_count']}`",
                f"- Checked sources: `{book['source_count']}`",
                f"- [Read the final book]({book_link})",
                "- [Explore related knowledge](../knowledge/index.md)",
                "",
                "### Central Question",
                "",
                book["central_question"],
                "",
                "### Chapters",
                "",
            ]
        )
        lines.extend(
            f"{chapter['position']}. {chapter['title']} (`{chapter['chapter_id']}`)"
            for chapter in book["chapters"]
        )
        lines.append("")
    return "\n".join(lines)


def write_text_atomically(path: Path, content: str) -> None:
    """Replace one derived view without exposing partial file content."""
    temporary_path = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        temporary_path.write_text(content, encoding="utf-8")
        os.replace(temporary_path, path)
    except OSError as error:
        temporary_path.unlink(missing_ok=True)
        raise BookshelfError(
            f"Bookshelf could not write {path.name}."
        ) from error


def build_bookshelf(
    project_path: Path,
    books_path: Path | None = None,
    knowledge_path: Path | None = None,
    bookshelf_path: Path | None = None,
) -> dict:
    """Validate every final book and rebuild the derived bookshelf views."""
    project_path = project_path.resolve()
    books_path = (
        books_path or project_path / "data" / "books"
    ).resolve()
    knowledge_path = (
        knowledge_path or project_path / "data" / "knowledge"
    ).resolve()
    bookshelf_path = (
        bookshelf_path or project_path / "data" / "bookshelf"
    ).resolve()

    assembly_paths = sorted(books_path.glob("*/assembly.json"))
    knowledge_graph = load_knowledge_graph(knowledge_path)
    books = [
        project_final_book(
            project_path,
            books_path,
            path,
            knowledge_graph,
        )
        for path in assembly_paths
    ]
    book_ids = [book["book_id"] for book in books]
    if len(book_ids) != len(set(book_ids)):
        raise BookshelfError("Bookshelf contains duplicate book IDs.")

    existing_catalog_path = bookshelf_path / "catalog.json"
    existing_catalog = None
    if existing_catalog_path.is_file():
        try:
            candidate = read_json_object(
                existing_catalog_path,
                "Existing bookshelf catalog",
            )
        except BookshelfError:
            candidate = None
        if (
            candidate is not None
            and candidate.get("schema_version") == "1.0"
            and candidate.get("books") == books
        ):
            existing_catalog = candidate

    generated_at = (
        existing_catalog["generated_at"]
        if existing_catalog is not None
        else current_utc_time()
    )
    catalog = {
        "schema_version": "1.0",
        "generated_at": generated_at,
        "book_count": len(books),
        "books": books,
    }
    catalog_content = (
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"
    )
    index_content = render_index(catalog)

    bookshelf_path.mkdir(parents=True, exist_ok=True)
    index_path = bookshelf_path / "index.md"
    if not index_path.is_file() or index_path.read_text(
        encoding="utf-8"
    ) != index_content:
        write_text_atomically(index_path, index_content)
    if not existing_catalog_path.is_file() or existing_catalog_path.read_text(
        encoding="utf-8"
    ) != catalog_content:
        write_text_atomically(existing_catalog_path, catalog_content)

    return {
        "book_count": len(books),
        "catalog_path": existing_catalog_path,
        "index_path": index_path,
        "books": books,
    }


if __name__ == "__main__":
    result = build_bookshelf(Path.cwd())
    print(f"Books: {result['book_count']}")
    print(f"Catalog: {result['catalog_path']}")
    print(f"Index: {result['index_path']}")
