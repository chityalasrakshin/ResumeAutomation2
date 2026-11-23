# ResumeAutomation2 - Architecture & Technical Overview

## 1. Executive Summary & Purpose

### The Problem
In enterprise IT consulting and staffing (specifically Coforge), resumes arrive from candidates in wildly varied formats: PDFs, DOCX, scanned images, different fonts, multi-column tables, and non-standard section headers. Submitting candidates to enterprise clients requires a **standardized, brand-compliant Word format** (`templates/Role - Name - Coforge.docx`).

Manual reformatting is slow, labor-intensive, error-prone, and often leads to dropped project details, altered dates, or broken Word layout structures.

### The Solution
**ResumeAutomation2** is an enterprise-grade automated resume ingestion, extraction, verification, human-in-the-loop review, and document generation pipeline.

Key architectural pillars:
- **Docling**: Multi-modal document parser (PDF, DOCX, images $\rightarrow$ structured Markdown).
- **Coforge AI Studio LLM Router API (`gpt-5-2`)**: Zero-shot, schema-constrained structured entity extraction.
- **Pydantic v2**: Strict structural schema validation and JSON Schema enforcement.
- **Lexical Anti-Hallucination Engine**: Verifies extracted information verbatim against the original document.
- **LangGraph + SQLite Checkpointing**: State-machine orchestration, failure recovery, idempotency, and Human-in-the-Loop (HITL) review.
- **Low-level OpenXML Engine (`python-docx` + `lxml`)**: Direct XML DOM cloning of Word tables for pixel-perfect document rendering.

---

## 2. High-Level Architecture & Pipeline Flow

The workflow is orchestrated via a deterministic **LangGraph `StateGraph`**:

```mermaid
flowchart TD
    START([START]) --> EXTRACT[Node 1: Extract<br/>• Convert input to Markdown via Docling<br/>• Call Coforge AI Studio LLM Router<br/>• Validate against Pydantic schema<br/>• Run lexical verification against source]
    
    EXTRACT --> VERIFY[Node 2: Verify<br/>• Check flagged issues count<br/>• Set validation_passed flag]
    
    VERIFY --> COND{validation_passed?}
    
    COND -- "True (Zero issues & confidence >= 0.90)" --> GENERATE[Node 3: Generate<br/>• Clone XML tables from master template<br/>• Populate candidate data & projects<br/>• Save standardized Coforge DOCX]
    
    COND -- "False (Discrepancies / Low confidence)" --> REVIEW[Node 4: Review (HITL Interrupt)<br/>• Trigger LangGraph interrupt()<br/>• Save frozen state to SQLite<br/>• Await operator approval / JSON edit]
    
    REVIEW -- "Manual Approval / Corrected JSON" --> GENERATE
    
    GENERATE --> END_NODE([END])
```

---

## 3. How LangGraph is Used

LangGraph acts as a stateful, fault-tolerant workflow orchestrator:

### A. Pipeline State (`OrchestrationState`)
The pipeline operates on a typed state object defined via Python's `TypedDict`:
```python
class OrchestrationState(TypedDict, total=False):
    file_path: str            # Absolute path to source resume
    extracted_data: dict      # Serialized Pydantic resume schema
    flagged_issues: list[str] # Discrepancies detected by lexical verification
    validation_passed: bool   # Routing condition (True -> Generate, False -> Review)
    output_docx_path: str     # Path to generated standardized DOCX
```

### B. Persistent SQLite Checkpointing (`SqliteSaver`)
- Every execution is persisted into `output/checkpoints.sqlite`.
- **Deterministic Thread IDs**: For every resume, an execution thread ID is generated via a SHA-256 hash:
  $$\text{Thread ID} = \text{SHA-256}(\text{path} + \text{raw\_file\_bytes})$$
- **Idempotency**: If a resume has already been processed and its target output exists, the pipeline returns the existing file immediately without re-calling the LLM.
- **Resilience**: If processing is interrupted or crashes mid-batch, the state graph resumes cleanly from the last completed node.

### C. Human-in-the-Loop (HITL) Interrupts
- When discrepancies or low confidence are detected, the graph routes to the `review` node and executes LangGraph's native `interrupt()` function:
  ```python
  decision = interrupt({
      "file": state["file_path"],
      "issues": state["flagged_issues"],
      "payload": state["extracted_data"]
  })
  ```
- This freezes execution and leaves the resume in a review state (`Review <THREAD_ID>`).
- Operators can inspect pending issues via the CLI (`--show <THREAD_ID>`) and resume execution by approving or submitting a corrected JSON payload (`--approve <THREAD_ID> --corrected <file.json>`).
- If batch processing should bypass manual pauses, the `--approve-all` flag auto-approves all extractions.

---

## 4. API & Model Specifications

- **Gateway**: Coforge AI Studio LLM Router
- **Endpoint**: `https://quasarmarket.coforge.com/qag/llmrouter-api/v2/chat/completions`
- **Protocol**: OpenAI-compatible REST API (`POST`)
- **Authentication**: Custom HTTP header `X-API-KEY: <API_KEY>`
- **Model**: `gpt-5-2` (configurable via `MODEL_NAME` in `.env`)
- **Inference Hyperparameters**:
  - `temperature = 0.0` (zero variance; strictly deterministic extraction)
  - `top_p = 1.0`
  - `max_tokens = 4000`
- **Custom Client Implementation**: Wrapped in a custom LangChain `LLM` subclass (`CustomLLM` in `resume_automation/extraction.py`), with dedicated timeout management, error handling, and completion-stop verification.

---

## 5. Detailed Component Breakdown

### 1. Multi-Modal Document Parsing (`extraction.py`)
- Uses IBM's `docling` library (`DocumentConverter`) to parse multi-page PDFs, Word documents (`.docx`), and scanned images into unified, clean Markdown.
- Preserves structure, tables, and bullet point relationships.
- Uses a thread mutex (`_conversion_lock`) to allow safe sharing of local parsing models across concurrent threads.
- Inspects Word drawing XML elements (`w:drawing`, `w:pict`) via XPath to flag text hidden in canvas graphics.

### 2. Schema Enforcement (`schema.py`)
- Implemented with Pydantic v2: `CoforgeResumeSchema` and `ProjectBlock`.
- Enforces `extra="forbid"`, `strict=True`, and `allow_inf_nan=False`.
- The Pydantic model is compiled into standard JSON Schema (`model_json_schema()`) and injected into the system prompt to guarantee valid structure.
- Fields:
  - `full_name`, `email`, `phone`
  - `profile_summary` (`list[str]`)
  - `technical_skills` (`dict[str, list[str]]`)
  - `certifications` (`list[str]`)
  - `projects` (`list[ProjectBlock]` with company, title, role, duration, description, and responsibilities)
  - `missing_sections` (`list[str]`)
  - `confidence_score` (`float` between 0.0 and 1.0)

### 3. Lexical Anti-Hallucination Verification (`pipeline.py`)
Rather than blindly accepting the LLM output, a deterministic validation algorithm runs against the source text:
- **Confidence Gate**: Checks `confidence_score >= 0.90`.
- **Verbatim Entity Checks**: Verifies that candidate name, phone, email, summary bullets, and skill tags exist as substring matches in the source text.
- **Project Boundary Anchoring**:
  - Slices the source text into distinct project ranges: `text[start_index:end_index]` based on company and project headings.
  - Verifies that project roles, durations, and responsibilities exist strictly within their respective source sections, preventing the LLM from mixing up or blending details across different employers.

### 4. OpenXML DOM Template Engine (`template_engine.py`)
- Standard text replacement breaks Word table layouts and styles. Instead, this engine directly manipulates the OpenXML DOM using `python-docx` and `lxml`.
- Reads `templates/Role - Name - Coforge.docx`.
- Populates profile summary bullets and categorized technical skills.
- **Dynamic XML Deep-Copy**:
  - Clones the master project table (`deepcopy(master._tbl)`) for every project engagement.
  - Strips Word paragraph IDs (`w14:paraId`, `w14:textId`) to avoid XML collisions.
  - Populates cells, applies `List Bullet` numbering styles, and removes the original blank template table.
- **Atomic File Writing**: Saves to a temporary file (`.tmp`) first and performs an atomic replace to eliminate corrupt or partially written documents.

### 5. Multi-Threaded Batch Runner (`cli.py` & `batch_run.py`)
- Implements `ThreadPoolExecutor` to process dozens of resumes concurrently.
- Supports configurable concurrency via `--workers <N>` (defaults to 3).
- Outputs files into deterministic folders named after the thread hash:
  `output/<thread_hash>/<Role> - <Candidate Name> - Coforge_Generated.docx`

---

## 6. Project Directory Reference

| Path | Purpose |
| :--- | :--- |
| `batch_run.py` | Top-level CLI entry point. |
| `resume_automation/cli.py` | Argument parsing, batch worker pool, review and approval subcommands. |
| `resume_automation/pipeline.py` | LangGraph definition, state schema, lexical verification (`verify`). |
| `resume_automation/extraction.py` | Docling document converter, custom LangChain LLM, prompt construction. |
| `resume_automation/schema.py` | Strict Pydantic v2 data models. |
| `resume_automation/template_engine.py` | OpenXML DOM manipulation for `.docx` generation. |
| `resume_automation/paths.py` | Centralized project paths (`Inputs/`, `output/`, `templates/`). |
| `templates/` | Stores the master Coforge Word template. |
| `Inputs/` | Source resume files. |
| `output/` | Standardized generated resumes and `checkpoints.sqlite`. |
| `tests/` | Unit tests for extraction, validation, rendering, and checkpointing. |

---

## 7. Quick Talking Points ("Elevator Pitch")

> *"ResumeAutomation2 is an enterprise resume transformation pipeline that standardizes arbitrary candidate resumes into formatted Coforge Word deliverables. It ingests multi-format files using Docling, parses them into strict Pydantic schemas via the Coforge AI Studio API running `gpt-5-2`, and subjects the output to deterministic lexical verification to prevent hallucinations. The entire lifecycle is choreographed using a LangGraph state machine backed by SQLite checkpoints, providing automatic idempotency, failure recovery, and human-in-the-loop review for flagged resumes before programmatically rendering the final output using direct OpenXML table cloning."*
