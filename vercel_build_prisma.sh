#!/bin/bash
set -ex

echo "========================================="
echo "Installing Python dependencies..."
echo "========================================="
pip install -r requirements.txt

echo "========================================="
echo "Setting up PATH for Prisma generator..."
echo "========================================="
# Find where prisma-client-py was installed
PYTHON_SITE_PACKAGES=$(python3 -c "import site; print(site.getsitepackages()[0])")
PRISMA_BIN_PATH=$(find $PYTHON_SITE_PACKAGES -name "prisma-client-py" 2>/dev/null | head -1)

if [ -n "$PRISMA_BIN_PATH" ]; then
    PRISMA_BIN_DIR=$(dirname "$PRISMA_BIN_PATH")
    export PATH="$PRISMA_BIN_DIR:$PATH"
    echo "Added to PATH: $PRISMA_BIN_DIR"
fi

echo "Current PATH: $PATH"

echo "========================================="
echo "Generating Prisma client..."
echo "========================================="
python3 -m prisma generate

echo "========================================="
echo "Verifying Prisma client..."
echo "========================================="
python3 -c "from prisma import Prisma; print('✅ Prisma client successfully generated and importable!')"

echo "========================================="
echo "Build complete!"
echo "========================================="
