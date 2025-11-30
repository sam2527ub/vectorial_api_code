#!/bin/bash
# Vercel build script
set -ex

echo "========================================="
echo "🔨 Starting Vercel Build Process"
echo "========================================="

echo "📦 Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "🔍 Python version:"
python --version

echo "🔍 Prisma version:"
python -m prisma version || echo "Prisma not installed yet"

echo "🔨 Generating Prisma client..."
python -m prisma generate

echo "✅ Verifying Prisma client was generated..."
python -c "from prisma import Prisma; print('✅ Prisma client import successful!')" || echo "❌ Prisma client import failed"

echo "========================================="
echo "✅ Build Complete!"
echo "========================================="
