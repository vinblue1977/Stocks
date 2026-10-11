#!/usr/bin/env bash
# Builds the Lambda layer (anthropic SDK + pydantic) for Python 3.13 on arm64.
# Uses pre-built manylinux wheels, so no Docker is needed.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/build/layer/python"
PYTHON="${PYTHON:-python3}"

rm -rf "$ROOT/build/layer"
mkdir -p "$OUT"
"$PYTHON" -m pip install \
  --quiet \
  --requirement "$ROOT/layer/requirements.txt" \
  --target "$OUT" \
  --platform manylinux2014_aarch64 \
  --implementation cp \
  --python-version 3.13 \
  --only-binary=:all: \
  --upgrade

# boto3/botocore are already in the Lambda runtime; drop copies pulled in transitively.
rm -rf "$OUT"/boto3* "$OUT"/botocore* "$OUT"/s3transfer* "$OUT"/bin
find "$OUT" -name "__pycache__" -type d -prune -exec rm -rf {} +
echo "Layer built at $ROOT/build/layer ($(du -sh "$ROOT/build/layer" | cut -f1))"
