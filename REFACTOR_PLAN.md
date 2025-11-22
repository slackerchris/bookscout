# BookScout v0.30.0 Refactoring Plan
## Folder-Based Scanning & Confidence Matching

**Target Version:** 0.30.0  
**Start Date:** November 22, 2025  
**Status:** Planning  

---

## Overview

Transition from Audiobookshelf-dependent scanning to direct filesystem scanning with intelligent confidence-based metadata matching. This reduces external dependencies and enables BookScout to work standalone or as a complement to existing audiobook managers.

---

## Phase 1: Database Schema Changes

### 1.1 Add Library Configuration Table
```sql
CREATE TABLE library_paths (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    name TEXT,
    scan_enabled INTEGER DEFAULT 1,
    last_scanned TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 1.2 Extend Books Table
```sql
ALTER TABLE books ADD COLUMN file_path TEXT;
ALTER TABLE books ADD COLUMN file_size INTEGER;
ALTER TABLE books ADD COLUMN duration_seconds INTEGER;
ALTER TABLE books ADD COLUMN audio_format TEXT; -- M4B, MP3, etc.
ALTER TABLE books ADD COLUMN match_confidence INTEGER; -- 0-130 score
ALTER TABLE books ADD COLUMN match_method TEXT; -- 'audiobookshelf', 'filesystem', 'manual'
ALTER TABLE books ADD COLUMN match_reviewed INTEGER DEFAULT 0;
ALTER TABLE books ADD COLUMN file_last_modified TIMESTAMP;
```

### 1.3 Create Metadata Cache Table
```sql
CREATE TABLE metadata_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cache_key TEXT UNIQUE NOT NULL,
    provider TEXT NOT NULL, -- 'openlibrary', 'google', 'audnexus', 'isbndb'
    data TEXT NOT NULL, -- JSON blob
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    hit_count INTEGER DEFAULT 0
);

CREATE INDEX idx_cache_key ON metadata_cache(cache_key);
CREATE INDEX idx_expires ON metadata_cache(expires_at);
```

### 1.4 Migration Script
Create `migrations/v0.30.0_schema.py` to handle existing data:
- Preserve current Audiobookshelf matches
- Mark existing books as `match_method='audiobookshelf'`
- Set default confidence scores for existing matches (100 points)

---

## Phase 2: Filesystem Scanner Module

### 2.1 Create `scanner.py` Module

**Purpose:** Scan filesystem for audiobook files and extract metadata

**Key Functions:**

```python
def scan_library_path(library_path_id):
    """
    Recursively scan a library path for audiobook files
    Returns list of discovered audiobooks with metadata
    """
    
def detect_audiobook_files(directory):
    """
    Find M4B, MP3 folders, M4A, FLAC files
    Returns file paths with basic info
    """
    
def extract_file_metadata(file_path):
    """
    Extract metadata from audiobook file:
    - ID3 tags (MP3)
    - M4A metadata (M4B)
    - Duration
    - File size
    Returns dict with extracted metadata
    """
    
def parse_filename_metadata(file_path):
    """
    Parse author, title, series from filename
    Common patterns:
    - "Author Name - Title.m4b"
    - "Author Name - Series Name 01 - Title.m4b"
    - "Title - Author Name.m4b"
    Returns dict with parsed fields
    """
```

**Dependencies to Add:**
- `mutagen` - Audio metadata extraction
- `audioread` - Duration detection
- `pathlib` - Modern file path handling

### 2.2 Metadata Extraction Strategy

**Priority Order:**
1. ID3/M4A tags (most reliable)
2. NFO files in same directory
3. Filename parsing
4. Directory structure analysis

**Extractable Fields:**
- Title
- Author(s)
- Narrator
- Series name and position
- Publication year
- Publisher
- ASIN (from tags)
- ISBN (from tags)
- Duration
- Genre/tags

---

## Phase 3: Confidence Matching Engine

### 3.1 Create `matcher.py` Module

**Purpose:** Match discovered audiobooks against metadata APIs with confidence scoring

**Confidence Algorithm:**

```python
def calculate_match_confidence(file_metadata, api_metadata):
    """
    Calculate confidence score (0-130 points)
    
    Scoring breakdown:
    - ASIN exact match: +40 points
    - ISBN exact match: +40 points (mutually exclusive with ASIN)
    - Title similarity: 0-30 points (Levenshtein distance)
    - Author match: 0-20 points (normalized name comparison)
    - Duration match: +20 points if within ±5 minutes
    - Series match: +10 points (name + position)
    
    Returns confidence score and match details
    """
    
def fuzzy_title_match(title1, title2):
    """
    Compare titles accounting for:
    - Subtitle variations
    - "The" prefix handling
    - Special character differences
    - Abbreviations
    Returns similarity score 0-30
    """
    
def duration_match(file_seconds, api_minutes):
    """
    Compare audiobook durations
    Tolerance: ±5 minutes = full 20 points
    ±15 minutes = 10 points
    >15 minutes = 0 points
    """
```

**Thresholds:**
- **90+ points:** Auto-accept (high confidence)
- **70-89 points:** Suggest for review (medium confidence)
- **50-69 points:** Flag for manual review (low confidence)
- **<50 points:** Reject, require manual entry

### 3.2 Batch Matching Process

```python
def match_unmatched_files():
    """
    Process all discovered files without matches
    1. Group by author (from file metadata)
    2. Query APIs for each author's catalog
    3. Calculate confidence for each file/book pair
    4. Store top 3 matches per file for user review
    """
```

---

## Phase 4: UI Components

### 4.1 Settings Page Updates

**Add Library Paths Section:**
- Input for new library path
- List of configured paths with scan status
- Enable/disable toggle per path
- Manual scan trigger button
- Delete path option

### 4.2 New "Unmatched Files" Page

**Purpose:** Review and approve suggested matches

**Layout:**
```
┌─────────────────────────────────────────────────┐
│ Unmatched Audiobooks (23)                       │
├─────────────────────────────────────────────────┤
│                                                 │
│ File: /audiobooks/Fantasy/Author/Book.m4b      │
│ Duration: 12h 34m                               │
│ File metadata: Title, Author, Series           │
│                                                 │
│ Suggested Match (Confidence: 95) ✓             │
│ ┌─────────────────────────────────────────┐   │
│ │ Exact title match                       │   │
│ │ Author: Exact match                     │   │
│ │ Series: Book 3 of Series Name           │   │
│ │ Duration: 12h 32m (within 5 min) ✓     │   │
│ │ ISBN: 978-1234567890                    │   │
│ │ [Accept Match] [View Alternatives]      │   │
│ └─────────────────────────────────────────┘   │
│                                                 │
│ Alternative Matches (2)                         │
│ - Similar title (Confidence: 72) [Review]      │
│ - Same author (Confidence: 65) [Review]        │
│                                                 │
│ [Mark as Owned Manually] [Skip]                │
└─────────────────────────────────────────────────┘
```

**Features:**
- Filter by confidence level
- Bulk accept high-confidence matches
- Side-by-side comparison view
- Manual metadata override
- Skip/ignore functionality

### 4.3 Dashboard Enhancements

**Add Statistics:**
- Total files discovered
- Matched vs. unmatched count
- Average confidence score
- Last scan timestamp

**Add Quick Actions:**
- Scan all libraries button
- Review unmatched files badge
- Low-confidence matches alert

---

## Phase 5: Background Processing

### 5.1 Create Job Queue System

**Simple Queue Implementation (v0.30.0):**
- SQLite-based job queue (no external dependencies)
- Job types: 'scan_library', 'match_files', 'scan_author'
- Status: 'pending', 'running', 'completed', 'failed'

```sql
CREATE TABLE job_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL,
    parameters TEXT, -- JSON
    status TEXT DEFAULT 'pending',
    progress INTEGER DEFAULT 0,
    total INTEGER DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);
```

**Simple Worker:**
- Polling mechanism (check every 5 seconds)
- Single worker thread initially
- Progress updates via database
- Web UI shows job status

### 5.2 Progress Indicators

**Add to UI:**
- Active jobs list in header
- Progress bars for long operations
- Estimated time remaining
- Cancel job button

---

## Phase 6: Hybrid Mode Implementation

### 6.1 Integration Strategy

**Modes:**
1. **Audiobookshelf Primary** (default for existing users)
   - Continue current behavior
   - Use filesystem scan as supplement
   - Match filesystem files against ABS library

2. **Filesystem Primary** (new mode)
   - Scan folders first
   - Query APIs for metadata
   - Optional: Sync matches back to ABS

3. **Hybrid Mode**
   - Scan both ABS and filesystem
   - Deduplicate based on file paths
   - Use ABS matches when available
   - Fill gaps with confidence matching

### 6.2 Deduplication Logic

```python
def deduplicate_audiobookshelf_and_filesystem():
    """
    When book exists in both ABS and filesystem:
    - Prefer ABS match if confidence >= 90
    - Prefer filesystem if has better metadata
    - Link both records (don't duplicate)
    - Store both match sources
    """
```

---

## Phase 7: Testing Strategy

### 7.1 Unit Tests

Create `tests/` directory:
- `test_scanner.py` - File detection and metadata extraction
- `test_matcher.py` - Confidence scoring algorithms
- `test_migrations.py` - Database schema changes

### 7.2 Integration Tests

- End-to-end scan and match workflow
- API integration tests (with mocked responses)
- Database integrity tests

### 7.3 Manual Testing Checklist

- [ ] Scan library with M4B files
- [ ] Scan library with MP3 folders
- [ ] Test with files containing ID3 tags
- [ ] Test with files lacking metadata (filename parsing)
- [ ] Verify high-confidence auto-matching
- [ ] Review medium-confidence suggestions
- [ ] Test manual match override
- [ ] Verify Audiobookshelf compatibility maintained
- [ ] Test hybrid mode deduplication
- [ ] Performance test with 100+ files

---

## Phase 8: Documentation Updates

### 8.1 Update README.md
- Document new filesystem scanning feature
- Add configuration examples
- Update screenshots

### 8.2 Update QUICKSTART.md
- Add library path setup steps
- Explain confidence matching
- Document review process

### 8.3 Update DEPLOYMENT.md
- Add volume mount for library folders
- Document Docker volume permissions
- Add example docker-compose with library mounts

### 8.4 Create MATCHING_GUIDE.md
- Explain confidence algorithm
- Tips for improving match accuracy
- Troubleshooting common issues
- Best practices for file naming

---

## Implementation Timeline

### Week 1: Foundation
- [ ] Database schema changes and migrations
- [ ] Add `mutagen` and dependencies
- [ ] Create `scanner.py` skeleton
- [ ] Basic file detection

### Week 2: Scanning
- [ ] Metadata extraction from files
- [ ] Filename parsing logic
- [ ] Duration calculation
- [ ] Settings UI for library paths

### Week 3: Matching
- [ ] Create `matcher.py` module
- [ ] Implement confidence algorithm
- [ ] Title fuzzy matching
- [ ] Duration comparison logic

### Week 4: UI & Integration
- [ ] Unmatched files page
- [ ] Match review interface
- [ ] Bulk operations
- [ ] Job queue system

### Week 5: Testing & Polish
- [ ] Unit tests
- [ ] Integration tests
- [ ] Manual testing
- [ ] Bug fixes

### Week 6: Documentation & Release
- [ ] Update all documentation
- [ ] Create migration guide from v0.29.4
- [ ] Docker image build and push
- [ ] Release v0.30.0

---

## Success Criteria

**v0.30.0 is ready when:**
- [ ] Can scan filesystem and detect audiobook files
- [ ] Extracts metadata from files (tags + filename)
- [ ] Calculates confidence scores accurately
- [ ] UI allows review and approval of matches
- [ ] Maintains backward compatibility with ABS
- [ ] No performance regression on existing features
- [ ] Documentation complete
- [ ] Zero critical bugs

---

## Risk Assessment

### Technical Risks

1. **Audio metadata parsing complexity**
   - *Mitigation:* Use well-tested `mutagen` library
   - *Fallback:* Filename parsing if tags fail

2. **Performance with large libraries**
   - *Mitigation:* Implement background jobs early
   - *Fallback:* Batch processing, progress indicators

3. **Filesystem permission issues**
   - *Mitigation:* Clear documentation on Docker volumes
   - *Fallback:* Detailed error messages, permission checker

4. **API rate limiting with bulk matching**
   - *Mitigation:* Implement metadata caching
   - *Fallback:* Configurable delays, queue management

### User Experience Risks

1. **Complexity of confidence scores**
   - *Mitigation:* Simple thresholds, clear visual indicators
   - *Fallback:* "Trust this match?" yes/no interface

2. **False positive matches**
   - *Mitigation:* Conservative auto-accept threshold (90+)
   - *Fallback:* Easy undo functionality

3. **Migration from v0.29.4**
   - *Mitigation:* Automatic schema migration
   - *Fallback:* Backup database before upgrade prompt

---

## Dependencies to Add

```txt
# Audio metadata extraction
mutagen>=1.47.0

# Duration detection fallback
audioread>=3.0.0

# Fuzzy string matching
python-Levenshtein>=0.21.0

# Background job processing (optional for v0.30)
# apscheduler>=3.10.0  # Consider for v0.35+
```

---

## Database Backup Strategy

Before v0.30.0 upgrade:
1. Automatic backup to `/data/bookscout.db.v0.29.4.backup`
2. Backup timestamp in filename
3. Verify backup readable before proceeding
4. Keep last 3 version backups

---

## Rollback Plan

If v0.30.0 has critical issues:
1. Stop container
2. Restore backup database: `cp bookscout.db.v0.29.4.backup bookscout.db`
3. Revert to v0.29.4 image: `docker pull ghcr.io/slackerchris/bookscout:0.29.4`
4. Restart container
5. Report issue on GitHub

---

## Next Steps

1. **Review this plan** - Confirm approach aligns with vision
2. **Prioritize phases** - Determine what's must-have vs. nice-to-have
3. **Set timeline** - Realistic timeline based on available development time
4. **Create branch** - `git checkout -b feature/v0.30-filesystem-scanning`
5. **Begin Phase 1** - Start with database schema changes

---

*Last Updated: November 22, 2025*
