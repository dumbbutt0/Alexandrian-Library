import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import book_synthesis


class BookAssemblyError(RuntimeError):
    """Reject an incomplete, changed, or internally inconsistent book."""


def current_utc_time() -> str:
    """Return an ISO 8601 timestamp in UTC."""
    return datetime.now(timezone.utc).isoformat()


def file_sha256(path: Path) -> str:
    """Hash one immutable assembly input or output."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise BookAssemblyError(f"Could not hash required file: {path}") from error


def canonical_json_sha256(document: dict, hash_field: str) -> str:
    """Hash one canonical JSON document without its recorded digest."""
    hashable = {
        key: value for key, value in document.items() if key != hash_field
    }
    encoded = json.dumps(
        hashable,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def read_json_object(path: Path, label: str) -> dict:
    """Read one required JSON object."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise BookAssemblyError(f"{label} could not be read: {path}") from error
    if not isinstance(document, dict):
        raise BookAssemblyError(f"{label} must contain a JSON object.")
    return document


def resolve_project_file(
    project_path: Path,
    relative_path: str,
    required_parent: Path,
    label: str,
) -> Path:
    """Resolve a recorded project path without allowing directory escape."""
    if not isinstance(relative_path, str) or not relative_path:
        raise BookAssemblyError(f"{label} has no valid path.")
    path = (project_path / relative_path).resolve()
    try:
        path.relative_to(required_parent.resolve())
    except ValueError as error:
        raise BookAssemblyError(f"{label} is outside its book directory.") from error
    if not path.is_file():
        raise BookAssemblyError(f"{label} does not exist: {path}")
    return path


def numeric_record_paths(directory: Path, prefix: str) -> list[Path]:
    """Return sequential canonical records in numeric order."""
    paths = []
    for path in directory.glob(f"{prefix}*.json"):
        if not re.fullmatch(rf"{prefix}[1-9][0-9]*", path.stem):
            raise BookAssemblyError(
                f"Invalid {prefix} record in {directory}: {path.name}"
            )
        paths.append(path)
    return sorted(paths, key=lambda item: int(item.stem[len(prefix) :]))


def chapter_is_draft_ready(chapter: dict) -> bool:
    """Apply an explicit V1.2 draft gate or legacy readiness."""
    draft_gate = chapter.get("draft_gate")
    if draft_gate is not None:
        if draft_gate not in {"ready", "blocked"}:
            raise BookAssemblyError(
                f"{chapter.get('chapter_id', 'Unknown chapter')} has an "
                "invalid draft gate."
            )
        return draft_gate == "ready"
    return chapter.get("readiness") == "ready_to_draft"


def required_chapters(manifest: dict) -> list[dict]:
    """Select included chapters and reject unresolved planned material."""
    chapter_plan = manifest.get("chapter_plan")
    if not isinstance(chapter_plan, list) or not chapter_plan:
        raise BookAssemblyError("Book manifest has no chapter plan.")

    selected = []
    for chapter in chapter_plan:
        if not isinstance(chapter, dict):
            raise BookAssemblyError("Book manifest contains an invalid chapter.")
        disposition = chapter.get("material_disposition")
        if disposition not in {"include", "appendix"}:
            continue
        if not chapter_is_draft_ready(chapter):
            raise BookAssemblyError(
                f"{chapter.get('chapter_id', 'Unknown chapter')} is included "
                "but is not ready to draft."
            )
        selected.append(chapter)

    if not selected:
        raise BookAssemblyError("Book manifest has no included chapters.")
    return selected


def load_current_workspace(
    project_path: Path,
    book_directory: Path,
    book_id: str,
    chapter_id: str,
) -> tuple[dict, dict, Path, Path]:
    """Load and verify the current workspace pointer and document."""
    workspace_directory = book_directory / "workspaces" / chapter_id
    index_path = workspace_directory / "index.json"
    index = read_json_object(index_path, f"{chapter_id} workspace index")
    if (
        index.get("book_id") != book_id
        or index.get("chapter_id") != chapter_id
        or not isinstance(index.get("current_version"), int)
        or not isinstance(index.get("current_json_path"), str)
    ):
        raise BookAssemblyError(f"{chapter_id} workspace index is invalid.")
    current_path = (
        workspace_directory / index["current_json_path"]
    ).resolve()
    try:
        current_path.relative_to(workspace_directory.resolve())
    except ValueError as error:
        raise BookAssemblyError(
            f"{chapter_id} current workspace is outside its directory."
        ) from error
    if not current_path.is_file():
        raise BookAssemblyError(
            f"{chapter_id} current workspace does not exist."
        )
    workspace_document = read_json_object(
        current_path,
        f"{chapter_id} current workspace",
    )
    if (
        workspace_document.get("workspace_version")
        != index["current_version"]
        or workspace_document.get("workspace_sha256")
        != canonical_json_sha256(workspace_document, "workspace_sha256")
        or not isinstance(workspace_document.get("workspace"), dict)
    ):
        raise BookAssemblyError(f"{chapter_id} current workspace is invalid.")
    return index, workspace_document, index_path, current_path


def load_completed_chapter(
    project_path: Path,
    book_directory: Path,
    book_id: str,
    chapter: dict,
) -> dict:
    """Verify and return the exact final draft for one completed chapter."""
    chapter_id = chapter.get("chapter_id")
    if not isinstance(chapter_id, str):
        raise BookAssemblyError("A planned chapter has no valid identity.")

    run_path = book_directory / "runs" / f"{chapter_id}.json"
    run = read_json_object(run_path, f"{chapter_id} run state")
    if (
        run.get("book_id") != book_id
        or run.get("chapter_id") != chapter_id
        or run.get("status") != "chapter_complete"
    ):
        raise BookAssemblyError(f"{chapter_id} has not completed its chapter loop.")

    (
        _,
        workspace_document,
        workspace_index_path,
        workspace_path,
    ) = load_current_workspace(
        project_path,
        book_directory,
        book_id,
        chapter_id,
    )
    draft_versions = workspace_document["workspace"].get("draft_versions")
    if not isinstance(draft_versions, list) or not draft_versions:
        raise BookAssemblyError(f"{chapter_id} has no registered final draft.")
    final_draft = draft_versions[-1]
    if (
        not isinstance(final_draft, dict)
        or not isinstance(final_draft.get("draft_id"), str)
    ):
        raise BookAssemblyError(f"{chapter_id} final draft is invalid.")
    human_draft_acceptance = (
        run.get("completion_authority_kind")
        == "human_draft_acceptance"
    )
    if (
        not human_draft_acceptance
        and (
            run.get("final_pass_requested") is not True
            or run.get("final_pass_source_draft_id")
            == final_draft.get("draft_id")
        )
    ):
        raise BookAssemblyError(
            f"{chapter_id} does not have a newer registered final-pass draft."
        )

    draft_path = resolve_project_file(
        project_path,
        final_draft.get("relative_path"),
        book_directory,
        f"{chapter_id} final draft",
    )
    if file_sha256(draft_path) != final_draft.get("sha256"):
        raise BookAssemblyError(f"{chapter_id} final draft hash does not match.")

    metadata_path = draft_path.with_suffix(".json")
    metadata = read_json_object(metadata_path, f"{chapter_id} draft metadata")
    for field_name in (
        "book_id",
        "chapter_id",
        "draft_id",
        "relative_path",
        "sha256",
        "context_sha256",
        "provider",
        "created_at",
    ):
        expected = (
            book_id
            if field_name == "book_id"
            else chapter_id
            if field_name == "chapter_id"
            else final_draft.get(field_name)
        )
        if metadata.get(field_name) != expected:
            raise BookAssemblyError(
                f"{chapter_id} draft metadata disagrees on {field_name}."
            )

    assessment_paths = numeric_record_paths(
        book_directory / "assessments" / chapter_id,
        "A",
    )
    if not assessment_paths:
        raise BookAssemblyError(f"{chapter_id} has no final assessment.")
    assessment_path = assessment_paths[-1]
    assessment_document = read_json_object(
        assessment_path,
        f"{chapter_id} final assessment",
    )
    assessment = assessment_document.get("assessment")
    completion_authority_path = None
    completion_authority_sha256 = None
    completion_authority_file_sha256 = None
    completion_human_input_path = None
    completion_human_input_file_sha256 = None
    if human_draft_acceptance:
        completion_authority_path = resolve_project_file(
            project_path,
            run.get("completion_authority_path"),
            book_directory,
            f"{chapter_id} completion authority",
        )
        completion_authority = read_json_object(
            completion_authority_path,
            f"{chapter_id} completion authority",
        )
        completion_authority_sha256 = (
            completion_authority.get("draft_acceptance_sha256")
        )
        completion_authority_file_sha256 = file_sha256(
            completion_authority_path
        )
        completion_human_input_path = resolve_project_file(
            project_path,
            completion_authority.get("human_input_path"),
            book_directory,
            f"{chapter_id} human input",
        )
        human_input = read_json_object(
            completion_human_input_path,
            f"{chapter_id} human input",
        )
        completion_human_input_file_sha256 = file_sha256(
            completion_human_input_path
        )
        if (
            completion_authority.get("schema_version") != "1.0"
            or completion_authority.get("book_id") != book_id
            or completion_authority.get("chapter_id") != chapter_id
            or completion_authority_sha256
            != canonical_json_sha256(
                completion_authority,
                "draft_acceptance_sha256",
            )
            or run.get("completion_authority_sha256")
            != completion_authority_sha256
            or completion_authority.get("accepted_draft_id")
            != final_draft["draft_id"]
            or completion_authority.get("accepted_draft_path")
            != final_draft["relative_path"]
            or completion_authority.get("accepted_draft_sha256")
            != final_draft["sha256"]
            or run.get("accepted_final_draft_id")
            != final_draft["draft_id"]
            or run.get("accepted_final_draft_sha256")
            != final_draft["sha256"]
            or completion_authority.get("assessment_id")
            != assessment_document.get("assessment_id")
            or completion_authority.get("assessment_sha256")
            != assessment_document.get("assessment_sha256")
            or completion_authority.get("assessment_path")
            != str(assessment_path.relative_to(project_path))
            or completion_authority.get("workspace_sha256")
            != workspace_document.get("workspace_sha256")
            or completion_authority.get("workspace_path")
            != str(workspace_path.relative_to(project_path))
            or completion_authority.get("workspace_file_sha256")
            != file_sha256(workspace_path)
            or completion_authority.get("human_input_sha256")
            != human_input.get("human_input_sha256")
            or human_input.get("human_input_sha256")
            != canonical_json_sha256(
                human_input,
                "human_input_sha256",
            )
            or human_input.get("assessment_id")
            != assessment_document.get("assessment_id")
            or human_input.get("assessment_sha256")
            != assessment_document.get("assessment_sha256")
            or completion_authority.get("provider_calls_added") != 0
            or completion_authority.get("new_research_allowed") is not False
        ):
            raise BookAssemblyError(
                f"{chapter_id} human draft acceptance is invalid."
            )
    if (
        not isinstance(assessment, dict)
        or assessment_document.get("assessment_id") != assessment_path.stem
        or assessment_document.get("assessment_sha256")
        != canonical_json_sha256(assessment_document, "assessment_sha256")
        or assessment.get("book_id") != book_id
        or assessment.get("chapter_id") != chapter_id
        or (
            assessment.get("outcome")
            != (
                "human_input_required"
                if human_draft_acceptance
                else "chapter_complete"
            )
        )
        or assessment.get("draft_id") != final_draft["draft_id"]
        or assessment.get("workspace_sha256")
        != workspace_document.get("workspace_sha256")
        or run.get("last_assessment_id")
        != assessment_document.get("assessment_id")
        or run.get("last_assessment_sha256")
        != assessment_document.get("assessment_sha256")
    ):
        raise BookAssemblyError(
            f"{chapter_id} final assessment does not validate its final draft."
        )

    try:
        markdown = draft_path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise BookAssemblyError(
            f"{chapter_id} final draft could not be read."
        ) from error
    if not markdown:
        raise BookAssemblyError(f"{chapter_id} final draft is empty.")

    return {
        "chapter_id": chapter_id,
        "working_title": chapter.get("working_title"),
        "material_disposition": chapter.get("material_disposition"),
        "draft_id": final_draft["draft_id"],
        "draft_path": draft_path,
        "draft_sha256": final_draft["sha256"],
        "draft_metadata_path": metadata_path,
        "draft_metadata_sha256": file_sha256(metadata_path),
        "workspace_index_path": workspace_index_path,
        "workspace_index_sha256": file_sha256(workspace_index_path),
        "workspace_path": workspace_path,
        "workspace_file_sha256": file_sha256(workspace_path),
        "assessment_id": assessment_document["assessment_id"],
        "assessment_path": assessment_path,
        "assessment_sha256": assessment_document["assessment_sha256"],
        "run_path": run_path,
        "run_sha256": file_sha256(run_path),
        "completion_authority_path": completion_authority_path,
        "completion_authority_sha256": completion_authority_sha256,
        "completion_authority_file_sha256": (
            completion_authority_file_sha256
        ),
        "completion_human_input_path": completion_human_input_path,
        "completion_human_input_file_sha256": (
            completion_human_input_file_sha256
        ),
        "markdown": markdown,
    }


def load_review_document(
    project_path: Path,
    manifest: dict,
) -> tuple[Path, dict]:
    """Load the one canonical review named by the book manifest."""
    review_ids = manifest.get("input_review_ids")
    if (
        not isinstance(review_ids, list)
        or len(review_ids) != 1
        or not isinstance(review_ids[0], str)
        or not review_ids[0].startswith("review-")
    ):
        raise BookAssemblyError("Book manifest has no valid input review.")
    conversation_id = review_ids[0][len("review-") :]
    review_path = project_path / "data" / "reviews" / f"{conversation_id}.json"
    review_document = read_json_object(review_path, "Canonical research review")
    if (
        review_document.get("review_id") != review_ids[0]
        or review_document.get("conversation_id") != conversation_id
        or not isinstance(review_document.get("review"), dict)
    ):
        raise BookAssemblyError("Canonical research review identity is invalid.")
    return review_path, review_document


def collect_sources(
    book_directory: Path,
    chapters: list[dict],
    review_document: dict,
) -> tuple[list[dict], list[dict]]:
    """Collect checked review and chapter sources without inventing citations."""
    checked_review_sources = {
        source.get("receipt_id"): source
        for source in review_document["review"].get("checked_sources", [])
        if isinstance(source, dict)
    }
    sources = []
    packet_inputs = []
    seen_receipts = set()
    for receipt in review_document.get("source_receipts", []):
        if not isinstance(receipt, dict):
            raise BookAssemblyError("Research review source index is invalid.")
        receipt_id = receipt.get("receipt_id")
        checked = checked_review_sources.get(receipt_id, {})
        if not isinstance(receipt_id, str) or receipt_id in seen_receipts:
            raise BookAssemblyError("Research review source identity is invalid.")
        seen_receipts.add(receipt_id)
        sources.append(
            {
                "receipt_id": receipt_id,
                "chapter_id": None,
                "title": receipt.get("title"),
                "url": receipt.get("final_url"),
                "author_or_organization": checked.get(
                    "author_or_organization"
                ),
                "publication_date": checked.get("publication_date"),
                "publisher_or_journal": checked.get("publisher_or_journal"),
                "doi": checked.get("doi"),
                "finding": checked.get("finding"),
                "limitations": checked.get("limitations"),
            }
        )

    for chapter in chapters:
        chapter_id = chapter["chapter_id"]
        research_directory = book_directory / "research" / chapter_id
        for packet_path in numeric_record_paths(research_directory, "R"):
            packet = read_json_object(
                packet_path,
                f"{chapter_id} research packet",
            )
            if (
                packet.get("research_packet_id") != packet_path.stem
                or packet.get("research_packet_sha256")
                != canonical_json_sha256(packet, "research_packet_sha256")
                or not isinstance(packet.get("sources"), list)
            ):
                raise BookAssemblyError(
                    f"{chapter_id} research packet is invalid."
                )
            packet_inputs.append(
                {
                    "role": "chapter_research_packet",
                    "chapter_id": chapter_id,
                    "research_packet_id": packet_path.stem,
                    "path": packet_path,
                    "sha256": file_sha256(packet_path),
                }
            )
            for source in packet["sources"]:
                if not isinstance(source, dict):
                    raise BookAssemblyError(
                        f"{chapter_id} source record is invalid."
                    )
                receipt_id = source.get("receipt_id")
                if not isinstance(receipt_id, str):
                    raise BookAssemblyError(
                        f"{chapter_id} source has no receipt identity."
                    )
                if receipt_id in seen_receipts:
                    continue
                seen_receipts.add(receipt_id)
                sources.append(
                    {
                        "receipt_id": receipt_id,
                        "chapter_id": chapter_id,
                        "title": source.get("title"),
                        "url": source.get("final_url"),
                        "author_or_organization": source.get(
                            "author_or_organization"
                        ),
                        "publication_date": source.get("publication_date"),
                        "publisher_or_journal": source.get(
                            "publisher_or_journal"
                        ),
                        "doi": source.get("doi"),
                        "finding": source.get("finding"),
                        "limitations": source.get("limitations"),
                    }
                )
    return sources, packet_inputs


def render_curiosity_signature(signature: dict) -> list[str]:
    """Render the approved shape of the user's curiosity as front matter."""
    labels = (
        ("why_this_question_matters", "Why This Question Matters"),
        ("original_intuition", "Original Intuition"),
        ("surprising_connections", "Surprising Connections"),
        ("central_tensions", "Central Tensions"),
        ("desired_transformation", "Desired Transformation"),
        ("questions_that_must_remain_open", "Questions That Remain Open"),
    )
    lines = ["## Curiosity Signature", ""]
    for field_name, label in labels:
        items = signature.get(field_name, [])
        if not isinstance(items, list) or not items:
            continue
        lines.extend([f"### {label}", ""])
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                lines.append(f"- {item['text']}")
        lines.append("")
    return lines


def render_experiments_and_use_cases(review: dict) -> list[str]:
    """Place actionable research material at the end of the book."""
    lines = ["# Experiments and Use Cases", "", "## Experiments", ""]
    experiments = review.get("experiments", [])
    if not experiments:
        lines.append("No bounded experiment was established in the source review.")
    for index, experiment in enumerate(experiments, start=1):
        if not isinstance(experiment, dict):
            continue
        lines.extend(
            [
                f"### Experiment {index}",
                "",
                f"- Hypothesis: {experiment.get('hypothesis', 'Not established.')}",
                "- Observable outcome: "
                f"{experiment.get('observable_outcome', 'Not established.')}",
                "- Comparison or baseline: "
                f"{experiment.get('comparison_or_baseline', 'Not established.')}",
                "- Evidence that would weaken the claim: "
                + str(
                    experiment.get(
                        "evidence_that_would_weaken_the_claim",
                        "Not established.",
                    )
                ),
                "- Major unresolved dependency: "
                f"{experiment.get('major_unresolved_dependency', 'Not established.')}",
                "",
            ]
        )

    lines.extend(["## Use Cases", ""])
    use_cases = review.get("use_case_candidates", [])
    rendered_use_cases = [
        item["text"]
        for item in use_cases
        if isinstance(item, dict) and isinstance(item.get("text"), str)
    ]
    lines.extend(
        [f"- {item}" for item in rendered_use_cases]
        or ["No grounded use case was established in the source review."]
    )
    lines.append("")
    return lines


def render_sources(sources: list[dict]) -> list[str]:
    """Render a transparent evidence index from canonical source records."""
    lines = ["# Evidence and Source Index", ""]
    if not sources:
        lines.extend(
            [
                "No external source was admitted into this book.",
                "",
            ]
        )
        return lines
    for index, source in enumerate(sources, start=1):
        title = source.get("title") or source.get("url") or source["receipt_id"]
        lines.extend(
            [
                f"## {index}. {title}",
                "",
                f"- Receipt: `{source['receipt_id']}`",
                f"- Chapter: {source.get('chapter_id') or 'Book review'}",
                f"- URL: {source.get('url') or 'Not recorded'}",
                "- Author or organization: "
                f"{source.get('author_or_organization') or 'Not recorded'}",
                "- Publication date: "
                f"{source.get('publication_date') or 'Not recorded'}",
                "- Publisher or journal: "
                f"{source.get('publisher_or_journal') or 'Not recorded'}",
                f"- DOI: {source.get('doi') or 'Not recorded'}",
                f"- Review finding: {source.get('finding') or 'Not recorded'}",
                f"- Known limitations: {source.get('limitations') or 'Not recorded'}",
                "",
            ]
        )
    return lines


def render_book(
    manifest: dict,
    review_document: dict,
    chapters: list[dict],
    sources: list[dict],
) -> str:
    """Combine exact completed drafts with deterministic book matter."""
    review = review_document["review"]
    lines = [
        f"# {manifest['working_title']}",
        "",
        "## Central Question",
        "",
        manifest["central_question"],
        "",
        f"Book mode: `{manifest.get('book_mode', 'unknown')}`",
        "",
        *render_curiosity_signature(manifest.get("curiosity_signature", {})),
    ]
    for chapter in chapters:
        lines.extend(
            [
                "---",
                "",
                chapter["markdown"],
                "",
            ]
        )
    lines.extend(["---", "", *render_experiments_and_use_cases(review)])
    lines.extend(["---", "", *render_sources(sources)])
    return "\n".join(lines).rstrip() + "\n"


def verify_existing_candidate(
    project_path: Path,
    book_directory: Path,
    book_id: str,
) -> dict | None:
    """Return an unchanged candidate, or reject partial or stale output."""
    candidate_path = book_directory / "candidate.md"
    receipt_path = book_directory / "candidate_assembly.json"
    if not candidate_path.exists() and not receipt_path.exists():
        return None
    if not candidate_path.is_file() or not receipt_path.is_file():
        raise BookAssemblyError("Existing candidate assembly is incomplete.")
    receipt = read_json_object(receipt_path, "Candidate assembly receipt")
    if (
        receipt.get("book_id") != book_id
        or receipt.get("lifecycle_status") != "candidate"
        or receipt.get("candidate_sha256") != file_sha256(candidate_path)
        or not isinstance(receipt.get("inputs"), list)
    ):
        raise BookAssemblyError("Existing candidate assembly receipt is invalid.")
    for record in receipt["inputs"]:
        if not isinstance(record, dict):
            raise BookAssemblyError("Existing candidate input record is invalid.")
        path = resolve_project_file(
            project_path,
            record.get("path"),
            project_path,
            "Existing candidate input",
        )
        if file_sha256(path) != record.get("sha256"):
            raise BookAssemblyError(
                "A canonical input changed after the candidate was assembled."
            )
    return receipt


def assemble_candidate_book(project_path: Path, book_id: str) -> dict:
    """Create one immutable, deterministic candidate from completed chapters."""
    project_path = project_path.resolve()
    book_directory = project_path / "data" / "books" / book_id
    if not book_directory.is_dir():
        raise BookAssemblyError(f"Book does not exist: {book_id}")

    existing = verify_existing_candidate(
        project_path,
        book_directory,
        book_id,
    )
    if existing is not None:
        return existing

    manifest_path = book_directory / "manifest.json"
    approval_path = book_directory / "approval.json"
    manifest = read_json_object(manifest_path, "Book manifest")
    approval = read_json_object(approval_path, "Book-plan approval")
    if manifest.get("book_id") != book_id:
        raise BookAssemblyError("Book manifest identity does not match.")
    if (
        approval.get("book_id") != book_id
        or approval.get("decision") != "approved_for_drafting"
        or approval.get("chapter_drafting_gate") != "ready"
    ):
        raise BookAssemblyError("Book plan is not approved for assembly.")

    review_path, review_document = load_review_document(project_path, manifest)
    chapters = [
        load_completed_chapter(
            project_path,
            book_directory,
            book_id,
            chapter,
        )
        for chapter in required_chapters(manifest)
    ]
    sources, research_packet_inputs = collect_sources(
        book_directory,
        chapters,
        review_document,
    )
    markdown = render_book(manifest, review_document, chapters, sources)
    book_sha256 = hashlib.sha256(markdown.encode("utf-8")).hexdigest()

    inputs = [
        {
            "role": "manifest",
            "path": str(manifest_path.relative_to(project_path)),
            "sha256": file_sha256(manifest_path),
        },
        {
            "role": "book_approval",
            "path": str(approval_path.relative_to(project_path)),
            "sha256": file_sha256(approval_path),
        },
        {
            "role": "research_review",
            "path": str(review_path.relative_to(project_path)),
            "sha256": file_sha256(review_path),
        },
    ]
    for chapter in chapters:
        chapter_inputs = [
                {
                    "role": "chapter_draft",
                    "chapter_id": chapter["chapter_id"],
                    "draft_id": chapter["draft_id"],
                    "path": str(
                        chapter["draft_path"].relative_to(project_path)
                    ),
                    "sha256": chapter["draft_sha256"],
                },
                {
                    "role": "chapter_draft_metadata",
                    "chapter_id": chapter["chapter_id"],
                    "draft_id": chapter["draft_id"],
                    "path": str(
                        chapter["draft_metadata_path"].relative_to(
                            project_path
                        )
                    ),
                    "sha256": chapter["draft_metadata_sha256"],
                },
                {
                    "role": "chapter_workspace_index",
                    "chapter_id": chapter["chapter_id"],
                    "path": str(
                        chapter["workspace_index_path"].relative_to(
                            project_path
                        )
                    ),
                    "sha256": chapter["workspace_index_sha256"],
                },
                {
                    "role": "chapter_workspace",
                    "chapter_id": chapter["chapter_id"],
                    "path": str(
                        chapter["workspace_path"].relative_to(project_path)
                    ),
                    "sha256": chapter["workspace_file_sha256"],
                },
                {
                    "role": "chapter_assessment",
                    "chapter_id": chapter["chapter_id"],
                    "assessment_id": chapter["assessment_id"],
                    "path": str(
                        chapter["assessment_path"].relative_to(project_path)
                    ),
                    "sha256": file_sha256(chapter["assessment_path"]),
                },
                {
                    "role": "chapter_run",
                    "chapter_id": chapter["chapter_id"],
                    "path": str(chapter["run_path"].relative_to(project_path)),
                    "sha256": chapter["run_sha256"],
                },
            ]
        if chapter["completion_authority_path"] is not None:
            chapter_inputs.extend(
                [
                    {
                        "role": "chapter_completion_authority",
                        "chapter_id": chapter["chapter_id"],
                        "path": str(
                            chapter["completion_authority_path"].relative_to(
                                project_path
                            )
                        ),
                        "sha256": chapter[
                            "completion_authority_file_sha256"
                        ],
                    },
                    {
                        "role": "chapter_human_input",
                        "chapter_id": chapter["chapter_id"],
                        "path": str(
                            chapter[
                                "completion_human_input_path"
                            ].relative_to(project_path)
                        ),
                        "sha256": chapter[
                            "completion_human_input_file_sha256"
                        ],
                    },
                ]
            )
        inputs.extend(chapter_inputs)
    inputs.extend(
        {
            **record,
            "path": str(record["path"].relative_to(project_path)),
        }
        for record in research_packet_inputs
    )

    assembled_at = current_utc_time()
    receipt = {
        "schema_version": "1.0",
        "assembly_id": f"candidate-{book_id}",
        "book_id": book_id,
        "assembled_at": assembled_at,
        "lifecycle_status": "candidate",
        "candidate_path": str(
            (book_directory / "candidate.md").relative_to(project_path)
        ),
        "candidate_sha256": book_sha256,
        "chapter_count": len(chapters),
        "chapters": [
            {
                key: chapter[key]
                for key in (
                    "chapter_id",
                    "working_title",
                    "material_disposition",
                    "draft_id",
                    "draft_sha256",
                    "assessment_id",
                    "assessment_sha256",
                )
            }
            for chapter in chapters
        ],
        "source_count": len(sources),
        "source_receipt_ids": [
            source["receipt_id"] for source in sources
        ],
        "inputs": inputs,
    }

    candidate_path = book_directory / "candidate.md"
    receipt_path = book_directory / "candidate_assembly.json"
    suffix = f".tmp-{uuid.uuid4().hex}"
    temporary_book = candidate_path.with_name(candidate_path.name + suffix)
    temporary_receipt = receipt_path.with_name(receipt_path.name + suffix)
    installed_book = False
    try:
        temporary_book.write_text(markdown, encoding="utf-8")
        temporary_receipt.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_book, candidate_path)
        installed_book = True
        os.replace(temporary_receipt, receipt_path)
    except OSError as error:
        temporary_book.unlink(missing_ok=True)
        temporary_receipt.unlink(missing_ok=True)
        if installed_book:
            candidate_path.unlink(missing_ok=True)
        raise BookAssemblyError("Candidate book could not be saved.") from error

    return receipt


def load_editorial_decision(
    book_directory: Path,
    book_id: str,
    candidate_sha256: str,
    synthesis_receipt: dict | None = None,
) -> tuple[Path, dict]:
    """Load the decision bound to the current candidate and audited edition."""
    decision_path = book_directory / "editorial_review.json"
    decision = read_json_object(decision_path, "Editorial review decision")
    project_path = book_directory.parents[2]
    synthesis_receipt_path = book_synthesis.synthesis_receipt_path(
        project_path,
        book_id,
    )
    decision_authority = decision.get("decision_authority", "human")
    if (
        decision.get("schema_version") != "1.0"
        or decision.get("book_id") != book_id
        or decision.get("candidate_sha256") != candidate_sha256
        or decision.get("candidate_assembly_sha256")
        != file_sha256(book_directory / "candidate_assembly.json")
        or decision.get("decision")
        not in {
            "approved_for_finalization",
            "needs_chapter_revision",
            "hold",
        }
        or not isinstance(decision.get("notes"), list)
        or not isinstance(decision.get("chapter_revisions"), list)
        or not isinstance(decision.get("decided_at"), str)
        or any(
            not isinstance(note, str) or not note.strip()
            for note in decision.get("notes", [])
        )
        or decision_authority
        not in {"human", "automated_audit_policy"}
        or (
            decision_authority == "automated_audit_policy"
            and decision.get("workflow_policy_version")
            != "source-permissions-only-v1"
        )
    ):
        raise BookAssemblyError(
            "Editorial review does not match the current candidate."
        )
    if synthesis_receipt is not None and (
        synthesis_receipt.get("book_id") != book_id
        or synthesis_receipt.get("candidate_sha256") != candidate_sha256
        or synthesis_receipt.get("lifecycle_status") != "ready_for_editorial"
        or decision.get("synthesis_receipt_path")
        != str(synthesis_receipt_path.relative_to(project_path))
        or decision.get("synthesis_receipt_sha256")
        != file_sha256(synthesis_receipt_path)
        or decision.get("edition_path") != synthesis_receipt.get("book_path")
        or decision.get("edition_sha256") != synthesis_receipt.get("book_sha256")
        or decision.get("synthesis_attempt")
        != synthesis_receipt.get("selected_attempt")
    ):
        raise BookAssemblyError(
            "Editorial review does not match the audited synthesized edition."
        )
    revisions = decision["chapter_revisions"]
    revision_ids = []
    for revision in revisions:
        if (
            not isinstance(revision, dict)
            or not isinstance(revision.get("chapter_id"), str)
            or not isinstance(revision.get("instructions"), list)
            or not revision["instructions"]
            or any(
                not isinstance(instruction, str) or not instruction.strip()
                for instruction in revision["instructions"]
            )
        ):
            raise BookAssemblyError(
                "Editorial chapter revision request is invalid."
            )
        revision_ids.append(revision["chapter_id"])
    if len(revision_ids) != len(set(revision_ids)):
        raise BookAssemblyError(
            "Editorial review repeats a chapter revision target."
        )
    if (
        decision["decision"] == "needs_chapter_revision"
        and not revisions
    ):
        raise BookAssemblyError(
            "Editorial revision requires at least one chapter target."
        )
    if (
        decision["decision"] != "needs_chapter_revision"
        and revisions
    ):
        raise BookAssemblyError(
            "Only a revision decision may contain chapter revisions."
        )
    return decision_path, decision


def editorial_history_directories(book_directory: Path) -> list[Path]:
    """Return preserved candidate review cycles in numeric order."""
    history_directory = book_directory / "editorial_history"
    versions = []
    for path in history_directory.glob("v[0-9][0-9][0-9]"):
        if path.is_dir():
            versions.append(path)
    return sorted(versions)


def archive_candidate_for_revision(
    project_path: Path,
    book_id: str,
) -> tuple[Path, dict]:
    """Archive one rejected candidate and its human review atomically."""
    project_path = project_path.resolve()
    book_directory = project_path / "data" / "books" / book_id
    candidate = verify_existing_candidate(
        project_path,
        book_directory,
        book_id,
    )
    if candidate is None:
        raise BookAssemblyError("No candidate exists for editorial revision.")
    try:
        synthesis_receipt = book_synthesis.verify_receipt(
            project_path,
            book_id,
            candidate["candidate_sha256"],
        )
    except book_synthesis.BookSynthesisError as error:
        raise BookAssemblyError(str(error)) from error
    _, decision = load_editorial_decision(
        book_directory,
        book_id,
        candidate["candidate_sha256"],
        synthesis_receipt,
    )
    if decision["decision"] != "needs_chapter_revision":
        raise BookAssemblyError(
            "Candidate was not returned for chapter revision."
        )
    candidate_chapter_ids = {
        chapter.get("chapter_id")
        for chapter in candidate.get("chapters", [])
        if isinstance(chapter, dict)
    }
    requested_chapter_ids = {
        revision["chapter_id"]
        for revision in decision["chapter_revisions"]
    }
    if not requested_chapter_ids.issubset(candidate_chapter_ids):
        raise BookAssemblyError(
            "Editorial review targets a chapter outside the candidate."
        )

    history_directory = book_directory / "editorial_history"
    history_directory.mkdir(parents=True, exist_ok=True)
    version = len(editorial_history_directories(book_directory)) + 1
    destination = history_directory / f"v{version:03d}"
    if destination.exists():
        raise BookAssemblyError("Editorial history version already exists.")
    staging = history_directory / f".tmp-{uuid.uuid4().hex}"
    staging.mkdir()
    sources = (
        book_directory / "candidate.md",
        book_directory / "candidate_assembly.json",
        book_directory / "synthesis",
        book_directory / "editorial_review.json",
    )
    moved = []
    try:
        for source in sources:
            target = staging / source.name
            os.replace(source, target)
            moved.append((source, target))
        os.replace(staging, destination)
    except OSError as error:
        for source, target in reversed(moved):
            if target.exists() and not source.exists():
                os.replace(target, source)
        try:
            staging.rmdir()
        except OSError:
            pass
        raise BookAssemblyError(
            "Candidate editorial cycle could not be archived."
        ) from error
    return destination, decision


def verify_existing_assembly(
    project_path: Path,
    book_directory: Path,
    book_id: str,
) -> dict | None:
    """Return an unchanged, authority-bound final book."""
    book_path = book_directory / "book.md"
    receipt_path = book_directory / "assembly.json"
    if not book_path.exists() and not receipt_path.exists():
        return None
    if not book_path.is_file() or not receipt_path.is_file():
        raise BookAssemblyError("Existing final assembly is incomplete.")
    receipt = read_json_object(receipt_path, "Final assembly receipt")
    candidate = verify_existing_candidate(
        project_path,
        book_directory,
        book_id,
    )
    if candidate is None:
        raise BookAssemblyError("Final book has no preserved candidate.")
    if receipt.get("schema_version") == "1.0":
        decision_path, decision = load_editorial_decision(
            book_directory,
            book_id,
            candidate["candidate_sha256"],
        )
        if (
            decision.get("decision") != "approved_for_finalization"
            or receipt.get("book_id") != book_id
            or receipt.get("lifecycle_status") != "final"
            or receipt.get("book_sha256") != file_sha256(book_path)
            or receipt.get("book_sha256") != candidate["candidate_sha256"]
            or receipt.get("candidate_sha256")
            != candidate["candidate_sha256"]
            or receipt.get("editorial_review_sha256")
            != file_sha256(decision_path)
        ):
            raise BookAssemblyError(
                "Existing legacy final assembly receipt is invalid."
            )
        return receipt

    try:
        synthesis_receipt = book_synthesis.verify_receipt(
            project_path,
            book_id,
            candidate["candidate_sha256"],
        )
    except book_synthesis.BookSynthesisError as error:
        raise BookAssemblyError(str(error)) from error
    decision_path, decision = load_editorial_decision(
        book_directory,
        book_id,
        candidate["candidate_sha256"],
        synthesis_receipt,
    )
    synthesis_receipt_path = book_synthesis.synthesis_receipt_path(
        project_path,
        book_id,
    )
    if (
        receipt.get("schema_version") != "1.1"
        or decision.get("decision") != "approved_for_finalization"
        or receipt.get("book_id") != book_id
        or receipt.get("lifecycle_status") != "final"
        or receipt.get("book_sha256") != file_sha256(book_path)
        or receipt.get("book_sha256") != synthesis_receipt["book_sha256"]
        or receipt.get("candidate_sha256") != candidate["candidate_sha256"]
        or receipt.get("synthesis_receipt_file_sha256")
        != file_sha256(synthesis_receipt_path)
        or receipt.get("synthesis_receipt_sha256")
        != synthesis_receipt["receipt_sha256"]
        or receipt.get("synthesis_attempt")
        != synthesis_receipt["selected_attempt"]
        or receipt.get("editorial_review_sha256")
        != file_sha256(decision_path)
        or receipt.get("finalization_authority", "human")
        != decision.get("decision_authority", "human")
        or receipt.get("workflow_policy_version")
        != decision.get("workflow_policy_version")
    ):
        raise BookAssemblyError("Existing final assembly receipt is invalid.")
    return receipt


def finalize_book(project_path: Path, book_id: str) -> dict:
    """Promote one approved candidate into the immutable final book."""
    project_path = project_path.resolve()
    book_directory = project_path / "data" / "books" / book_id
    if not book_directory.is_dir():
        raise BookAssemblyError(f"Book does not exist: {book_id}")
    existing = verify_existing_assembly(
        project_path,
        book_directory,
        book_id,
    )
    if existing is not None:
        return existing

    candidate = verify_existing_candidate(
        project_path,
        book_directory,
        book_id,
    )
    if candidate is None:
        raise BookAssemblyError("No candidate book exists for finalization.")
    try:
        synthesis_receipt = book_synthesis.verify_receipt(
            project_path,
            book_id,
            candidate["candidate_sha256"],
        )
    except book_synthesis.BookSynthesisError as error:
        raise BookAssemblyError(str(error)) from error
    if synthesis_receipt["lifecycle_status"] != "ready_for_editorial":
        raise BookAssemblyError(
            "Book synthesis has not passed its independent audit."
        )
    decision_path, decision = load_editorial_decision(
        book_directory,
        book_id,
        candidate["candidate_sha256"],
        synthesis_receipt,
    )
    if decision.get("decision") != "approved_for_finalization":
        raise BookAssemblyError(
            "Candidate book has no valid finalization decision."
        )

    candidate_path = book_directory / "candidate.md"
    candidate_receipt_path = book_directory / "candidate_assembly.json"
    synthesis_receipt_path = book_synthesis.synthesis_receipt_path(
        project_path,
        book_id,
    )
    synthesized_book_path = resolve_project_file(
        project_path,
        synthesis_receipt["book_path"],
        book_synthesis.synthesis_directory(project_path, book_id),
        "Synthesized edition",
    )
    book_path = book_directory / "book.md"
    receipt_path = book_directory / "assembly.json"
    receipt = {
        "schema_version": "1.1",
        "assembly_id": f"assembly-{book_id}",
        "book_id": book_id,
        "assembled_at": current_utc_time(),
        "lifecycle_status": "final",
        "book_path": str(book_path.relative_to(project_path)),
        "book_sha256": synthesis_receipt["book_sha256"],
        "candidate_path": str(candidate_path.relative_to(project_path)),
        "candidate_sha256": candidate["candidate_sha256"],
        "candidate_assembly_path": str(
            candidate_receipt_path.relative_to(project_path)
        ),
        "candidate_assembly_sha256": file_sha256(candidate_receipt_path),
        "synthesis_receipt_path": str(
            synthesis_receipt_path.relative_to(project_path)
        ),
        "synthesis_receipt_file_sha256": file_sha256(
            synthesis_receipt_path
        ),
        "synthesis_receipt_sha256": synthesis_receipt["receipt_sha256"],
        "synthesis_attempt": synthesis_receipt["selected_attempt"],
        "synthesis_context_sha256": synthesis_receipt["context_sha256"],
        "synthesis_inventory_sha256": synthesis_receipt["inventory_sha256"],
        "synthesis_plan_sha256": synthesis_receipt["plan_sha256"],
        "synthesis_audit_sha256": synthesis_receipt["audit_sha256"],
        "publication_profile": synthesis_receipt["profile"],
        "editorial_review_path": str(
            decision_path.relative_to(project_path)
        ),
        "editorial_review_sha256": file_sha256(decision_path),
        "finalization_authority": decision.get(
            "decision_authority",
            "human",
        ),
        "workflow_policy_version": decision.get(
            "workflow_policy_version"
        ),
        "chapter_count": candidate["chapter_count"],
        "chapters": candidate["chapters"],
        "source_count": candidate["source_count"],
        "source_receipt_ids": candidate["source_receipt_ids"],
    }

    suffix = f".tmp-{uuid.uuid4().hex}"
    temporary_book = book_path.with_name(book_path.name + suffix)
    temporary_receipt = receipt_path.with_name(receipt_path.name + suffix)
    installed_book = False
    try:
        temporary_book.write_bytes(synthesized_book_path.read_bytes())
        temporary_receipt.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_book, book_path)
        installed_book = True
        os.replace(temporary_receipt, receipt_path)
    except OSError as error:
        temporary_book.unlink(missing_ok=True)
        temporary_receipt.unlink(missing_ok=True)
        if installed_book:
            book_path.unlink(missing_ok=True)
        raise BookAssemblyError("Final book could not be saved.") from error
    return receipt
