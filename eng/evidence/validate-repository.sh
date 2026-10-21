#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
repository="$(cd "$script_dir/../.." && pwd -P)"
cd "$repository"

required_paths=(
    LICENSE
    README.md
    run-ios-app.sh
    toolchain.lock.json
    docs/specification.md
    docs/status/compatibility-matrix.md
    docs/evidence/receipt-schema.md
    docs/evidence/receipt.schema.json
    docs/evidence/receipts/index.md
    docs/decisions/README.md
    docs/runbooks/installation-and-device-troubleshooting.md
    src/msbuild
    src/packs
    src/shims
    eng/runtime
    eng/toolchain
    tests/AotProbe/AotProbe.csproj
    tests/HelloIos/HelloIos.csproj
)
for path in "${required_paths[@]}"; do
    [[ -e "$path" ]] || { printf 'required repository path is missing: %s\n' "$path" >&2; exit 1; }
done

legacy_paths=(msbuild packs patches runtime scripts shims toolchain SPEC.md macios-references)
for path in "${legacy_paths[@]}"; do
    [[ ! -e "$path" ]] || { printf 'legacy repository path still exists: %s\n' "$path" >&2; exit 1; }
done

python3 -m json.tool toolchain.lock.json >/dev/null
python3 -m json.tool docs/evidence/receipt.schema.json >/dev/null
while IFS= read -r -d '' receipt; do
    python3 eng/evidence/validate-receipt.py "$receipt"
done < <(find docs/evidence/receipts -type f -name '*.json' -print0)
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/contracts -p 'test_*.py' -q

while IFS= read -r -d '' script; do
    bash -n "$script"
done < <(find . -path ./.git -prune -o -type f -name '*.sh' -print0)

python3 - <<'PY'
import hashlib
import json
from pathlib import Path
import re
import subprocess

forbidden_suffixes = (".p12", ".pfx", ".p8", ".mobileprovision", ".provisionprofile", ".pem", ".key", ".binlog")
forbidden_parts = {"_CodeSignature"}
paths = subprocess.run(
    ["git", "ls-files", "-co", "--exclude-standard"],
    check=True,
    capture_output=True,
    text=True,
).stdout.splitlines()
for raw in paths:
    path = Path(raw)
    if raw.endswith(forbidden_suffixes) or forbidden_parts.intersection(path.parts) or ".app" in path.parts:
        raise SystemExit(f"private or generated source-control candidate: {raw}")

link_pattern = re.compile(r"\[[^]]*\]\(([^)]+)\)")
for document in Path(".").rglob("*.md"):
    if ".git" in document.parts:
        continue
    for target in link_pattern.findall(document.read_text(encoding="utf-8")):
        target = target.strip().split("#", 1)[0]
        if not target or "://" in target or target.startswith(("mailto:", "#")):
            continue
        resolved = (document.parent / target).resolve()
        if not resolved.exists():
            raise SystemExit(f"broken local documentation link: {document} -> {target}")

lock = json.loads(Path("toolchain.lock.json").read_text(encoding="utf-8"))
license_metadata = lock["projectLicense"]
license_path = Path(license_metadata["file"])
license_hash = hashlib.sha256(license_path.read_bytes()).hexdigest()
if license_hash != license_metadata["sha256"]:
    raise SystemExit("LICENSE hash does not match toolchain.lock.json")

runtime = lock["components"]["monoRuntime"]
omarchy = lock["components"]["omarchyAppleDev"]
locked_paths = [
    runtime["compilerConfiguration"]["patch"],
    runtime["linuxAppIntegration"]["gate"],
    *runtime["linuxAppIntegration"]["projectsUnchanged"],
    omarchy["components"]["xtool"]["completePackageResolvedLock"],
    omarchy["components"]["pymobiledevice3"]["requirementsLock"],
]
for locked_path in locked_paths:
    if not Path(locked_path).exists():
        raise SystemExit(f"locked repository path is missing: {locked_path}")
PY

git diff --check
printf 'repository_validation=passed\n'
