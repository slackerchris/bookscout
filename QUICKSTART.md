# BookScout - Quick Start Guide

## Installation (Choose One Method)

### Method 1: Docker Compose (Easiest - Recommended)

```bash
# Navigate to the bookscout directory
cd bookscout

# Run the setup script
./start.sh

# Or manually:
docker-compose up -d
```

Access at: **http://localhost:5000**

### Method 2: Manual Python Setup

```bash
cd bookscout
pip install -r requirements.txt
python app.py
```

Access at: **http://localhost:5000**

---

## First Time Setup (5 minutes)

### Step 1: Configure Settings

1. Open http://localhost:5000/settings
2. Enter your details:

**Audiobookshelf:**
- URL: `http://your-server:13378` (replace with your ABS URL)
- API Token: Get from ABS → Settings → Users → Your User → API Token

**Prowlarr:**
- URL: `http://your-server:9696` (replace with your Prowlarr URL)
- API Key: Get from Prowlarr → Settings → General → API Key

3. Click "Save Settings"

### Step 2: Add Your First Author

1. Go to home page (http://localhost:5000)
2. Type an author name (e.g., "Andrew Rowe")
3. Click "Add & Scan"
4. Wait 10-30 seconds for results

### Step 3: View Results

You'll see:
- ✅ Books you already have (green badge)
- 📚 Books you don't have (with "Search via Prowlarr" button)
- Multiple sources aggregated (OpenLibrary, GoogleBooks, Audnexus)

---

## Daily Use

### Finding Missing Books

1. Click on any author from your watchlist
2. Click "Search via Prowlarr" on missing books
3. Prowlarr opens with search results
4. Download from your preferred indexer

### Adding More Authors

1. Home page → Enter author name → Add & Scan
2. Repeat for all authors you want to track

### Checking for New Releases

- **Single author**: Click "Scan" button on author card
- **All authors**: Click "Scan All" in navigation bar

---

## Docker Commands

```bash
# View logs
docker-compose logs -f bookscout

# Stop BookScout
docker-compose down

# Restart
docker-compose restart

# Update (after pulling new code)
docker-compose down
docker-compose build
docker-compose up -d
```

---

## File Structure

```
bookscout/
├── app.py                  # Main Flask application
├── templates/              # HTML templates
├── requirements.txt        # Python dependencies
├── Dockerfile             # Docker image definition
├── docker-compose.yml     # Docker Compose configuration
├── bookscout.db          # SQLite database (auto-created)
├── start.sh              # Quick start script
└── README.md             # Full documentation
```

---

## Troubleshooting

**"Books not marked as Have"**
→ Check Settings: Verify Audiobookshelf URL and token are correct

**"Prowlarr search fails"**
→ Check Settings: Verify Prowlarr URL and API key are correct

**"Author shows too few books"**
→ Try re-scanning (click "Scan" again)
→ Some sources update slowly

**"Port 5000 already in use"**
→ Edit docker-compose.yml, change `"5000:5000"` to `"5001:5000"` (or any free port)

---

## What's Next?

- Add all your favorite authors
- Set up a cron job or scheduled task to hit `/scan-all` weekly
- Integrate with your existing homelab (Nginx Proxy Manager for SSL, etc.)
- Check for new books regularly

---

## Support

This is a custom-built tool. No official support, but:
- Check README.md for detailed documentation
- Common issues usually involve API credentials
- Test manually accessing your ABS/Prowlarr APIs first

Enjoy! 📚
