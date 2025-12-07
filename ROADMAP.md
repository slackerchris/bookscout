# BookScout Roadmap

## Current Status: v0.29.4 (Beta)
Stable and feature-complete for personal use with Audiobookshelf integration.

---

## Future Major Features

### 1. Folder-Based Library Scanning (v0.30.0)
**Effort: 6/10 | Timeline: 2-3 weeks**

Replace/supplement Audiobookshelf dependency with direct filesystem scanning.

#### Features:
- **Folder Scanning**: Point to `/audiobooks/` directory
- **Metadata Extraction**:
  - Audio file metadata (ID3 tags, duration, bitrate)
  - Filename parsing (title, series, position)
  - Existing ASIN/ISBN from tags
- **Confidence-Based Matching**:
  - 🟢 High (90-100%): Auto-match with override option
  - 🟡 Medium (60-89%): Suggested matches for approval
  - 🔴 Low (<60%): Manual search required
- **Hybrid Mode**: Support both ABS API and folder scanning

#### Confidence Scoring Algorithm:
```
ASIN/ISBN in file metadata:     +40 points
Exact title match:               +30 points
Author name match:               +20 points
Duration match (±5 min):         +20 points ⭐ KEY FEATURE
Series + position match:         +10 points
Release date (±1 year):          +5 points
Publisher match:                 +5 points
────────────────────────────────────────────
Total possible:                  130 points

Thresholds:
  🟢 90-130: High confidence (auto-match)
  🟡 60-89:  Medium confidence (suggest)
  🔴 0-59:   Low confidence (manual)
```

#### Why Duration Matching is Critical:
- **Highly distinctive**: Different books rarely have same length
- **Easy to extract**: Available in audio file metadata
- **Consistent**: Unabridged versions match well across sources
- **Reliable**: Eliminates false matches on generic titles

#### Implementation Notes:
- Use `mutagen` library for audio metadata extraction
- Audnexus API provides duration in `runtimeLengthMin`
- Fall back gracefully if duration unavailable
- Cache matches to avoid re-scanning on every run

---

### 2. ID-Based Author System (v0.40.0)
**Effort: 8/10 | Timeline: 1-2 months**

Replace name-based author matching with canonical IDs (like Readarr/Sonarr).

#### Features:
- **Canonical Author IDs**: Use OpenLibrary/GoodReads/Audible IDs
- **Name Variations**: Multiple spellings point to same author ID
  - "J.N. Chaney" = "JN Chaney" = "j n chaney"
- **Automatic Deduplication**: No more duplicate authors
- **API Caching**: Store metadata locally, refresh on schedule
- **Faster Operations**: Lookup by ID instead of live API searches

#### Database Changes:
```sql
-- Authors table uses external IDs as primary keys
CREATE TABLE authors (
    id INTEGER PRIMARY KEY,
    openlibrary_id TEXT UNIQUE,
    goodreads_id TEXT,
    audible_id TEXT,
    primary_name TEXT,  -- Display name
    name_variations TEXT,  -- JSON array of alternate names
    metadata_cache TEXT,  -- JSON blob of author info
    last_updated TIMESTAMP
);

-- Many-to-many for co-authors
CREATE TABLE book_authors (
    book_id INTEGER,
    author_id INTEGER,
    position INTEGER,  -- 1 = primary, 2+ = co-authors
    FOREIGN KEY (book_id) REFERENCES books(id),
    FOREIGN KEY (author_id) REFERENCES authors(id)
);
```

#### Migration Strategy:
- Run alongside v1.x (feature flag)
- Gradual migration of existing authors
- Export/import tool for clean database

---

### 3. Background Processing & Scheduling
**Effort: 5/10 | Timeline: 1 week**

Move heavy operations to background workers.

#### Features:
- **Async Scanning**: Don't block UI during author scans
- **Scheduled Updates**: Auto-rescan authors nightly/weekly
- **Rate Limiting**: Respect API limits properly
- **Progress Tracking**: Real-time scan progress
- **Queue Management**: Prioritize user-initiated scans

#### Implementation:
- Use Celery or Python threading
- Redis for queue (optional - can use SQLite)
- WebSocket for live progress updates

---

### 4. Advanced Metadata Features
**Effort: 4/10 | Timeline: 1 week**

Enhance metadata handling and display.

#### Features:
- **Multiple Editions**: Track hardcover/ebook/audiobook separately
- **Cover Art Gallery**: Multiple covers per book
- **Series Management**: Better series detection and ordering
- **Narrator Tracking**: Store audiobook narrators
- **Tags/Collections**: Custom categorization
- **Reading Status**: Reading, Finished, Abandoned, etc.

---

### 5. Enhanced Download Integration
**Effort: 3/10 | Timeline: 3-5 days**

Improve download client integration.

#### Features:
- **Direct Torrent/NZB Viewing**: Preview before download
- **Auto-download High Matches**: Skip manual search for 🟢 matches
- **Download History**: Track what's been searched/downloaded
- **Blacklist**: Mark books you don't want

---

## Nice-to-Have Features

### User Accounts & Multi-User
- Separate libraries per user
- Share collections
- User preferences

### Mobile App / Progressive Web App
- Offline support
- Push notifications for new releases
- Better mobile UI

### Advanced Search & Filters
- Complex queries (series, date ranges, formats)
- Saved searches
- Smart collections

### Reading Analytics
- Books per month
- Author statistics
- Genre analysis

### Export/Import
- Export to Goodreads/StoryGraph
- Import from other systems
- Backup/restore

---

## Architecture Considerations for v0.40+

### Current Limitations:
- ❌ Name-based author matching (causes duplicates)
- ❌ Live API searches (slow, rate limits)
- ❌ Co-authors as JSON text (not relational)
- ❌ No caching/metadata persistence
- ❌ Single-threaded (blocking operations)

### Target Architecture (v0.40-0.90):
- ✅ ID-based entities (authors, books)
- ✅ Metadata caching layer
- ✅ Background workers
- ✅ Proper many-to-many relationships
- ✅ API rate limiting & retry logic
- ✅ Event-driven updates
- ✅ Testing suite

### Production Ready (v1.0):
- ✅ All above features stable
- ✅ Comprehensive documentation
- ✅ Multi-user support (optional)
- ✅ Security hardening
- ✅ Performance optimization
- ✅ Community feedback incorporated

---

## Version Planning

- **v0.29.x**: Current beta, bug fixes only
- **v0.30.0**: Folder scanning + confidence matching
- **v0.40.0**: ID-based authors, caching, background workers (major refactor)
- **v0.50.0**: Polish, testing, performance optimization
- **v0.90.0**: Release candidate testing
- **v1.0.0**: Production release (stable, public-ready)

---

## Decision: When to Upgrade?

### Stay on v0.29.x if:
- Current features meet your needs
- Audiobookshelf integration works well
- You can work around duplicate authors manually

### Move to v0.30 (folder scanning) if:
- Want independence from Audiobookshelf
- Need better matching confidence
- Have large library with many edge cases
- Duration-based matching appeals to you

### Move to v0.40 (full refactor) if:
- Planning to share/publish eventually
- Need multi-user support
- Want significant performance improvements
- Ready to invest 1-2 months

### Move to v1.0 (production) when:
- All major features complete
- Thoroughly tested
- Documentation complete
- Ready for public/community use

---

**Last Updated**: November 22, 2025
**Current Version**: v0.29.4
