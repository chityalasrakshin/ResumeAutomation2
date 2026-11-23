# Installation and Usage

## Installation

1. Create and activate a virtual environment:

   **Windows (PowerShell):**
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

   **Linux / macOS:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Set up the environment configuration:
   Create a `.env` file by copying `.env.example`:
   ```bash
   cp .env.example .env
   ```
   On Windows PowerShell:
   ```powershell
   Copy-Item .env.example .env
   ```
   Open `.env` and configure your `API_KEY`.

## How to Run

### Run Batch Processing
Process all resumes in the `Inputs` directory and save standardized resumes in `output`:
```bash
python batch_run.py
```

### Auto-Approve All Resumes
Run batch processing and automatically approve all extractions without pausing for human review:
```bash
python batch_run.py --approve-all
```

### Run Options
- **Adjust concurrency (workers):**
  ```bash
  python batch_run.py --workers 4
  ```
- **Specify custom input and output folders:**
  ```bash
  python batch_run.py --inputs "path/to/inputs" --output "path/to/output"
  ```
- **Inspect a pending review thread:**
  ```bash
  python batch_run.py --show <THREAD_ID>
  ```
- **Approve a thread with corrections:**
  ```bash
  python batch_run.py --approve <THREAD_ID> --corrected path/to/corrected.json
  ```
