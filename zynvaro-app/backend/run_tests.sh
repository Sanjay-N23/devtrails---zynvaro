#!/bin/bash
echo "=========================================="
echo "  Zynvaro Backend Test Suite"
echo "  DEVTrails 2026 - Phase 2 Validation"
echo "=========================================="
cd "$(dirname "$0")"
python -m pytest tests/ -v --tb=short
echo ""
echo "=========================================="
echo "  Tests complete."
echo "=========================================="
