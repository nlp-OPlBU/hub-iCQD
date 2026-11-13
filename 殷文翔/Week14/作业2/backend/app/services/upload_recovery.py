"""Reconcile upload receipts only while upload writers are stopped.

Dry run: python -m app.services.upload_recovery
Apply:   python -m app.services.upload_recovery --apply --uploads-stopped
Always connects to the configured primary MySQL, never to a read replica.
"""
import argparse
import json
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import create_db_engine, session_factory
from app.repositories.document import DocumentRepository
from app.services.storage import FileStorage
from app.services.uploads import check_database


def reconcile_uploads(storage: FileStorage, factory, *, apply: bool = False) -> dict[str, int]:
    """Caller must quiesce all upload processes before applying compensation.

    No age heuristic: a slow active transaction must never lose its source file.
    DB errors and invalid receipts leave all affected files untouched.
    """
    check_database(factory)
    counts = {"referenced": 0, "orphaned": 0, "errors": 0}
    for path in sorted(storage.recovery_dir.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            document_id = str(UUID(str(record["document_id"])))
            UUID(str(record["task_id"]))
            expected_key = f"uploads/{document_id}.pdf"
            if path.name != f"{document_id}.json" or record["storage_key"] != expected_key:
                raise ValueError("Invalid receipt identity")
            with factory() as session, session.begin():
                key = DocumentRepository(session).storage_key(document_id)
            if key is not None:
                if key != expected_key or not storage.pdf_path(document_id).is_file():
                    raise ValueError("Referenced upload requires manual repair")
                counts["referenced"] += 1
                if apply and not storage.forget_receipt(document_id):
                    counts["errors"] += 1
            else:
                counts["orphaned"] += 1
                if apply and not storage.discard_uncommitted(document_id):
                    counts["errors"] += 1
        except (OSError, ValueError, KeyError, TypeError, SQLAlchemyError):
            counts["errors"] += 1
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--uploads-stopped", action="store_true",
                        help="Confirm all API/upload writers have been stopped before cleanup")
    args = parser.parse_args()
    if args.apply and not args.uploads_stopped:
        parser.error("--apply requires --uploads-stopped; first stop all upload writers")
    settings = get_settings()
    configure_logging(settings)
    engine = create_db_engine(settings)
    try:
        counts = reconcile_uploads(FileStorage(settings), session_factory(engine), apply=args.apply)
        print(json.dumps({"applied": args.apply, **counts}))
        return 1 if counts["errors"] else 0
    except Exception as exc:
        # Do not expose credentials, SQL or local paths in CLI output either.
        print(json.dumps({"error": "RECOVERY_UNAVAILABLE", "type": type(exc).__name__}))
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
