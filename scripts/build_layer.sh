#!/usr/bin/env bash
# Builds the Lambda layer from the lockfile (layer/requirements.txt) for Python 3.14 on arm64.
# Uses pre-built manylinux wheels, so no Docker is needed. The python3.14 runtime is Amazon Linux 2023
# (glibc 2.34), so manylinux_2_28 wheels work too (newer Pillow ships only those for arm64).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/build/layer/python"
PYTHON="${PYTHON:-python3}"

rm -rf "$ROOT/build/layer"
mkdir -p "$OUT"
# requirements.txt is the lockfile: exact versions, each checked against its recorded hash.
"$PYTHON" -m pip install \
  --quiet \
  --require-hashes \
  --no-deps \
  --requirement "$ROOT/layer/requirements.txt" \
  --target "$OUT" \
  --platform manylinux2014_aarch64 \
  --platform manylinux_2_28_aarch64 \
  --implementation cp \
  --python-version 3.14 \
  --only-binary=:all: \
  --upgrade

# boto3/botocore are already in the Lambda runtime; drop the copies pulled in transitively, and the
# packages only they need (jmespath, urllib3, python-dateutil, six), so the runtime's matching set is used.
rm -rf "$OUT"/boto3* "$OUT"/botocore* "$OUT"/s3transfer* "$OUT"/jmespath* "$OUT"/urllib3* \
  "$OUT"/dateutil "$OUT"/python_dateutil* "$OUT"/six.py "$OUT"/six-* "$OUT"/bin
find "$OUT" -name "__pycache__" -type d -prune -exec rm -rf {} +
echo "Layer built at $ROOT/build/layer ($(du -sh "$ROOT/build/layer" | cut -f1))"
