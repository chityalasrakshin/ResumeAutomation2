# Resume Automation project overview

The application converts PDF, DOCX, and image resumes into the Word layout defined by `templates/coforge/master_template.docx`. Local reference documents are stored under the Git-ignored `local_data/reference_samples/`; candidate inputs are read from `local_data/incoming/`.

## Processing flow

1. **Read the source.** Docling converts each input to Markdown. For PDFs, pypdfium2 also reads the native text layer, which can retain dates that disappear from multi-column Markdown. Partial conversions and embedded Word graphics can trigger review.
2. **Extract structured data.** The Coforge AI Studio gateway receives the source text and a JSON schema. Pydantic validates the response. The payload retains source durations, organization names, private names, roles, project titles, descriptions, and responsibilities.
3. **Check the evidence.** The pipeline compares extracted values against the source and uses organization and project-title anchors to locate each engagement. Recovery can fill missing metadata from a project block when it finds an unambiguous candidate. Missing fields, uncertain matches, descriptive project captions, and confidence below 0.90 can trigger review.
4. **Route the result.** LangGraph sends a verified payload to generation. A flagged payload pauses with a thread ID. An operator can approve or correct it, or use `--approve-all` to accept flagged payloads as extracted. Parsing and API failures are reported per file.
5. **Generate the DOCX.** The renderer fills the master template and clones its project table for each engagement. It writes a temporary file in the destination folder and replaces the final file after the save succeeds.

## Word output

| Section | Display |
| --- | --- |
| Profile summary | Bulleted summary statements using the supplied template formatting. |
| Technical skills | Bold category headings and individual skill bullets. |
| Project heading and Role row | Project/organization heading and cleaned job designation. Identified employer, client, and engagement-industry names are filtered during rendering. |
| Description and responsibilities | Project description and responsibility bullets. |
| Project title | The source title when available. A descriptive caption drawn from the engagement can be flagged for confirmation. |
| Duration | Recognized month/year ranges use full English month names and an en dash; year-only dates retain year precision. |

## Batch operation and checkpoints

From the project root, run `.\run.cmd` for an unattended batch. It reads supported files directly inside `local_data/incoming/`, uses three workers by default, and accepts flagged results. For review-first processing, run `.\.venv\Scripts\python.exe -m resume_automation` without `--approve-all`.

The default output location is `artifacts/`. LangGraph persists state in `artifacts/checkpoints.sqlite`; each generated document is stored in a hash-named subfolder. The thread ID hashes the processing version and file contents, not the source path, so identical files share a checkpoint and generated document. Input hashing is streamed in bounded chunks.

| State at the next run | Behavior |
| --- | --- |
| No checkpoint for an input | Creates a checkpoint and extracts the input. |
| Completed checkpoint and generated DOCX | Returns the existing document path. |
| Completed checkpoint and missing DOCX | Renders the document from the saved payload. |
| Pending review | Reports the thread ID or resumes with approval when using `--approve-all`. |

The older root `Inputs/` directory remains a supported compatibility path. Any files left there are considered alongside the new inbox when either the default inbox or legacy directory is selected.

## Package map

| Path | Responsibility |
| --- | --- |
| `run.cmd`, `batch_run.py`, `src/resume_automation/__main__.py` | Compatibility launchers and Python module entry point. |
| `src/resume_automation/cli.py` | Input discovery, concurrency, review operations, and checkpoint handling. |
| `src/resume_automation/extraction.py` | Docling conversion, PDF text fallback, gateway requests, and project metadata recovery. |
| `src/resume_automation/schema.py` | Strict Pydantic models for candidate and project payloads. |
| `src/resume_automation/pipeline.py` | Source checks, review routing, and generation. |
| `src/resume_automation/presentation.py` | Display dates, role cleanup, and private-name filtering. |
| `src/resume_automation/template_engine.py` | Word table cloning, field placement, and list formatting. |
| `src/resume_automation/paths.py` | Default and configurable input, output, and template paths. |
| `tests/test_pipeline.py` | Regression coverage for rendering, extraction, gateway handling, and batch review. |

See the [README](../README.md) for setup and operating commands.
