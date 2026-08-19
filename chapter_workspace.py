import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SPINE_SECTION_NAMES = (
    "central_question",
    "original_intuition",
    "why_it_matters",
    "current_answer",
    "evidence_and_reasoning",
    "important_contradictions",
    "evolution_of_understanding",
    "practical_implications",
    "unresolved_frontier",
    "connection_to_larger_book",
)


class WorkspaceError(RuntimeError):
    """Report an invalid or unsafe chapter-workspace operation."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PositionType(str, Enum):
    PROVISIONAL_ANSWER = "provisional_answer"
    COMPETING_EXPLANATIONS = "competing_explanations"
    WORKING_HYPOTHESIS = "working_hypothesis"
    UNRESOLVED_FRAMING = "unresolved_framing"


class SectionStatus(str, Enum):
    UNADDRESSED = "unaddressed"
    IN_PROGRESS = "in_progress"
    ADDRESSED = "addressed"
    ROUTED = "routed"


class BranchOrigin(str, Enum):
    IDEA = "idea"
    CURRICULUM = "curriculum"


class QuestionKind(str, Enum):
    CONCEPTUAL = "conceptual"
    EMPIRICAL = "empirical"
    PHILOSOPHICAL = "philosophical"
    DESIGN = "design"
    INTERPRETIVE = "interpretive"


class BranchImportance(str, Enum):
    BLOCKING = "blocking"
    NONBLOCKING = "nonblocking"


class BranchStatus(str, Enum):
    UNEXPLORED = "unexplored"
    RESEARCHING = "researching"
    RESOLVED = "resolved"
    BOUNDED_UNRESOLVED = "bounded_unresolved"
    ROUTED = "routed"


class NextAction(str, Enum):
    RESEARCH = "research"
    EXPLAIN = "explain"
    EXPERIMENT = "experiment"
    IMPLEMENT = "implement"
    REFLECT = "reflect"
    ROUTE = "route"


class DestinationType(str, Enum):
    CURRENT_CHAPTER = "current_chapter"
    PREREQUISITE_CHAPTER = "prerequisite_chapter"
    LATER_CHAPTER = "later_chapter"
    SEPARATE_BOOK = "separate_book"
    EXPERIMENT = "experiment"
    PROJECT = "project"
    OPEN_QUESTION = "open_question"


class ClosureReason(str, Enum):
    EVIDENCE_SUFFICIENT = "evidence_sufficient"
    OUTSIDE_CHAPTER_SCOPE = "outside_chapter_scope"
    REQUIRES_EXPERIMENT = "requires_experiment"
    REQUIRES_USER_REFLECTION = "requires_user_reflection"
    CURRENTLY_UNANSWERABLE = "currently_unanswerable"
    FUNDAMENTALLY_OPEN = "fundamentally_open"
    MOVED_TO_OTHER_ARTIFACT = "moved_to_other_artifact"
    DUPLICATE = "duplicate"


class RequirementCategory(str, Enum):
    HISTORICAL_CONTEXT = "historical_context"
    PHILOSOPHY = "philosophy"
    MATHEMATICS = "mathematics"
    SCIENTIFIC_MECHANISM = "scientific_mechanism"
    IMPLEMENTATION = "implementation"
    EXPERIMENT = "experiment"


class RequirementStatus(str, Enum):
    UNADDRESSED = "unaddressed"
    IN_PROGRESS = "in_progress"
    SATISFIED = "satisfied"
    BOUNDED_UNRESOLVED = "bounded_unresolved"
    ROUTED = "routed"


class BeliefChangeType(str, Enum):
    STRENGTHENED = "strengthened"
    NARROWED = "narrowed"
    REVISED = "revised"
    CONTRADICTED = "contradicted"
    UNCHANGED_AFTER_INVESTIGATION = "unchanged_after_investigation"
    UNRESOLVED = "unresolved"


class FalsificationStatus(str, Enum):
    UNTESTED = "untested"
    TESTABLE = "testable"
    TESTED_WEAKENED = "tested_weakened"
    TESTED_SURVIVED = "tested_survived"
    INCONCLUSIVE = "inconclusive"


class RevisionStatus(str, Enum):
    PLANNED = "planned"
    APPLIED = "applied"
    SUPERSEDED = "superseded"


class ContextReferences(StrictModel):
    claim_ids: list[str] = Field(default_factory=list)
    evidence_receipt_ids: list[str] = Field(default_factory=list)
    knowledge_node_ids: list[str] = Field(default_factory=list)
    transcript_references: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_references(self) -> "ContextReferences":
        for field_name in (
            "claim_ids",
            "evidence_receipt_ids",
            "knowledge_node_ids",
            "transcript_references",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(
                    f"{field_name} cannot contain duplicate references."
                )
        return self


class CurrentPosition(ContextReferences):
    position_type: PositionType
    statement: str = Field(min_length=1)
    confidence_basis: str = Field(min_length=1)
    unresolved_tensions: list[str] = Field(default_factory=list)


class SpineSection(ContextReferences):
    objective: str = Field(min_length=1)
    status: SectionStatus
    open_questions: list[str] = Field(default_factory=list)


class ChapterSpine(StrictModel):
    central_question: SpineSection
    original_intuition: SpineSection
    why_it_matters: SpineSection
    current_answer: SpineSection
    evidence_and_reasoning: SpineSection
    important_contradictions: SpineSection
    evolution_of_understanding: SpineSection
    practical_implications: SpineSection
    unresolved_frontier: SpineSection
    connection_to_larger_book: SpineSection


class ResearchBranch(ContextReferences):
    branch_id: str = Field(pattern=r"^BR[1-9][0-9]*$")
    origin: BranchOrigin
    question: str = Field(min_length=1)
    reason_required: str = Field(min_length=1)
    question_kind: QuestionKind
    importance: BranchImportance
    status: BranchStatus
    next_action: NextAction
    destination_type: DestinationType
    destination_id: str | None = None
    closure_reason: ClosureReason | None = None

    @model_validator(mode="after")
    def validate_state(self) -> "ResearchBranch":
        closed_statuses = {
            BranchStatus.RESOLVED,
            BranchStatus.BOUNDED_UNRESOLVED,
            BranchStatus.ROUTED,
        }
        if self.status in closed_statuses and self.closure_reason is None:
            raise ValueError("A closed branch requires a closure reason.")
        if self.status not in closed_statuses and self.closure_reason is not None:
            raise ValueError("An active branch cannot have a closure reason.")
        if (
            self.status == BranchStatus.ROUTED
            and self.destination_type == DestinationType.CURRENT_CHAPTER
        ):
            raise ValueError("A routed branch requires an external destination.")
        if (
            self.destination_type == DestinationType.CURRENT_CHAPTER
            and self.destination_id is not None
        ):
            raise ValueError("A current-chapter branch cannot name a destination.")
        return self


class BeliefEvolutionEvent(ContextReferences):
    event_id: str = Field(pattern=r"^BE[1-9][0-9]*$")
    belief_before: str = Field(min_length=1)
    belief_after: str = Field(min_length=1)
    change_type: BeliefChangeType
    reason_for_change: str = Field(min_length=1)
    remaining_uncertainty: str = Field(min_length=1)


class DevelopmentRequirement(ContextReferences):
    requirement_id: str = Field(pattern=r"^REQ[1-9][0-9]*$")
    category: RequirementCategory
    description: str = Field(min_length=1)
    why_required: str = Field(min_length=1)
    related_branch_ids: list[str] = Field(default_factory=list)
    status: RequirementStatus
    destination_id: str | None = None
    closure_reason: ClosureReason | None = None

    @model_validator(mode="after")
    def validate_routing(self) -> "DevelopmentRequirement":
        if self.status == RequirementStatus.ROUTED and not self.destination_id:
            raise ValueError("A routed requirement needs a destination.")
        if (
            self.status == RequirementStatus.BOUNDED_UNRESOLVED
            and self.closure_reason is None
        ):
            raise ValueError(
                "A bounded requirement needs an explicit closure reason."
            )
        if (
            self.status
            in {
                RequirementStatus.UNADDRESSED,
                RequirementStatus.IN_PROGRESS,
            }
            and self.closure_reason is not None
        ):
            raise ValueError(
                "An active requirement cannot have a closure reason."
            )
        return self


class ChapterCompletionContract(StrictModel):
    frozen: Literal[True] = True
    frozen_at_version: int = Field(ge=1)
    blocking_branch_ids: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)
    research_packet_limit_per_item: int = Field(default=2, ge=1, le=10)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "ChapterCompletionContract":
        for field_name in ("blocking_branch_ids", "requirement_ids"):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(
                    f"{field_name} cannot contain duplicate identities."
                )
        return self


class FalsificationCondition(ContextReferences):
    condition_id: str = Field(pattern=r"^FC[1-9][0-9]*$")
    proposed_mechanism: str = Field(min_length=1)
    comparison_or_baseline: str = Field(min_length=1)
    observable_failure: str = Field(min_length=1)
    status: FalsificationStatus


class DraftVersionReference(StrictModel):
    draft_id: str = Field(pattern=r"^D[1-9][0-9]*$")
    relative_path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: str = Field(min_length=1)
    created_at: str = Field(min_length=1)


class TargetedRevision(StrictModel):
    revision_id: str = Field(pattern=r"^REV[1-9][0-9]*$")
    source_draft_id: str = Field(min_length=1)
    target_spine_sections: list[
        Literal[
            "central_question",
            "original_intuition",
            "why_it_matters",
            "current_answer",
            "evidence_and_reasoning",
            "important_contradictions",
            "evolution_of_understanding",
            "practical_implications",
            "unresolved_frontier",
            "connection_to_larger_book",
        ]
    ] = Field(min_length=1)
    reason: str = Field(min_length=1)
    related_branch_ids: list[str] = Field(default_factory=list)
    status: RevisionStatus
    result_draft_id: str | None = None

    @model_validator(mode="after")
    def validate_result(self) -> "TargetedRevision":
        if self.status == RevisionStatus.APPLIED and not self.result_draft_id:
            raise ValueError("An applied revision requires a result draft.")
        return self


class ChapterWorkspaceProposal(StrictModel):
    book_id: str = Field(pattern=r"^book-[A-Za-z0-9._-]+$")
    chapter_id: str = Field(pattern=r"^CH[1-9][0-9]*$")
    context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    completion_contract: ChapterCompletionContract | None = None
    current_position: CurrentPosition
    spine: ChapterSpine
    branches: list[ResearchBranch] = Field(default_factory=list)
    belief_evolution: list[BeliefEvolutionEvent] = Field(default_factory=list)
    development_requirements: list[DevelopmentRequirement] = Field(
        default_factory=list
    )
    falsification_conditions: list[FalsificationCondition] = Field(
        default_factory=list
    )
    draft_versions: list[DraftVersionReference] = Field(default_factory=list)
    targeted_revisions: list[TargetedRevision] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_internal_references(self) -> "ChapterWorkspaceProposal":
        enforce_sequential_ids(self.branches, "branch_id", "BR")
        enforce_sequential_ids(self.belief_evolution, "event_id", "BE")
        enforce_sequential_ids(
            self.development_requirements,
            "requirement_id",
            "REQ",
        )
        enforce_sequential_ids(
            self.falsification_conditions,
            "condition_id",
            "FC",
        )
        enforce_sequential_ids(self.draft_versions, "draft_id", "D")
        enforce_sequential_ids(
            self.targeted_revisions,
            "revision_id",
            "REV",
        )

        branch_ids = {branch.branch_id for branch in self.branches}
        blocking_branch_ids = {
            branch.branch_id
            for branch in self.branches
            if branch.importance == BranchImportance.BLOCKING
        }
        requirement_ids = {
            requirement.requirement_id
            for requirement in self.development_requirements
        }
        if self.completion_contract is not None:
            if (
                set(self.completion_contract.blocking_branch_ids)
                != blocking_branch_ids
            ):
                raise ValueError(
                    "Completion contract must contain every blocking branch."
                )
            if (
                set(self.completion_contract.requirement_ids)
                != requirement_ids
            ):
                raise ValueError(
                    "Completion contract must contain every requirement."
                )
        draft_ids = {draft.draft_id for draft in self.draft_versions}
        for requirement in self.development_requirements:
            unknown = set(requirement.related_branch_ids) - branch_ids
            if unknown:
                raise ValueError("A requirement references an unknown branch.")
        for revision in self.targeted_revisions:
            if revision.source_draft_id not in draft_ids:
                raise ValueError("A revision references an unknown source draft.")
            if (
                revision.result_draft_id is not None
                and revision.result_draft_id not in draft_ids
            ):
                raise ValueError("A revision references an unknown result draft.")
            if set(revision.related_branch_ids) - branch_ids:
                raise ValueError("A revision references an unknown branch.")
        return self


def freeze_completion_contract(
    proposal: ChapterWorkspaceProposal,
    frozen_at_version: int,
    source_workspace: dict | None = None,
) -> ChapterWorkspaceProposal:
    """Freeze the finite branch and requirement set for chapter closeout."""
    source = source_workspace or proposal.model_dump(mode="json")
    existing_contract = (
        source.get("completion_contract")
        if source_workspace is not None
        else None
    )
    if existing_contract is not None:
        contract = ChapterCompletionContract.model_validate(
            existing_contract
        )
    else:
        blocking_branch_ids = [
            branch["branch_id"]
            for branch in source.get("branches", [])
            if branch.get("importance") == BranchImportance.BLOCKING.value
        ]
        requirement_ids = [
            requirement["requirement_id"]
            for requirement in source.get("development_requirements", [])
        ]
        contract = ChapterCompletionContract(
            frozen_at_version=frozen_at_version,
            blocking_branch_ids=blocking_branch_ids,
            requirement_ids=requirement_ids,
        )
    if (
        proposal.completion_contract is not None
        and proposal.completion_contract != contract
    ):
        raise WorkspaceError(
            "Workspace completion contract is derived by the Library and "
            "cannot be supplied or changed by the agent."
        )
    if proposal.completion_contract == contract:
        return proposal
    try:
        return ChapterWorkspaceProposal.model_validate(
            {
                **proposal.model_dump(mode="json"),
                "completion_contract": contract.model_dump(mode="json"),
            }
        )
    except ValueError as error:
        raise WorkspaceError(
            "Workspace cannot expand its blocking completion contract."
        ) from error


def completion_blockers(
    workspace: ChapterWorkspaceProposal,
) -> list[str]:
    """Return every nonterminal item that prevents a chapter draft closeout."""
    contract = workspace.completion_contract
    if contract is None:
        return ["completion contract is not frozen"]

    blockers = []
    for section_name in SPINE_SECTION_NAMES:
        section = getattr(workspace.spine, section_name)
        if section.status not in {
            SectionStatus.ADDRESSED,
            SectionStatus.ROUTED,
        }:
            blockers.append(f"spine:{section_name}")

    branches = {
        branch.branch_id: branch for branch in workspace.branches
    }
    for branch_id in contract.blocking_branch_ids:
        branch = branches[branch_id]
        if branch.status not in {
            BranchStatus.RESOLVED,
            BranchStatus.BOUNDED_UNRESOLVED,
            BranchStatus.ROUTED,
        }:
            blockers.append(f"branch:{branch_id}")

    requirements = {
        requirement.requirement_id: requirement
        for requirement in workspace.development_requirements
    }
    for requirement_id in contract.requirement_ids:
        requirement = requirements[requirement_id]
        if requirement.status not in {
            RequirementStatus.SATISFIED,
            RequirementStatus.BOUNDED_UNRESOLVED,
            RequirementStatus.ROUTED,
        }:
            blockers.append(f"requirement:{requirement_id}")
    return blockers


def enforce_sequential_ids(
    records: list[Any],
    field_name: str,
    prefix: str,
) -> None:
    """Require deterministic identities ordered from one."""
    actual = [getattr(record, field_name) for record in records]
    expected = [
        f"{prefix}{index}" for index in range(1, len(records) + 1)
    ]
    if actual != expected:
        raise ValueError(f"{prefix} identities must be sequential from one.")


def current_utc_time() -> str:
    return datetime.now(timezone.utc).isoformat()


def collect_reference_values(value: Any, key: str) -> set[str]:
    """Collect one reference field recursively from a workspace proposal."""
    if isinstance(value, dict):
        values = set()
        for nested_key, nested_value in value.items():
            if nested_key == key and isinstance(nested_value, list):
                values.update(
                    item for item in nested_value if isinstance(item, str)
                )
            else:
                values.update(collect_reference_values(nested_value, key))
        return values
    if isinstance(value, list):
        values = set()
        for item in value:
            values.update(collect_reference_values(item, key))
        return values
    return set()


def build_deterministic_initial_workspace(
    context: dict,
) -> ChapterWorkspaceProposal:
    """Map approved context into a prose-free, draftable workspace skeleton."""
    book_id = context.get("book_id")
    chapter_id = context.get("chapter_id")
    context_sha256 = context.get("context_sha256")
    central_question = context.get("central_question")
    if not all(
        isinstance(value, str) and value
        for value in (
            book_id,
            chapter_id,
            context_sha256,
            central_question,
        )
    ):
        raise WorkspaceError(
            "Approved context cannot bootstrap a chapter workspace."
        )

    claim_ids = sorted(
        claim["claim_id"]
        for claim in context.get("claims", [])
        if isinstance(claim, dict) and isinstance(claim.get("claim_id"), str)
    )
    evidence_receipt_ids = sorted(
        packet["receipt_id"]
        for packet in context.get("evidence_packets", [])
        if isinstance(packet, dict)
        and isinstance(packet.get("receipt_id"), str)
    )
    knowledge_node_ids = sorted(
        node["id"]
        for node in context.get("knowledge", {}).get("nodes", [])
        if isinstance(node, dict) and isinstance(node.get("id"), str)
    )
    open_questions = [
        question
        for question in context.get("open_questions", [])
        if isinstance(question, str) and question
    ]
    signature = context.get("approved_curiosity_signature", {})
    original_items = signature.get("original_intuition", [])
    original_intuition = next(
        (
            item["text"]
            for item in original_items
            if isinstance(item, dict)
            and isinstance(item.get("text"), str)
            and item["text"]
        ),
        "Present the approved starting intuition and its limits.",
    )

    section_objectives = {
        "central_question": central_question,
        "original_intuition": original_intuition,
        "why_it_matters": (
            "Explain the approved stakes before moving into abstraction."
        ),
        "current_answer": (
            "Develop the strongest answer permitted by the approved record."
        ),
        "evidence_and_reasoning": (
            "Connect every approved claim to its evidence and limitations."
        ),
        "important_contradictions": (
            "Keep approved contradictions and counterpressure visible."
        ),
        "evolution_of_understanding": (
            "Show how the investigation changes or bounds the starting view."
        ),
        "practical_implications": (
            "State only implications supported by the approved record."
        ),
        "unresolved_frontier": (
            "Preserve every approved open question without false closure."
        ),
        "connection_to_larger_book": (
            "Create the approved transition into the larger book argument."
        ),
    }
    spine_sections = {}
    for section_name in SPINE_SECTION_NAMES:
        references = {}
        questions = []
        if section_name == "evidence_and_reasoning":
            references = {
                "claim_ids": claim_ids,
                "evidence_receipt_ids": evidence_receipt_ids,
                "knowledge_node_ids": knowledge_node_ids,
            }
        if section_name == "unresolved_frontier":
            questions = open_questions
        spine_sections[section_name] = SpineSection(
            objective=section_objectives[section_name],
            status=SectionStatus.ADDRESSED,
            open_questions=questions,
            **references,
        )

    category_by_kind = {
        "research": RequirementCategory.SCIENTIFIC_MECHANISM,
        "experiment": RequirementCategory.EXPERIMENT,
        "human_decision": RequirementCategory.PHILOSOPHY,
        "contradiction": RequirementCategory.PHILOSOPHY,
    }
    requirements = []
    for index, requirement in enumerate(
        context.get("open_requirements", []),
        start=1,
    ):
        if not isinstance(requirement, dict):
            continue
        disposition = requirement.get("disposition")
        status = RequirementStatus.UNADDRESSED
        destination_id = None
        closure_reason = None
        if disposition == "completed":
            status = RequirementStatus.SATISFIED
        elif disposition == "bounded_unresolved":
            status = RequirementStatus.BOUNDED_UNRESOLVED
            closure_reason = ClosureReason.CURRENTLY_UNANSWERABLE
        elif disposition == "routed_to_later":
            status = RequirementStatus.ROUTED
            destination_id = "later_work"
            closure_reason = ClosureReason.MOVED_TO_OTHER_ARTIFACT
        requirements.append(
            DevelopmentRequirement(
                requirement_id=f"REQ{index}",
                category=category_by_kind.get(
                    requirement.get("kind"),
                    RequirementCategory.PHILOSOPHY,
                ),
                description=str(
                    requirement.get("description")
                    or "Approved chapter requirement"
                ),
                why_required=(
                    "The approved chapter context requires this boundary to "
                    "remain explicit."
                ),
                status=status,
                destination_id=destination_id,
                closure_reason=closure_reason,
            )
        )

    proposal = ChapterWorkspaceProposal(
        book_id=book_id,
        chapter_id=chapter_id,
        context_sha256=context_sha256,
        current_position=CurrentPosition(
            position_type=PositionType.UNRESOLVED_FRAMING,
            statement=central_question,
            confidence_basis=(
                "The approved context defines the starting boundary; no "
                "chapter conclusion has been drafted yet."
            ),
            unresolved_tensions=open_questions,
        ),
        spine=ChapterSpine(**spine_sections),
        development_requirements=requirements,
    )
    validate_against_context(proposal, context)
    return proposal


def validate_against_context(
    proposal: ChapterWorkspaceProposal,
    context: dict,
) -> None:
    """Keep every workspace statement tied to its approved chapter context."""
    if (
        proposal.book_id != context.get("book_id")
        or proposal.chapter_id != context.get("chapter_id")
        or proposal.context_sha256 != context.get("context_sha256")
    ):
        raise WorkspaceError(
            "Workspace identity does not match its approved chapter context."
        )

    proposal_data = proposal.model_dump(mode="json")
    reference_sets = {
        "claim_ids": {
            claim.get("claim_id")
            for claim in context.get("claims", [])
            if isinstance(claim, dict)
        },
        "evidence_receipt_ids": {
            packet.get("receipt_id")
            for packet in context.get("evidence_packets", [])
            if isinstance(packet, dict)
        },
        "knowledge_node_ids": {
            node.get("id")
            for node in context.get("knowledge", {}).get("nodes", [])
            if isinstance(node, dict)
        },
        "transcript_references": collect_reference_values(
            context,
            "transcript_references",
        ),
    }
    for field_name, allowed_values in reference_sets.items():
        submitted = collect_reference_values(proposal_data, field_name)
        unknown = sorted(submitted - allowed_values)
        if unknown:
            raise WorkspaceError(
                f"Workspace references unknown {field_name}: "
                + ", ".join(unknown)
            )

    if (
        proposal.spine.central_question.objective
        != context.get("central_question")
    ):
        raise WorkspaceError(
            "Workspace spine.central_question.objective must exactly match "
            "the approved context central_question."
        )

    required_claim_ids = reference_sets["claim_ids"]
    mapped_claim_ids = set(
        proposal.spine.evidence_and_reasoning.claim_ids
    )
    if not required_claim_ids.issubset(mapped_claim_ids):
        raise WorkspaceError(
            "Workspace evidence spine omits context claims."
        )

    required_receipt_ids = reference_sets["evidence_receipt_ids"]
    mapped_receipt_ids = set(
        proposal.spine.evidence_and_reasoning.evidence_receipt_ids
    )
    if not required_receipt_ids.issubset(mapped_receipt_ids):
        raise WorkspaceError(
            "Workspace evidence spine omits context evidence receipts."
        )

    required_open_questions = set(context.get("open_questions", []))
    mapped_open_questions = set(
        proposal.spine.unresolved_frontier.open_questions
    )
    if not required_open_questions.issubset(mapped_open_questions):
        raise WorkspaceError(
            "Workspace frontier omits approved open questions."
        )


def record_map(records: list[dict], identity: str) -> dict[str, dict]:
    return {record[identity]: record for record in records}


def enforce_preserved_records(
    previous: dict,
    proposed: dict,
    collection: str,
    identity: str,
    immutable_fields: tuple[str, ...],
    append_only_fields: tuple[str, ...] = (),
    fully_immutable: bool = False,
) -> None:
    """Reject deleted, reordered, or identity-mutated durable records."""
    previous_records = previous.get(collection, [])
    proposed_records = proposed.get(collection, [])
    previous_ids = [record[identity] for record in previous_records]
    proposed_ids = [record[identity] for record in proposed_records]
    if proposed_ids[: len(previous_ids)] != previous_ids:
        raise WorkspaceError(
            f"Workspace cannot delete or reorder {collection}; preserve "
            f"existing {identity} values in their original order."
        )

    proposed_by_id = record_map(proposed_records, identity)
    for previous_record in previous_records:
        proposed_record = proposed_by_id[previous_record[identity]]
        if fully_immutable and proposed_record != previous_record:
            raise WorkspaceError(
                f"Workspace cannot mutate existing {collection} record "
                f"{previous_record[identity]}."
            )
        for field_name in immutable_fields:
            if proposed_record.get(field_name) != previous_record.get(
                field_name
            ):
                raise WorkspaceError(
                    f"Workspace cannot rewrite {collection} record "
                    f"{previous_record[identity]} field {field_name}; copy "
                    "its current value exactly."
                )
        for field_name in append_only_fields:
            previous_values = previous_record.get(field_name, [])
            proposed_values = proposed_record.get(field_name, [])
            if proposed_values[: len(previous_values)] != previous_values:
                raise WorkspaceError(
                    f"Workspace cannot remove or reorder {collection} "
                    "references."
                )


def validate_revision(
    previous_document: dict,
    proposal: ChapterWorkspaceProposal,
) -> None:
    """Preserve branch identity and append-only intellectual history."""
    proposed = proposal.model_dump(mode="json")
    previous = previous_document["workspace"]
    previous_contract = previous.get("completion_contract")
    proposed_contract = proposed.get("completion_contract")
    if previous_contract is not None and proposed_contract != previous_contract:
        raise WorkspaceError(
            "Workspace completion contract is frozen and cannot change."
        )
    enforce_preserved_records(
        previous,
        proposed,
        "branches",
        "branch_id",
        (
            "origin",
            "question",
            "reason_required",
            "question_kind",
        ),
        append_only_fields=(
            "claim_ids",
            "evidence_receipt_ids",
            "knowledge_node_ids",
            "transcript_references",
        ),
    )
    enforce_preserved_records(
        previous,
        proposed,
        "belief_evolution",
        "event_id",
        (),
        fully_immutable=True,
    )
    enforce_preserved_records(
        previous,
        proposed,
        "development_requirements",
        "requirement_id",
        ("category", "description", "why_required", "related_branch_ids"),
    )
    enforce_preserved_records(
        previous,
        proposed,
        "falsification_conditions",
        "condition_id",
        (
            "proposed_mechanism",
            "comparison_or_baseline",
            "observable_failure",
            "claim_ids",
        ),
    )
    enforce_preserved_records(
        previous,
        proposed,
        "draft_versions",
        "draft_id",
        (),
        fully_immutable=True,
    )
    enforce_preserved_records(
        previous,
        proposed,
        "targeted_revisions",
        "revision_id",
        (
            "source_draft_id",
            "target_spine_sections",
            "reason",
            "related_branch_ids",
        ),
    )


def make_workspace_document(
    proposal: ChapterWorkspaceProposal,
    version: int,
    created_at: str,
) -> dict:
    workspace = proposal.model_dump(mode="json")
    document = {
        "schema_version": "1.0",
        "workspace_version": version,
        "created_at": created_at,
        "workspace": workspace,
    }
    document["workspace_sha256"] = workspace_document_sha256(document)
    return document


def workspace_document_sha256(document: dict) -> str:
    """Hash a workspace document without trusting its recorded hash."""
    hashable_document = {
        key: value
        for key, value in document.items()
        if key != "workspace_sha256"
    }
    encoded = json.dumps(
        hashable_document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def render_workspace(document: dict) -> str:
    workspace = document["workspace"]
    lines = [
        "# Alexandrian Chapter Workspace",
        "",
        f"Book: `{workspace['book_id']}`",
        "",
        f"Chapter: `{workspace['chapter_id']}`",
        "",
        f"Workspace version: `{document['workspace_version']}`",
        "",
        f"Workspace hash: `{document['workspace_sha256']}`",
        "",
        "This artifact tracks chapter research state. It is not chapter prose.",
        "",
        "## Current Position",
        "",
        f"Type: `{workspace['current_position']['position_type']}`",
        "",
        workspace["current_position"]["statement"],
        "",
        f"Confidence basis: {workspace['current_position']['confidence_basis']}",
        "",
        "## Completion Contract",
        "",
        (
            "Not frozen."
            if workspace.get("completion_contract") is None
            else (
                "Frozen at workspace version "
                f"`{workspace['completion_contract']['frozen_at_version']}` "
                "with a research limit of "
                f"`{workspace['completion_contract']['research_packet_limit_per_item']}` "
                "packet(s) per contracted item."
            )
        ),
        "",
        "## Chapter Spine",
        "",
    ]
    for section_name in SPINE_SECTION_NAMES:
        section = workspace["spine"][section_name]
        heading = section_name.replace("_", " ").title()
        lines.extend(
            [
                f"### {heading}",
                "",
                f"- Status: `{section['status']}`",
                f"- Objective: {section['objective']}",
                "- Claims: " + (", ".join(section["claim_ids"]) or "None"),
                "- Evidence: "
                + (", ".join(section["evidence_receipt_ids"]) or "None"),
                "",
            ]
        )

    for heading, collection, identity, summary_key in (
        ("Branches", "branches", "branch_id", "question"),
        (
            "Belief Evolution",
            "belief_evolution",
            "event_id",
            "belief_after",
        ),
        (
            "Development Requirements",
            "development_requirements",
            "requirement_id",
            "description",
        ),
        (
            "Falsification Conditions",
            "falsification_conditions",
            "condition_id",
            "observable_failure",
        ),
        ("Draft Versions", "draft_versions", "draft_id", "relative_path"),
        (
            "Targeted Revisions",
            "targeted_revisions",
            "revision_id",
            "reason",
        ),
    ):
        lines.extend([f"## {heading}", ""])
        records = workspace[collection]
        if not records:
            lines.extend(["None recorded.", ""])
            continue
        for record in records:
            lines.append(
                f"- `{record[identity]}`: {record[summary_key]}"
            )
        lines.append("")
    return "\n".join(lines)


def read_json_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise WorkspaceError(f"{label} does not exist.") from error
    except (json.JSONDecodeError, OSError) as error:
        raise WorkspaceError(f"{label} is not valid JSON.") from error
    if not isinstance(value, dict):
        raise WorkspaceError(f"{label} must contain an object.")
    return value


def workspace_directory(book_directory: Path, chapter_id: str) -> Path:
    if not re.fullmatch(r"CH[1-9][0-9]*", chapter_id):
        raise WorkspaceError("Chapter ID is invalid.")
    return book_directory / "workspaces" / chapter_id


def initialize_workspace(
    book_directory: Path,
    proposal: ChapterWorkspaceProposal,
) -> dict:
    destination = workspace_directory(book_directory, proposal.chapter_id)
    if destination.exists():
        raise WorkspaceError("Chapter workspace already exists.")

    proposal = freeze_completion_contract(proposal, 1)
    created_at = current_utc_time()
    document = make_workspace_document(proposal, 1, created_at)
    parent = destination.parent
    temporary = parent / f".tmp-{uuid.uuid4().hex}"
    try:
        (temporary / "versions").mkdir(parents=True)
        json_path = temporary / "versions" / "v001.json"
        markdown_path = temporary / "versions" / "v001.md"
        json_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        markdown_path.write_text(
            render_workspace(document),
            encoding="utf-8",
        )
        index = {
            "schema_version": "1.0",
            "book_id": proposal.book_id,
            "chapter_id": proposal.chapter_id,
            "context_sha256": proposal.context_sha256,
            "current_version": 1,
            "current_json_path": "versions/v001.json",
            "current_markdown_path": "versions/v001.md",
        }
        (temporary / "index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, destination)
    except OSError as error:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise WorkspaceError("Chapter workspace could not be created.") from error
    return workspace_result(destination, document)


def load_current_workspace(
    book_directory: Path,
    chapter_id: str,
) -> tuple[Path, dict, dict]:
    directory = workspace_directory(book_directory, chapter_id)
    index = read_json_object(directory / "index.json", "Workspace index")
    resolved_directory = directory.resolve()
    current_path = (
        directory / str(index.get("current_json_path", ""))
    ).resolve()
    try:
        current_path.relative_to(resolved_directory)
    except ValueError as error:
        raise WorkspaceError(
            "Workspace index path escapes its directory."
        ) from error
    document = read_json_object(current_path, "Current workspace")
    workspace = document.get("workspace")
    if (
        index.get("schema_version") != "1.0"
        or document.get("schema_version") != "1.0"
        or not isinstance(workspace, dict)
        or index.get("chapter_id") != chapter_id
        or document.get("workspace_version") != index.get("current_version")
        or workspace.get("book_id") != index.get("book_id")
        or workspace.get("chapter_id") != chapter_id
        or workspace.get("context_sha256") != index.get("context_sha256")
    ):
        raise WorkspaceError(
            "Workspace index does not match its current version."
        )
    recorded_hash = document.get("workspace_sha256")
    if (
        not isinstance(recorded_hash, str)
        or recorded_hash != workspace_document_sha256(document)
    ):
        raise WorkspaceError("Current workspace hash does not match its content.")
    try:
        ChapterWorkspaceProposal.model_validate(workspace)
    except ValueError as error:
        raise WorkspaceError(
            "Current workspace content is structurally invalid."
        ) from error
    return directory, index, document


def revise_workspace(
    book_directory: Path,
    proposal: ChapterWorkspaceProposal,
    expected_version: int,
) -> dict:
    directory = workspace_directory(book_directory, proposal.chapter_id)
    lock_path = directory / ".revision.lock"
    try:
        lock_descriptor = os.open(
            lock_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
    except FileExistsError as error:
        raise WorkspaceError(
            "Another chapter workspace revision is in progress."
        ) from error
    except OSError as error:
        raise WorkspaceError(
            "Chapter workspace revision lock could not be created."
        ) from error
    try:
        return revise_workspace_while_locked(
            book_directory,
            proposal,
            expected_version,
        )
    finally:
        os.close(lock_descriptor)
        lock_path.unlink(missing_ok=True)


def revise_workspace_while_locked(
    book_directory: Path,
    proposal: ChapterWorkspaceProposal,
    expected_version: int,
) -> dict:
    """Write one revision while the public entry point holds its lock."""
    directory, index, previous = load_current_workspace(
        book_directory,
        proposal.chapter_id,
    )
    if index.get("book_id") != proposal.book_id:
        raise WorkspaceError("Workspace belongs to another book.")
    if index.get("context_sha256") != proposal.context_sha256:
        raise WorkspaceError("Workspace context identity cannot change.")
    if index.get("current_version") != expected_version:
        raise WorkspaceError("Workspace version changed before this revision.")

    proposal = freeze_completion_contract(
        proposal,
        expected_version + 1,
        previous.get("workspace"),
    )
    validate_revision(previous, proposal)
    version = expected_version + 1
    document = make_workspace_document(
        proposal,
        version,
        current_utc_time(),
    )
    version_name = f"v{version:03d}"
    json_path = directory / "versions" / f"{version_name}.json"
    markdown_path = directory / "versions" / f"{version_name}.md"
    if json_path.exists() or markdown_path.exists():
        raise WorkspaceError("Workspace version already exists.")

    suffix = f".tmp-{uuid.uuid4().hex}"
    temporary_json = json_path.with_name(json_path.name + suffix)
    temporary_markdown = markdown_path.with_name(markdown_path.name + suffix)
    installed_json = False
    installed_markdown = False
    try:
        temporary_json.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_markdown.write_text(
            render_workspace(document),
            encoding="utf-8",
        )
        os.replace(temporary_json, json_path)
        installed_json = True
        os.replace(temporary_markdown, markdown_path)
        installed_markdown = True
        updated_index = {
            **index,
            "current_version": version,
            "current_json_path": f"versions/{version_name}.json",
            "current_markdown_path": f"versions/{version_name}.md",
        }
        temporary_index = directory / f"index.json{suffix}"
        temporary_index.write_text(
            json.dumps(updated_index, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_index, directory / "index.json")
    except OSError as error:
        temporary_json.unlink(missing_ok=True)
        temporary_markdown.unlink(missing_ok=True)
        (directory / f"index.json{suffix}").unlink(missing_ok=True)
        if installed_json:
            json_path.unlink(missing_ok=True)
        if installed_markdown:
            markdown_path.unlink(missing_ok=True)
        raise WorkspaceError(
            "Chapter workspace revision could not be saved."
        ) from error
    return workspace_result(directory, document)


def workspace_result(directory: Path, document: dict) -> dict:
    version = document["workspace_version"]
    version_name = f"v{version:03d}"
    return {
        "workspace_version": version,
        "workspace_sha256": document["workspace_sha256"],
        "index_path": directory / "index.json",
        "json_path": directory / "versions" / f"{version_name}.json",
        "markdown_path": directory / "versions" / f"{version_name}.md",
    }
