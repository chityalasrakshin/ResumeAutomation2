import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import redirect_stdout
from io import StringIO
import sys
import unittest
from unittest.mock import Mock, patch

from docx import Document
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from lxml import etree

from resume_automation.cli import main, process
from resume_automation.extraction import SourceDocument, extract_source, load_source_with_docling
from resume_automation.paths import MASTER_TEMPLATE as MASTER
from resume_automation.pipeline import build_graph
from resume_automation.template_engine import populate_coforge_document


def payload():
    projects = [dict(organization_name=f"Org {i}", project_title=f"Project {i}",
                     role="Engineer", duration=f"January 202{i}", description="Reviewed data.",
                     responsibilities=["Checked records."]) for i in (3, 2, 1)]
    return dict(full_name="Test Candidate", email="candidate@example.org", phone="555-0102", profile_summary=["Builds tools."],
                technical_skills={"Languages": ["Python"]}, certifications=["Certificate A"],
                projects=projects, missing_sections=[], confidence_score=0.9)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.source = self.folder / "input.docx"
        self.data = payload()
        doc = Document()
        doc.add_paragraph("Test Candidate\ncandidate@example.org\n555-0102\nBuilds tools.\nPython\nCertificate A")
        for project in self.data["projects"]:
            for value in project.values():
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

    def test_render(self):
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
            self.assertTrue(tbl.cell(0, 0).text.endswith(self.data["projects"][i]["organization_name"]))
            self.assertEqual(tbl.rows[4].cells[-1].paragraphs[0].style.name, "List Bullet")
            for xpath in ("./w:tblPr", "./w:tblGrid", "./w:tr/w:tc/w:tcPr"):
                self.assertEqual([etree.tostring(e) for e in tbl._tbl.xpath(xpath)],
                                 [etree.tostring(e) for e in master._tbl.xpath(xpath)])
        self.assertEqual(doc.tables[4].rows[2].cells[1].text, "")
        self.assertEqual(doc.tables[4].rows[2].cells[-1].text, "Project 2")

    def test_automatic_and_repeat(self):
        extractor = Mock(return_value=self.data)
        graph = build_graph(template_path=MASTER, output_dir=self.folder, extractor=extractor)
        status = process(graph, self.source)
        self.assertTrue(status.startswith("Generated "), status)
        self.assertTrue(Path(status.removeprefix("Generated ")).is_file())
        self.assertEqual(process(graph, self.source), status)
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
    def test_gateway_to_document(self, post):
        response = post.return_value
        response.is_redirect = False
        choice = {"finish_reason": "stop", "message": {"content": json.dumps(self.data)}}
        response.json.return_value = {"choices": [choice]}
        graph = build_graph(template_path=MASTER, output_dir=self.folder)
        status = process(graph, self.source)
        self.assertTrue(status.startswith("Generated "), status)
        self.converter.convert.assert_called_once_with(self.source, raises_on_error=False)
        args, kwargs = post.call_args
        self.assertEqual(args[0], os.environ["API_URL"])
        self.assertEqual(kwargs["headers"]["X-API-KEY"], "test-key")
        self.assertEqual(kwargs["json"]["model"], "gpt-5-2")
        self.assertEqual(len(kwargs["json"]["messages"]), 1)
        self.assertTrue(kwargs["json"]["messages"][0]["content"].endswith(self.markdown))
        choice["finish_reason"] = "length"
        with self.assertRaisesRegex(ValueError, "Incomplete Coforge response"):
            extract_source(SourceDocument(self.markdown, []))

    @patch("resume_automation.extraction.call_coforge_ai_studio")
    def test_incomplete_or_invalid_json(self, gateway):
        incomplete = dict(self.data)
        del incomplete["confidence_score"]
        for content in (json.dumps(incomplete), "invalid", json.dumps({**self.data, "confidence_score": 2})):
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
        gateway.return_value = json.dumps(self.data)
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
