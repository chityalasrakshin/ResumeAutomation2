import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import redirect_stdout
from io import StringIO
from hashlib import sha256
import sys
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile

from docx import Document
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from lxml import etree

from resume_automation.cli import discover, main, process
from resume_automation.coverage import source_coverage
from resume_automation.overviews import sections
from resume_automation.extraction import SourceDocument, extract_source, load_source_with_docling
from resume_automation.paths import MASTER_TEMPLATE as MASTER
from resume_automation.pipeline import build_graph, verify
from resume_automation.schema import CoforgeResumeSchema
from resume_automation.template_engine import populate_coforge_document


def payload():
    projects = [dict(organization_name=f"Org {i}", project_title=f"Project {i}", descriptive_title=False,
                     role="Engineer", duration=f"January 202{i}", description="Reviewed data.",
                     responsibilities=["Checked records."]) for i in (3, 2, 1)]
    return dict(full_name="Test Candidate", private_names=[], email="candidate@example.org", phone="555-0102", profile_summary=["Builds tools."],
                technical_skills={"Languages": ["Python"]}, education=[], certifications=["Certificate A"],
                projects=projects, missing_sections=[], confidence_score=0.9)


def router_json(data):
    return json.dumps({**data, "technical_skills": [
        {"category": category, "items": items} for category, items in data["technical_skills"].items()
    ]})


class PipelineTests(unittest.TestCase):
    def test_discover_single_resume_and_folder(self):
        other = self.folder / "another resume.PDF"
        other.touch()
        legacy = self.folder / "legacy.doc"
        legacy.touch()
        self.assertEqual(discover(other), [other])
        self.assertEqual(discover(self.folder), sorted([self.source, other]))
        with self.assertRaisesRegex(ValueError, "Unsupported resume"):
            discover(legacy)
        with self.assertRaisesRegex(ValueError, "does not exist"):
            discover(self.folder / "missing.pdf")

    @patch("resume_automation.extraction.call_coforge_ai_studio")
    def test_cli_single_document_goes_to_default_output(self, gateway):
        gateway.return_value = router_json(self.data)
        target = self.folder / "output"
        with patch("resume_automation.cli.OUTPUT_DIR", target), patch.object(
                sys, "argv", ["batch_run.py", "--inputs", str(self.source), "--approve-all"]
        ), redirect_stdout(StringIO()):
            self.assertEqual(main(), 0)
        generated, = target.glob("*/*Generated.docx")
        self.assertEqual(len(Document(generated).tables[3:]), len(self.data["projects"]))
        self.assertTrue((target / "checkpoints.sqlite").is_file())

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.source = self.folder / "input.docx"
        self.data = payload()
        doc = Document()
        doc.add_paragraph("Test Candidate\ncandidate@example.org\n555-0102\nBuilds tools.\nPython\nCertificate A")
        for project in self.data["projects"]:
            for value in (v for v in project.values() if isinstance(v, (str, list))):
                doc.add_paragraph(value if isinstance(value, str) else value[0])
        doc.save(self.source)
        converter_patch = patch("resume_automation.extraction.get_converter")
        self.converter = converter_patch.start().return_value
        self.addCleanup(converter_patch.stop)
        self.conversion = self.converter.convert.return_value
        self.conversion.status.value = "success"
        self.conversion.errors = []
        self.markdown = "\n".join(p.text for p in doc.paragraphs)
        self.conversion.document.export_to_markdown.return_value = self.markdown

    def test_project_table_row0_organization_header(self):
        self.data["projects"][1]["duration"] = ""
        target = self.folder / "out.docx"
        populate_coforge_document(self.data, MASTER, target)
        doc = Document(target)
        self.assertEqual(len(doc.tables), 6)
        skills = doc.tables[1].rows[1].cells[0].paragraphs
        self.assertEqual([(p.text, p.runs[0].bold) for p in skills],
                         [("Languages", True), ("Python", False)])
        master = Document(MASTER).tables[3]
        for i, tbl in enumerate(doc.tables[3:]):
            self.assertEqual(tbl.cell(0, 0).text, "Name of the Project/Organization: Org " + str(3-i))
            self.assertEqual(tbl.rows[4].cells[-1].paragraphs[0].style.name, "List Paragraph")
            for xpath in ("./w:tblPr", "./w:tblGrid", "./w:tr/w:tc/w:tcPr"):
                self.assertEqual([etree.tostring(e) for e in tbl._tbl.xpath(xpath)],
                                 [etree.tostring(e) for e in master._tbl.xpath(xpath)])
        self.assertEqual(doc.tables[4].rows[2].cells[1].text, "")
        self.assertEqual(doc.tables[4].rows[2].cells[-1].text, "Project 2")

    def test_role_only_and_display_dates(self):
        self.data["private_names"] = ["Banking"]
        self.data["projects"][0].update(role="Developer, HSN Tech Solutions", duration="03/2016 – 09/2017",
                                        description="Delivered reports for Org 3 in Banking.")
        self.data["projects"][1].update(role="Consultant | Banking", duration="Sept. 2021 to current")
        self.data["projects"][2].update(duration="2019-2021")
        doc = Document(populate_coforge_document(self.data, MASTER, self.folder / "roles.docx"))
        for table, role, dates in zip(doc.tables[3:], ["Developer", "Consultant", "Engineer"],
                                      ["March 2016 – September 2017", "September 2021 – Present", "2019 – 2021"]):
            self.assertEqual(table.rows[3].cells[-1].text, role)
            self.assertEqual(table.rows[2].cells[1].text, dates)
        self.assertEqual(doc.tables[3].rows[1].cells[-1].text, "Delivered reports for Org 3 in Banking.")

    def test_reference_list_formatting(self):
        self.data["profile_summary"] = ["Builds tools. Tests APIs."]
        self.data["technical_skills"] = {"Platforms": ["Sales Cloud, AWS (EC2, S3)"]}
        doc = Document(populate_coforge_document(self.data, MASTER, self.folder / "lists.docx"))
        summary = doc.tables[0].cell(1, 0).paragraphs[1:]
        category, *skills = doc.tables[1].cell(1, 0).paragraphs
        self.assertEqual([p.text for p in summary], ["Builds tools.", "Tests APIs."])
        self.assertEqual([p.text for p in skills], ["Sales Cloud", "AWS (EC2, S3)"])
        self.assertTrue(category.runs[0].bold)
        self.assertIsNone(category._p.pPr.numPr)
        for p in [*summary, *skills, doc.tables[3].rows[4].cells[-1].paragraphs[0]]:
            n = doc.part.numbering_part.element
            num = n.num_having_numId(p._p.pPr.numPr.numId.val)
            lvl = f"./w:abstractNum[@w:abstractNumId='{num.abstractNumId.val}']/w:lvl[@w:ilvl='0']"
            self.assertTrue(n.xpath(lvl + "/w:numFmt[@w:val='bullet']"))
            self.assertTrue(n.xpath(lvl + "/w:pPr/w:ind[@w:left='720'][@w:hanging='360']"))
        self.assertEqual(skills[0].runs[0].font.name, "Cambria")
        self.assertEqual(skills[0].runs[0].font.size.pt, 12)

    def test_word_header_evidence(self):
        doc = Document(self.source)
        doc.sections[0].header.paragraphs[0].text = "Candidate contact in header"
        doc.save(self.source)
        source = load_source_with_docling(self.source)
        self.assertTrue(source.text.startswith("Candidate contact in header\n"))

    @patch("resume_automation.extraction.call_coforge_ai_studio")
    def test_missing_project_metadata_from_pdf_layer(self, gateway):
        for project in self.data["projects"]:
            project.update(project_title="", duration="")
        gateway.return_value = router_json(self.data)
        native = ("Client: Org 1\nProject Title: Migration\n03/2020 – 04/2022\n"
                  "Client: Org 2\n| Duration | May 2022 - Present | Title | Reporting |\n"
                  "Client: Org 3\nEducation\n2015 - 2019")
        recovered = extract_source(SourceDocument("Org 1\nOrg 2\nOrg 3", [], native))
        self.assertEqual([(p["project_title"], p["duration"]) for p in recovered["projects"]],
                         [("", ""), ("Reporting", "May 2022 - Present"), ("Migration", "03/2020 – 04/2022")])

    def test_empty_description_leaves_cell_blank(self):
        self.data["projects"][0]["description"] = ""
        self.data["education"] = ["Degree A"]
        self.data["certifications"].append("Degree A.")
        self.data["profile_summary"].append("Degree A")
        doc = Document(populate_coforge_document(self.data, MASTER, self.folder / "blank.docx"))
        self.assertEqual(doc.tables[3].rows[1].cells[-1].text, "")
        self.assertNotIn("Degree A", doc.element.body.xml)

    def test_education_evidence_and_optional_template_table(self):
        self.data["education"] = ["Degree A"]
        self.data["projects"][0]["description"] = ""
        resume = CoforgeResumeSchema.model_validate(self.data)
        issues = verify(resume, SourceDocument(self.markdown, []))
        self.assertEqual(issues, ["Inspect education: 'Degree A'."])
        self.assertEqual(verify(resume, SourceDocument("Degree A\n" + self.markdown, [])), [])
        template = Document(MASTER)
        table = template.add_table(rows=2, cols=1)
        table.cell(0, 0).text = "Education"
        path = self.folder / "template.docx"
        template.save(path)
        rendered = Document(populate_coforge_document(resume, path, self.folder / "education.docx"))
        self.assertEqual(rendered.tables[-1].cell(1, 0).text, "Degree A")

    @patch("resume_automation.extraction.call_coforge_ai_studio")
    def test_description_provenance(self, gateway):
        for p, description in zip(self.data["projects"], ["Built a billing service.", "IDMC", "Reviewed tickets."]):
            p["description"] = description
        gateway.return_value = router_json(self.data)
        source = SourceDocument("Org 3\nProject overview: Built a billing service.\nOrg 2\nEnvironment: IDMC\n"
                                "Org 1\nResponsibilities\nReviewed tickets.", [])
        self.assertEqual([p["description"] for p in extract_source(source)["projects"]],
                         ["Built a billing service.", "", ""])

    def test_responsibilities_grammar_preservation(self):
        lines = ["Interfaced with Barclays team", "Automated Verizon billing system",
                 "Reported to Citi leadership"]
        self.data["private_names"] = ["Barclays", "Verizon", "Citi"]
        self.data["projects"][0]["responsibilities"] = lines
        doc = Document(populate_coforge_document(self.data, MASTER, self.folder / "grammar.docx"))
        self.assertEqual([p.text for p in doc.tables[3].rows[4].cells[-1].paragraphs], lines)

    @patch("resume_automation.extraction.call_coforge_ai_studio")
    def test_combined_technical_and_key_skills(self, gateway):
        self.data["technical_skills"] = {"Languages": ["Python"], "Tools": ["python, AWS (EC2, S3)", "Teamwork"]}
        gateway.return_value = router_json(self.data)
        result = extract_source(SourceDocument("Technical Skills: Python\nKey Skills: python, AWS (EC2, S3)\n"
                                               "Methodologies: Scrum\nEnvironment: Git", []))
        self.assertEqual(result["technical_skills"], {"Languages": ["Python"], "Tools": ["AWS (EC2, S3)"],
                         "Methodologies & Frameworks": ["Scrum"], "Tools & Technologies": ["Git"]})

    @patch("resume_automation.extraction.call_coforge_ai_studio")
    def test_title_decision_logic(self, gateway):
        for p in self.data["projects"]:
            p.update(project_title="Customer Billing Platform", descriptive_title=True)
        self.data["projects"][2].update(project_title="Name of the Project: Explicit Name", descriptive_title=False)
        gateway.return_value = router_json(self.data)
        source = SourceDocument("Org 3\nProject: ePay 2.0\nOrg 2\nOrg 1\nTitle: Other", [])
        projects = extract_source(source)["projects"]
        self.assertEqual([(p["project_title"], p["descriptive_title"]) for p in projects],
                         [("ePay 2.0", False), ("Customer Billing Platform", True), ("Explicit Name", False)])

    @patch("resume_automation.extraction.request_captions")
    @patch("resume_automation.extraction.call_coforge_ai_studio")
    def test_role_and_tool_titles_receive_project_specific_captions(self, gateway, captions):
        self.data["projects"][0]["project_title"] = "Engineer"
        self.data["projects"][1]["project_title"] = "Python"
        gateway.return_value = router_json(self.data)
        captions.return_value = ["Customer Records Validation", "Transaction Data Reconciliation"]
        projects = extract_source(SourceDocument(self.markdown, []))["projects"]
        self.assertEqual([p["project_title"] for p in projects[:2]], captions.return_value)
        self.assertTrue(all(p["descriptive_title"] for p in projects[:2]))
        self.assertEqual([p["organization"] for p in captions.call_args.args[0]], ["Org 3", "Org 2"])
        captions.return_value = ["Engineer", "Python"]
        with self.assertRaisesRegex(ValueError, "invalid descriptive project caption"):
            extract_source(SourceDocument(self.markdown, []))

    def test_automatic_and_repeat(self):
        extractor = Mock(return_value=self.data)
        graph = build_graph(template_path=MASTER, output_dir=self.folder, extractor=extractor)
        status = process(graph, self.source)
        self.assertTrue(status.startswith("Generated "), status)
        self.assertTrue(Path(status.removeprefix("Generated ")).is_file())
        self.assertEqual(process(graph, self.source), status)
        renamed = self.folder / "identical.docx"
        renamed.write_bytes(self.source.read_bytes())
        self.assertEqual(process(graph, renamed), status)
        path = Path(status.removeprefix("Generated "))
        original = path.read_bytes()
        with ZipFile(path) as package:
            self.assertEqual({m.date_time for m in package.infolist()}, {(1980, 1, 1, 0, 0, 0)})
        path.unlink()
        self.assertEqual(process(graph, renamed), status)
        self.assertEqual(path.read_bytes(), original)
        extractor.assert_called_once()

    def test_persistent_review(self):
        self.data["projects"][1]["duration"] = "January 2023"  # belongs to another project
        database = str(self.folder / "state.sqlite")
        with SqliteSaver.from_conn_string(database) as saver:
            graph = build_graph(saver, MASTER, self.folder, lambda _: self.data)
            status = process(graph, self.source)
            self.assertTrue(status.startswith("Review "), status)
            self.assertEqual(list(self.folder.rglob("*Generated.docx")), [])
        with SqliteSaver.from_conn_string(database) as saver:
            graph = build_graph(saver, MASTER, self.folder)
            config = {"configurable": {"thread_id": status.split()[1]}}
            self.assertIn("duration", str(graph.get_state(config).interrupts[0].value["issues"]))
            self.data["projects"][1]["duration"] = ""
            result = graph.invoke(Command(resume={"approved": True, "corrected_payload": self.data}), config)
            self.assertTrue(Path(result["output_docx_path"]).is_file())

    @patch.dict(os.environ, {"API_KEY": "test-key", "MODEL_NAME": "gpt-5-2",
                             "API_URL": "https://quasarmarket.coforge.com/qag/llmrouter-api/v2/chat/completions"})
    @patch("resume_automation.extraction.requests.post")
    def test_structured_outputs_payload_conformance(self, post):
        response = post.return_value
        response.is_redirect = False
        choice = {"finish_reason": "stop", "message": {"content": router_json(self.data)}}
        response.json.return_value = {"choices": [choice]}
        graph = build_graph(template_path=MASTER, output_dir=self.folder)
        status = process(graph, self.source)
        self.assertTrue(status.startswith("Generated "), status)
        self.converter.convert.assert_called_once_with(self.source, raises_on_error=False)
        args, kwargs = post.call_args
        self.assertEqual(args[0], os.environ["API_URL"])
        self.assertEqual(kwargs["headers"]["X-API-KEY"], "test-key")
        self.assertEqual(kwargs["json"]["model"], "gpt-5-2")
        payload = kwargs["json"]
        self.assertEqual([m["role"] for m in payload["messages"]], ["system", "user"])
        self.assertEqual((payload["temperature"], payload["top_p"]), (0.0, 1.0))
        response_format = payload["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        self.assertIs(response_format["json_schema"]["strict"], True)
        schema = response_format["json_schema"]["schema"]
        for obj in [schema, *schema["$defs"].values()]:
            self.assertEqual(set(obj["required"]), set(obj["properties"]))
            self.assertIs(obj["additionalProperties"], False)
        self.assertTrue(kwargs["json"]["messages"][1]["content"].endswith(self.markdown))
        choice["finish_reason"] = "length"
        with self.assertRaisesRegex(ValueError, "Incomplete Coforge response"):
            extract_source(SourceDocument(self.markdown, []))

    @patch("resume_automation.extraction.call_coforge_ai_studio")
    def test_incomplete_or_invalid_json(self, gateway):
        incomplete = dict(self.data)
        del incomplete["confidence_score"]
        for content in (router_json(incomplete), "invalid", router_json({**self.data, "confidence_score": 2}),
                        "```json\n" + router_json(self.data) + "\n```", json.dumps(self.data)):
            with self.subTest(content=content), self.assertRaises(ValueError):
                gateway.return_value = content
                extract_source(SourceDocument(self.markdown, []))

    def test_partial_conversion_requires_review(self):
        self.conversion.status.value = "partial_success"
        graph = build_graph(template_path=MASTER, output_dir=self.folder, extractor=lambda _: self.data)
        self.assertTrue(process(graph, self.source).startswith("Review "))

    @patch("resume_automation.extraction.call_coforge_ai_studio")
    def test_batch_show_and_approve(self, gateway):
        self.data["confidence_score"] = 0.89
        gateway.return_value = router_json(self.data)
        output = self.folder / "output"

        def run(*args):
            argv = ["batch_run.py", "--inputs", str(self.folder), "--output", str(output), *args]
            with patch.object(sys, "argv", argv), redirect_stdout(StringIO()) as stream:
                self.assertEqual(main(), 0)
                return stream.getvalue().strip()

        thread = run().split("Review ")[1]
        pending = json.loads(run("--show", thread))
        self.assertTrue(any("confidence" in issue for issue in pending["issues"]))
        self.assertEqual(pending["payload"], self.data)
        corrected = self.folder / "corrected.json"
        corrected.write_text(json.dumps(self.data), encoding="utf-8")
        generated = run("--approve", thread, "--corrected", str(corrected))
        self.assertTrue(Path(generated).is_file())
        self.assertIn(generated, run())
        gateway.assert_called_once()

    @patch("resume_automation.extraction.requests.post")
    def test_failed_and_empty_conversion(self, post):
        self.conversion.document.export_to_markdown.return_value = ""
        for status in ("failure", "success"):
            self.conversion.status.value = status
            with self.subTest(status=status), self.assertRaises(ValueError):
                load_source_with_docling(self.source)
        post.assert_not_called()
    @patch("resume_automation.extraction.call_coforge_ai_studio")
    def test_all_eight_word_overviews_from_original_cells(self, gateway):
        source_file = next((MASTER.parent.parent / "Inputs").glob("5_Prashant*.docx"))
        tables = Document(source_file).tables[1:]
        self.assertEqual(len(tables), 8)
        projects, expected = [], []
        for table in reversed(tables):
            header = table.rows[0].cells
            projects.append(dict(self.data["projects"][0],
                                 organization_name=header[0].text.split(":", 1)[1].strip(),
                                 duration=header[-1].text.split(":", 1)[1].strip(),
                                 role=table.rows[3].cells[-1].text.strip(), description="",
                                 project_title="Banking Operations Workflow Initiatives", descriptive_title=True))
            expected.append(" ".join(table.rows[4].cells[-1].text.split()))
        self.data["projects"] = projects
        gateway.return_value = router_json(self.data)
        self.conversion.document.export_to_markdown.return_value = "Tables lost during conversion"
        data = extract_source(load_source_with_docling(source_file))
        doc = Document(populate_coforge_document(data, MASTER, self.folder / "recovered.docx"))
        self.assertEqual([t.rows[1].cells[-1].text for t in doc.tables[3:]], expected)

    @patch("resume_automation.extraction.call_coforge_ai_studio")
    def test_wrapped_pdf_overviews_and_empty_section(self, gateway):
        for project in self.data["projects"]:
            project.update(description="", organization_name="Shared Client")
        gateway.return_value = router_json(self.data)
        native = ("Project 1: Project 1\nClient: Shared Client\nProject Description: First line\ncontinued here.\n"
                  "Responsibilities:\nBuild reports.\nProject 2: Project 2\nClient: Shared Client\n"
                  "Project Overview\nSecond overview.\nEnvironment: SQL\nProject 3: Project 3\n"
                  "Client: Shared Client\nProject Overview\nRoles and Responsibilities\nBuild tools.")
        result = extract_source(SourceDocument("", [], native))
        self.assertEqual([p["description"] for p in result["projects"]], ["", "Second overview.", "First line continued here."])

    @patch("resume_automation.extraction.call_coforge_ai_studio")
    def test_ambiguous_overviews_require_review(self, gateway):
        projects = self.data["projects"][:2]
        for project in projects:
            project.update(organization_name="Shared", description="", duration="")
        self.data["projects"] = projects
        gateway.return_value = router_json(self.data)
        tables = tuple(sections("Client: Shared\nProject Overview | First.\nClient: Shared\nProject Overview | Second."))
        source = SourceDocument("", [], tables=tables)
        data = CoforgeResumeSchema.model_validate(extract_source(source))
        self.assertEqual([p.description for p in data.projects], ["", ""])
        self.assertEqual(sum("Match source overview" in s for s in verify(data, source)), 2)

    def test_existing_checkpoint_recovers_missing_overview_once(self):
        self.data["projects"][0]["description"] = ""
        self.conversion.document.export_to_markdown.return_value = self.markdown.replace(
            "Reviewed data.", "Project Overview: Original source overview.", 1)
        extractor = Mock(return_value=self.data)
        graph = build_graph(template_path=MASTER, output_dir=self.folder, extractor=extractor)
        status = process(graph, self.source, True)
        key = sha256(b"coforge-v4\0" + self.source.read_bytes()).hexdigest()
        config = {"configurable": {"thread_id": key}}
        graph.update_state(config, {"overviews_checked": False}, as_node="generate")
        self.assertEqual(process(graph, self.source, True), status)
        doc = Document(status.removeprefix("Generated "))
        self.assertEqual(doc.tables[3].rows[1].cells[-1].text, "Original source overview.")
        self.assertEqual(doc.tables[4].rows[1].cells[-1].text, "Reviewed data.")
        self.assertTrue(graph.get_state(config).values["overviews_checked"])
        self.assertEqual(process(graph, self.source), status)
        self.assertEqual(self.converter.convert.call_count, 2)
        extractor.assert_called_once()

    @patch("resume_automation.extraction.call_coforge_ai_studio")
    def test_word_overview_column_preceding_client_column(self, gateway):
        doc = Document()
        table = doc.add_table(rows=4, cols=3)
        rows = [("Project Description", "Client", "Responsibilities"),
                ("First overview.", "Org 1", "First duty."),
                ("", "Org 2", "Second duty."),
                ("Third overview.", "Org 3", "Third duty.")]
        for row, values in zip(table.rows, rows):
            for cell, value in zip(row.cells, values):
                cell.text = value
        doc.save(self.source)
        self.conversion.document.export_to_markdown.return_value = "Columns unavailable"
        for project in self.data["projects"]:
            project["description"] = ""
        gateway.return_value = router_json(self.data)
        result = extract_source(load_source_with_docling(self.source))
        self.assertEqual([p["description"] for p in result["projects"]], ["Third overview.", "", "First overview."])

    def test_bulk_approval_stops_until_omitted_project_is_restored(self):
        self.data["projects"] = self.data["projects"][:1]
        self.conversion.document.export_to_markdown.return_value = (
            "Client: Org 3\nProject Overview: Claims processing.\n"
            "Client: Other Client\nProject Overview: Billing reconciliation.")
        graph = build_graph(output_dir=self.folder, extractor=lambda _: self.data)
        status = process(graph, self.source, True)
        self.assertTrue(status.startswith("Review "))
        config = {"configurable": {"thread_id": status.split()[1]}}
        result = graph.invoke(Command(resume={"approved": True}), config)
        self.assertTrue(result["__interrupt__"])
        reviewed = result["__interrupt__"][0].value["payload"]
        reviewed["projects"].append(dict(reviewed["projects"][0], organization_name="Other Client",
                                         project_title="Billing", description="Billing reconciliation."))
        reviewed["projects"][0]["description"] = "Claims"
        result = graph.invoke(Command(resume={"approved": True, "corrected_payload": reviewed}), config)
        self.assertTrue(result["__interrupt__"])
        self.assertFalse(list(self.folder.rglob("*Generated.docx")))
        reviewed["projects"][0]["description"] = "Claims processing."
        result = graph.invoke(Command(resume={"approved": True, "corrected_payload": reviewed}), config)
        doc = Document(result["output_docx_path"])
        self.assertEqual([t.rows[1].cells[-1].text for t in doc.tables[3:]],
                         ["Claims processing.", "Billing reconciliation."])

    def test_source_list_coverage_restores_missing_items_in_order(self):
        data = CoforgeResumeSchema.model_validate(self.data)
        data.profile_summary = ["First.", "Third."]
        source = SourceDocument("## Profile Summary\n- First.\n- Second.\n- Third.\n"
                                "## Certifications\n- Certificate B\nTechnical Skills: SQL\n"
                                "Org 3\nResponsibilities\n- Built the payment platform.", [])
        self.assertEqual(source_coverage(data, source, repair=True), [])
        self.assertEqual(data.profile_summary, ["First.", "Second.", "Third."])
        self.assertIn("Certificate B", data.certifications)
        self.assertIn("SQL", [s for values in data.technical_skills.values() for s in values])
        self.assertIn("Built the payment platform.", data.projects[0].responsibilities)
        self.assertEqual(source_coverage(data, source), [])

    def test_missing_serialized_cell_preserves_previous_output(self):
        from resume_automation import template_engine
        original = template_engine._write
        def faulty_write(paragraph, value):
            return original(paragraph, "" if value == "Reviewed data." else value)
        dest = self.folder / "existing.docx"
        dest.write_bytes(b"previous document")
        with patch.object(template_engine, "_write", side_effect=faulty_write):
            with self.assertRaisesRegex(ValueError, "project 1 description"):
                populate_coforge_document(self.data, MASTER, dest)
        self.assertEqual(dest.read_bytes(), b"previous document")

    def test_source_overview_counts_and_full_paragraphs(self):
        data = CoforgeResumeSchema.model_validate(self.data)
        data.projects = data.projects[:1]
        paragraph = "First paragraph.\n\nSecond paragraph."
        native = (f"Client: Org 3\nProject Overview: {paragraph}\nResponsibilities:\n- Duty A\n"
                  f"Client: Another Client\nProject Overview: {paragraph}\nResponsibilities:\n- Duty B")
        issues = source_coverage(data, SourceDocument("", [], native), repair=True)
        self.assertEqual(data.projects[0].description, "First paragraph. Second paragraph.")
        self.assertTrue(any("(1 missing)" in issue for issue in issues))
