#!/bin/bash

echo "======================================"
echo "  BookScout - Setup & Start"
echo "======================================"
echo ""

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed. Please install Docker first."
    exit 1
fi

# Check if docker-compose is installed
if ! command -v docker-compose &> /dev/null; then
    echo "❌ docker-compose is not installed. Please install docker-compose first."
    exit 1
fi

echo "✅ Docker and docker-compose found"
echo ""

# Create .env file if it doesn't exist
if [ ! -f .env ]; then
    echo "📝 Creating .env file from template..."
    cp .env.example .env
    echo "⚠️  Please edit .env with your API credentials, or configure via web UI later"
    echo ""
fi

# Create data directory
mkdir -p data

echo "🏗️  Building Docker image..."
docker-compose build

echo ""
echo "🚀 Starting BookScout..."
docker-compose up -d

echo ""
echo "======================================"
echo "✅ BookScout is running!"
echo "======================================"
echo ""
echo "📍 Access the web interface at:"
echo "   http://localhost:5000"
echo ""
echo "⚙️  Configure integrations:"
echo "   1. Open http://localhost:5000/settings"
echo "   2. Enter your Audiobookshelf and Prowlarr details"
echo ""
echo "📚 Add authors:"
echo "   1. Go to home page"
echo "   2. Enter author name (e.g., 'Andrew Rowe')"
echo "   3. Click 'Add & Scan'"
echo ""
echo "Useful commands:"
echo "  - View logs: docker-compose logs -f"
echo "  - Stop: docker-compose down"
echo "  - Restart: docker-compose restart"
echo ""
