#!/bin/bash
set -ex

echo "========================================="
echo "Installing Python dependencies..."
echo "========================================="
pip install -r requirements.txt

echo "========================================="
echo "Locating Prisma generator..."
echo "========================================="
# Get Python site-packages location
PYTHON_SITE_PACKAGES=$(python3 -c "import site; print(site.getsitepackages()[0])")
echo "Python site-packages: $PYTHON_SITE_PACKAGES"

# Get Prisma package location
PRISMA_PKG_PATH=$(python3 -c "import prisma; import os; print(os.path.dirname(prisma.__file__))")
echo "Prisma package path: $PRISMA_PKG_PATH"

# Set generator path if it exists
if [ -d "$PRISMA_PKG_PATH/generator" ]; then
    export PRISMA_GENERATOR_PATH="$PRISMA_PKG_PATH/generator"
    echo "Found generator at: $PRISMA_GENERATOR_PATH"
fi

# List what's in the prisma directory
echo "Contents of Prisma package:"
ls -la "$PRISMA_PKG_PATH/" || echo "Could not list Prisma directory"

# Add potential binary locations to PATH
export PATH="$PYTHON_SITE_PACKAGES/prisma/binaries:$PYTHON_SITE_PACKAGES/bin:$HOME/.local/bin:$PATH"
echo "Updated PATH: $PATH"

echo "========================================="
echo "Generating Prisma client..."
echo "========================================="

# Try generation with explicit schema path
python3 -m prisma generate --schema=schema.prisma

echo "========================================="
echo "Verifying Prisma client..."
echo "========================================="
python3 -c "from prisma import Prisma; print('✅ Prisma client successfully generated and importable!')"

echo "========================================="
echo "Build complete!"
echo "========================================="
