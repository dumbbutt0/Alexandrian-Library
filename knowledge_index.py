import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


NODE_ID_PATTERN = re.compile(r"^ki-[A-Za-z0-9._-]+$")
RELATIONSHIP_TYPES = {
    "addresses",
    "analogous_to",
    "applies_to",
    "branches_into",
    "contradicts",
    "depends_on",
    "qualifies",
    "revises",
    "supported_by",
    "supports",
    "tested_by",
}

NODE_TYPE_LABELS = {
    "core_question": "Core Questions",
    "belief": "Starting Beliefs",
    "belief_revision": "Belief Revisions",
    "claim": "Claims",
    "source": "Checked Sources",
    "concept": "Connected Concepts",
    "book_branch": "Book Branches",
    "reflection_question": "Reflection Questions",
    "experiment": "Experiments",
    "use_case": "Use Cases",
    "evidence_gap": "Evidence Gaps",
}

RELATIONSHIP_LABELS = {
    "addresses": "Addresses",
    "analogous_to": "Analogous to",
    "applies_to": "Applies to",
    "branches_into": "Branches into",
    "contradicts": "Contradicts",
    "depends_on": "Depends on",
    "qualifies": "Qualifies",
    "revises": "Revises",
    "supported_by": "Supported by",
    "supports": "Supports",
    "tested_by": "Tested by",
}

CURIOSITY_FIELD_LABELS = {
    "why_this_question_matters": "Why This Question Matters",
    "original_intuition": "Original Intuition",
    "surprising_connections": "Surprising Connections",
    "central_tensions": "Central Tensions",
    "desired_transformation": "Desired Transformation",
    "questions_that_must_remain_open": (
        "Questions That Must Remain Open"
    ),
}
CURIOSITY_SINGLE_VALUE_FIELDS = {
    "why_this_question_matters",
    "original_intuition",
    "desired_transformation",
}


class KnowledgeIndexError(RuntimeError):
    """Report a validation or persistence failure in the knowledge index."""


@dataclass(frozen=True)
class KnowledgeRelation:
    relationship: str
    target_node_id: str

    def __post_init__(self) -> None:
        if self.relationship not in RELATIONSHIP_TYPES:
            raise KnowledgeIndexError(
                f"Unsupported knowledge relationship: {self.relationship}"
            )
        if not NODE_ID_PATTERN.fullmatch(self.target_node_id):
            raise KnowledgeIndexError(
                f"Invalid relationship target: {self.target_node_id}"
            )

    def encode(self) -> str:
        """Encode one typed relationship as a flat frontmatter value."""
        return f"{self.relationship}::{self.target_node_id}"


@dataclass(frozen=True)
class KnowledgeSection:
    heading: str
    body: str


@dataclass(frozen=True)
class KnowledgeNode:
    node_id: str
    node_type: str
    title: str
    summary: str
    status: str
    source_review_ids: tuple[str, ...]
    claim_ids: tuple[str, ...] = ()
    source_receipt_ids: tuple[str, ...] = ()
    transcript_references: tuple[str, ...] = ()
    relations: tuple[KnowledgeRelation, ...] = ()
    sections: tuple[KnowledgeSection, ...] = ()
    relative_path: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not NODE_ID_PATTERN.fullmatch(self.node_id):
            raise KnowledgeIndexError(f"Invalid knowledge node ID: {self.node_id}")
        if self.node_type not in NODE_TYPE_LABELS:
            raise KnowledgeIndexError(
                f"Unsupported knowledge node type: {self.node_type}"
            )
        if not self.title.strip() or not self.summary.strip():
            raise KnowledgeIndexError(
                f"Knowledge node {self.node_id} requires a title and summary."
            )
        if not self.status.strip() or not self.source_review_ids:
            raise KnowledgeIndexError(
                f"Knowledge node {self.node_id} lacks status or provenance."
            )


def current_utc_time() -> str:
    """Return an ISO 8601 timestamp in UTC."""
    return datetime.now(timezone.utc).isoformat()


def normalized_text(value: Any) -> str:
    """Normalize one required text value from a canonical review."""
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def required_text(value: Any, label: str) -> str:
    """Return normalized non-empty text or reject an invalid review."""
    text = normalized_text(value)
    if not text:
        raise KnowledgeIndexError(f"Approved review has no valid {label}.")
    return text


def string_list(value: Any) -> tuple[str, ...]:
    """Keep only normalized non-empty strings while preserving order."""
    if not isinstance(value, list):
        return ()
    return tuple(
        text
        for item in value
        if (text := normalized_text(item))
    )


def grounded_references(value: Any) -> tuple[str, ...]:
    """Read transcript references from one grounded review record."""
    if not isinstance(value, dict):
        return ()
    return string_list(value.get("transcript_references"))


def grounded_text(value: Any, label: str) -> str:
    """Read the text from one grounded review record."""
    if not isinstance(value, dict):
        raise KnowledgeIndexError(f"Approved review has no valid {label}.")
    return required_text(value.get("text"), label)


def compact_title(text: str, fallback: str, limit: int = 84) -> str:
    """Create a readable graph title without changing the underlying summary."""
    title = normalized_text(text).strip(" \t\r\n\"'")
    if not title:
        return fallback
    if len(title) <= limit:
        return title

    shortened = title[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return (shortened or title[: limit - 1]).rstrip() + "…"


def make_node_id(conversation_id: str, suffix: str) -> str:
    """Create a Windows-safe deterministic node identity."""
    node_id = f"ki-{conversation_id}-{suffix}"
    if not NODE_ID_PATTERN.fullmatch(node_id):
        raise KnowledgeIndexError(
            f"Conversation creates an invalid knowledge node ID: {conversation_id}"
        )
    return node_id


def numbered_node_id(
    conversation_id: str,
    category: str,
    index: int,
) -> str:
    """Create a stable identity for an ordered review collection."""
    return make_node_id(conversation_id, f"{category}-{index:02d}")


def relationship_for_connected_idea(relationship: str) -> tuple[str, bool]:
    """Map the review vocabulary to one graph edge and its direction."""
    mapping = {
        "prerequisite": ("depends_on", True),
        "supports": ("supports", False),
        "contradicts": ("contradicts", False),
        "analogy": ("analogous_to", False),
        "application": ("applies_to", True),
        "measurement_problem": ("qualifies", False),
        "implementation_branch": ("branches_into", True),
        "separate_research_question": ("branches_into", True),
    }
    try:
        return mapping[relationship]
    except KeyError as error:
        raise KnowledgeIndexError(
            f"Approved review contains an unknown idea relationship: {relationship}"
        ) from error


def build_review_nodes(
    review_document: dict,
    human_decision: dict,
) -> list[KnowledgeNode]:
    """Deterministically promote one approved review into reusable nodes."""
    review_id = required_text(review_document.get("review_id"), "review ID")
    conversation_id = required_text(
        review_document.get("conversation_id"),
        "conversation ID",
    )
    if (
        human_decision.get("review_id") != review_id
        or human_decision.get("conversation_id") != conversation_id
        or human_decision.get("decision") != "approved_for_book_planning"
        or human_decision.get("review_research_route")
        != "ready_for_book_consideration"
        or human_decision.get("book_planning_gate") != "ready"
    ):
        raise KnowledgeIndexError(
            "Only a matching human-approved review can enter the knowledge index."
        )

    review = review_document.get("review")
    if not isinstance(review, dict):
        raise KnowledgeIndexError("Approved review has no canonical review object.")
    if (
        review.get("conversation_id") != conversation_id
        or review.get("research_route") != "ready_for_book_consideration"
    ):
        raise KnowledgeIndexError(
            "Approved review identity or research route does not match its record."
        )

    review_signature = review.get("curiosity_signature")
    approved_signature = human_decision.get("approved_curiosity_signature")
    signature_decision = human_decision.get("curiosity_signature_decision")
    if (
        not isinstance(review_signature, dict)
        or not isinstance(approved_signature, dict)
        or signature_decision not in {"accepted", "corrected"}
        or set(review_signature) != set(CURIOSITY_FIELD_LABELS)
        or set(approved_signature) != set(CURIOSITY_FIELD_LABELS)
    ):
        raise KnowledgeIndexError(
            "Approved review has no valid human-approved curiosity signature."
        )

    expected_accepted_signature = {}
    for field_name in CURIOSITY_FIELD_LABELS:
        review_items = review_signature[field_name]
        approved_items = approved_signature[field_name]
        if not isinstance(review_items, list) or not isinstance(
            approved_items,
            list,
        ):
            raise KnowledgeIndexError(
                f"Curiosity signature field is invalid: {field_name}"
            )
        if (
            field_name in CURIOSITY_SINGLE_VALUE_FIELDS
            and (len(review_items) > 1 or len(approved_items) > 1)
        ):
            raise KnowledgeIndexError(
                f"Curiosity signature field must be singular: {field_name}"
            )

        expected_accepted_signature[field_name] = []
        grounded_items = set()
        for item in review_items:
            if (
                not isinstance(item, dict)
                or not normalized_text(item.get("text"))
                or not string_list(item.get("transcript_references"))
            ):
                raise KnowledgeIndexError(
                    f"Reviewed curiosity field is invalid: {field_name}"
                )
            normalized_item = {
                "text": normalized_text(item["text"]),
                "provenance": "review_extraction",
                "transcript_references": list(
                    string_list(item["transcript_references"])
                ),
            }
            expected_accepted_signature[field_name].append(normalized_item)
            grounded_items.add(
                (
                    normalized_item["text"],
                    tuple(normalized_item["transcript_references"]),
                )
            )

        for item in approved_items:
            if not isinstance(item, dict):
                raise KnowledgeIndexError(
                    f"Approved curiosity field is invalid: {field_name}"
                )
            text = normalized_text(item.get("text"))
            provenance = item.get("provenance")
            references = string_list(item.get("transcript_references"))
            if not text or provenance not in {
                "review_extraction",
                "human_checkpoint",
            }:
                raise KnowledgeIndexError(
                    f"Approved curiosity field is invalid: {field_name}"
                )
            if provenance == "review_extraction":
                if (text, references) not in grounded_items:
                    raise KnowledgeIndexError(
                        "Approved curiosity signature invents review provenance."
                    )
            elif references:
                raise KnowledgeIndexError(
                    "Human curiosity corrections cannot claim transcript provenance."
                )

    if (
        signature_decision == "accepted"
        and approved_signature != expected_accepted_signature
    ):
        raise KnowledgeIndexError(
            "Accepted curiosity signature differs from the reviewed extraction."
        )

    review_claims = review.get("claims")
    claim_index = review_document.get("claim_index")
    if not isinstance(review_claims, list) or not isinstance(claim_index, list):
        raise KnowledgeIndexError("Approved review has an invalid claim index.")
    expected_claim_index = [
        {
            "claim_id": f"{review_id}:C{index}",
            **claim,
        }
        for index, claim in enumerate(review_claims, start=1)
        if isinstance(claim, dict)
    ]
    if (
        len(expected_claim_index) != len(review_claims)
        or claim_index != expected_claim_index
    ):
        raise KnowledgeIndexError(
            "Approved review claim identities do not match its canonical claims."
        )

    core_record = review.get("core_question")
    original_core_question = grounded_text(core_record, "core question")
    confirmed_core_question = required_text(
        human_decision.get("confirmed_core_question_or_thesis"),
        "human-confirmed core question",
    )
    core_id = make_node_id(conversation_id, "core-question")
    core_sections = []
    signature_references = []
    for field_name, heading in CURIOSITY_FIELD_LABELS.items():
        items = approved_signature[field_name]
        if not items:
            body = "Not established."
        else:
            rendered_items = []
            for item in items:
                references = string_list(item.get("transcript_references"))
                signature_references.extend(references)
                provenance = (
                    ", ".join(f"`{reference}`" for reference in references)
                    if references
                    else "`human checkpoint correction`"
                )
                rendered_items.append(
                    f"- {normalized_text(item['text'])} ({provenance})"
                )
            body = "\n".join(rendered_items)
        core_sections.append(KnowledgeSection(heading, body))

    if original_core_question != confirmed_core_question:
        core_sections.append(
            KnowledgeSection("Reviewer wording", original_core_question)
        )

    checkpoint_sections = [
        ("Accepted belief revisions", "accepted_belief_revisions"),
        ("Disputed points", "disputed_points"),
        ("Excluded material", "excluded_material"),
    ]
    for heading, key in checkpoint_sections:
        values = string_list(human_decision.get(key))
        if values:
            core_sections.append(
                KnowledgeSection(
                    heading,
                    "\n".join(f"- {value}" for value in values),
                )
            )

    nodes = [
        KnowledgeNode(
            node_id=core_id,
            node_type="core_question",
            title=compact_title(
                confirmed_core_question,
                "Core research question",
            ),
            summary=confirmed_core_question,
            status="active",
            source_review_ids=(review_id,),
            transcript_references=tuple(
                dict.fromkeys(
                    grounded_references(core_record)
                    + tuple(signature_references)
                )
            ),
            sections=tuple(core_sections),
        )
    ]

    starting_model = review.get("starting_model")
    if not isinstance(starting_model, dict):
        starting_model = {}
    belief_node_ids: dict[str, str] = {}
    for index, belief in enumerate(starting_model.get("beliefs", []), start=1):
        text = grounded_text(belief, "starting belief")
        node_id = numbered_node_id(conversation_id, "belief", index)
        belief_node_ids[text] = node_id
        nodes.append(
            KnowledgeNode(
                node_id=node_id,
                node_type="belief",
                title=compact_title(text, f"Starting belief {index}"),
                summary=text,
                status="recorded",
                source_review_ids=(review_id,),
                transcript_references=grounded_references(belief),
                relations=(KnowledgeRelation("addresses", core_id),),
            )
        )

    for index, revision in enumerate(
        review.get("belief_revisions", []),
        start=1,
    ):
        if not isinstance(revision, dict):
            raise KnowledgeIndexError("Belief revision is not a valid object.")
        original = revision.get("original_belief")
        original_text = grounded_text(original, "original belief")
        references = list(grounded_references(original))
        sections = [KnowledgeSection("Original belief", original_text)]

        for heading, key in (
            ("Assistant challenge", "assistant_challenge"),
            ("User response", "user_response"),
        ):
            grounded = revision.get(key)
            if grounded is None:
                continue
            sections.append(
                KnowledgeSection(heading, grounded_text(grounded, heading.lower()))
            )
            references.extend(grounded_references(grounded))

        target_id = belief_node_ids.get(original_text, core_id)
        nodes.append(
            KnowledgeNode(
                node_id=numbered_node_id(
                    conversation_id,
                    "belief-revision",
                    index,
                ),
                node_type="belief_revision",
                title=compact_title(
                    original_text,
                    f"Belief revision {index}",
                ),
                summary=original_text,
                status=required_text(
                    revision.get("status"),
                    "belief revision status",
                ),
                source_review_ids=(review_id,),
                transcript_references=tuple(dict.fromkeys(references)),
                relations=(KnowledgeRelation("revises", target_id),),
                sections=tuple(sections),
            )
        )

    source_receipts = review_document.get("source_receipts", [])
    if not isinstance(source_receipts, list):
        raise KnowledgeIndexError("Approved review has an invalid source index.")
    checked_sources = review.get("checked_sources", [])
    assessments = {
        source.get("receipt_id"): source
        for source in checked_sources
        if isinstance(source, dict) and isinstance(source.get("receipt_id"), str)
    }
    receipt_ids = [
        receipt.get("receipt_id")
        for receipt in source_receipts
        if isinstance(receipt, dict)
    ]
    if (
        len(receipt_ids) != len(source_receipts)
        or len(receipt_ids) != len(set(receipt_ids))
        or set(assessments) != set(receipt_ids)
    ):
        raise KnowledgeIndexError(
            "Approved review source identities do not match its checked sources."
        )
    source_node_ids = {}
    for index, receipt in enumerate(source_receipts, start=1):
        if not isinstance(receipt, dict):
            raise KnowledgeIndexError("Source receipt is not a valid object.")
        receipt_id = required_text(receipt.get("receipt_id"), "source receipt ID")
        assessment = assessments.get(receipt_id, {})
        title = (
            normalized_text(receipt.get("title"))
            or normalized_text(receipt.get("final_url"))
            or receipt_id
        )
        summary = (
            normalized_text(assessment.get("evidence_summary"))
            or f"Checked research source: {title}"
        )
        source_node_id = numbered_node_id(conversation_id, "source", index)
        source_node_ids[receipt_id] = source_node_id
        sections = []
        for heading, key in (
            ("Source", "final_url"),
            ("Evidence location", "evidence_location"),
            ("Assessment", "assessment"),
            ("Limitations", "limitations"),
            (
                "Counterevidence or alternative explanation",
                "counterevidence_or_alternative_explanation",
            ),
        ):
            value = (
                receipt.get(key)
                if key == "final_url"
                else assessment.get(key)
            )
            text = normalized_text(value)
            if text:
                sections.append(KnowledgeSection(heading, text))

        nodes.append(
            KnowledgeNode(
                node_id=source_node_id,
                node_type="source",
                title=compact_title(title, f"Checked source {index}"),
                summary=summary,
                status=normalized_text(assessment.get("finding")) or "checked",
                source_review_ids=(review_id,),
                source_receipt_ids=(receipt_id,),
                transcript_references=grounded_references(assessment),
                sections=tuple(sections),
            )
        )

    for index, claim in enumerate(claim_index, start=1):
        if not isinstance(claim, dict):
            raise KnowledgeIndexError("Indexed claim is not a valid object.")
        claim_id = required_text(claim.get("claim_id"), "claim ID")
        claim_text = required_text(claim.get("claim"), "claim")
        receipt_ids = string_list(claim.get("source_receipt_ids"))
        relations = [KnowledgeRelation("addresses", core_id)]
        status = required_text(claim.get("status"), "claim status")

        for receipt_id in receipt_ids:
            try:
                source_node_id = source_node_ids[receipt_id]
            except KeyError as error:
                raise KnowledgeIndexError(
                    f"Claim references an unindexed source receipt: {receipt_id}"
                ) from error

            if status == "contradicted_by_checked_source":
                relations.append(
                    KnowledgeRelation("contradicts", source_node_id)
                )
            elif status == "qualified_by_checked_source":
                relations.append(KnowledgeRelation("qualifies", source_node_id))
            else:
                relations.append(
                    KnowledgeRelation("supported_by", source_node_id)
                )

        sections = (
            KnowledgeSection(
                "Origin",
                required_text(claim.get("origin"), "claim origin"),
            ),
            KnowledgeSection(
                "Next verification",
                required_text(
                    claim.get("next_verification"),
                    "claim verification step",
                ),
            ),
        )
        nodes.append(
            KnowledgeNode(
                node_id=numbered_node_id(conversation_id, "claim", index),
                node_type="claim",
                title=compact_title(claim_text, f"Claim {index}"),
                summary=claim_text,
                status=status,
                source_review_ids=(review_id,),
                claim_ids=(claim_id,),
                source_receipt_ids=receipt_ids,
                transcript_references=string_list(
                    claim.get("transcript_references")
                ),
                relations=tuple(relations),
                sections=sections,
            )
        )

    for index, idea in enumerate(
        review.get("connected_ideas", []),
        start=1,
    ):
        if not isinstance(idea, dict):
            raise KnowledgeIndexError("Connected idea is not a valid object.")
        text = required_text(idea.get("idea"), "connected idea")
        review_relationship = required_text(
            idea.get("relationship"),
            "idea relationship",
        )
        relationship, core_is_source = relationship_for_connected_idea(
            review_relationship
        )
        node_id = numbered_node_id(conversation_id, "concept", index)
        if core_is_source:
            relation = KnowledgeRelation("addresses", core_id)
            core_relation = KnowledgeRelation(relationship, node_id)
        else:
            relation = KnowledgeRelation(relationship, core_id)
            core_relation = None

        nodes.append(
            KnowledgeNode(
                node_id=node_id,
                node_type="concept",
                title=compact_title(text, f"Connected concept {index}"),
                summary=text,
                status="active",
                source_review_ids=(review_id,),
                transcript_references=string_list(
                    idea.get("transcript_references")
                ),
                relations=(relation,),
                sections=(
                    KnowledgeSection(
                        "Review relationship",
                        review_relationship,
                    ),
                ),
            )
        )
        if core_relation is not None:
            core_node = nodes[0]
            nodes[0] = KnowledgeNode(
                **{
                    **core_node.__dict__,
                    "relations": core_node.relations + (core_relation,),
                }
            )

    for index, branch in enumerate(
        review.get("separate_branches", []),
        start=1,
    ):
        if not isinstance(branch, dict):
            raise KnowledgeIndexError("Separate branch is not a valid object.")
        text = required_text(branch.get("idea"), "separate book branch")
        node_id = numbered_node_id(conversation_id, "book-branch", index)
        nodes.append(
            KnowledgeNode(
                node_id=node_id,
                node_type="book_branch",
                title=compact_title(text, f"Book branch {index}"),
                summary=text,
                status="candidate",
                source_review_ids=(review_id,),
                transcript_references=string_list(
                    branch.get("transcript_references")
                ),
                relations=(KnowledgeRelation("addresses", core_id),),
                sections=(
                    KnowledgeSection(
                        "Review relationship",
                        required_text(
                            branch.get("relationship"),
                            "branch relationship",
                        ),
                    ),
                ),
            )
        )
        core_node = nodes[0]
        nodes[0] = KnowledgeNode(
            **{
                **core_node.__dict__,
                "relations": core_node.relations
                + (KnowledgeRelation("branches_into", node_id),),
            }
        )

    for index, question in enumerate(
        review.get("thought_questions", []),
        start=1,
    ):
        text = grounded_text(question, "reflection question")
        nodes.append(
            KnowledgeNode(
                node_id=numbered_node_id(
                    conversation_id,
                    "reflection-question",
                    index,
                ),
                node_type="reflection_question",
                title=compact_title(text, f"Reflection question {index}"),
                summary=text,
                status="open",
                source_review_ids=(review_id,),
                transcript_references=grounded_references(question),
                relations=(KnowledgeRelation("addresses", core_id),),
            )
        )

    for index, experiment in enumerate(
        review.get("experiments", []),
        start=1,
    ):
        if not isinstance(experiment, dict):
            raise KnowledgeIndexError("Experiment is not a valid object.")
        hypothesis = required_text(
            experiment.get("hypothesis"),
            "experiment hypothesis",
        )
        node_id = numbered_node_id(conversation_id, "experiment", index)
        sections = []
        for heading, key in (
            ("Observable outcome", "observable_outcome"),
            ("Comparison or baseline", "comparison_or_baseline"),
            (
                "Evidence that would weaken the claim",
                "evidence_that_would_weaken_the_claim",
            ),
            (
                "Major unresolved dependency",
                "major_unresolved_dependency",
            ),
        ):
            sections.append(
                KnowledgeSection(
                    heading,
                    required_text(
                        experiment.get(key),
                        f"experiment {key.replace('_', ' ')}",
                    ),
                )
            )
        nodes.append(
            KnowledgeNode(
                node_id=node_id,
                node_type="experiment",
                title=compact_title(hypothesis, f"Experiment {index}"),
                summary=hypothesis,
                status="proposed",
                source_review_ids=(review_id,),
                transcript_references=string_list(
                    experiment.get("transcript_references")
                ),
                relations=(KnowledgeRelation("addresses", core_id),),
                sections=tuple(sections),
            )
        )
        core_node = nodes[0]
        nodes[0] = KnowledgeNode(
            **{
                **core_node.__dict__,
                "relations": core_node.relations
                + (KnowledgeRelation("tested_by", node_id),),
            }
        )

    for index, use_case in enumerate(
        review.get("use_case_candidates", []),
        start=1,
    ):
        text = grounded_text(use_case, "use case")
        node_id = numbered_node_id(conversation_id, "use-case", index)
        nodes.append(
            KnowledgeNode(
                node_id=node_id,
                node_type="use_case",
                title=compact_title(text, f"Use case {index}"),
                summary=text,
                status="candidate",
                source_review_ids=(review_id,),
                transcript_references=grounded_references(use_case),
                relations=(KnowledgeRelation("addresses", core_id),),
            )
        )
        core_node = nodes[0]
        nodes[0] = KnowledgeNode(
            **{
                **core_node.__dict__,
                "relations": core_node.relations
                + (KnowledgeRelation("applies_to", node_id),),
            }
        )

    for index, gap in enumerate(review.get("evidence_gaps", []), start=1):
        text = grounded_text(gap, "evidence gap")
        nodes.append(
            KnowledgeNode(
                node_id=numbered_node_id(
                    conversation_id,
                    "evidence-gap",
                    index,
                ),
                node_type="evidence_gap",
                title=compact_title(text, f"Evidence gap {index}"),
                summary=text,
                status="open",
                source_review_ids=(review_id,),
                transcript_references=grounded_references(gap),
                relations=(KnowledgeRelation("qualifies", core_id),),
            )
        )

    validate_nodes(nodes)
    return nodes


def validate_nodes(nodes: list[KnowledgeNode]) -> None:
    """Reject duplicate nodes, duplicate edges, and dangling relationships."""
    node_ids = [node.node_id for node in nodes]
    duplicates = sorted(
        node_id for node_id in set(node_ids) if node_ids.count(node_id) > 1
    )
    if duplicates:
        raise KnowledgeIndexError(
            "Knowledge index contains duplicate node IDs: "
            + ", ".join(duplicates)
        )

    known_node_ids = set(node_ids)
    seen_edges = set()
    for node in nodes:
        for relation in node.relations:
            if relation.target_node_id not in known_node_ids:
                raise KnowledgeIndexError(
                    f"{node.node_id} links to an unknown knowledge node: "
                    f"{relation.target_node_id}"
                )
            edge = (
                node.node_id,
                relation.relationship,
                relation.target_node_id,
            )
            if edge in seen_edges:
                raise KnowledgeIndexError(
                    f"Knowledge index repeats a relationship: {edge}"
                )
            seen_edges.add(edge)


def json_frontmatter(value: Any) -> str:
    """Render values as a YAML-compatible JSON scalar or collection."""
    return json.dumps(value, ensure_ascii=False)


def render_node(node: KnowledgeNode) -> str:
    """Render one canonical Markdown knowledge node."""
    frontmatter = {
        "id": node.node_id,
        "title": node.title,
        "type": node.node_type,
        "status": node.status,
        "summary": node.summary,
        "source_reviews": list(node.source_review_ids),
        "claim_refs": list(node.claim_ids),
        "source_receipts": list(node.source_receipt_ids),
        "transcript_refs": list(node.transcript_references),
        "relations": [relation.encode() for relation in node.relations],
    }
    lines = ["---"]
    lines.extend(
        f"{key}: {json_frontmatter(value)}"
        for key, value in frontmatter.items()
    )
    lines.extend(["---", "", f"# {node.title}", "", node.summary, ""])

    for section in node.sections:
        lines.extend([f"## {section.heading}", "", section.body, ""])

    if node.relations:
        lines.extend(["## Relationships", ""])
        for relation in node.relations:
            label = RELATIONSHIP_LABELS[relation.relationship]
            lines.append(f"- {label}: [[{relation.target_node_id}]]")
        lines.append("")

    lines.extend(
        [
            "## Provenance",
            "",
            "- Review: "
            + ", ".join(f"`{item}`" for item in node.source_review_ids),
            "- Claims: "
            + (
                ", ".join(f"`{item}`" for item in node.claim_ids)
                or "None"
            ),
            "- Source receipts: "
            + (
                ", ".join(f"`{item}`" for item in node.source_receipt_ids)
                or "None"
            ),
            "- Transcript: "
            + (
                ", ".join(f"`{item}`" for item in node.transcript_references)
                or "None"
            ),
            "",
        ]
    )
    return "\n".join(lines)


def parse_frontmatter(path: Path) -> dict[str, Any]:
    """Parse the flat JSON-valued frontmatter written by this module."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise KnowledgeIndexError(
            f"Knowledge node could not be read: {path}"
        ) from error

    if not lines or lines[0] != "---":
        raise KnowledgeIndexError(
            f"Knowledge node has no frontmatter: {path.name}"
        )

    frontmatter = {}
    for line in lines[1:]:
        if line == "---":
            return frontmatter
        key, separator, encoded_value = line.partition(":")
        if not separator:
            raise KnowledgeIndexError(
                f"Knowledge node has invalid frontmatter: {path.name}"
            )
        try:
            frontmatter[key] = json.loads(encoded_value.strip())
        except json.JSONDecodeError as error:
            raise KnowledgeIndexError(
                f"Knowledge node has invalid {key} metadata: {path.name}"
            ) from error

    raise KnowledgeIndexError(
        f"Knowledge node frontmatter is not closed: {path.name}"
    )


def parse_relation(encoded: Any, path: Path) -> KnowledgeRelation:
    """Parse one flat relationship from a Markdown node."""
    if not isinstance(encoded, str):
        raise KnowledgeIndexError(
            f"Knowledge node has a non-text relationship: {path.name}"
        )
    relationship, separator, target = encoded.partition("::")
    if not separator:
        raise KnowledgeIndexError(
            f"Knowledge node has an invalid relationship: {path.name}"
        )
    return KnowledgeRelation(relationship, target)


def load_node(path: Path, knowledge_path: Path) -> KnowledgeNode:
    """Load the indexed fields needed to rebuild derived views."""
    frontmatter = parse_frontmatter(path)
    try:
        node = KnowledgeNode(
            node_id=frontmatter["id"],
            node_type=frontmatter["type"],
            title=frontmatter["title"],
            summary=frontmatter["summary"],
            status=frontmatter["status"],
            source_review_ids=tuple(frontmatter["source_reviews"]),
            claim_ids=tuple(frontmatter["claim_refs"]),
            source_receipt_ids=tuple(frontmatter["source_receipts"]),
            transcript_references=tuple(frontmatter["transcript_refs"]),
            relations=tuple(
                parse_relation(item, path)
                for item in frontmatter["relations"]
            ),
            relative_path=path.relative_to(knowledge_path).as_posix(),
        )
    except (KeyError, TypeError) as error:
        raise KnowledgeIndexError(
            f"Knowledge node metadata is incomplete: {path.name}"
        ) from error

    if path.stem != node.node_id:
        raise KnowledgeIndexError(
            f"Knowledge node filename does not match its ID: {path.name}"
        )
    return node


def load_existing_nodes(knowledge_path: Path) -> list[KnowledgeNode]:
    """Load all canonical Markdown nodes currently in the index."""
    nodes_path = knowledge_path / "nodes"
    if not nodes_path.exists():
        return []
    return [
        load_node(path, knowledge_path)
        for path in sorted(nodes_path.glob("*/*.md"))
    ]


def render_index(nodes: list[KnowledgeNode], generated_at: str) -> str:
    """Render the human-readable catalog used for browsing the index."""
    lines = [
        "# Alexandrian Knowledge Index",
        "",
        f"Generated at: `{generated_at}`",
        "",
        "This catalog is derived from the canonical Markdown nodes under "
        "`nodes/`.",
        "",
    ]
    for node_type, heading in NODE_TYPE_LABELS.items():
        matching_nodes = sorted(
            (node for node in nodes if node.node_type == node_type),
            key=lambda node: (node.title.casefold(), node.node_id),
        )
        if not matching_nodes:
            continue
        lines.extend([f"## {heading}", ""])
        for node in matching_nodes:
            if node.relative_path is None:
                raise KnowledgeIndexError(
                    f"Knowledge node has no catalog path: {node.node_id}"
                )
            lines.append(
                f"- [{node.title}]({node.relative_path})"
                f" - {node.summary} (`{node.status}`)"
            )
        lines.append("")

    return "\n".join(lines)


def render_graph(nodes: list[KnowledgeNode], generated_at: str) -> str:
    """Render a machine-readable graph derived from canonical Markdown."""
    graph_nodes = []
    graph_edges = []
    for node in sorted(nodes, key=lambda item: item.node_id):
        graph_nodes.append(
            {
                "id": node.node_id,
                "type": node.node_type,
                "title": node.title,
                "summary": node.summary,
                "status": node.status,
                "path": node.relative_path,
                "source_review_ids": list(node.source_review_ids),
                "claim_ids": list(node.claim_ids),
                "source_receipt_ids": list(node.source_receipt_ids),
                "transcript_references": list(node.transcript_references),
            }
        )
        graph_edges.extend(
            {
                "source": node.node_id,
                "target": relation.target_node_id,
                "type": relation.relationship,
                "source_review_ids": list(node.source_review_ids),
            }
            for relation in node.relations
        )

    return (
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at": generated_at,
                "nodes": graph_nodes,
                "edges": sorted(
                    graph_edges,
                    key=lambda edge: (
                        edge["source"],
                        edge["type"],
                        edge["target"],
                    ),
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


def write_text_atomically(path: Path, content: str) -> None:
    """Replace one derived view without exposing partial content."""
    temporary_path = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        temporary_path.write_text(content, encoding="utf-8")
        os.replace(temporary_path, path)
    except OSError as error:
        temporary_path.unlink(missing_ok=True)
        raise KnowledgeIndexError(
            f"Knowledge index could not write {path.name}."
        ) from error


def append_promotion_event(
    knowledge_path: Path,
    review_id: str,
    promoted_at: str,
    nodes: list[KnowledgeNode],
) -> None:
    """Append one parseable audit event after a successful promotion."""
    event = {
        "schema_version": "1.0",
        "event": "review_promoted",
        "review_id": review_id,
        "promoted_at": promoted_at,
        "node_ids": [node.node_id for node in nodes],
    }
    try:
        with (knowledge_path / "events.jsonl").open(
            "a",
            encoding="utf-8",
        ) as event_file:
            event_file.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError as error:
        raise KnowledgeIndexError(
            "Knowledge promotion was saved, but its audit event was not."
        ) from error


def promote_approved_review(
    review_path: Path,
    decision_path: Path,
    knowledge_path: Path,
) -> dict:
    """Promote one approved review and rebuild its derived index views."""
    try:
        review_document = json.loads(review_path.read_text(encoding="utf-8"))
        human_decision = json.loads(decision_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise KnowledgeIndexError(
            "Knowledge promotion inputs could not be read."
        ) from error
    if not isinstance(review_document, dict) or not isinstance(
        human_decision,
        dict,
    ):
        raise KnowledgeIndexError(
            "Knowledge promotion inputs must contain JSON objects."
        )

    review_id = required_text(review_document.get("review_id"), "review ID")
    nodes = build_review_nodes(review_document, human_decision)
    knowledge_path.mkdir(parents=True, exist_ok=True)
    nodes_path = knowledge_path / "nodes"
    nodes_path.mkdir(parents=True, exist_ok=True)
    review_directory = nodes_path / review_id
    if review_directory.exists():
        raise KnowledgeIndexError(
            f"Review has already been promoted: {review_id}"
        )

    existing_nodes = load_existing_nodes(knowledge_path)
    staged_directory = nodes_path / f".tmp-{uuid.uuid4().hex}"
    promoted_at = current_utc_time()

    try:
        staged_directory.mkdir()
        staged_nodes = []
        for node in nodes:
            node_path = staged_directory / f"{node.node_id}.md"
            node_path.write_text(render_node(node), encoding="utf-8")
            staged_nodes.append(
                KnowledgeNode(
                    **{
                        **node.__dict__,
                        "relative_path": (
                            f"nodes/{review_id}/{node.node_id}.md"
                        ),
                    }
                )
            )

        all_nodes = existing_nodes + staged_nodes
        validate_nodes(all_nodes)
        index_content = render_index(all_nodes, promoted_at)
        graph_content = render_graph(all_nodes, promoted_at)

        os.replace(staged_directory, review_directory)
        write_text_atomically(knowledge_path / "index.md", index_content)
        write_text_atomically(knowledge_path / "graph.json", graph_content)
    except (OSError, KnowledgeIndexError) as error:
        if staged_directory.exists():
            shutil.rmtree(staged_directory)
        if isinstance(error, KnowledgeIndexError):
            raise
        raise KnowledgeIndexError(
            "Knowledge promotion could not be saved."
        ) from error

    append_promotion_event(
        knowledge_path,
        review_id,
        promoted_at,
        staged_nodes,
    )
    edge_count = sum(len(node.relations) for node in staged_nodes)
    return {
        "review_id": review_id,
        "promoted_at": promoted_at,
        "node_count": len(staged_nodes),
        "edge_count": edge_count,
        "review_nodes_path": review_directory,
        "index_path": knowledge_path / "index.md",
        "graph_path": knowledge_path / "graph.json",
        "event_log_path": knowledge_path / "events.jsonl",
    }
