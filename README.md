# BookScout 📚

Multi-source book discovery and monitoring system that aggregates data from Open Library, Google Books, and Audnexus to find complete author bibliographies.

## Features

- **Multi-Source Discovery**: Queries Open Library, Google Books, and Audnexus (Audible) simultaneously
- **Smart Deduplication**: Automatically merges results from multiple sources
- **Audiobookshelf Integration**: Checks which books you already have in your library
- **Prowlarr Integration**: One-click search for missing books across all your indexers
- **Author Watchlist**: Track your favorite authors and scan for new releases
- **Web UI**: Clean, responsive interface built with Bootstrap

## Why BookScout?

Existing tools like Readarr and LazyLibrarian have incomplete metadata databases. BookScout solves this by:
1. Querying multiple sources simultaneously
2. Merging and deduplicating results
3. Providing significantly more complete author bibliographies

Example: Andrew Rowe has 15+ published books, but most tools only show 6-7. BookScout finds them all.

## Quick Start

### Using Docker Compose (Recommended)

```bash
# Clone or download the bookscout directory
cd bookscout

# Optional: Create .env file with your API credentials
cp .env.example .env
# Edit .env with your settings

# Build and start
docker-compose up -d

# Access at http://localhost:5000
```

### Manual Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Run the application
python app.py

# Access at http://localhost:5000
```

## Configuration

### Option 1: Environment Variables

Set these in your `.env` file or docker-compose.yml:

```env
AUDIOBOOKSHELF_URL=http://your-abs-server:13378
AUDIOBOOKSHELF_TOKEN=your_token_here
PROWLARR_URL=http://your-prowlarr:9696
PROWLARR_API_KEY=your_key_here
```

### Option 2: Web UI

Navigate to Settings in the web interface and configure:

**Audiobookshelf:**
- URL: Full URL to your Audiobookshelf instance
- API Token: Get from ABS Settings → Users → [Your User] → API Token

**Prowlarr:**
- URL: Full URL to your Prowlarr instance  
- API Key: Get from Prowlarr Settings → General → API Key

## Usage

### Adding Authors

1. Go to the home page
2. Enter author name (e.g., "Andrew Rowe")
3. Click "Add & Scan"
4. BookScout will query all sources and display results

### Viewing Books

- Click "View Books" on any author to see their complete bibliography
- Books you already have (in Audiobookshelf) are marked with a green badge
- Missing books show a "Search via Prowlarr" button

### Searching for Books

Click "Search via Prowlarr" on any missing book to:
1. Automatically search Prowlarr with the book title + author
2. Open Prowlarr search results in a new tab
3. Download from your preferred indexer

### Scanning for Updates

- **Single Author**: Click "Scan" on the author card or "Re-scan Now" on the author page
- **All Authors**: Click "Scan All" in the navigation bar

## Data Sources

BookScout queries these APIs:

1. **Open Library** - Comprehensive free database from Internet Archive
2. **Google Books** - Google's book database with good metadata
3. **Audnexus** - Community-maintained Audible metadata API

## How It Works

```
User adds author → BookScout queries 3 APIs → Results merged & deduplicated
                                                        ↓
                                          Checks Audiobookshelf for matches
                                                        ↓
                                          Displays complete bibliography
                                                        ↓
                              User clicks "Search" → Opens in Prowlarr
```

## Integration with Your Homelab

BookScout is designed to work alongside:
- **Audiobookshelf**: Library management and playback
- **Prowlarr**: Indexer aggregation and search
- **OpenAudible**: Audible library backup
- **Calibre**: Ebook management
- **Readarr/LazyLibrarian**: Can complement (not replace) these tools

## Database

BookScout uses SQLite and stores:
- Author watchlist
- Book metadata from all sources
- Scan history
- Settings

Database location: `./bookscout.db`

## Ports

- Web UI: `5000` (configurable in docker-compose.yml)

## Updating

```bash
# Pull latest changes
git pull  # or re-download files

# Rebuild container
docker-compose down
docker-compose build
docker-compose up -d
```

## Troubleshooting

### Books Not Showing as "Have"

- Verify Audiobookshelf URL and API token in Settings
- Check that ABS is accessible from the BookScout container
- Try re-scanning the author

### Prowlarr Search Not Working

- Verify Prowlarr URL and API key in Settings
- Ensure Prowlarr is accessible from BookScout
- Check Prowlarr logs for API errors

### Missing Books for Author

- Try re-scanning (data sources update over time)
- Some very new releases may not be in all databases yet
- Self-published or indie books may have limited coverage

### API Rate Limits

BookScout respects API rate limits, but if you scan many authors simultaneously:
- Open Library: No rate limit (public API)
- Google Books: 1000 requests/day (generous)
- Audnexus: Rate limited to 60 requests/minute

## Technical Details

- **Backend**: Python 3.11 + Flask
- **Database**: SQLite
- **Frontend**: Bootstrap 5 + Vanilla JS
- **Container**: Docker with slim Python base image

## Privacy

BookScout:
- Runs entirely on your infrastructure
- Makes API calls only to public book databases
- Stores no data externally
- Does not track or phone home

## Roadmap

Potential future features:
- Email/Discord notifications for new releases
- Scheduled automatic scanning
- Series tracking and organization
- GoodReads import/export
- Custom metadata sources
- More sophisticated matching algorithms

## Contributing

This was built as a custom solution for a specific need. Feel free to:
- Fork and modify for your use case
- Submit issues or suggestions
- Improve the API integrations

## License

Use it however you want. No warranty, use at your own risk.

## Credits

Built to solve the problem of incomplete book metadata in existing automation tools. Inspired by frustration with Readarr, LazyLibrarian, and the general state of book automation tooling.

---

**Note**: BookScout is for personal use to manage your legally acquired content. Respect copyright laws and support authors by purchasing their work.
