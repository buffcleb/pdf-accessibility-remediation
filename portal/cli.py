"""Operator commands.

    python -m portal.cli genkeys                     print a new JWT signing key and secret for .env
    python -m portal.cli migrate                     apply database migrations
    python -m portal.cli downgrade REVISION          take the database back to that migration (a rollback)
    python -m portal.cli sign-in-code EMAIL --operator WHO [--reason TEXT]
                                                     issue a sign-in code and print it (when mail isn't working)
    python -m portal.cli export-files [--prefix P]   write stored files as a tar stream to stdout
    python -m portal.cli import-files [--prefix P] [--overwrite] [--dry-run]
                                                     restore files from a tar stream on stdin
    python -m portal.cli upload-backup DIR s3://bucket/prefix      copy a backup folder off the machine
    python -m portal.cli download-backup s3://bucket/prefix/NAME DIR
    python -m portal.cli prune-backups DIR --daily N --weekly N [--dry-run]
                                                     keep the newest N backups and one a week for N weeks
    python -m portal.cli backup-done                 record that the nightly backup finished (for its alert)
    python -m portal.cli resummarize-improved [--dry-run]
                                                     Adobe documents finished before #499: their verdict and review
                                                     items about the improved copy, not Adobe's file (once)

The file and backup commands are used by scripts/backup.sh and scripts/restore.sh.
"""
from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path


def genkeys(_args=None) -> None:
    from portal.security.tokens import generate_private_key_pem
    pem = generate_private_key_pem().strip().replace("\n", "\\n")
    print(f'PORTAL_JWT_PRIVATE_KEYS="{pem}"')
    print(f"PORTAL_SECRET_KEY={secrets.token_urlsafe(48)}")


def migrate(_args=None) -> None:
    from alembic import command
    from alembic.config import Config
    cfg = Config(str(Path(__file__).with_name("alembic.ini")))
    cfg.set_main_option("script_location", str(Path(__file__).with_name("migrations")))
    command.upgrade(cfg, "head")


def downgrade(args) -> int:
    """Back to an earlier migration (docs/operations/rollback.md): each one undone drops what it added, and with it
    whatever was written there since. Only to an earlier revision: one that isn't, or that doesn't exist, is refused."""
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, text

    from portal.config import get_portal_settings
    cfg = Config(str(Path(__file__).with_name("alembic.ini")))
    cfg.set_main_option("script_location", str(Path(__file__).with_name("migrations")))
    revisions = [r.revision for r in ScriptDirectory.from_config(cfg).walk_revisions()]
    with create_engine(get_portal_settings().database_url).connect() as conn:
        current = conn.execute(text("select version_num from alembic_version")).scalar()
    if args.revision not in revisions:
        print(f"no migration {args.revision!r}", file=sys.stderr)
        return 2
    if current not in revisions or revisions.index(args.revision) <= revisions.index(current):
        print(f"the database is at {current}: {args.revision} isn't before it", file=sys.stderr)
        return 2
    command.downgrade(cfg, args.revision)
    print(f"database taken back from {current} to {args.revision}")
    return 0


def _sign_in_code(args) -> int:
    """Issue a sign-in code for someone and print it, for when email isn't arriving. The same checks as the sign-in
    page (the address must be allowed, the account not disabled); recorded in the audit log, never the code."""
    from portal import audit
    from portal.db import _sessionmaker
    from portal.identity import eligibility, normalize_email
    from portal.routers.auth import _blocked_message, issue_code
    from portal.config import get_portal_settings
    email = normalize_email(args.email)
    with _sessionmaker()() as db:
        from portal import rls
        rls.identify(db, email=email)  # only this address's own account, as on the sign-in page (#350)
        elig = eligibility(db, email)
        if not elig.allowed:
            print(f"{email} can't sign in here: no account, and neither the address nor its domain is on a group.",
                  file=sys.stderr)
            return 2
        if elig.user is not None and _blocked_message(db, elig.user):
            print(f"{email}'s account is disabled (or its group is).", file=sys.stderr)
            return 2
        code = issue_code(db, email)
        audit.record(db, None, "login.code_issued", actor=elig.user, actor_email=email,
                     group_id=elig.user.group_id if elig.user else (elig.group.id if elig.group else None),
                     details={"by": "operator", "operator": args.operator[:200],
                              **({"reason": args.reason[:500]} if args.reason else {})})
        db.commit()
    minutes = get_portal_settings().otp_minutes
    print(f"Sign-in code for {email}: {code}")
    print(f"It works once, for {minutes} minutes, and replaces any code sent before.")
    return 0


def _export(args) -> None:
    from portal.backup_tools import export_files
    n, size = export_files(sys.stdout.buffer, args.prefix)
    print(f"exported {n} files, {size / 1e6:.1f} MB", file=sys.stderr)


def _import(args) -> None:
    from portal.backup_tools import import_files
    stats = import_files(sys.stdin.buffer, overwrite=args.overwrite, dry_run=args.dry_run, prefix=args.prefix)
    verb = "would restore" if args.dry_run else "restored"
    print(f"{verb} {stats['restored']} files ({stats['bytes'] / 1e6:.1f} MB); "
          f"{stats['skipped_existing']} already present, {stats['skipped_other']} skipped", file=sys.stderr)


def _upload(args) -> None:
    from portal.backup_tools import upload_dir
    print(f"uploaded {upload_dir(Path(args.directory), args.url)} files", file=sys.stderr)


def _download(args) -> None:
    from portal.backup_tools import download_dir
    print(f"downloaded {download_dir(args.url, Path(args.directory))} files", file=sys.stderr)


def _prune(args) -> None:
    from portal.backup_tools import prune_backups
    gone = prune_backups(Path(args.directory), args.daily, args.weekly, dry_run=args.dry_run)
    for n in gone:
        print(f"{'would remove' if args.dry_run else 'removed'} {n}", file=sys.stderr)


def _backup_done(_args) -> None:
    from portal.backup_tools import record_backup
    record_backup()


def _resummarize(args) -> None:
    from portal.db import system_session
    from portal.worker import resummarize_improved
    with system_session("resummarize improved copies (#499)") as db:
        updated, skipped = resummarize_improved(db, dry_run=args.dry_run)
    verb = "would update" if args.dry_run else "updated"
    print(f"{verb} {updated} documents; {skipped} skipped (no JSON report to read)", file=sys.stderr)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m portal.cli", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("genkeys").set_defaults(fn=genkeys)
    sub.add_parser("migrate").set_defaults(fn=migrate)
    p = sub.add_parser("downgrade")
    p.add_argument("revision", help="the migration to go back to, e.g. 0043")
    p.set_defaults(fn=downgrade)
    p = sub.add_parser("sign-in-code")
    p.add_argument("email")
    p.add_argument("--operator", required=True, help="who is issuing it (recorded in the audit log)")
    p.add_argument("--reason", default="")
    p.set_defaults(fn=_sign_in_code)
    p = sub.add_parser("export-files")
    p.add_argument("--prefix", default="", help="only keys under this prefix, e.g. groups/<group id>/")
    p.set_defaults(fn=_export)
    p = sub.add_parser("import-files")
    p.add_argument("--prefix", default="")
    p.add_argument("--overwrite", action="store_true", help="replace objects that already exist")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=_import)
    p = sub.add_parser("upload-backup")
    p.add_argument("directory")
    p.add_argument("url")
    p.set_defaults(fn=_upload)
    p = sub.add_parser("download-backup")
    p.add_argument("url")
    p.add_argument("directory")
    p.set_defaults(fn=_download)
    p = sub.add_parser("prune-backups")
    p.add_argument("directory")
    p.add_argument("--daily", type=int, required=True)
    p.add_argument("--weekly", type=int, required=True)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=_prune)
    sub.add_parser("backup-done").set_defaults(fn=_backup_done)
    p = sub.add_parser("resummarize-improved")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=_resummarize)
    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        ap.print_help()
        return 2
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
