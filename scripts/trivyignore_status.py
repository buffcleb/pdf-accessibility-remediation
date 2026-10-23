"""What .trivyignore is hiding, for scripts/build.sh to print after each build: the ignored findings, grouped by the
reason they were ignored (the comment above them) and when they stop applying (`exp:`).

An ignore with an end date usually waits for a fix to be two weeks old, the rule for pinning a new release; when it
ends, Trivy reports the findings again and the build holds the image. So it says, for each group:
- how many days are left;
- when the wait is nearly over (DUE_DAYS): bump the package then;
- when it's over (EXPIRED): bump the package now;
- an ignore with no end date: revisit it from time to time.

    python3 scripts/trivyignore_status.py [path] [--today YYYY-MM-DD]
"""
from __future__ import annotations

import sys
from datetime import date

DUE_DAYS = 3


def groups(text: str) -> list[dict]:
    """The ignored IDs, grouped by their reason (the first line of the comment above them) and end date."""
    out: dict[tuple, dict] = {}
    head, fresh = "", True  # the reason's first line; whether the next comment starts a new reason
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            fresh = True
            continue
        if line.startswith("#"):
            if fresh:
                head = line.lstrip("# ").strip()
                fresh = False
            continue
        fresh = True
        parts = line.split()
        exp = next((p[4:] for p in parts[1:] if p.startswith("exp:")), None)
        g = out.setdefault((head, exp), {"reason": head, "exp": exp, "ids": []})
        g["ids"].append(parts[0])
    return list(out.values())


def _short(reason: str, width: int = 70) -> str:
    """The reason's start, cut at a word."""
    if len(reason) <= width:
        return reason
    return reason[:width].rsplit(" ", 1)[0].rstrip(",;:(") + "…"


def status(text: str, today: date) -> list[str]:
    """The lines to print, most urgent first; none when nothing is ignored."""
    found = groups(text)
    if not found:
        return []

    def days(g):
        return (date.fromisoformat(g["exp"]) - today).days if g["exp"] else None

    found.sort(key=lambda g: (days(g) is None, days(g) if days(g) is not None else 0))
    lines = [f"ignored findings (.trivyignore): {sum(len(g['ids']) for g in found)}"]
    for g in found:
        n, left = len(g["ids"]), days(g)
        ids = ", ".join(g["ids"][:3]) + (f" and {n - 3} more" if n > 3 else "")
        reason = f"{ids} ({_short(g['reason'])})" if g["reason"] else ids
        if left is None:
            lines.append(f"  no end date, revisit: {reason}")
        elif left < 0:
            lines.append(f"  EXPIRED {g['exp']}: Trivy reports them again, bump the fix now: {reason}")
        elif left <= DUE_DAYS:
            lines.append(f"  DUE {g['exp']}, in {left} day{'s' if left != 1 else ''}: the two-week wait is nearly over, "
                         f"bump the fix then: {reason}")
        else:
            lines.append(f"  until {g['exp']} ({left} days): {reason}")
    return lines


def main(argv: list[str]) -> int:
    path, today = ".trivyignore", date.today()
    args = list(argv[1:])
    if "--today" in args:
        i = args.index("--today")
        today = date.fromisoformat(args[i + 1])
        del args[i:i + 2]
    if args:
        path = args[0]
    try:
        text = open(path, encoding="utf-8").read()
    except FileNotFoundError:
        return 0
    print("\n".join(status(text, today)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
