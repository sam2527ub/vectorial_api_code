#!/bin/bash
# Vercel build script - runs before Python code is packaged
set -e

echo "🔨 Building Prisma client for Vercel..."
python3 -m prisma generate || python -m prisma generate

echo "✅ Prisma client generated!"
