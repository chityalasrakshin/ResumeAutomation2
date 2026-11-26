# MISSION BRIEF: Production-Grade Upgrade of ResumeAutomation2

You are tasked with upgrading the `ResumeAutomation2` repository to a robust, enterprise-grade, and production-ready system. There is zero tolerance for shortcuts, temporary fixes, loose data types, or inconsistent behavior. Every change must be implemented thoroughly, verified against real documents, and backed by comprehensive unit tests without breaking existing strengths.

---

## 1. Context & Architecture Overview

`ResumeAutomation2` standardizes candidate resumes (PDF, DOCX, scanned images) into enterprise Coforge Word documents conforming to `templates/Role - Name - Coforge.docx` (with reference implementations in `data/reference_samples/`).

### Tech Stack
- **Python 3.12** in `.venv`
- **Docling**: Multi-modal document parser (PDF/DOCX/Images → Markdown).
- **Coforge AI Studio LLM Router API**: OpenAI-compatible endpoint at `https://quasarmarket.coforge.com/qag/llmrouter-api/v2/chat/completions` using `gpt-5-2` / `gpt-5.5`.
- **Pydantic v2**: Data contracts and schema validation.
- **LangGraph with SQLite Checkpointing** (`SqliteSaver`): Fault-tolerant execution, idempotency, and Human-in-the-Loop review.
- **OpenXML DOM Engine (`python-docx` + `lxml`)**: Direct XML cloning and table formatting.

---

## 2. Core Architectural Mandates

### A. Strict Structured Outputs (OpenAI `json_schema` Mode)
- **Problem**: The current implementation sends JSON schema text inside the system prompt and performs naive string stripping of markdown fences. This can yield unpredictable outputs, malformed fields, or schema drift.
- **Mandate**: Leverage native **Structured Outputs** via `response_format` supported by GPT-5.5 / OpenAI-compatible routers:
  ```python
  payload = {
      "model": self.model,
      "messages": [
          {"role": "system", "content": SYSTEM_PROMPT},
          {"role": "user", "content": f"Resume Markdown:\n{markdown}"}
      ],
      "response_format": {
          "type": "json_schema",
          "json_schema": {
              "name": "CoforgeResumeSchema",
              "strict": True,
              "schema": schema_dict
          }
      },
      "temperature": 0.0,
      "top_p": 1.0,
      "max_tokens": self.max_tokens,
  }
  ```
- Generate `schema_dict` from `CoforgeResumeSchema.model_json_schema()`. Ensure full adherence to OpenAI's strict JSON Schema rules:
  1. `additionalProperties: False` on all object definitions.
  2. Every property defined in `properties` must be listed in `required`.
  3. Nullable types must be represented as `anyOf: [{"type": "<type>"}, {"type": "null"}]` (or `["string", "null"]`).
  4. Disallow recursive refs or unsupported keywords.

### B. Output Consistency & Determinism
- Enforce `temperature=0.0` and `top_p=1.0`.
- Maintain deterministic SHA-256 thread hashing in `cli.py` (`sha256(b"coforge-v4\0" + file_bytes)`) to guarantee idempotency across re-runs.
- Repeated executions with the same input document must yield byte-for-byte identical JSON structures and document layouts.

---

## 3. Detailed Fixes for All Image-Based Requirements

### Requirement 1 & 5: Project Description Extraction & Blanking Rules
- **Observed Bug**: In `extraction.py` line 33-34, the prompt currently instructs: *"When there is no overview, select a representative sentence from that engagement for the description."* Furthermore, when Docling encounters different fonts or layouts, project overviews are often misread, causing the LLM to pull bullets from the responsibilities section or copy tools/environments (e.g., inserting "Reltio" or "IDMC" into Description).
- **Mandate**:
  1. **Strict Extraction Rule**: Extract `description` **only** if the source resume contains an explicit project overview, summary paragraph, or description section.
  2. **Blank Fallback**: If no explicit description is present (or if font/layout variations make it unidentifiable), `description` **must be set to an empty string `""`**.
  3. **Zero Contamination**: **NEVER** borrow, summarize, or extract text from responsibilities bullets, environments, or tool stacks to populate the description.
  4. **Document Rendering**: In `template_engine.py`, when `description` is `""`, cell `(1, 1)` in the project table must remain completely blank without placeholder text, duplicate roles, or stray spaces.

### Requirement 2: Project Title Hallucination & Decision Framework
- **Observed Bug**: Project titles are frequently hallucinated, filled with company names, candidate roles (e.g. "End to End UAT Engineer"), or tool names (e.g. "Reltio, Databricks, Java, IICS").
- **Mandate**: Implement a deterministic two-tier decision logic in extraction:
  - **Tier 1 (Explicit Title Exists)**: If the resume explicitly provides a project title/name (e.g., labeled as "Project Name:", "Project:", "Title:", or explicit project header), extract it **verbatim as-is**. Set `descriptive_title = False`.
  - **Tier 2 (Missing Title)**: If no explicit project title is present:
    - Do **not** hallucinate random titles, and do **not** use the company/organization name or candidate job role.
    - Generate a clean, professional, concise project title (a 3 to 6-word noun phrase, Title Case) based strictly on that specific engagement's domain and responsibilities (e.g., *"Customer Billing & Invoicing Platform"*, *"Enterprise Claims Processing Portal"*).
    - Set `descriptive_title = True`.
  - In `extraction.py:recover_project_metadata`, ensure regex recovery only overwrites empty or descriptive titles if an unambiguous explicit title label (`Project:`, `Title:`) is found in the raw text/PDF layer.

### Requirement 3: Technical Skills + Key Skills Combination & Filtering
- **Observed Bug**: Resumes having separate sections for "Technical Skills", "Key Skills", "Core Competencies", "Tools & Technologies", or "Professional Skills" frequently have the secondary skills dropped, ignored, or dumped into profile summary.
- **Mandate**:
  1. In `extraction.py`, extract skills from **all** related sections: `Technical Skills`, `Key Skills`, `Core Competencies`, `Tools & Technologies`, `Methodologies`, `Areas of Expertise`, and `Primary Skills`.
  2. Consolidate and categorize them into `technical_skills: dict[str, list[str]]` using clean, industry-standard categories (e.g., `"Programming Languages"`, `"Cloud & DevOps"`, `"Databases"`, `"BPM & Enterprise Tools"`, `"Methodologies & Frameworks"`).
  3. **Deduplication & Hygiene**: Deduplicate skills case-insensitively across categories. Strip soft skills, narrative sentences, or non-technical boilerplate. Each item in the skill list must be an individual skill/tool name (e.g., `"Python"`, `"AWS (EC2, S3)"`).
  4. Render all categories into Table 1 of the Word template with bold category headers and bulleted items in Cambria 12 pt.

### Requirement 4: Education Clarification & Handling
- **Clarification**: The official client-facing template (`templates/Role - Name - Coforge.docx`) and reference deliverables (`Pavan P.docx`, `Sreenivasa G.docx`) contain **only four sections**:
  1. Profile Summary (Table 0)
  2. Technical Skills (Table 1)
  3. Certifications (Table 2)
  4. Project Experience (Table 3 repeated per project)
  There is **no Education table** in the Coforge client presentation template.
- **Mandate**:
  1. **In `schema.py`**: Add `education: list[str] = Field(default_factory=list, description="Candidate degrees, institutions, and graduation years extracted verbatim from source")` to maintain complete extracted data fidelity.
  2. **In `template_engine.py`**: Do **not** inject education into the Word document unless the template contains an education table. **Never** contaminate Certifications or Profile Summary with degrees.
  3. In `pipeline.py:verify`, ensure education is checked against the source text when present, but does not trigger validation failure when omitted from document rendering.

### Requirement 6: Methods & Tools Clarification
- **Clarification**: Methods and tools appear in two contexts: (a) global resume sections like "Methodologies & Tools", and (b) inline project environments like "Environment: Java, Spring, Oracle" or "Methods: Agile/Scrum".
- **Mandate**:
  1. **Global Skills**: When "Methods and Tools" appears as a resume-level section or competency, categorize its items under `technical_skills` (e.g., under `"Methodologies & Frameworks"` or `"Tools & Utilities"`).
  2. **Project-Level Environment**: If listed inside a project block, do **not** dump the environment string into `description` or `project_title`. If the project mentions tools not already captured in `technical_skills`, include them in the appropriate `technical_skills` category.

### Requirement 7: Organization Name Grammar Breakdown Fix
- **Observed Bug**: In `presentation.py` line 54:
  `value = names.sub("" if role else "the organization", value)`
  This naive regex replacement systematically replaces every company name in project responsibilities with the literal text `"the organization"`. This creates disastrous grammatical errors:
  - *"Interfaced with Barclays team"* → *"Interfaced with the organization team"*
  - *"Automated Verizon billing system"* → *"Automated the organization billing system"*
  - *"Reported to Citi leadership"* → *"Reported to the organization leadership"*
- **Mandate**:
  1. **Stop Blind Substring Replacement**: Eliminate `names.sub("the organization", value)` from responsibility bullet points and descriptions.
  2. **Grammar-Preserving Presentation**:
     - Keep candidate responsibility bullet points grammatically natural and intact.
     - If client/organization anonymization is required by the business rule, perform context-aware redaction (e.g., regex pattern `(?i)\b(?:at|for|with)\s+[A-Z][A-Za-z0-9&., ]+\b` → `"with the client"`, or phrase replacement like `"the client's billing system"`), OR preserve the verbatim text if the candidate already described it neutrally.
     - Never allow raw token substitution to produce broken English.

---

## 4. Master Template & Table 0/3 Header Alignment

### Critical Table Header Bug in `template_engine.py`
- Look at `templates/Role - Name - Coforge.docx` Table 3:
  - **Row 0**: `Name of the Project/Organization:`
  - **Row 1**: `Description` | `<Project Description>`
  - **Row 2**: `Duration` | `<Duration>` | `Title` | `<Title>`
  - **Row 3**: `Role` | `<Candidate Role>`
  - **Row 4**: `My Responsibilities` | `<Bullet List>`
- In `template_engine.py` line 132-137, the current code incorrectly overwrites Row 0 with `role`:
  ```python
  # BUGGY EXISTING CODE:
  header = _cell(table, 0).paragraphs[0]
  role = display_text(project.role, names, role=True)
  _write(header, role)  # <--- OVERWROTE "Name of the Project/Organization:" WITH ROLE!
  ```
- **Correction**: Align with `data/reference_samples/pavan_p/Pavan P.docx`:
  - **Row 0**: Must be: `f"Name of the Project/Organization: {display_org_name}"` (e.g., `Name of the Project/Organization: American Express`).
  - **Row 3**: Must be: `role` (e.g., `Salesforce Data Cloud & Analytics Consultant`).

---

## 5. File-by-File Implementation Plan

### 1. `resume_automation/schema.py`
- Enforce strict Pydantic v2 models compatible with OpenAI Structured Outputs (`model_config = ConfigDict(extra="forbid", strict=True)`).
- `ProjectBlock`:
  - `organization_name: str`
  - `project_title: str`
  - `descriptive_title: bool = False`
  - `role: str`
  - `duration: str = ""`
  - `description: str = Field(default="", description="Explicit project overview/description if present in source, otherwise empty string")`
  - `responsibilities: list[str]`
- `CoforgeResumeSchema`:
  - Add `education: list[str] = Field(default_factory=list)`
  - Ensure all fields are typed cleanly, nullable fields use `str | None = None`.

### 2. `resume_automation/extraction.py`
- Update `CustomLLM._call` to pass `response_format={"type": "json_schema", "json_schema": {"name": "CoforgeResumeSchema", "strict": True, "schema": ...}}`.
- Update `SYSTEM` prompt:
  - Add explicit instructions for:
    - Empty description when overview is absent; never copy from responsibilities.
    - Explicit verbatim project title vs 3-6 word generated descriptive title.
    - Combining Technical Skills, Key Skills, Core Competencies, and Methods into categorized technical skills with deduplication.
    - Extracting Education into the `education` array.
- In `recover_project_metadata`: Ensure regex fallback logic does not corrupt valid LLM-extracted titles or empty descriptions.

### 3. `resume_automation/presentation.py`
- Fix `display_text`:
  - Remove destructive `names.sub("the organization", value)` on responsibilities.
  - Implement clean, grammar-preserving handling for organizations in text.
- Maintain `display_duration` with full month names (e.g. `March 2016 – September 2017`).

### 4. `resume_automation/template_engine.py`
- Correct Table 3 Row 0 to render `Name of the Project/Organization: <Org Name>`.
- Correct Table 3 Row 1 to leave Description cell completely empty when `project.description == ""`.
- Ensure Table 1 renders combined and categorized Technical Skills cleanly with bold category headers and bullet points.

### 5. `resume_automation/pipeline.py`
- Adjust lexical `verify` function:
  - Allow `description` to be empty string without flagging validation failure.
  - Verify skills against combined technical and key skills in the source text.
  - Ensure confidence threshold (0.90) and descriptive title flagging operate smoothly with LangGraph review nodes.

### 6. `tests/test_pipeline.py`
- Update existing tests and add new test suites covering:
  1. `test_empty_description_leaves_cell_blank`: Verifies that a project with `description=""` renders an empty cell in the Word doc.
  2. `test_title_decision_logic`: Verifies explicit title extracted as-is vs missing title generating a descriptive title.
  3. `test_combined_technical_and_key_skills`: Verifies skills from separate sections are merged and deduplicated.
  4. `test_responsibilities_grammar_preservation`: Verifies organization names inside responsibility bullets do not become ungrammatical "the organization".
  5. `test_project_table_row0_organization_header`: Verifies Table 3 Row 0 has `Name of the Project/Organization:` and Row 3 has `Role`.
  6. `test_structured_outputs_payload_conformance`: Verifies the request payload includes `response_format` conforming to the JSON schema.

---

## 6. Execution & Verification Checklist

Before reporting completion, execute the following commands in PowerShell from the project root:
1. Run all tests:
   ```powershell
   .\.venv\Scripts\python.exe -m unittest discover -v tests
   ```
2. Run a batch test on the real input resumes:
   ```powershell
   .\.venv\Scripts\python.exe .\batch_run.py --inputs Inputs --output output_test --approve-all
   ```
3. Inspect the generated documents in `output_test` using python-docx to verify:
   - Project descriptions are blank when absent in source.
   - Project titles are properly formatted and not hallucinated.
   - Table 3 Row 0 displays `Name of the Project/Organization:` correctly.
   - Responsibilities have zero grammar errors from organization replacement.
   - Technical skills include all key skills and competencies without duplication.

Implement every item with precision. Do not skip any detail.
