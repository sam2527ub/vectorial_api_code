#!/bin/bash
# Build script for Vercel deployment
# Generates Prisma client before Python build

set -e

echo "🔨 Building Prisma client..."
python3 -m prisma generate

echo "✅ Build complete!"
