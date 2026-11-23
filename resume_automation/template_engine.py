from copy import deepcopy
from pathlib import Path
from tempfile import NamedTemporaryFile

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt
from docx.table import Table, _Cell

from .schema import CoforgeResumeSchema


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


def _fill(cell, entries, *, keep=0):
    old = cell.paragraphs[keep:]
    props = deepcopy(old[0]._p.pPr) if old else None
    for p in old:
        cell._tc.remove(p._p)
    for value, kind in entries:
        p = cell.add_paragraph()
        if props is not None:
            p._p.insert(0, deepcopy(props))
        run = _write(p, value)
        if kind == "bullet":
            p.style = "List Bullet"
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.line_spacing = 1.15
        elif kind in ("category", "skill"):
            run.bold = kind == "category"
            p.paragraph_format.space_after = Pt(1)
            if kind == "skill":
                p.paragraph_format.left_indent = Inches(0.15)
    if not cell.paragraphs:
        p = cell.add_paragraph()
        if props is not None:
            p._p.insert(0, props)


def _bullets(doc):
    if "List Bullet" in doc.styles:
        return
    style = doc.styles.add_style("List Bullet", WD_STYLE_TYPE.PARAGRAPH)
    style.base_style = doc.styles["List Paragraph"]
    numbering = doc.part.numbering_part.element
    for num in numbering.num_lst:
        aid = num.abstractNumId.val
        if numbering.xpath(f"./w:abstractNum[@w:abstractNumId='{aid}']/w:lvl[@w:ilvl='0']/w:numFmt[@w:val='bullet']"):
            props = style.element.get_or_add_pPr().get_or_add_numPr()
            props.get_or_add_ilvl().val = 0
            props.get_or_add_numId().val = num.numId
            return
    raise ValueError("Template lacks a bullet numbering definition")


def populate_coforge_document(data, template_path, output_path):
    resume = CoforgeResumeSchema.model_validate(data)
    output = Path(output_path)
    if Path(template_path).resolve() == output.resolve():
        raise ValueError("Choose an output path distinct from the master template")
    doc = Document(template_path)
    profile, skills, certifications, master = doc.tables
    _bullets(doc)
    _write(_cell(profile, 0).paragraphs[0], resume.full_name)
    _fill(_cell(profile, 1), ((s, "bullet") for s in resume.profile_summary), keep=1)
    entries = []
    for category, items in resume.technical_skills.items():
        entries.append((category, "category"))
        entries.extend((item, "skill") for item in items)
    _fill(_cell(skills, 1), entries)
    _fill(_cell(certifications, 1), ((s, "bullet") for s in resume.certifications))

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
        _write(header, header.text.rstrip() + " " + project.organization_name)
        for row, col, value in [(1, 1, project.description), (2, 1, project.duration),
                                (2, 3, project.project_title), (3, 1, project.role)]:
            _write(_cell(table, row, col).paragraphs[0], value)
        _fill(_cell(table, 4, 1), ((s, "bullet") for s in project.responsibilities))
    master._tbl.getparent().remove(master._tbl)
    output.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=output.parent, suffix=".tmp", delete=False) as temp:
        path = Path(temp.name)
    try:
        doc.save(path)
        path.replace(output)
    finally:
        path.unlink(missing_ok=True)
    return output
