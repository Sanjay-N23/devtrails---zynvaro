#!/bin/bash
echo ""
echo " ========================================="
echo "  ZYNVARO — Starting Backend API"
echo "  Guidewire DEVTrails 2026"
echo " ========================================="
echo ""

cd "$(dirname "$0")/backend"

# Install deps if needed
if [ ! -f ".deps_installed" ]; then
    echo "Installing dependencies..."
    pip install -r requirements.txt
    touch .deps_installed
fi

echo ""
echo " Backend:  http://localhost:8000"
echo " API Docs: http://localhost:8000/api/docs"
echo " App:      Open frontend/app.html in browser"
echo ""
echo " Demo Login: 9876543210 / demo1234"
echo ""

python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
