#!/bin/bash
# Deployment script for Vercel

echo "🚀 Deploying to Vercel..."
echo ""

# Check if Vercel CLI is installed
if ! command -v vercel &> /dev/null; then
    echo "❌ Vercel CLI not found. Installing..."
    npm install -g vercel
fi

# Check if logged in
if ! vercel whoami &> /dev/null; then
    echo "⚠️  Not logged in to Vercel. Please login:"
    vercel login
fi

echo "✅ Vercel CLI ready"
echo ""

# Check if .env exists and remind about environment variables
if [ -f .env ]; then
    echo "📝 Found .env file"
    echo "⚠️  Remember to set these in Vercel dashboard:"
    echo "   - DATABASE_URL"
    echo "   - PDL_API_KEY"
    echo "   - APIFY_API_TOKEN"
    echo "   - OPENAI_API_KEY"
    echo ""
fi

# Deploy
echo "🚀 Deploying..."
vercel --prod

echo ""
echo "✅ Deployment complete!"
echo ""
echo "📋 Next steps:"
echo "   1. Set environment variables in Vercel dashboard"
echo "   2. Run migrations: prisma migrate deploy"
echo "   3. Test: curl https://your-project.vercel.app/"
echo ""

