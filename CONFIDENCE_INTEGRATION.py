# ============================================================
# BookScout — confidence scoring integration patch
# ============================================================
#
# Three changes needed in app.py:
#
#   1. Import the module (top of file)
#   2. Call score_books() inside scan_author()
#   3. Expose score/confidence_band in the author template
#
# No existing logic is removed — scoring is purely additive.
# ============================================================


# ── 1. ADD IMPORT ────────────────────────────────────────────
# Place right after the existing imports block, around line 10.

from confidence import score_books


# ── 2. CALL score_books() IN scan_author() ───────────────────
#
# In scan_author() (around line 500), AFTER `merge_books()` is called
# and BEFORE the Audiobookshelf check loop, insert:

    # --- Confidence scoring ---
    all_books = score_books(all_books, search_author=author_name)
    # all_books is now sorted by score descending.
    # Each book dict now has:
    #   book['score']            int
    #   book['confidence_band']  'high' | 'medium' | 'low'
    #   book['score_reasons']    list[str]   (for debugging)

    # --- existing loop continues unchanged ---
    for book in all_books:
        has_it, abs_series, abs_series_pos = check_audiobookshelf(...)
        ...

# ── 3. PERSIST score & confidence_band TO DB ─────────────────
#
# In the same scan_author() function, the INSERT block currently ends with:
#
#     db.execute('''
#         INSERT INTO books (author_id, title, ..., have_it, co_authors)
#         VALUES (?, ?, ..., ?, ?)
#     ''', (...))
#
# The books table has no score columns yet, so add a migration in init_db():

    # In init_db(), add to the migration block near the bottom:
    if 'score' not in columns:
        db.execute('ALTER TABLE books ADD COLUMN score INTEGER DEFAULT 0')
    if 'confidence_band' not in columns:
        db.execute('ALTER TABLE books ADD COLUMN confidence_band TEXT DEFAULT "low"')
    if 'score_reasons' not in columns:
        db.execute('ALTER TABLE books ADD COLUMN score_reasons TEXT')

# And update the INSERT in scan_author() to include the three new columns:
#
#   INSERT INTO books (..., have_it, co_authors, score, confidence_band, score_reasons)
#   VALUES (?, ..., ?, ?, ?, ?, ?)
#
# Extra values at the end of the tuple:
#   book.get('score', 0),
#   book.get('confidence_band', 'low'),
#   json.dumps(book.get('score_reasons', []))
#
# And the UPDATE block needs two new lines:
#
#   UPDATE books SET
#       have_it = ?,
#       score = ?,
#       confidence_band = ?,
#       score_reasons = ?,
#       ...
#   WHERE id = ?
#
# Extra values:
#   book.get('score', 0),
#   book.get('confidence_band', 'low'),
#   json.dumps(book.get('score_reasons', [])),


# ── 4. TEMPLATE: show badge in author.html ───────────────────
#
# In templates/author.html, inside the book card body,
# after the release_date/format block, add:
#
#   {% if book.confidence_band %}
#   <div class="mb-1">
#     {% if book.confidence_band == 'high' %}
#       <span class="badge bg-success" title="{{ book.score }} pts">
#         <i class="bi bi-check2-circle"></i> High confidence
#       </span>
#     {% elif book.confidence_band == 'medium' %}
#       <span class="badge bg-warning text-dark" title="{{ book.score }} pts">
#         <i class="bi bi-exclamation-circle"></i> Medium confidence
#       </span>
#     {% else %}
#       <span class="badge bg-danger" title="{{ book.score }} pts">
#         <i class="bi bi-question-circle"></i> Low confidence
#       </span>
#     {% endif %}
#   </div>
#   {% endif %}


# ── 5. OPTIONAL: expose score in the API response ────────────
#
# If you ever add a JSON endpoint for the book list, include:
#   "score": book["score"],
#   "confidence_band": book["confidence_band"],
#   "score_reasons": book["score_reasons"],
