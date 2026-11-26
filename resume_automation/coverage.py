"""Compare identifiable source content with the fields destined for the resume."""
from collections import Counter
import re

from .extraction import overview_records, project_blocks, recover_skill_lists
from .overviews import RESPONSIBILITIES, sections
from .schema import CoforgeResumeSchema


def normalized(value):
    return " ".join(value.replace("**", "").split()).casefold()


def listed_items(text, heading):
    """Read explicit list items under a heading, stopping at the next section."""
    active = False
    item = ""
    for line in text.splitlines():
        content = line.strip().strip("|#* ").rstrip(":").strip()
        if re.fullmatch(heading, content, re.I):
            active = True
        elif active:
            bullet = re.match(r"^\s*(?:[-*•▪●]|\d+[.)])\s+(.+)", line)
            if bullet:
                if item:
                    yield item
                item = bullet[1].strip()
            elif item and line.startswith(("  ", "\t")) and content and not content.endswith(":"):
                item += " " + content
            elif content:
                active = False
        if not active and item:
            yield item
            item = ""
    if item:
        yield item


def source_coverage(data, source, *, repair=False):
    """Return omissions that must be resolved before publishing a document."""
    issues = []
    def compare_items(label, items, expected):
        expected = list(expected)
        for index, value in enumerate(expected):
            if normalized(value) in normalized(" ".join(items)):
                continue
            if repair:
                # Insert before the next retained source bullet, preserving the
                # source ordering even when extraction dropped a middle item.
                later = {normalized(v) for v in expected[index + 1:]}
                position = next((i for i, v in enumerate(items) if normalized(v) in later), len(items))
                items.insert(position, value)
            else:
                issues.append(f"Missing {label} from source: {value!r}.")

    degrees = {normalized(v).rstrip(".") for v in data.education}
    for label, items, heading in [
        ("profile summary item", data.profile_summary, r"(?:profile|professional|career|executive) summary"),
        ("certification", data.certifications, r"(?:professional |technical )?certifications?"),
    ]:
        entries = list(listed_items(source.text, heading)) or listed_items(source.pdf_text, heading)
        compare_items(label, items, (v for v in entries if normalized(v).rstrip(".") not in degrees))

    skills = {category: items.copy() for category, items in data.technical_skills.items()}
    recover_skill_lists(skills, source.text)
    cleaned = CoforgeResumeSchema.clean_skills(skills)
    if repair:
        data.technical_skills = cleaned
    else:
        present = {normalized(v) for items in data.technical_skills.values() for v in items}
        issues.extend(f"Missing technical skill from source: {v!r}." for items in cleaned.values()
                      for v in items if normalized(v) not in present)

    records = overview_records(data.projects, source)
    for i, (project, record, block) in enumerate(zip(data.projects, records, project_blocks(data.projects, source.text)), 1):
        if record and record.overview and normalized(project.description) != normalized(record.overview):
            if repair:
                project.description = record.overview
            else:
                issues.append(f"Project {i} needs the full source overview: {record.overview!r}.")
        if record:
            for field, label in (("role", r"(?:candidate |project )?role"),
                                 ("duration", r"(?:project )?duration|period"),
                                 ("organization_name", r"(?:client|organisation|organization)(?: name)?"),
                                 ("project_title", r"project (?:title|name)|name of the project")):
                if getattr(project, field).strip():
                    continue
                values = {m.strip() for m in re.findall(
                    rf"(?:^|\|)\s*(?:{label})\s*[:|]\s*([^|\n]+)", record.text, re.I | re.M) if m.strip()}
                if len(values) == 1 and repair:
                    setattr(project, field, values.pop())
                    if field == "project_title":
                        project.descriptive_title = False
                elif values:
                    issues.append(f"Project {i} missing source {field}: {sorted(values)!r}.")
        duties = list(listed_items(record.text, RESPONSIBILITIES)) if record else []
        compare_items(f"project {i} responsibility", project.responsibilities,
                      duties or listed_items(block, RESPONSIBILITIES))

    for record in source.tables:
        duties = list(listed_items(record.text, RESPONSIBILITIES))
        if duties and record not in records and not any(
                all(normalized(d) in normalized(" ".join(p.responsibilities)) for d in duties)
                for p in data.projects):
            issues.append(f"Match source responsibilities to a project: {duties[0]!r}.")

    # Count occurrences separately in each reading. A second reading of a PDF
    # and two distinct engagements using the same overview have different meanings.
    required = Counter()
    duties = frozenset(normalized(b) for p in data.projects for b in p.responsibilities)
    for reading, native_table in ((source.tables, True), (sections(source.text, duties), False),
                                  (sections(source.pdf_text, duties), False)):
        counts = Counter(normalized(r.overview) for r in reading if r.overview and (
            native_table or re.search(r"(?im)^#*\s*(?:project\s+(?:description|overview|summary)|overview|description)\s*[:|\n]?",
                                      r.text)))
        required |= counts
    descriptions = [normalized(p.description) for p in data.projects if p.description]
    for value, count in required.items():
        missing = count - sum(f" {value} " in f" {description} " for description in descriptions)
        if missing > 0:
            issues.append(f"Match source overview to a project: {value!r} ({missing} missing).")
    return issues
