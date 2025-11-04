# BookScout Architecture

## System Flow

```
┌─────────────────────────────────────────────────────────────┐
│                         BookScout                            │
│                      (Flask Web App)                         │
└─────────────────────────────────────────────────────────────┘
                              │
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
┌──────────────┐      ┌──────────────┐     ┌──────────────┐
│ Open Library │      │ Google Books │     │   Audnexus   │
│     API      │      │     API      │     │     API      │
└──────────────┘      └──────────────┘     └──────────────┘
        │                     │                     │
        └─────────────────────┼─────────────────────┘
                              │
                    ┌─────────▼──────────┐
                    │   Merge & Dedupe   │
                    │   Book Results     │
                    └─────────┬──────────┘
                              │
            ┌─────────────────┼─────────────────┐
            │                 │                 │
            ▼                 ▼                 ▼
    ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
    │   Store in   │  │  Check ABS   │  │   Display    │
    │   SQLite     │  │  for "Have"  │  │   in Web UI  │
    └──────────────┘  └──────────────┘  └──────────────┘
                              │
                              │ (User clicks "Search")
                              │
                              ▼
                      ┌──────────────┐
                      │   Prowlarr   │
                      │   Search     │
                      └──────────────┘
```

## Data Flow Example: Adding Andrew Rowe

```
User enters "Andrew Rowe"
         │
         ▼
┌─────────────────────────────────────────┐
│ BookScout queries 3 APIs simultaneously │
└─────────────────────────────────────────┘
         │
         ├──► Open Library: Found 12 books
         ├──► Google Books: Found 14 books  
         └──► Audnexus: Found 8 audiobooks
         │
         ▼
┌─────────────────────────────────────────┐
│ Merge by ISBN/ASIN/Title                │
│ Result: 18 unique books                 │
└─────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│ Check Audiobookshelf                    │
│ User has: 5 books                       │
│ Missing: 13 books                       │
└─────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│ Display in Web UI                       │
│ ✅ 5 books (green badge)                │
│ 📚 13 missing (search button)           │
└─────────────────────────────────────────┘
```

## Integration Points

### Audiobookshelf Integration
- **Purpose**: Check which books user already has
- **Method**: REST API calls to ABS server
- **Required**: ABS URL + API Token
- **Frequency**: Every author scan

### Prowlarr Integration
- **Purpose**: Search for missing books
- **Method**: REST API to trigger Prowlarr search
- **Required**: Prowlarr URL + API Key
- **Trigger**: User clicks "Search via Prowlarr" button

## Database Schema

```sql
-- Authors being monitored
authors (
    id, name, openlibrary_id, audible_id, 
    goodreads_id, last_scanned, active
)

-- Books found from all sources
books (
    id, author_id, title, subtitle, isbn, isbn13, 
    asin, release_date, format, source, cover_url, 
    description, series, series_position, 
    found_date, have_it
)

-- Scan history for analytics
scan_history (
    id, author_id, scan_date, 
    books_found, new_books
)

-- User settings
settings (
    key, value
)
```

## Why This Works Better

### Traditional Approach (Readarr/LazyLibrarian)
```
Single source (GoodReads) → Limited data → 6 books found
```

### BookScout Approach
```
3 sources → Merged results → 18 books found
         ↓
    90%+ more complete
```

## Technology Stack

```
Frontend:
├── Bootstrap 5 (UI framework)
├── Bootstrap Icons
└── Vanilla JavaScript (no frameworks)

Backend:
├── Python 3.11
├── Flask (web framework)
└── Requests (HTTP library)

Data:
├── SQLite (local database)
└── JSON (API responses)

Deployment:
├── Docker (containerization)
├── docker-compose (orchestration)
└── systemd (alternative)
```

## Network Requirements

**Outbound (BookScout → Internet):**
- openlibrary.org (port 443)
- googleapis.com (port 443)
- api.audnex.us (port 443)

**Inbound (You → BookScout):**
- Port 5000 (web interface)

**Local Network (BookScout → Your Services):**
- Audiobookshelf server (typically port 13378)
- Prowlarr server (typically port 9696)

## Security Considerations

- Runs on local network (not exposed to internet)
- API tokens stored in SQLite database (local file)
- No external data transmission except API queries
- No telemetry or phone-home
- HTTPS recommended via reverse proxy (Nginx Proxy Manager)

## Performance

**Scan Time (per author):**
- 3 API calls in parallel
- Typically 5-15 seconds
- Depends on: author popularity, API response time

**Database Size:**
- ~1KB per book entry
- 100 authors × 20 books each = ~2MB database

**Memory Usage:**
- Flask app: ~50-100MB
- SQLite: Minimal (file-based)
- Total container: ~200MB

## Extensibility

Want to add more sources? Easy:

```python
def query_new_source(author_name):
    # Your API call here
    return books_list

# Add to scan_author() function:
new_source_books = query_new_source(author_name)
all_books = merge_books([
    openlibrary_books, 
    google_books, 
    audnexus_books,
    new_source_books  # ← Add here
])
```
