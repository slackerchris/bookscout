# BookScout: Intelligent Audiobook Metadata Management System
## Technical Paper

**Version:** 0.29.4  
**Last Updated:** November 22, 2025  
**Status:** Beta (Personal Use)  

---

## Abstract

BookScout is an intelligent metadata management system designed to enhance audiobook collections by automatically discovering, cataloging, and organizing audiobook metadata from multiple authoritative sources. The system addresses the common problem of incomplete or missing metadata in personal audiobook libraries by providing automated author scanning, multi-source metadata aggregation, and intelligent book matching capabilities.

---

## 1. System Overview

### 1.1 Core Problem Statement

Personal audiobook collections often suffer from:
- Incomplete metadata (missing ISBNs, series information, publication dates)
- Inconsistent author naming conventions
- Lack of integration between audiobook players and metadata sources
- Manual metadata management overhead
- Co-author attribution issues in collaborative works

### 1.2 Solution Architecture

BookScout provides an automated bridge between audiobook library managers (currently Audiobookshelf) and multiple metadata APIs, employing intelligent matching algorithms to discover and catalog complete book information.

**Key Components:**
- Flask-based web application (Python 3.11)
- SQLite database for metadata persistence
- Multi-source API aggregation layer
- Web-based management interface
- Docker containerization for easy deployment

---

## 2. Current Features (v0.29.4)

### 2.1 Author Management

**Automated Author Discovery:**
- Integrates with Audiobookshelf API to detect authors in user's collection
- Automatic scanning of author catalogs from multiple sources
- Configurable scan frequency and active/inactive author management
- Last scanned timestamp tracking

**Author Normalization:**
- Intelligent name matching algorithm handles variations:
  - Initial differences (J.N. Chaney vs John Chaney)
  - Punctuation variations (J.N. vs JN)
  - Suffix handling (Jr., Sr., III)
  - Case-insensitive matching
  
**Co-Author Support:**
- JSON-based co-author storage
- Full author attribution for collaborative works
- Display format: "Primary Author *with* Co-Author 1, Co-Author 2"
- Audnexus API integration for complete author lists

**Duplicate Detection & Merging:**
- Similarity-based duplicate detection
- Shared book analysis (matching ASINs/ISBNs)
- Web UI for reviewing and approving merges
- Automatic book reassignment during merge operations

### 2.2 Multi-Source Metadata Aggregation

**Supported APIs:**
1. **OpenLibrary** (Primary ISBN source)
   - Maximum 200 results per author
   - ISBN-13 and ISBN-10 extraction
   - Publication year and series information
   
2. **Google Books** (Comprehensive metadata)
   - Pagination support (40 results per page, max 3 pages = 120 total)
   - ISBN, publisher, publication date
   - Page counts and descriptions
   - Series detection
   
3. **Audnexus** (Audiobook-specific)
   - ASIN (Amazon) identifier extraction
   - Audiobook-specific metadata
   - Complete author arrays for co-authored works
   - Direct book details API for full metadata
   - Maximum 100 results per author
   
4. **ISBNdb** (Premium, optional)
   - Requires API key
   - ISBN lookup and validation
   - Additional metadata enrichment

### 2.3 Book Management

**Metadata Tracking:**
- Title, ISBN (10 & 13), ASIN
- Author and co-author attribution
- Series name and position
- Publication year
- Source API tracking
- Scan timestamps

**Match Detection:**
- Automatic matching against Audiobookshelf library
- ISBN and ASIN-based matching
- Manual ownership toggle override
- Support for books with missing identifiers

**Deduplication:**
- Cross-API duplicate detection
- Preference hierarchy: Audnexus > ISBNdb > Google > OpenLibrary
- Manual duplicate management interface

### 2.4 User Interface

**Dashboard:**
- Author overview with book counts
- Filter by active/inactive status
- Search functionality
- Scan status indicators

**Author Details:**
- Complete book catalog display
- Owned vs. available book distinction
- Co-author attribution display
- Book card layout with metadata
- Quick scan and management actions

**Duplicate Management:**
- Dedicated duplicate authors page
- Side-by-side comparison
- Shared book counts
- Bulk merge operations

**Settings:**
- Audiobookshelf connection configuration
- API key management (ISBNdb)
- Language filtering preferences
- System configuration

### 2.5 Technical Features

**Database Schema:**
```sql
authors (
    id INTEGER PRIMARY KEY,
    name TEXT,
    openlibrary_id TEXT,
    audible_id TEXT,
    last_scanned TIMESTAMP,
    active INTEGER DEFAULT 1
)

books (
    id INTEGER PRIMARY KEY,
    author_id INTEGER,
    title TEXT,
    isbn TEXT,
    isbn13 TEXT,
    asin TEXT,
    co_authors TEXT (JSON),
    series_name TEXT,
    series_position TEXT,
    year INTEGER,
    source TEXT,
    scanned_at TIMESTAMP,
    owned INTEGER DEFAULT 0,
    deleted INTEGER DEFAULT 0
)
```

**API Rate Limiting:**
- Configurable delays between API calls
- Respectful API usage patterns
- Error handling and retry logic

**Docker Deployment:**
- Image: `ghcr.io/slackerchris/bookscout:latest`
- Volume persistence for database
- Port 5001 default
- Environment variable configuration

---

## 3. Future Roadmap

### 3.1 Version 0.30.0 - Folder-Based Scanning

**Objective:** Reduce dependency on Audiobookshelf by enabling direct filesystem scanning.

**Features:**
- Library folder path configuration
- Recursive audiobook file detection (M4B, MP3, etc.)
- Metadata extraction from:
  - Filename parsing
  - ID3/M4A tags
  - Audiobook duration
  - NFO files

**Confidence-Based Matching System:**

Inspired by Audiobookshelf's matching algorithm, implement confidence scoring:

```
Confidence Score = ASIN/ISBN Match (40 points)
                 + Title Similarity (30 points)
                 + Author Match (20 points)
                 + Duration Match (20 points if ±5 minutes)
                 + Series Match (10 points)
                 ─────────────────────────────────
                 Maximum: 130 points
```

**Match Thresholds:**
- 90+ points: Automatic match
- 70-89 points: Suggest for review
- Below 70: Manual review required

**Hybrid Mode:**
- Continue Audiobookshelf integration when available
- Fall back to folder scanning for unmatched books
- User-selectable primary mode

**Duration Matching:**
- Extract audiobook length from file metadata
- Compare against API-reported durations
- ±5 minute tolerance for high confidence
- Critical for distinguishing between editions (unabridged vs. abridged)

### 3.2 Version 0.40.0 - ID-Based Architecture Refactor

**Objective:** Eliminate duplicate author issues through proper relational architecture.

**Database Schema Changes:**

```sql
-- New author_ids table
author_ids (
    id INTEGER PRIMARY KEY,
    provider TEXT, -- 'openlibrary', 'audible', 'goodreads', etc.
    provider_id TEXT,
    name TEXT,
    UNIQUE(provider, provider_id)
)

-- Bridge table for author identity resolution
author_identity (
    id INTEGER PRIMARY KEY,
    canonical_name TEXT,
    primary_provider TEXT,
    primary_provider_id TEXT
)

-- Link multiple IDs to one identity
author_identity_mapping (
    identity_id INTEGER REFERENCES author_identity(id),
    provider_id INTEGER REFERENCES author_ids(id),
    confidence REAL DEFAULT 1.0
)

-- Many-to-many co-authorship
book_authors (
    book_id INTEGER REFERENCES books(id),
    author_identity_id INTEGER REFERENCES author_identity(id),
    role TEXT DEFAULT 'author', -- 'author', 'narrator', 'translator'
    position INTEGER DEFAULT 0
)
```

**Benefits:**
- Single canonical author identity across all providers
- No more duplicate author entries
- Proper co-author relationship modeling
- Support for narrators and translators
- Merge operations become identity links, not data moves

**Metadata Caching:**
- Cache API responses to reduce redundant calls
- Configurable cache TTL
- Selective cache invalidation
- Background refresh for stale data

**Background Processing:**
- Asynchronous scan jobs
- Queue-based task management
- Progress indicators and notifications
- Scheduled automatic scans

### 3.3 Version 0.50.0 - Polish & Optimization

**Performance Enhancements:**
- Asyncio for concurrent API calls
- Multiprocessing for file scanning
- Database query optimization
- Caching layer improvements

**User Experience:**
- Advanced search and filtering
- Bulk operations (scan multiple authors)
- Export functionality (CSV, JSON)
- Import from other systems

**API Expansion:**
- Goodreads integration (if available)
- LibraryThing support
- Custom metadata sources
- Web scraping fallbacks

### 3.4 Version 1.0.0 - Production Release

**Requirements for v1.0:**
- Comprehensive automated testing
- Complete user documentation
- Multi-user support (if needed)
- Security hardening
- Performance validation with large libraries (1000+ books)
- Backup and restore functionality
- Migration tools from other systems

**Stability Criteria:**
- 6+ months of beta testing
- No critical bugs
- Proven scalability
- Community feedback incorporated

---

## 4. Technical Considerations

### 4.1 Language & Framework Assessment

**Current: Python 3.11 + Flask**

*Advantages:*
- Rapid development and iteration
- Excellent library ecosystem for APIs
- Easy metadata parsing (JSON, XML)
- Simple deployment

*Limitations:*
- Single-threaded execution model
- Performance constraints with large datasets
- Memory overhead for long-running scans

**Future Considerations:**

For v0.40.0+ refactor, evaluate:

1. **Stay Python:**
   - Adequate for personal use (<2000 books)
   - Optimize with asyncio and multiprocessing
   - Keep familiar codebase
   - Best if no public release planned

2. **Migrate to Go:**
   - Superior concurrency (goroutines)
   - Better performance for large libraries
   - Lower memory footprint
   - Ideal for public/scaled deployment
   - More complex initial development

3. **TypeScript/Node.js:**
   - Modern async/await patterns
   - Good web framework options
   - Large developer community
   - Middle ground between Python and Go

**Recommendation:** Continue Python optimization through v0.35, reassess at v0.40 based on performance requirements and deployment goals.

### 4.2 Scalability Analysis

**Current Bottlenecks:**
- Sequential API calls (mitigated by delays)
- SQLite limitations for concurrent writes
- Synchronous scanning operations

**Optimization Strategies:**
1. Implement connection pooling
2. Add read replicas for queries
3. Use message queue for scan jobs
4. Cache frequent queries
5. Batch database operations

**Scale Targets:**
- Personal Use: 500-2000 books, 50-200 authors ✅ Current architecture adequate
- Power User: 2000-5000 books, 200-500 authors → Optimize Python (v0.36-0.39)
- Public/Shared: 5000+ books, 500+ authors → Consider Go rewrite (v0.40+)

### 4.3 API Strategy

**Rate Limiting & Ethics:**
- Respect API terms of service
- Implement exponential backoff
- Cache aggressively to minimize calls
- Provide attribution to sources

**Fallback Hierarchy:**
1. Local cache (instant)
2. ISBNdb (paid, reliable)
3. Audnexus (audiobook-specific)
4. Google Books (comprehensive)
5. OpenLibrary (fallback)

**Error Handling:**
- Graceful degradation when APIs unavailable
- Partial metadata acceptance
- Manual entry fallback
- Retry with exponential backoff

---

## 5. Use Cases

### 5.1 Personal Library Management
*User: Audiobook collector with 500-1000 books*
- Automatically catalog entire collection
- Discover books by owned authors
- Track series completion
- Identify gaps in author catalogs

### 5.2 Series Completionist
*User: Tracks specific series*
- View all books in series
- Identify missing entries
- Track publication order
- Monitor for new releases (future feature)

### 5.3 Metadata Researcher
*User: Needs comprehensive book metadata*
- Aggregate data from multiple sources
- Compare metadata across APIs
- Export for external use
- Validate ISBNs and identifiers

### 5.4 Collection Curator
*User: Manages multiple audiobook libraries*
- Folder-based scanning (v0.30+)
- Confidence matching for uncertain metadata
- Bulk operations on multiple authors
- Import/export for migration

---

## 6. System Requirements

### 6.1 Current Requirements

**Server:**
- Python 3.11+
- 512MB RAM minimum (1GB recommended)
- 100MB disk space + database growth
- Linux/macOS/Windows

**Dependencies:**
- Flask 3.0.0
- Requests 2.31.0
- SQLite 3.x

**External Services:**
- Audiobookshelf instance (optional with v0.30+)
- Internet connection for API access
- ISBNdb API key (optional)

### 6.2 Future Requirements (v0.40+)

**Enhanced:**
- 2GB RAM for background workers
- 500MB disk space for caching
- PostgreSQL option for multi-user
- Redis for job queue (optional)

---

## 7. Installation & Deployment

### 7.1 Docker Deployment (Recommended)

```bash
docker run -d \
  --name bookscout \
  -p 5001:5001 \
  -v /path/to/data:/data \
  -e AUDIOBOOKSHELF_URL="http://your-abs:13378" \
  -e AUDIOBOOKSHELF_TOKEN="your_token" \
  -e ISBNDB_KEY="your_key_optional" \
  ghcr.io/slackerchris/bookscout:latest
```

### 7.2 Manual Installation

```bash
git clone https://github.com/slackerchris/bookscout.git
cd bookscout
pip install -r requirements.txt
python app.py
```

### 7.3 Configuration

Environment variables:
- `AUDIOBOOKSHELF_URL`: Base URL of Audiobookshelf instance
- `AUDIOBOOKSHELF_TOKEN`: API token for authentication
- `ISBNDB_KEY`: ISBNdb API key (optional)
- `DATA_DIR`: Database location (default: `/data`)

---

## 8. Future Research Directions

### 8.1 Machine Learning Integration
- Author name disambiguation using ML
- Confidence scoring enhancement via neural networks
- Automatic genre classification
- Series detection from titles

### 8.2 Community Features
- Shared metadata corrections
- User-submitted reviews and ratings
- Reading/listening statistics
- Social features (friends, recommendations)

### 8.3 Advanced Matching
- Fuzzy title matching with edit distance
- Author name entity resolution
- Edition detection (hardcover vs. audiobook vs. ebook)
- Language and translation tracking

---

## 9. Conclusion

BookScout represents a focused solution to audiobook metadata management, bridging the gap between personal libraries and authoritative metadata sources. The current v0.29.4 release provides a solid foundation for personal use, with a clear roadmap toward more sophisticated features including folder-based scanning, confidence matching, and proper relational architecture.

The project's evolution from v0.30 through v1.0 will address scalability concerns and expand functionality while maintaining the core mission: making audiobook collection management effortless and comprehensive.

**Project Status:** Active Development (Beta)  
**Target v1.0 Release:** 2026  
**License:** [To be determined]  
**Repository:** https://github.com/slackerchris/bookscout  
**Docker Registry:** ghcr.io/slackerchris/bookscout

---

## Appendix A: API Documentation Summary

### OpenLibrary API
- Endpoint: `https://openlibrary.org/search.json`
- Rate Limit: Respectful usage, no hard limit
- Authentication: None required
- Data: ISBNs, titles, publication years, series

### Google Books API  
- Endpoint: `https://www.googleapis.com/books/v1/volumes`
- Rate Limit: 1000 requests/day (free tier)
- Authentication: API key (optional)
- Data: Comprehensive metadata, descriptions, page counts

### Audnexus API
- Endpoint: `https://api.audnex.us/books`
- Rate Limit: Unknown (use respectfully)
- Authentication: None required
- Data: Audiobook ASINs, narrators, duration, authors

### ISBNdb API
- Endpoint: `https://api2.isbndb.com/book`
- Rate Limit: Based on subscription tier
- Authentication: API key required
- Data: ISBN validation, publisher info, enriched metadata

---

## Appendix B: Database Statistics (Typical Installation)

**Personal Library Example:**
- Authors: 50-150
- Books cataloged: 2,000-5,000
- Owned books: 300-800
- Database size: 5-15 MB
- Scan time per author: 30-120 seconds
- Full library scan: 1-3 hours

---

## Appendix C: Glossary

**ASIN:** Amazon Standard Identification Number - unique identifier for products on Amazon  
**ISBN:** International Standard Book Number - unique identifier for books  
**Audiobookshelf:** Open-source audiobook and podcast server  
**Confidence Score:** Numerical value indicating match certainty between filesystem audiobook and metadata  
**Co-author:** Additional authors beyond the primary author of a work  
**Deduplication:** Process of identifying and merging duplicate entries  
**Metadata:** Descriptive information about books (title, author, ISBN, etc.)  
**OpenLibrary:** Free, open catalog of books maintained by Internet Archive  
**Scan:** Process of querying APIs to discover books by an author

---

*This technical paper is subject to updates as BookScout evolves. Last revision: November 22, 2025*
