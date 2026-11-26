# Resume Automation

Convert PDF, DOCX, and image resumes into Coforge-formatted Word documents. The application extracts structured resume data, validates it against the source, routes uncertain results for review, and renders approved results using the master template.

## Project layout

- `src/resume_automation/` — installable Python package and CLI.
- `templates/coforge/master_template.docx` — preferred output template. The legacy `templates/Role - Name - Coforge.docx` is used automatically when the preferred path is absent.
- `local_data/incoming/` — local candidate resumes.
- `local_data/reference_samples/` — local-only reference documents.
- `artifacts/` — generated documents and LangGraph checkpoints.
- `docs/` — project overview and implementation requirements.

`local_data/` and `artifacts/` are ignored by Git. Candidate/reference files are local data and should not be committed. Existing Git history is not rewritten. Root-level `batch_run.py` and `run.cmd` remain compatibility launchers; `Inputs` is also supported as a legacy input path.

Remaining files in the legacy `Inputs/` folder are included in default discovery; checkpoints can continue to reuse an existing generated document at its saved legacy `output/` path.

## First-time setup

Run these commands from the project root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

Set `API_KEY` in `.env` to your Coforge AI Studio key. The endpoint and model defaults are in `.env.example`.

## Add resumes

Put each resume directly inside `local_data\incoming` (create the folder if needed). Supported formats are PDF, DOCX, and PNG, JPG/JPEG, TIF/TIFF, BMP, or WEBP images. The default scan is not recursive, so do not place files in nested subfolders.

For example, to copy a resume from Downloads into the inbox:

```powershell
New-Item -ItemType Directory -Force .\local_data\incoming | Out-Null
Copy-Item "$env:USERPROFILE\Downloads\resume.pdf" .\local_data\incoming\
```

You can instead pass a different file or folder with `--inputs`. The legacy `Inputs\` folder is also supported. Resume files are local data; do not commit them to Git.

## Run

Process every supported resume directly inside `local_data\incoming`:

```powershell
.\run.cmd
```

`run.cmd` uses `--approve-all`: it accepts routine extraction flags without manual review, but pauses when source content is missing. To review flagged extractions before approving, run:

```powershell
.\.venv\Scripts\python.exe -m resume_automation
```

To process one file with `run.cmd`:

```powershell
.\run.cmd "local_data\incoming\kusuma.pdf"
```

For one file with review-first behavior:

```powershell
.\.venv\Scripts\python.exe -m resume_automation --inputs "local_data\incoming\kusuma.pdf"
```

The legacy `Inputs` path remains accepted. For example, `.\run.cmd "Inputs\<resume>.pdf"` redirects to the matching file in `local_data\incoming` when it has been moved. Any files still in `Inputs` are included when the default inbox or legacy directory is scanned.

## Find the output

Generated DOCX files are **not** placed beside the input resumes. By default, the application stores them under `artifacts\`, in a subfolder for each unique resume content hash. LangGraph review checkpoints are stored in `artifacts\checkpoints.sqlite`.

```text
artifacts\
  checkpoints.sqlite
  <resume-hash>\
    <role> - <candidate> - Coforge_Generated.docx
```

List generated documents and print their full paths with:

```powershell
Get-ChildItem .\artifacts -Recurse -Filter '*Generated.docx' | Select-Object -ExpandProperty FullName
```

Rerunning a completed resume reuses its checkpoint and existing DOCX; if the DOCX was removed, it is regenerated from the saved checkpoint. To use a different output/checkpoint folder, pass `--output`, for example `.\.venv\Scripts\python.exe -m resume_automation --inputs ".\local_data\incoming" --output ".\artifacts_test"`. Use the same `--output` when reviewing or approving that run.

Other options include `--template` and `--workers`. Environment equivalents for the input directory, output directory, and template path are `RESUME_INPUT_DIR`, `RESUME_OUTPUT_DIR`, and `RESUME_TEMPLATE_PATH`.

## Review flagged resumes

Run without `--approve-all`; a `Review` line includes the thread ID:

```powershell
.\.venv\Scripts\python.exe -m resume_automation
$threadId = Read-Host 'Review thread ID'
.\.venv\Scripts\python.exe -m resume_automation --show $threadId
```

Approve the extraction as shown:

```powershell
.\.venv\Scripts\python.exe -m resume_automation --approve $threadId
```

Or save the `payload` from `--show` to `corrected.json`, edit it, and submit it:

```powershell
.\.venv\Scripts\python.exe -m resume_automation --approve $threadId --corrected .\corrected.json
```

Use the same `--output` value for review and approval when overriding the default output directory. Completed checkpoints are reused; if a generated document is missing, it can be rendered again from the saved payload.

## PDF extraction note

If Docling cannot download its layout model, PDFs with an embedded text layer can use the local text fallback. Wrapped bullet lines are rejoined and the result is flagged for careful review. Scanned PDFs without embedded text still require access to the Docling model.

## Tests and help

```powershell
.\.venv\Scripts\python.exe -m resume_automation --help
.\.venv\Scripts\python.exe -m unittest discover -v
```

See the [project overview](docs/project-overview.md) and [implementation requirements](docs/implementation-requirements.md) for additional details.
