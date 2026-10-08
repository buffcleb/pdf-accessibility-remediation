"""Operator commands.

    python -m portal.cli genkeys                     print a new JWT signing key and secret for .env
    python -m portal.cli migrate                     apply database migrations
    python -m portal.cli sign-in-code EMAIL --operator WHO [--reason TEXT]
                                                     issue a sign-in code and print it (when mail isn't working)
    python -m portal.cli export-files [--prefix P]   write stored files as a tar stream to stdout
    python -m portal.cli import-files [--prefix P] [--overwrite] [--dry-run]
                                                     restore files from a tar stream on stdin
    python -m portal.cli upload-backup DIR s3://bucket/prefix      copy a backup folder off the machine
    python -m portal.cli download-backup s3://bucket/prefix/NAME DIR

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
        elig = eligibility(db, email)
        if not elig.allowed:
            print(f"{email} can't sign in here: no account, and its domain isn't in a group.", file=sys.stderr)
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


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m portal.cli", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("genkeys").set_defaults(fn=genkeys)
    sub.add_parser("migrate").set_defaults(fn=migrate)
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
    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        ap.print_help()
        return 2
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
