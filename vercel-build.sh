#!/bin/bash
# Vercel build script - runs before Python code is packaged
set -e

echo "🔨 Installing dependencies..."
pip install -r requirements.txt

echo "🔨 Generating Prisma client for Vercel..."
python -m prisma generate

echo "✅ Build complete!"

