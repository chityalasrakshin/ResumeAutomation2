import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _configured_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    path = Path(value).expanduser() if value else default
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


INPUT_DIR = _configured_path("RESUME_INPUT_DIR", PROJECT_ROOT / "local_data" / "incoming")
OUTPUT_DIR = _configured_path("RESUME_OUTPUT_DIR", PROJECT_ROOT / "artifacts")
_default_template = PROJECT_ROOT / "templates" / "coforge" / "master_template.docx"
_legacy_template = PROJECT_ROOT / "templates" / "Role - Name - Coforge.docx"
MASTER_TEMPLATE = _configured_path(
    "RESUME_TEMPLATE_PATH",
    _default_template if _default_template.exists() or not _legacy_template.exists() else _legacy_template,
)
LEGACY_INPUT_DIR = PROJECT_ROOT / "Inputs"


def resolve_input_path(path: Path) -> Path:
    candidate = path.expanduser()
    if candidate.exists():
        return candidate

    if candidate.is_absolute():
        try:
            relative = candidate.resolve().relative_to(LEGACY_INPUT_DIR.resolve())
        except ValueError:
            return candidate
    elif candidate.parts and candidate.parts[0].casefold() == "inputs":
        relative = Path(*candidate.parts[1:])
    else:
        return candidate

    redirected = INPUT_DIR / relative
    return redirected if redirected.exists() else candidate
