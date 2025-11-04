# BookScout Changelog

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
