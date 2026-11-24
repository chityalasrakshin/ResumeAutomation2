from hashlib import sha256
from pathlib import Path
import re
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .extraction import extract_source, load_source_with_docling, project_blocks
from .paths import MASTER_TEMPLATE, OUTPUT_DIR
from .presentation import display_text, private_names
from .schema import CoforgeResumeSchema
from .template_engine import populate_coforge_document


class OrchestrationState(TypedDict, total=False):
    file_path: str
    extracted_data: dict
    flagged_issues: list[str]
    validation_passed: bool
    output_docx_path: str


def verify(data: CoforgeResumeSchema, source):
    issues = list(source.warnings)
    if data.confidence_score < 0.90:
        issues.append(f"Low extraction confidence ({data.confidence_score}).")
    issues.extend(f"Reported missing: {item}" for item in data.missing_sections)
    def quote_text(value):
        # Docling and the native text layer may encode the same date separator differently.
        return " ".join(value.replace("–", "-").replace("—", "-").split())

    text = quote_text(source.text) + "\n" + quote_text(source.pdf_text)

    def check(label, values, evidence=text):
        if not values:
            issues.append(f"Missing {label}.")
        for value in values:
            quote = quote_text(value)
            if not quote or not re.search(r"(?<!\w)" + re.escape(quote) + r"(?!\w)", evidence):
                issues.append(f"Inspect {label}: {value!r}.")

    check("candidate name", [data.full_name])
    for field in ("email", "phone"):
        value = getattr(data, field)
        check(field, [value] if value else [])
    for field in ("profile_summary", "certifications"):
        check(field, getattr(data, field))
    if not data.technical_skills:
        issues.append("Missing technical skills.")
    for category, items in data.technical_skills.items():
        check(f"skills in {category}", items)
    if not data.projects:
        issues.append("Zero project history detected.")
    for i, (project, markdown, native) in enumerate(zip(
        data.projects, project_blocks(data.projects, source.text), project_blocks(data.projects, source.pdf_text)
    )):
        label = f"project {i + 1}"
        if project.descriptive_title:
            issues.append(f"Confirm descriptive title for {label}: {project.project_title!r}.")
        evidence = quote_text(markdown) + "\n" + quote_text(native)
        if not evidence.strip():
            issues.append(f"Verify which source block belongs to {label}.")
        for field in ("organization_name", "project_title", "role", "duration", "description"):
            check(f"{label} {field}", [getattr(project, field)], evidence)
        check(f"{label} responsibilities", project.responsibilities, evidence)
    return issues


def _filename_part(value):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")[:60]


def build_graph(checkpointer=None, template_path=MASTER_TEMPLATE,
                output_dir=OUTPUT_DIR, extractor=extract_source):
    def extraction(state):
        source = load_source_with_docling(Path(state["file_path"]))
        data = CoforgeResumeSchema.model_validate(extractor(source))
        return {"extracted_data": data.model_dump(), "flagged_issues": verify(data, source)}

    def verification(state):
        return {"validation_passed": not state["flagged_issues"]}

    def review(state):
        decision = interrupt({"file": state["file_path"], "issues": state["flagged_issues"],
                              "payload": state["extracted_data"]})
        if not isinstance(decision, dict) or decision.get("approved") is not True:
            raise ValueError("Explicit review approval required.")
        reviewed = CoforgeResumeSchema.model_validate(decision.get("corrected_payload", state["extracted_data"]))
        return {"extracted_data": reviewed.model_dump(), "validation_passed": True}

    def generate(state, config):
        data = CoforgeResumeSchema.model_validate(state["extracted_data"])
        names = private_names(data)
        role = next(filter(None, (display_text(p.role, names, role=True) for p in data.projects)), "")
        filename = " - ".join(filter(None, (_filename_part(role), _filename_part(data.full_name),
                                             "Coforge_Generated.docx")))
        key = sha256(config["configurable"]["thread_id"].encode()).hexdigest()[:16]
        dest = Path(output_dir).resolve() / key / filename
        populate_coforge_document(data, template_path, dest)
        return {"output_docx_path": str(dest)}

    graph = StateGraph(OrchestrationState)
    for name, node in [("extract", extraction), ("verify", verification),
                       ("review", review), ("generate", generate)]:
        graph.add_node(name, node)
    graph.add_edge(START, "extract")
    graph.add_edge("extract", "verify")
    graph.add_conditional_edges("verify", lambda s: "generate" if s["validation_passed"] else "review",
                                ["review", "generate"])
    graph.add_edge("review", "generate")
    graph.add_edge("generate", END)
    return graph.compile(checkpointer=checkpointer or InMemorySaver())
