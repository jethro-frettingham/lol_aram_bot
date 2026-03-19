#!/bin/bash
# Packages the Lambda function into a zip for Terraform to deploy.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT="$SCRIPT_DIR/lambda.zip"

cd "$SCRIPT_DIR/src"
zip -r "$OUT" . -x "*.pyc" -x "__pycache__/*"
echo "Built $OUT"
