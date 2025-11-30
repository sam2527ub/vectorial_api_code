#!/bin/bash
set -ex

# Store original directory
PROJECT_ROOT=$(pwd)
echo "Project root: $PROJECT_ROOT"

echo "========================================="
echo "Installing Python dependencies..."
echo "========================================="
pip install -r requirements.txt

echo "========================================="
echo "Setting up Prisma Generator Environment..."
echo "========================================="

# Get Prisma package location
PRISMA_PKG_PATH=$(python3 -c "import prisma; import os; print(os.path.dirname(prisma.__file__))")
echo "Prisma package path: $PRISMA_PKG_PATH"

# Change to generator directory and set it up
cd "$PRISMA_PKG_PATH/generator"
echo "Current directory: $(pwd)"
echo "Contents of generator directory:"
ls -la

# Check if package.json exists
if [ -f "package.json" ]; then
    echo "Found package.json, installing generator dependencies..."
    # Use npm (Node.js is available on Vercel)
    npm install --production
    echo "Generator dependencies installed"
    
    # Add node_modules/.bin to PATH
    export PATH="$(pwd)/node_modules/.bin:$PATH"
    echo "Added generator binaries to PATH"
fi

# Go back to project root
cd "$PROJECT_ROOT"
echo "Back to project root: $(pwd)"

echo "========================================="
echo "Generating Prisma client..."
echo "========================================="

# Generate Prisma client
python3 -m prisma generate --schema=schema.prisma

echo "========================================="
echo "Verifying Prisma client..."
echo "========================================="
python3 -c "from prisma import Prisma; print('✅ Prisma client successfully generated and importable!')"

echo "========================================="
echo "Build complete!"
echo "========================================="
