# ResumeAutomation2 project overview

ResumeAutomation2 converts PDF, DOCX and image resumes into the Word layout defined in `templates/Role - Name - Coforge.docx`. The sample input/output pairs in `data/reference_samples` serve as formatting references. The batch reads candidate files directly from `Inputs`.

## Processing flow

1. **Read the source.** Docling converts each input to Markdown. For PDFs, pypdfium2 also reads the native text layer, which can retain dates that disappear from multi-column Markdown. Partial conversions and embedded Word graphics can trigger review.
2. **Extract the resume.** The Coforge AI Studio gateway receives the source text and a JSON schema. Pydantic validates the response. The payload retains source durations, organization names, private names, roles, project titles, descriptions and responsibilities. Private names include identified employers, clients and engagement industry labels.
3. **Check the evidence.** The pipeline checks extracted values against the source and uses organization and project-title anchors to locate the corresponding engagement in each reading. Recovery can fill missing metadata from a project block when it finds an unambiguous candidate. Missing fields, uncertain matches, descriptive project captions and confidence below 0.90 are reasons for review.
4. **Route the result.** LangGraph sends a verified payload to generation. A flagged payload pauses with a thread ID. An operator can approve or correct it, or run the batch with `--approve-all` to accept flagged payloads as extracted. Parsing or API failures are reported per file.
5. **Generate the DOCX.** The renderer fills the master template and clones its project table for each engagement. It saves a temporary file in the destination folder and replaces the final file after the save succeeds.

## Word output

| Section | Display |
| --- | --- |
| Profile summary | Aptos 12 pt in the supplied template, with Word bullets for summary statements. |
| Technical skills | Bold category headings and individual skill bullets in Cambria 12 pt. |
| Project heading and Role row | Job designation. Identified employer, client and engagement industry names are filtered during rendering. The filename also uses the cleaned role. |
| Description and responsibilities | A compact description in Cambria 11 pt and a list of responsibility bullets. |
| Project title | The source title when available. A descriptive caption drawn from the engagement is flagged for confirmation. |
| Duration | Recognized month/year ranges use full English month names and an en dash. For example, `03/2016 – 09/2017` becomes `March 2016 – September 2017`. Open-ended ranges can end in `Present`. Year-only dates retain year precision. |

## Batch operation and checkpoints

From the project root, run `.\.venv\Scripts\python.exe .\batch_run.py --approve-all` for unattended generation of all supported inputs. Omit `--approve-all` to review flagged resumes individually. The default worker count is three. The full setup and review commands are in the [README](README.md).

LangGraph persists each resume's state in `output/checkpoints.sqlite`. The thread ID incorporates the processing version, absolute source path and file contents. Each generated document goes into its own hash-named subfolder under `output`. The CLI reports the full path.

| State at the next run | Behavior |
| --- | --- |
| Empty output directory | Creates a checkpoint database and extracts each input from scratch. |
| Completed checkpoint and generated DOCX | Returns the existing document path. |
| Completed checkpoint and missing DOCX | Renders the document from the saved payload. |
| Pending review | Reports the thread ID or resumes with approval when using `--approve-all`. |

## Implementation map

| Path | Responsibility |
| --- | --- |
| `batch_run.py`, `resume_automation/cli.py` | CLI entry point, input discovery, concurrency and checkpoint operations. |
| `resume_automation/extraction.py` | Docling conversion, PDF text layer, gateway requests and project metadata recovery. |
| `resume_automation/schema.py` | Strict Pydantic models for the candidate and project payloads. |
| `resume_automation/pipeline.py` | Source checks, review routing and generation. |
| `resume_automation/presentation.py` | Display dates, role cleanup and private-name filtering. |
| `resume_automation/template_engine.py` | Word table cloning, field placement and native list formatting. |
| `resume_automation/paths.py` | Default input, output and template paths. |
| `tests/test_pipeline.py` | Regression coverage for rendering, project metadata, gateway handling and batch review. |
