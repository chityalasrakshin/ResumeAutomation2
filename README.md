# Coforge resume automation

Convert candidate resumes in `Inputs` into Word documents using the Coforge template. Each document includes a bulleted profile summary, categorized technical skills and one table per project. Project headings show the role, and recognized month/year ranges use full month names, such as **March 2016 – September 2017**.

## Regenerate all inputs on Windows

From the project root in PowerShell, with the existing `.venv` and configured `.env`, run this command for an unattended batch. **`--approve-all` accepts flagged extractions as-is**, including suggested project titles and missing fields.

```powershell
.\.venv\Scripts\python.exe .\batch_run.py --approve-all
```

The runner scans files directly inside `Inputs`, uses three workers by default, creates `output\checkpoints.sqlite` and prints the generated path or error for each file. An empty `output` folder starts a fresh extraction. If the checkpoint database still exists, the runner can regenerate deleted documents from saved data. For a separate fresh run, pass `--output` with a new folder name.

List the resulting Word documents with:

```powershell
Get-ChildItem .\output -Recurse -Filter '*Coforge_Generated.docx' | Select-Object -ExpandProperty FullName
```

## Review flagged resumes before generation

```powershell
.\.venv\Scripts\python.exe .\batch_run.py
```

A `Generated` line gives the output path. A `Review` line gives a thread ID. To inspect that resume, paste the ID when prompted.

```powershell
$threadId = Read-Host 'Review thread ID'
.\.venv\Scripts\python.exe .\batch_run.py --show $threadId
```

To approve it as shown, run:

```powershell
.\.venv\Scripts\python.exe .\batch_run.py --approve $threadId
```

To submit edits, save the `payload` object from `--show` as `corrected.json`, edit the fields and run:

```powershell
.\.venv\Scripts\python.exe .\batch_run.py --approve $threadId --corrected .\corrected.json
```

Use the same `--output` folder for inspection and approval if you selected a custom output directory. Rerunning a batch reuses completed documents and reports pending reviews. Conversion and API errors appear beside the affected filename.

## First-time setup

On Windows, run these commands from the project root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

Set `API_KEY` in `.env` to your Coforge AI Studio key. The default endpoint and model are listed in `.env.example`.

On Linux or macOS, create the environment with `python3 -m venv .venv`, install with `.venv/bin/python -m pip install -r requirements.txt`, and copy `.env.example` to `.env` on first setup. Run `.venv/bin/python batch_run.py --approve-all` for the unattended batch.

## Options and tests

Use `--inputs`, `--output`, `--template` and `--workers` to override the defaults. To see every option or run the local tests:

```powershell
.\.venv\Scripts\python.exe .\batch_run.py --help
.\.venv\Scripts\python.exe -m unittest discover -v
```

For extraction, verification and rendering details, see the [project overview](PROJECT_OVERVIEW.md).
