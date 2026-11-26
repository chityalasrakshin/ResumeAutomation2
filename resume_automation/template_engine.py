from copy import deepcopy
from io import BytesIO
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from zipfile import ZipFile

from docx.shared import Pt
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.table import Table, _Cell

from .schema import CoforgeResumeSchema
from .presentation import display_duration, display_text, private_names


def _cell(table, row, column=0):
    # row.cells repeats merged cells for each occupied grid column.
    return _Cell(table.rows[row]._tr.tc_lst[column], table)


def _write(paragraph, value):
    runs = paragraph.runs
    if runs:
        runs[0].text = value
        for run in runs[1:]:
            run.text = ""
        return runs[0]
    run = paragraph.add_run(value)
    props = paragraph._p.xpath("./w:pPr/w:rPr")
    if props:
        run._r.insert(0, deepcopy(props[0]))
    return run


def _fill(cell, entries, numbers, *, keep=0):
    old = cell.paragraphs[keep:]
    props = deepcopy(old[0]._p.pPr) if old else None
    for p in old:
        cell._tc.remove(p._p)
    for value, kind in entries:
        p = cell.add_paragraph()
        if props is not None:
            p._p.insert(0, deepcopy(props))
        run = _write(p, value)
        if kind in ("bullet", "skill"):
            if kind == "bullet":
                p.style = "List Paragraph"
            numbering = p._p.get_or_add_pPr().get_or_add_numPr()
            numbering.get_or_add_ilvl().val = 0
            numbering.get_or_add_numId().val = numbers[kind]
        if kind in ("category", "skill"):
            run.font.name = "Cambria"
            run.font.size = Pt(12)
            run.bold = kind == "category"
    if not cell.paragraphs:
        p = cell.add_paragraph()
        if props is not None:
            p._p.insert(0, props)


def _bullets(doc):
    # Both reference lists use a Symbol dot. Skill markers have a 10 pt override.
    numbering = doc.part.numbering_part.element
    numbers = {}
    for num in numbering.num_lst:
        level = f"./w:abstractNum[@w:abstractNumId='{num.abstractNumId.val}']/w:lvl[@w:ilvl='0']"
        if numbering.xpath(level + "/w:numFmt[@w:val='bullet']"):
            kind = "skill" if numbering.xpath(level + "/w:rPr/w:sz[@w:val='20']") else "bullet"
            numbers.setdefault(kind, num.numId)
    if not numbers:
        raise ValueError("Template lacks a bullet numbering definition")
    fallback = next(iter(numbers.values()))
    return {kind: numbers.get(kind, fallback) for kind in ("bullet", "skill")}


def _items(values, *, sentences=False):
    for value in values:
        # Collapse soft wraps while retaining explicit source bullet boundaries.
        chunks = re.split(r"(?:^|\n)\s*[-*]\s+|[•●▪]|(?:^|\s)·(?=\S)", value)
        for chunk in chunks:
            line = " ".join(chunk.split())
            if sentences:
                parts = re.split(r"(?<!\b[A-Z]\.)(?<!\be\.g\.)(?<!\bi\.e\.)(?<=[.!?])\s+(?=[A-Z])", line)
            else:
                parts = [line]
            yield from (part.strip() for part in parts if part.strip())


def check_rendered_content(path, resume, names):
    """Reopen the staged Word package and check the actual destination cells."""
    def check(label, actual, expected):
        if actual != expected:
            raise ValueError(f"Generated document failed content verification for {label}.")

    tables = []
    for table in Document(path).tables:
        if _cell(table, 0).text.strip().casefold() == "education":
            check("education", _cell(table, 1).text, "\n".join(resume.education))
        else:
            tables.append(table)
    check("project table count", len(tables), 3 + len(resume.projects))
    profile, skills, certs, *projects = tables
    check("candidate name", _cell(profile, 0).text.strip(), resume.full_name.strip())
    check("profile summary", "\n".join(p.text for p in _cell(profile, 1).paragraphs[1:]),
          "\n".join(_items(resume.profile_summary, sentences=True)))
    check("technical skills", _cell(skills, 1).text,
          "\n".join(v.strip() for k, vs in resume.technical_skills.items() for v in (k, *vs) if v.strip()))
    check("certifications", _cell(certs, 1).text, "\n".join(v.strip() for v in resume.certifications if v.strip()))
    for i, (table, project) in enumerate(zip(projects, resume.projects), 1):
        for row, col, label, value in (
            (0, 0, "organization", f"Name of the Project/Organization: {project.organization_name}".rstrip()),
            (1, 1, "description", " ".join(project.description.split())),
            (2, 1, "duration", display_duration(project.duration)),
            (2, 3, "title", project.project_title.strip()),
            (3, 1, "role", display_text(project.role, names, role=True)),
            (4, 1, "responsibilities", "\n".join(_items(project.responsibilities))),
        ):
            check(f"project {i} {label}", _cell(table, row, col).text, value)


def populate_coforge_document(data, template_path, output_path):
    resume = CoforgeResumeSchema.model_validate(data)
    output = Path(output_path)
    if Path(template_path).resolve() == output.resolve():
        raise ValueError("Choose an output path distinct from the master template")
    doc = Document(template_path)
    tables = []
    numbers = _bullets(doc)
    for table in doc.tables:
        if _cell(table, 0).text.strip().casefold() == "education":
            _fill(_cell(table, 1), ((item, "bullet") for item in resume.education), numbers)
        else:
            tables.append(table)
    profile, skills, certifications, master = tables
    names = private_names(resume)

    def visible(items):
        return filter(None, (display_text(item, names) for item in items))

    _write(_cell(profile, 0).paragraphs[0], "  " + resume.full_name)
    _fill(_cell(profile, 1), ((s, "bullet") for s in visible(_items(resume.profile_summary, sentences=True))), numbers, keep=1)
    entries = []
    for category, items in resume.technical_skills.items():
        category = display_text(category, names)
        if category:
            entries.append((category, "category"))
        entries.extend((item, "skill") for item in visible(items))
    _fill(_cell(skills, 1), entries, numbers)
    _fill(_cell(certifications, 1), ((s, "bullet") for s in visible(resume.certifications)), numbers)

    for index, project in enumerate(resume.projects):
        xml = deepcopy(master._tbl)
        if index:
            master._tbl.addprevious(OxmlElement("w:p"))
            for paragraph in xml.xpath(".//w:p"):
                paragraph.attrib.pop(qn("w14:paraId"), None)
                paragraph.attrib.pop(qn("w14:textId"), None)
        master._tbl.addprevious(xml)
        table = Table(xml, master._parent)
        header = _cell(table, 0).paragraphs[0]
        role = display_text(project.role, names, role=True)
        _write(header, f"Name of the Project/Organization: {project.organization_name}".rstrip())
        for row, col, value in [(1, 1, display_text(" ".join(project.description.split()), names)),
                                (2, 1, display_duration(project.duration)),
                                (2, 3, display_text(project.project_title, names)), (3, 1, role)]:
            _write(_cell(table, row, col).paragraphs[0], value)
        _fill(_cell(table, 4, 1), ((s, "bullet") for s in visible(_items(project.responsibilities))), numbers)
    master._tbl.getparent().remove(master._tbl)
    output.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=output.parent, suffix=".tmp", delete=False) as temp:
        path = Path(temp.name)
    try:
        # python-docx timestamps each ZIP member with the current wall clock.
        package = BytesIO()
        doc.save(package)
        with ZipFile(package) as source, ZipFile(path, "w") as target:
            for member in source.infolist():
                content = source.read(member)
                member.date_time = (1980, 1, 1, 0, 0, 0)
                target.writestr(member, content)
        check_rendered_content(path, resume, names)
        path.replace(output)
    finally:
        path.unlink(missing_ok=True)
    return output
