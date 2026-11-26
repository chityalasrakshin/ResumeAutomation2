import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
import json
from pathlib import Path
import sys
from threading import Lock

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .paths import INPUT_DIR, MASTER_TEMPLATE, OUTPUT_DIR
from .extraction import load_source_with_docling
from .coverage import source_coverage
from .pipeline import build_graph, verify
from .schema import CoforgeResumeSchema


def discover(folder):
    extensions = {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
    if folder.is_file():
        if folder.name.startswith("~$") or folder.suffix.lower() not in extensions:
            raise ValueError(f"Unsupported resume: {folder.name}. Expected PDF, DOCX or a supported image.")
        return [folder]
    if not folder.is_dir():
        raise ValueError(f"Input file or directory does not exist: {folder}")
    return sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith("~$")
                  and p.suffix.lower() in extensions)


# Identical files in concurrent batch slots share one checkpoint and output.
_thread_locks = [Lock() for _ in range(64)]


def process(graph, path, approve_all=False):
    # Regenerate with the current extraction and layout after the format upgrade.
    digest = sha256(b"coforge-v4\0" + path.read_bytes()).hexdigest()
    with _thread_locks[int(digest[:2], 16) % len(_thread_locks)]:
        return _process_thread(graph, path, approve_all, digest)


def _process_thread(graph, path, approve_all, digest):
    config = {"configurable": {"thread_id": digest}}
    snapshot = graph.get_state(config)
    if snapshot.values.get("extracted_data") and (
            not snapshot.values.get("overviews_checked") or "coverage_issues" not in snapshot.values):
        data = CoforgeResumeSchema.model_validate(snapshot.values["extracted_data"])
        source = load_source_with_docling(path)
        coverage = source_coverage(data, source, repair=True)
        updates = {"extracted_data": data.model_dump(), "overviews_checked": True, "coverage_issues": coverage}
        if coverage or updates["extracted_data"] != snapshot.values["extracted_data"] or snapshot.next:
            updates["flagged_issues"] = verify(data, source, coverage)
            graph.update_state(config, updates, as_node="extract")
        else:
            graph.update_state(config, {**updates, "validation_passed": True}, as_node="review")
        snapshot = graph.get_state(config)
    if snapshot.interrupts:
        result = {"__interrupt__": snapshot.interrupts}
    elif snapshot.next:
        result = graph.invoke(None, config)
    elif snapshot.values and Path(snapshot.values["output_docx_path"]).is_file():
        return f"Generated {snapshot.values['output_docx_path']}"
    elif snapshot.values:
        # Re-render a removed output from the approved checkpoint.
        graph.update_state(config, {"validation_passed": True}, as_node="review")
        result = graph.invoke(None, config)
    else:
        result = graph.invoke({"file_path": str(path.resolve())}, config)
    if result.get("__interrupt__"):
        if not approve_all or result["__interrupt__"][0].value.get("coverage_issues"):
            return f"Review {digest}"
        result = graph.invoke(Command(resume={"approved": True}), config)
        if result.get("__interrupt__"):
            return f"Review {digest}"
    return f"Generated {result['output_docx_path']}"


def run_bulk_batch(graph, folder, workers, approve_all=False):
    files = discover(folder)
    print(f"Discovered {len(files)} resumes.")
    failures = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(process, graph, path, approve_all): path for path in files}
        for future in as_completed(futures):
            try:
                status = future.result()
                print(f"{futures[future].name}: {status}")
                if approve_all and status.startswith("Review "):
                    failures += 1
            except Exception as exc:
                failures += 1
                print(f"{futures[future].name}: {type(exc).__name__}: {exc}", file=sys.stderr)
    return int(bool(failures))


def main():
    parser = argparse.ArgumentParser(description="Generate or review Coforge resumes")
    parser.add_argument("--inputs", type=Path, default=INPUT_DIR, help="Resume file or directory (default: Inputs)")
    parser.add_argument("--template", type=Path, default=MASTER_TEMPLATE)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--workers", type=int, default=3)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--show", metavar="THREAD_ID")
    mode.add_argument("--approve", metavar="THREAD_ID")
    mode.add_argument("--approve-all", action="store_true",
                      help="Approve routine review flags. Detected source omissions require a corrected payload.")
    parser.add_argument("--corrected", type=Path, help="Reviewed resume payload JSON for --approve")
    args = parser.parse_args()
    if args.workers < 1 or (args.corrected and not args.approve):
        parser.error("Use positive --workers and use --corrected only with --approve.")
    args.output.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(str(args.output / "checkpoints.sqlite")) as saver:
        graph = build_graph(saver, args.template, args.output)
        thread = args.show or args.approve
        if not thread:
            try:
                return run_bulk_batch(graph, args.inputs, args.workers, args.approve_all)
            except ValueError as exc:
                parser.error(str(exc))
        config = {"configurable": {"thread_id": thread}}
        pending = graph.get_state(config).interrupts
        if not pending:
            parser.error("No pending review for this thread.")
        if args.show:
            print(json.dumps(pending[0].value, indent=2))
            return 0
        decision = {"approved": True}
        if args.corrected:
            decision["corrected_payload"] = CoforgeResumeSchema.model_validate_json(
                args.corrected.read_text(encoding="utf-8-sig")).model_dump()
        result = graph.invoke(Command(resume=decision), config)
        if result.get("__interrupt__"):
            print(json.dumps(result["__interrupt__"][0].value, indent=2))
            return 1
        print(result["output_docx_path"])
        return 0


if __name__ == "__main__":
    sys.exit(main())
