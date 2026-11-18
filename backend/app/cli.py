import argparse
import csv
import json
import logging
import sys
from pathlib import Path
from typing import Sequence

from alembic import command
from alembic.config import Config
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.session import SessionLocal, engine
from app.services.master_data_import import (
    ImportSourceError,
    import_contract_pdfs,
    import_master_data,
)
from app.services.batch_processing import (
    OUTPUT_COLUMNS,
    BatchInputError,
    process_batch_csv,
)
from app.utils.logging import configure_logging

logger = logging.getLogger(__name__)
BACKEND_DIR = Path(__file__).resolve().parents[1]
WORKSPACE_DIR = BACKEND_DIR.parent


def _default_source_directory() -> Path:
    candidates = [
        WORKSPACE_DIR / "task_kit",
        WORKSPACE_DIR / "Cantill_FullStack_Task_Kit",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def _upgrade_schema() -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(config, "head")


def _print_report(report: BaseModel) -> None:
    print(json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=True))


def _run_import(command_name: str, source_directory: Path) -> int:
    try:
        _upgrade_schema()
        with SessionLocal() as session:
            if command_name == "import-master-data":
                report = import_master_data(session, source_directory)
                has_rejections = report.has_rejections
            else:
                report = import_contract_pdfs(session, source_directory)
                has_rejections = report.rejected > 0
        _print_report(report)
        return 1 if has_rejections else 0
    except ImportSourceError as error:
        print(
            json.dumps(
                {"command": command_name, "error": str(error)},
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2
    except Exception:
        logger.exception("Import command failed", extra={"command": command_name})
        print(
            json.dumps(
                {
                    "command": command_name,
                    "error": "Import command failed; see structured logs for details.",
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2
    finally:
        engine.dispose()


def _run_batch(input_path: Path, output_path: Path) -> int:
    try:
        _upgrade_schema()
        content = input_path.read_bytes()
        with SessionLocal() as session:
            batch = process_batch_csv(session, content)
            session.commit()
            result_rows = batch.result_rows
            report = {
                "batch_id": batch.id,
                "input": str(input_path),
                "output": str(output_path),
                **batch.statistics,
            }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8-sig", newline="") as output_file:
            writer = csv.DictWriter(output_file, fieldnames=OUTPUT_COLUMNS)
            writer.writeheader()
            writer.writerows(result_rows)
        print(json.dumps(report, indent=2, ensure_ascii=True, default=str))
        return 1 if report["not_processed"] else 0
    except (OSError, BatchInputError) as error:
        print(json.dumps({"command": "process-batch", "error": str(error)}), file=sys.stderr)
        return 2
    except Exception:
        logger.exception("Batch processing command failed")
        print(
            json.dumps(
                {
                    "command": "process-batch",
                    "error": "Batch processing failed; see structured logs for details.",
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2
    finally:
        engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command_name in ("import-master-data", "import-contract-pdfs"):
        command_parser = subparsers.add_parser(command_name)
        command_parser.add_argument(
            "--source",
            type=Path,
            default=_default_source_directory(),
            help="Source kit directory (defaults to the workspace task kit).",
        )
    batch_parser = subparsers.add_parser("process-batch")
    batch_parser.add_argument("--input", type=Path, required=True, help="Input PR CSV path.")
    batch_parser.add_argument("--output", type=Path, required=True, help="Decision CSV output path.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging("INFO")
    arguments = build_parser().parse_args(argv)
    if arguments.command == "process-batch":
        return _run_batch(arguments.input, arguments.output)
    return _run_import(arguments.command, arguments.source)


if __name__ == "__main__":
    raise SystemExit(main())
