#!/bin/bash

# Build and serve amazee.ai documentation

set -e

echo "🚀 Building amazee.ai documentation..."

# Check if mkdocs is installed
if ! command -v mkdocs &> /dev/null; then
    echo "❌ mkdocs is not installed. Installing dependencies..."
    pip install -r docs-requirements.txt
fi

# Build the documentation
echo "📚 Building documentation..."
mkdocs build

echo "✅ Documentation built successfully!"
echo "📖 You can view the documentation at: http://127.0.0.1:8000"
echo ""
echo "To serve the documentation locally, run:"
echo "  mkdocs serve"
echo ""
echo "To deploy to GitHub Pages, run:"
echo "  mkdocs gh-deploy"