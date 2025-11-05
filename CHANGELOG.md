# BookScout Changelog

## [0.29.4] - 2025-11-05

### Changed
- **Version Numbering**: Reset to 0.x.x to indicate beta/personal-use status
  - Major refactor would be needed for public release (ID-based authors, caching, etc.)
  - Current version is stable and feature-complete for personal use

### Added
- **Duplicate Author Finder**: Find and merge duplicate authors
  - Detects authors with similar names using normalization logic
  - Identifies authors sharing books (same ASINs/ISBNs)
  - UI to review and approve merges
  - Moves all books to primary author, deactivates duplicates
  - Accessible via "Duplicate Authors" in navigation

### Fixed
- **Audnexus Author Extraction**: Fetch complete author list from book details
  - Search results only showed searched author name
  - Now fetches full book details by ASIN to get all co-authors
  - Enables proper co-author display for audiobooks

---

## [2.9.3] - 2025-11-05

### Added
- **Co-Author Support**: Track and display multiple authors per book
  - New `co_authors` JSON column stores additional authors beyond primary
  - APIs automatically extract all authors from responses (OpenLibrary, Google Books)
  - Co-authors displayed on book cards as "with [Author 2], [Author 3]"
  - Manual add/edit forms include co-authors field (comma-separated input)
  - Primary author concept: book belongs to one author (first/main), others shown as collaborators
  - Similar to Readarr's author model for practical management

---

## [2.9.2] - 2025-11-05

### Added
- **Premium API Support**: Optional paid API keys in settings
  - ISBNdb API key support ($10-50/month for comprehensive ISBN metadata)
  - Google Books API key support (free, increases rate limits)
  - Premium APIs integrated into author scanning and metadata search
  - Settings UI shows links to API documentation and signup
- **Delete Book Button**: Quick delete on each book card
  - Red "Delete Book" button below Edit/Find Info buttons
  - Confirmation dialog before deletion
  - Easier to remove mismatched books without using Manage Duplicates
- **Increased API Result Limits**: Better book discovery for prolific authors
  - OpenLibrary: 100 → 200 results
  - Google Books: 40 → 120 results (pagination over 3 pages)
  - Audnexus: 40 → 100 results
  - Timeouts increased to 15 seconds

### Fixed
- **Author Name Normalization**: Handle author name variations and inconsistencies
  - Handles initials with/without periods: "J.N. Chaney" vs "j n Chaney" vs "JN Chaney"
  - Handles spacing variations: "j. n. Chaney" vs "j.n.chaney"
  - Removes suffixes: Jr, Sr, II, III, IV for better matching
  - Bidirectional initial matching: "j n chaney" matches "john nicholas chaney"
  - Applied consistently across OpenLibrary and Google Books queries
  - Reduces false positives while catching formatting variations

---

## [2.9.1] - 2025-11-05

### Fixed
- **Soft Delete System**: Prevent merged/deleted books from re-appearing during rescans
  - Books are now marked as `deleted = 1` instead of being permanently removed
  - Deleted books are filtered out from all views
  - Rescans skip books marked as deleted
  - Preserves database history while keeping interface clean
- **Preserve Manual Edits During Rescan**: Only update empty fields
  - Changed UPDATE logic to use `COALESCE(existing, new)`
  - Manual edits to ISBN, ASIN, series, descriptions are now protected
  - `have_it` status still updates to reflect current library state

---

## [2.9.0] - 2025-11-05

### Added
- **Manual Book Management**: Complete suite of manual book editing features
  - Add books manually with comprehensive form (title, subtitle, series, ISBN, ASIN, release date, format, cover URL, description)
  - Edit existing book details with pre-populated form
  - Search book metadata across Open Library, Google Books, and Audnexus APIs
  - Select and apply correct metadata from search results
  - Edit and Find Info buttons on each book card
- **Metadata Search**: Visual card-based results display showing:
  - Book cover images
  - All available identifiers (ISBN, ISBN-13, ASIN)
  - Series information
  - Release dates and formats
  - Source badges (OpenLibrary, GoogleBooks, Audnexus)
  - One-click "Use This" to apply selected metadata

### Technical
- New backend routes: `/books/<id>/edit`, `/books/<id>/search-metadata`, `/books/<id>/apply-metadata`
- Smart metadata merging only updates non-empty fields
- Search filters results by title similarity across all three APIs
- Complete JavaScript handlers for edit and metadata search workflows

### Fixed
- JSON parsing error in metadata apply button (stored objects directly on DOM elements instead of as JSON strings in data attributes)
- Proper HTML escaping to prevent XSS vulnerabilities

---

## [2.4.0] - 2025-11-03

### Fixed
- **CRITICAL:** Pagination finally works correctly!
  - Changed from `offset` parameter to `page` parameter (ABS API requirement)
  - Was processing same 100 books repeatedly (that's why only 39 authors)
  - Now actually pages through all 1747 books
  - Should find 300+ unique authors now

### Changed
- Better logging shows: "page X, items Y-Z" instead of "offset X"
- Shows final count: "Finished: processed X of Y items"

---

## [2.3.1] - 2025-11-03

### Fixed
- **CRITICAL:** Bulk import now properly splits multi-author books
  - Handles "Author A, Author B" → creates 2 authors
  - Handles "Author A & Author B" → creates 2 authors  
  - Handles "Author A and Author B" → creates 2 authors
  - Should now find 300+ authors instead of only 39
- Fixed template crash when viewing author pages (Jinja2 syntax error)

---

## [2.3.0] - 2025-11-03

### Added
- **Edit Author Names** - Click pencil icon to fix import errors or spelling
  - Available on home page (author cards)
  - Available on author detail page
  - Uses modal popup for clean UX
  - Validates for duplicates

---

## [2.2.0] - 2025-11-03

### Added
- **Statistics Dashboard** on home page showing:
  - Total authors being monitored
  - How many have been scanned
  - How many are pending scan

---

## [2.1.1] - 2025-11-03

### Fixed
- Footer now properly supports dark mode (text readable in both themes)

---

## [2.1.0] - 2025-11-03

### Fixed
- **CRITICAL:** Bulk import from Audiobookshelf now works correctly
  - Fixed API response parsing to match actual ABS structure
  - Added pagination support (fetches all books, not just first 100)
  - Handles multi-author books (splits "Author A, Author B" into separate authors)
  - Better error logging for debugging

### Technical
- Updated `get_all_authors_from_audiobookshelf()` function
- Reads from `media.metadata.authorName` structure
- Paginates through library with 100 items per request
- Prints progress to Docker logs

---

## [2.0.0] - 2025-11-03

### Added
- **Bulk Import from Audiobookshelf** - Import all authors from your library at once
- **Show Missing Only Filter** - Toggle to view only books you don't have
- **Dark Mode** - Full dark theme with persistent preference
- **Success Messages** - Flash notifications for all actions
- **Better UX** - Save button redirects to home page with confirmation

### Changed
- Settings save now redirects to home page (instead of staying on settings)
- All forms now show success/error messages via flash alerts
- Improved visual feedback throughout the app

### Fixed
- Dockerfile now handles empty static/ directory correctly

---

## [1.0.0] - 2025-11-03

### Initial Release
- Multi-source book discovery (Open Library, Google Books, Audnexus)
- Manual author management
- Audiobookshelf integration (check what you have)
- Prowlarr integration (search for missing books)
- SQLite database
- Web UI with Bootstrap 5
- Docker deployment support
