"""Print a Kubernetes Secret (JSON) from env files, read the way Docker Compose reads them: KEY=VALUE lines, comments
and blank lines ignored, surrounding quotes removed. (kubectl's --from-env-file keeps the quotes, which breaks
values such as PEM keys.)

    python3 env2secret.py NAME NAMESPACE FILE... [--keys REGEX] [--drop REGEX] | kubectl apply -f -

With more than one file a later file's value wins. --keys keeps only the keys the pattern matches (anchored at the
start, as re.match does); --drop leaves those out. Each pod gets a secret holding what it reads and nothing more,
so a foothold in one of them doesn't hand over the keys of the others (deploy.sh builds five this way).
"""
import argparse
import json
import re
import sys

LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def parse(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    with open(path, encoding="utf-8") as fh:
        for n, raw in enumerate(fh, 1):
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            m = LINE.match(raw.rstrip("\n"))
            if not m:
                sys.exit(f"{path}:{n}: not KEY=VALUE")
            key, value = m.groups()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            elif " #" in value:
                value = value.split(" #", 1)[0].rstrip()  # trailing comment on an unquoted value
            out[key] = value
    return out


def select(data: dict[str, str], keys: str | None, drop: str | None) -> dict[str, str]:
    """The entries whose key matches --keys (all, when not given) and does not match --drop."""
    keep = re.compile(keys) if keys else None
    out = re.compile(drop) if drop else None
    return {k: v for k, v in data.items()
            if (keep is None or keep.match(k)) and (out is None or not out.match(k))}


def secret(name: str, namespace: str, data: dict[str, str]) -> dict:
    return {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
            "metadata": {"name": name, "namespace": namespace,
                         "labels": {"app.kubernetes.io/part-of": "pdf-remediation"}},
            "stringData": data}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("name")
    ap.add_argument("namespace")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--keys", help="keep only the keys this pattern matches")
    ap.add_argument("--drop", help="leave out the keys this pattern matches")
    args = ap.parse_args(argv)
    data: dict[str, str] = {}
    for path in args.files:
        data.update(parse(path))
    json.dump(secret(args.name, args.namespace, select(data, args.keys, args.drop)), sys.stdout)


if __name__ == "__main__":
    main()
