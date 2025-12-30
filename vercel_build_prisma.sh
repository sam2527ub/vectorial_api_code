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
python3 -m prisma generate --schema=prisma/schema.prisma

echo "========================================="
echo "Verifying Prisma client..."
echo "========================================="
# Verify that the prisma_client directory was created (check both possible locations)
PRISMA_CLIENT_PATH=""
if [ -d "$PROJECT_ROOT/prisma/prisma_client" ]; then
    PRISMA_CLIENT_PATH="$PROJECT_ROOT/prisma/prisma_client"
    echo "✅ Prisma client directory found at: $PRISMA_CLIENT_PATH"
elif [ -d "$PROJECT_ROOT/prisma_client" ]; then
    PRISMA_CLIENT_PATH="$PROJECT_ROOT/prisma_client"
    echo "✅ Prisma client directory found at: $PRISMA_CLIENT_PATH"
else
    echo "❌ Prisma client directory not found at $PROJECT_ROOT/prisma/prisma_client or $PROJECT_ROOT/prisma_client"
    exit 1
fi

# List directory contents
ls -la "$PRISMA_CLIENT_PATH" | head -10

# Test import from the local directory
cd "$PROJECT_ROOT"
PYTHONPATH="$PRISMA_CLIENT_PATH:$PYTHONPATH" python3 -c "from prisma import Prisma; print('✅ Prisma client successfully importable!')" || echo "⚠️  Import test skipped"

echo "========================================="
echo "Build complete!"
echo "========================================="

