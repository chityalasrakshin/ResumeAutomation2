"""Recover explicitly labelled project overviews with their engagement metadata."""
from dataclasses import dataclass
import re

from docx.table import _Cell

from .presentation import display_duration


_OVERVIEW = re.compile(r"^(?:project\s+)?(?:overview|description|summary)\s*(?:[:|]\s*(.*)|$)", re.I)
RESPONSIBILITIES = r"(?:(?:my|key|project) |roles?\s*(?:&|and)\s*)?responsibilities"
_IDENTITY = re.compile(
    r"^(client(?: name)?|customer(?: name)?|organi[sz]ation(?: name)?|name of the project|"
    r"project(?: name| title|\s*#?\d+)?)\s*[:|]\s*(.+)", re.I)
_NEXT_FIELD = re.compile(
    r"^(?:roles?\b|responsibilit|duties\b|achievements?\b|environment\b|technolog|tools\b|"
    r"duration\b|client\b|project\s*(?:name|title|\d|:)|team size\b|education\b|certification|"
    r"technical skills\b|work experience\b|professional experience\b|key skills\b|"
    r"qualifications\b|professional summary\b|employment history\b|[-*•●▪]\s*|\d+[.)]\s)", re.I)


def _key(value):
    return " ".join(re.findall(r"[^\W_]+", value.casefold()))


@dataclass(frozen=True)
class Section:
    text: str
    overview: str


def sections(text: str, responsibilities: frozenset[str] = frozenset()) -> list[Section]:
    """Split repeated metadata groups before reading overview paragraphs."""
    groups, lines, identities = [], [], set()
    has_overview = False
    for raw in text.splitlines():
        line = raw.strip().strip("|").strip().replace("**", "").strip()
        content = line.lstrip("#").strip()
        identity = _IDENTITY.match(content)
        if identity:
            kind = "project" if "project" in identity[1].lower() else "client"
            if kind in identities or has_overview:
                groups.append(lines)
                lines, identities = [], set()
                has_overview = False
            identities.add(kind)
        has_overview |= bool(_OVERVIEW.match(content))
        lines.append(line)
    groups.append(lines)
    result = []
    for lines in groups:
        values = []
        for i, line in enumerate(lines):
            match = _OVERVIEW.match(line.lstrip("# "))
            if not match:
                continue
            value = (match[1] or "").lstrip(" |").split("|")[0].strip()
            if _NEXT_FIELD.match(value) and len(value.split()) <= 3 and "|" in line:
                continue  # Adjacent column headers are not an overview.
            parts = [value] if value else []
            # Table values end at their cell boundary. A standalone label may
            # introduce wrapped prose on the following lines.
            if "|" not in line:
                for following in lines[i+1:]:
                    if not following:
                        continue
                    if (following.casefold() in responsibilities or following.startswith("#")
                            or "|" in following or _NEXT_FIELD.match(following)
                            or _OVERVIEW.match(following) or re.match(r"^[\w &/-]{1,35}\s*:", following)):
                        break
                    parts.append(following)
            if parts:
                values.append(" ".join(" ".join(parts).split()))
        # Keep every labelled passage even if the text layer lost the project
        # boundary. The matcher can abstain and coverage can report the gap.
        for value in values or [""]:
            result.append(Section("\n".join(lines), value))
    return result


def word_sections(document) -> tuple[Section, ...]:
    records = []
    for table in document.tables:
        # Read each physical cell once, including horizontally merged cells.
        rows = []
        for row in table.rows:
            raw = [_Cell(tc, table).text for tc in row._tr.tc_lst]
            cells = [" ".join(value.split()) for value in raw]
            if len(cells) == 2 and re.fullmatch(RESPONSIBILITIES, cells[0].rstrip(" :"), re.I):
                bullets = [re.sub(r"^[-*•▪●]\s*", "", line.strip()) for line in raw[1].splitlines() if line.strip()]
                rows.append(["\n".join([cells[0].rstrip(" :"), *("- " + value for value in bullets)])])
            else:
                rows.append(cells)
        if not rows:
            continue
        labels = [cell.rstrip(" :").casefold() for cell in rows[0]]
        if (any(_OVERVIEW.fullmatch(label) for label in labels)
                and any(label in {"client", "client name", "organization", "project", "project name", "project title"}
                        for label in labels)):
            for cells in rows[1:]:
                if len(cells) == len(labels):
                    # Transpose the row into a single engagement with metadata first.
                    pairs = list(zip(labels, cells))
                    lines = [f"{label} | {value}" for label, value in pairs if not _OVERVIEW.fullmatch(label)]
                    descriptions = [value for label, value in pairs if _OVERVIEW.fullmatch(label)]
                    overview = descriptions[0] if len(descriptions) == 1 else ""
                    records.append(Section("\n".join([*lines, f"Project Overview | {overview}"]), overview))
        else:
            records.extend(sections("\n".join(" | ".join(cells) for cells in rows)))
    return tuple(records)


def match_sections(projects, records):
    """Accept only a unique strongest engagement in both directions."""
    def score(project, record):
        body = " " + _key(record.text) + " "
        heading = []
        for line in record.text.splitlines():
            if _OVERVIEW.match(line.lstrip("# ")):
                break
            heading.append(line)
        header = " " + _key(" ".join(heading)) + " "

        def contains(value, evidence=body):
            key = _key(value)
            return bool(key and " " + key + " " in evidence)

        org = contains(project.organization_name, header)
        title = not project.descriptive_title and contains(project.project_title, header)
        duties = sum(contains(b) for b in project.responsibilities if len(b.split()) >= 4)
        if not (org or title or duties):
            return 0
        dates = re.findall(r"(?:duration|period|dates)\s*[:|]\s*([^|\n]+)", record.text, re.I)
        date = bool(project.duration) and (contains(project.duration) or any(
            _key(display_duration(d)) == _key(display_duration(project.duration)) for d in dates))
        if dates and project.duration and not date and not (title or duties):
            return 0
        return 8 * int(title) + 6 * int(org) + 5 * int(date) + 4 * min(duties, 3) + int(contains(project.role))

    scores = [[score(project, record) for record in records] for project in projects]
    for row in scores:
        best = max(row, default=0)
        if best and row.count(best) == 1:
            index = row.index(best)
            column = [other[index] for other in scores]
            if max(column) == best and column.count(best) == 1:
                yield records[index]
                continue
        yield None
