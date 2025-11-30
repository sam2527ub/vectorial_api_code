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
echo "Creating Prisma generator wrapper..."
echo "========================================="

# Get Prisma package location
PRISMA_PKG_PATH=$(python3 -c "import prisma; import os; print(os.path.dirname(prisma.__file__))")
echo "Prisma package path: $PRISMA_PKG_PATH"

# Create a wrapper script for the generator
cat > /tmp/prisma-client-py << 'WRAPPER_EOF'
#!/usr/bin/env python3
import sys
from prisma.generator.generator import Generator
if __name__ == '__main__':
    Generator().run()
WRAPPER_EOF

chmod +x /tmp/prisma-client-py

# Add /tmp to PATH so Prisma CLI can find the generator
export PATH="/tmp:$PATH"
echo "Created generator wrapper at /tmp/prisma-client-py"
echo "Updated PATH: $PATH"

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
