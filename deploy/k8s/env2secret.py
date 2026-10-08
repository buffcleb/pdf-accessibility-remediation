"""Print a Kubernetes Secret (JSON) from an env file, read the way Docker Compose reads it: KEY=VALUE lines, comments
and blank lines ignored, surrounding quotes removed. (kubectl's --from-env-file keeps the quotes, which breaks
values such as PEM keys.)

    python3 env2secret.py NAME NAMESPACE FILE | kubectl apply -f -
"""
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


def main() -> None:
    name, namespace, path = sys.argv[1:4]
    data = parse(path)
    json.dump({"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
               "metadata": {"name": name, "namespace": namespace,
                            "labels": {"app.kubernetes.io/part-of": "pdf-remediation"}},
               "stringData": data}, sys.stdout)


if __name__ == "__main__":
    main()
