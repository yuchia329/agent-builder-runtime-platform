"""Builds the gateway's Lambda package (ADR 0019): build/gateway-lambda.zip.

    cd tool-gateway && uv run python scripts/lambda_zip.py

Contents, at the zip's root: the locked runtime dependencies as Linux arm64 wheels for Python 3.12
(Lambda runtime python3.12, Amazon Linux 2023), the `gateway` package, `connectors/`, the
contract schemas in `contracts/`, and `run.sh` (the handler, run by the Lambda Web Adapter layer).
Only wheels are used: nothing is compiled on this machine.
"""

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent
BUILD = ROOT / "build"
PACKAGE = BUILD / "lambda"
ZIP = BUILD / "gateway-lambda.zip"
PLATFORM = "aarch64-manylinux_2_34"  # Amazon Linux 2023 on arm64
PYTHON = "3.12"
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")


def run(*args: str) -> None:
    subprocess.run(args, check=True, cwd=ROOT)


def main() -> int:
    shutil.rmtree(PACKAGE, ignore_errors=True)
    PACKAGE.mkdir(parents=True)
    requirements = BUILD / "requirements.txt"
    run(
        "uv",
        "export",
        "--frozen",
        "--no-dev",
        "--no-hashes",
        "--no-emit-project",
        "--output-file",
        str(requirements),
        "--quiet",
    )
    run(
        "uv",
        "pip",
        "install",
        "--quiet",
        "--requirements",
        str(requirements),
        "--target",
        str(PACKAGE),
        "--python-platform",
        PLATFORM,
        "--python-version",
        PYTHON,
        "--only-binary",
        ":all:",
    )
    shutil.copytree(ROOT / "src" / "gateway", PACKAGE / "gateway", ignore=SKIP)
    shutil.copytree(ROOT / "connectors", PACKAGE / "connectors", ignore=SKIP)
    (PACKAGE / "contracts").mkdir()
    for schema in (REPO / "contracts").glob("*.schema.json"):
        shutil.copy2(schema, PACKAGE / "contracts" / schema.name)
    shutil.copy2(ROOT / "run.sh", PACKAGE / "run.sh")

    ZIP.unlink(missing_ok=True)
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(p for p in PACKAGE.rglob("*") if p.is_file()):
            name = path.relative_to(PACKAGE).as_posix()
            if "__pycache__" in name:
                continue
            info = zipfile.ZipInfo.from_file(path, name)
            # run.sh must stay executable; everything else is readable.
            info.external_attr = (0o100755 if name == "run.sh" else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    unzipped = sum(p.stat().st_size for p in PACKAGE.rglob("*") if p.is_file())
    print(
        f"{ZIP.relative_to(ROOT)}: {ZIP.stat().st_size / 1e6:.1f} MB "
        f"({unzipped / 1e6:.1f} MB unzipped; Lambda allows 50 MB direct, 250 MB unzipped)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
