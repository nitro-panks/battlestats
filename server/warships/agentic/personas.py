from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

from .artifacts import (
    ArchitectureArtifact,
    DesignBriefArtifact,
    EngineeringHandoffArtifact,
    ProductRequirementArtifact,
    QASummaryArtifact,
    RoutingPlanArtifact,
    SafetyReviewArtifact,
    UXBriefArtifact,
)


@dataclass(frozen=True)
class PersonaSpec:
    key: str
    label: str
    file_path: str
    crew_role: str
    crew_goal: str
    expected_output: str
    artifact_model: type[BaseModel]
    allow_delegation: bool = False


PERSONA_SPECS: tuple[PersonaSpec, ...] = (
    PersonaSpec(
        key="project_coordinator",
        label="Project Coordinator",
        file_path="agents/project-coordinator.md",
        crew_role="Project Coordinator",
        crew_goal="Convert incoming work into a routed, dependency-aware execution packet.",
        expected_output="A scoped routing plan with sequence, dependencies, and blockers.",
        artifact_model=RoutingPlanArtifact,
        allow_delegation=True,
    ),
    PersonaSpec(
        key="project_manager",
        label="Project Manager",
        file_path="agents/project-manager.md",
        crew_role="Project Manager",
        crew_goal="Turn the request into clear scope, milestones, and measurable acceptance criteria.",
        expected_output="A PRD-lite summary with scope, non-goals, acceptance criteria, and risks.",
        artifact_model=ProductRequirementArtifact,
        allow_delegation=True,
    ),
    PersonaSpec(
        key="architect",
        label="Architect",
        file_path="agents/architect.md",
        crew_role="System Architect",
        crew_goal="Define the integration boundaries, rollout shape, and operational guardrails.",
        expected_output="A technical design note covering interfaces, migration steps, and rollback.",
        artifact_model=ArchitectureArtifact,
    ),
    PersonaSpec(
        key="ux",
        label="UX",
        file_path="agents/ux.md",
        crew_role="UX Strategist",
        crew_goal="Specify user flows, feedback states, and cognitive-load reductions for the requested change.",
        expected_output="A concise UX brief with primary flow, edge states, and acceptance checks.",
        artifact_model=UXBriefArtifact,
    ),
    PersonaSpec(
        key="designer",
        label="Designer",
        file_path="agents/designer.md",
        crew_role="Product Designer",
        crew_goal="Translate UX intent into concrete visual, interaction, and responsive guidance.",
        expected_output="A visual implementation brief with state coverage and responsive notes.",
        artifact_model=DesignBriefArtifact,
    ),
    PersonaSpec(
        key="engineer",
        label="Engineer",
        file_path="agents/engineer-web-dev.md",
        crew_role="Staff Web Engineer",
        crew_goal="Implement the smallest production-ready vertical slice that satisfies the scoped requirements.",
        expected_output="An implementation handoff covering touched surfaces, edge states, and validation results.",
        artifact_model=EngineeringHandoffArtifact,
    ),
    PersonaSpec(
        key="qa",
        label="QA",
        file_path="agents/qa.md",
        crew_role="QA Lead",
        crew_goal="Build a risk-based verification plan and decide whether the scoped work is release-ready.",
        expected_output="A QA summary with traceability, risks, and a release recommendation.",
        artifact_model=QASummaryArtifact,
    ),
    PersonaSpec(
        key="safety",
        label="Safety",
        file_path="agents/safety.md",
        crew_role="Safety Reviewer",
        crew_goal="Surface security, privacy, abuse, and policy risks before release.",
        expected_output="A safety review with mitigations, residual risks, and go/no-go guidance.",
        artifact_model=SafetyReviewArtifact,
    ),
)


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    for candidate in current.parents:
        if (candidate / "docker-compose.yml").exists():
            return candidate
    return current.parents[3]


@lru_cache(maxsize=1)
def get_persona_specs() -> dict[str, PersonaSpec]:
    return {spec.key: spec for spec in PERSONA_SPECS}


def get_persona_sequence(keys: list[str] | None = None) -> list[PersonaSpec]:
    registry = get_persona_specs()
    if not keys:
        return list(PERSONA_SPECS)
    return [registry[key] for key in keys if key in registry]


@lru_cache(maxsize=None)
def read_persona_markdown(key: str) -> str:
    spec = get_persona_specs()[key]
    target = _repo_root() / spec.file_path
    return target.read_text(encoding="utf-8")


def get_persona_artifact_fields(key: str) -> list[str]:
    spec = get_persona_specs()[key]
    return list(spec.artifact_model.model_fields.keys())


def build_persona_runtime_brief(key: str) -> str:
    spec = get_persona_specs()[key]
    artifact_fields = ", ".join(get_persona_artifact_fields(key)) or "none"
    return (
        f"Persona key: {spec.key}\n"
        f"Persona label: {spec.label}\n"
        f"Crew role: {spec.crew_role}\n"
        f"Primary goal: {spec.crew_goal}\n"
        f"Expected output: {spec.expected_output}\n"
        f"Artifact fields: {artifact_fields}"
    )


def render_persona_backstory(key: str) -> str:
    return build_persona_runtime_brief(key) + "\n\nRole contract:\n" + read_persona_markdown(key)


def read_persona_context(keys: list[str] | None = None) -> dict[str, str]:
    specs = get_persona_sequence(keys)
    return {spec.key: read_persona_markdown(spec.key) for spec in specs}


def persona_keys() -> list[str]:
    return [spec.key for spec in PERSONA_SPECS]
