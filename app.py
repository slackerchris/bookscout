"""
BookScout - Multi-source book discovery and monitoring
"""
import os
import sqlite3
import requests
import json
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from typing import List, Dict, Optional

app = Flask(__name__)

# Use /data directory for persistent storage if it exists, otherwise use current directory
DATA_DIR = '/data' if os.path.exists('/data') and os.access('/data', os.W_OK) else '.'
app.config['DATABASE'] = os.path.join(DATA_DIR, 'bookscout.db')
app.config['CONFIG_FILE'] = os.path.join(DATA_DIR, 'config.json')
app.secret_key = os.getenv('SECRET_KEY', 'bookscout-secret-key-change-in-production')

# Read version from VERSION file
def get_version():
    """Read version from VERSION file"""
    version_file = os.path.join(os.path.dirname(__file__), 'VERSION')
    try:
        with open(version_file, 'r') as f:
            return f.read().strip()
    except:
        return 'unknown'

# Make version available to all templates
@app.context_processor
def inject_version():
    return dict(version=get_version())

# API Configuration
OPENLIBRARY_API = "https://openlibrary.org/search.json"
GOOGLE_BOOKS_API = "https://www.googleapis.com/books/v1/volumes"
AUDNEXUS_API = "https://api.audnex.us"

# User configuration (to be set via env vars or UI)
AUDIOBOOKSHELF_URL = os.getenv('AUDIOBOOKSHELF_URL', 'http://localhost:13378')
AUDIOBOOKSHELF_TOKEN = os.getenv('AUDIOBOOKSHELF_TOKEN', '')
PROWLARR_URL = os.getenv('PROWLARR_URL', 'http://localhost:9696')
PROWLARR_API_KEY = os.getenv('PROWLARR_API_KEY', '')


def get_db():
    """Get database connection"""
    db = sqlite3.connect(app.config['DATABASE'])
    db.row_factory = sqlite3.Row
    return db


def init_db():
    """Initialize the database"""
    db = get_db()
    db.executescript('''
        CREATE TABLE IF NOT EXISTS authors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            openlibrary_id TEXT,
            audible_id TEXT,
            goodreads_id TEXT,
            last_scanned TIMESTAMP,
            active INTEGER DEFAULT 1
        );
        
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author_id INTEGER,
            title TEXT NOT NULL,
            subtitle TEXT,
            isbn TEXT,
            isbn13 TEXT,
            asin TEXT,
            release_date TEXT,
            format TEXT,
            source TEXT,
            cover_url TEXT,
            description TEXT,
            series TEXT,
            series_position TEXT,
            found_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            have_it INTEGER DEFAULT 0,
            FOREIGN KEY (author_id) REFERENCES authors (id)
        );
        
        CREATE TABLE IF NOT EXISTS scan_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author_id INTEGER,
            scan_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            books_found INTEGER,
            new_books INTEGER,
            FOREIGN KEY (author_id) REFERENCES authors (id)
        );
        
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    ''')
    
    # Migration: Add missing columns to existing tables
    cursor = db.cursor()
    
    # Check and add missing columns to authors table
    cursor.execute("PRAGMA table_info(authors)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'openlibrary_id' not in columns:
        db.execute('ALTER TABLE authors ADD COLUMN openlibrary_id TEXT')
    if 'audible_id' not in columns:
        db.execute('ALTER TABLE authors ADD COLUMN audible_id TEXT')
    if 'goodreads_id' not in columns:
        db.execute('ALTER TABLE authors ADD COLUMN goodreads_id TEXT')
    if 'last_scanned' not in columns:
        db.execute('ALTER TABLE authors ADD COLUMN last_scanned TIMESTAMP')
    if 'active' not in columns:
        db.execute('ALTER TABLE authors ADD COLUMN active INTEGER DEFAULT 1')
    
    # Check and add missing columns to books table
    cursor.execute("PRAGMA table_info(books)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'subtitle' not in columns:
        db.execute('ALTER TABLE books ADD COLUMN subtitle TEXT')
    if 'isbn' not in columns:
        db.execute('ALTER TABLE books ADD COLUMN isbn TEXT')
    if 'isbn13' not in columns:
        db.execute('ALTER TABLE books ADD COLUMN isbn13 TEXT')
    if 'asin' not in columns:
        db.execute('ALTER TABLE books ADD COLUMN asin TEXT')
    if 'release_date' not in columns:
        db.execute('ALTER TABLE books ADD COLUMN release_date TEXT')
    if 'format' not in columns:
        db.execute('ALTER TABLE books ADD COLUMN format TEXT')
    if 'source' not in columns:
        db.execute('ALTER TABLE books ADD COLUMN source TEXT')
    if 'cover_url' not in columns:
        db.execute('ALTER TABLE books ADD COLUMN cover_url TEXT')
    if 'description' not in columns:
        db.execute('ALTER TABLE books ADD COLUMN description TEXT')
    if 'series' not in columns:
        db.execute('ALTER TABLE books ADD COLUMN series TEXT')
    if 'series_position' not in columns:
        db.execute('ALTER TABLE books ADD COLUMN series_position TEXT')
    if 'have_it' not in columns:
        db.execute('ALTER TABLE books ADD COLUMN have_it INTEGER DEFAULT 0')
    if 'deleted' not in columns:
        db.execute('ALTER TABLE books ADD COLUMN deleted INTEGER DEFAULT 0')
    
    db.commit()
    db.close()


def query_openlibrary(author_name: str, language_filter: str = None) -> List[Dict]:
    """Query Open Library API for books by author"""
    try:
        response = requests.get(
            OPENLIBRARY_API,
            params={'author': author_name, 'limit': 100},
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        
        books = []
        for doc in data.get('docs', []):
            # OpenLibrary uses 'language' field (list of language codes)
            book_languages = doc.get('language', ['en'])
            
            # Skip if language filter is set and doesn't match any of the book's languages
            if language_filter and language_filter != 'all':
                if language_filter not in book_languages:
                    continue
            
            book = {
                'title': doc.get('title', ''),
                'subtitle': doc.get('subtitle', ''),
                'isbn': doc.get('isbn', [None])[0] if doc.get('isbn') else None,
                'isbn13': next((isbn for isbn in doc.get('isbn', []) if len(isbn) == 13), None),
                'release_date': str(doc.get('first_publish_year', '')),
                'cover_url': f"https://covers.openlibrary.org/b/id/{doc.get('cover_i')}-M.jpg" if doc.get('cover_i') else None,
                'language': book_languages[0] if book_languages else 'en',
                'source': 'OpenLibrary'
            }
            if book['title']:
                books.append(book)
        
        return books
    except Exception as e:
        print(f"Error querying OpenLibrary: {e}")
        return []


def query_google_books(author_name: str, language_filter: str = None) -> List[Dict]:
    """Query Google Books API for books by author"""
    try:
        params = {'q': f'inauthor:"{author_name}"', 'maxResults': 40}
        
        # Add language restriction if specified
        if language_filter and language_filter != 'all':
            params['langRestrict'] = language_filter
        
        response = requests.get(
            GOOGLE_BOOKS_API,
            params=params,
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        
        books = []
        for item in data.get('items', []):
            vol_info = item.get('volumeInfo', {})
            identifiers = {id_info['type']: id_info['identifier'] 
                          for id_info in vol_info.get('industryIdentifiers', [])}
            
            book_lang = vol_info.get('language', 'en')
            
            # Skip if language filter is set and doesn't match
            if language_filter and language_filter != 'all' and book_lang != language_filter:
                continue
            
            book = {
                'title': vol_info.get('title', ''),
                'subtitle': vol_info.get('subtitle', ''),
                'isbn': identifiers.get('ISBN_10'),
                'isbn13': identifiers.get('ISBN_13'),
                'release_date': vol_info.get('publishedDate', ''),
                'cover_url': vol_info.get('imageLinks', {}).get('thumbnail'),
                'description': vol_info.get('description', ''),
                'language': book_lang,
                'source': 'GoogleBooks'
            }
            if book['title']:
                books.append(book)
        
        return books
    except Exception as e:
        print(f"Error querying Google Books: {e}")
        return []


def query_audnexus(author_name: str, language_filter: str = None) -> List[Dict]:
    """Query Audnexus API for audiobooks by author"""
    try:
        # First search for author
        search_response = requests.get(
            f"{AUDNEXUS_API}/search",
            params={'name': author_name},
            timeout=10
        )
        
        if search_response.status_code != 200:
            return []
        
        search_data = search_response.json()
        books = []
        
        # Extract books from search results
        for item in search_data.get('results', [])[:40]:  # Limit results
            # Audnexus doesn't provide language info in search results
            # We'll assume audiobooks from Audible are mostly English unless specified
            # Could enhance this by fetching full book details for language check
            
            book = {
                'title': item.get('title', ''),
                'subtitle': item.get('subtitle', ''),
                'asin': item.get('asin', ''),
                'release_date': item.get('releaseDate', ''),
                'cover_url': item.get('image'),
                'format': 'audiobook',
                'language': 'en',  # Default to English for audiobooks
                'source': 'Audnexus'
            }
            if book['title']:
                books.append(book)
        
        return books
    except Exception as e:
        print(f"Error querying Audnexus: {e}")
        return []


def extract_series_from_title(title: str) -> tuple:
    """Extract series name and position from title"""
    import re
    
    # Multiple patterns to match different series formats
    patterns = [
        # Parenthetical formats
        r'\(([^)]+?)\s*#(\d+(?:\.\d+)?)\)',  # (Series #1)
        r'\(([^)]+?),?\s*Book\s+(\d+(?:\.\d+)?)\)',  # (Series, Book 1) or (Series Book 1)
        r'\(([^)]+?),?\s*Vol\.?\s+(\d+(?:\.\d+)?)\)',  # (Series, Vol 1)
        r'\(([^)]+?)\s*-\s*Book\s+(\d+(?:\.\d+)?)\)',  # (Series - Book 1)
        # Colon/subtitle formats
        r'^(.+?):\s*Book\s+(\d+(?:\.\d+)?)\s*[-:]',  # Series: Book 1 - Title
        r'^(.+?)\s*#(\d+(?:\.\d+)?)\s*[-:]',  # Series #1 - Title
        # Trailing number formats  
        r'(.+?)\s+Book\s+(\d+(?:\.\d+)?)$',  # Title Book 1
        r'(.+?)\s+#(\d+(?:\.\d+)?)$',  # Title #1
    ]
    
    for pattern in patterns:
        match = re.search(pattern, title, re.IGNORECASE)
        if match:
            series = match.group(1).strip()
            position = match.group(2)
            # Clean up the title - remove the series pattern
            clean_title = re.sub(pattern, '', title, flags=re.IGNORECASE).strip()
            # Remove extra punctuation
            clean_title = re.sub(r'^[-:\s]+|[-:\s]+$', '', clean_title).strip()
            # If clean_title is empty or too short, use original
            if len(clean_title) < 3:
                clean_title = title
            return clean_title, series, position
    
    return title, None, None


def merge_books(books_lists: List[List[Dict]]) -> List[Dict]:
    """Merge and deduplicate books from multiple sources"""
    merged = {}
    
    for books in books_lists:
        for book in books:
            # Extract series information from title
            title, series, series_pos = extract_series_from_title(book['title'])
            book['title'] = title
            if series:
                book['series'] = series
                book['series_position'] = series_pos
            else:
                book['series'] = None
                book['series_position'] = None
            # Create a key for deduplication
            key = None
            if book.get('isbn13'):
                key = f"isbn13:{book['isbn13']}"
            elif book.get('isbn'):
                key = f"isbn:{book['isbn']}"
            elif book.get('asin'):
                key = f"asin:{book['asin']}"
            else:
                # Use normalized title as fallback
                normalized_title = book['title'].lower().strip()
                key = f"title:{normalized_title}"
            
            if key not in merged:
                merged[key] = book
            else:
                # Merge additional info from this source
                existing = merged[key]
                for field in ['subtitle', 'isbn', 'isbn13', 'asin', 'cover_url', 'description']:
                    if not existing.get(field) and book.get(field):
                        existing[field] = book[field]
                # Track multiple sources
                if isinstance(existing.get('source'), str):
                    existing['source'] = [existing['source']]
                if isinstance(book['source'], str):
                    existing['source'].append(book['source'])
    
    return list(merged.values())


def search_audible_metadata_direct(book_title: str, author_name: str) -> tuple:
    """Search Audible API directly for book metadata including series
    Two-step process:
    1. Query api.audible.com to get ASIN
    2. Query api.audnex.us with ASIN to get full metadata including series
    Returns: (series_name: str|None, series_position: str|None)
    """
    try:
        # Step 1: Get ASIN from Audible API
        params = {
            'num_results': '1',
            'products_sort_by': 'Relevance',
            'title': book_title
        }
        if author_name:
            params['author'] = author_name
        
        audible_url = 'https://api.audible.com/1.0/catalog/products'
        response = requests.get(audible_url, params=params, timeout=10)
        
        if response.status_code != 200:
            return (None, None)
        
        data = response.json()
        products = data.get('products', [])
        
        if not products:
            return (None, None)
        
        # Get ASIN from first result
        asin = products[0].get('asin')
        if not asin:
            return (None, None)
        
        # Step 2: Get full metadata from Audnexus using ASIN
        audnexus_url = f'https://api.audnex.us/books/{asin}'
        audnexus_response = requests.get(audnexus_url, timeout=10)
        
        if audnexus_response.status_code != 200:
            return (None, None)
        
        book_data = audnexus_response.json()
        
        # Check for series in seriesPrimary field (Audnexus format)
        series_primary = book_data.get('seriesPrimary')
        if series_primary:
            series_name = series_primary.get('name')
            series_position = series_primary.get('position')
            
            if series_name:
                print(f"    Found series via Audible API: {series_name} #{series_position}")
                return (series_name, series_position)
        
        return (None, None)
        
    except requests.exceptions.Timeout:
        print(f"  Audible API timeout for '{book_title}'")
        return (None, None)
    except Exception as e:
        print(f"  Error searching Audible API: {e}")
        return (None, None)


def check_audiobookshelf(book_title: str, author_name: str) -> tuple:
    """Check if book exists in Audiobookshelf library
    Returns: (has_book: bool, series_name: str|None, series_position: str|None)
    """
    # Get settings from database
    settings = get_settings_from_db()
    abs_url = settings.get('audiobookshelf_url') or AUDIOBOOKSHELF_URL
    abs_token = settings.get('audiobookshelf_token') or AUDIOBOOKSHELF_TOKEN
    
    if not abs_url or not abs_token:
        print(f"  Audiobookshelf not configured (URL: {abs_url is not None}, Token: {abs_token is not None})")
        return (False, None, None)
    
    try:
        headers = {'Authorization': f'Bearer {abs_token}'}
        response = requests.get(
            f"{abs_url}/api/libraries",
            headers=headers,
            timeout=10
        )
        
        if response.status_code != 200:
            return (False, None, None)
        
        libraries = response.json().get('libraries', [])
        
        # Normalize book title for better matching
        # Remove common series indicators, punctuation, and extra spaces
        normalized_title = book_title.lower().strip()
        normalized_title = normalized_title.replace(':', '').replace(',', '').replace('-', ' ')
        title_words = set(normalized_title.split())
        
        # Search each library
        for library in libraries:
            search_response = requests.get(
                f"{abs_url}/api/libraries/{library['id']}/search",
                params={'q': book_title},
                headers=headers,
                timeout=10
            )
            
            if search_response.status_code == 200:
                results = search_response.json()
                book_results = results.get('book', [])
                
                if len(book_results) > 0:
                    print(f"  Audiobookshelf search for '{book_title}' returned {len(book_results)} results")
                    # Debug: show structure of first result
                    if len(book_results) > 0:
                        print(f"  First result keys: {list(book_results[0].keys())}")
                
                for item in book_results:
                    # Search API returns libraryItem object directly
                    library_item = item.get('libraryItem', {})
                    media = library_item.get('media', {})
                    metadata = media.get('metadata', {})
                    
                    abs_title = metadata.get('title', '').lower().strip()
                    
                    # Try exact substring match first
                    if book_title.lower() in abs_title or abs_title in book_title.lower():
                        # Extract series info from search result
                        series_name = None
                        series_pos = None
                        series_list = metadata.get('series', [])
                        
                        if series_list and len(series_list) > 0:
                            series_name = series_list[0].get('name')
                            series_pos = series_list[0].get('sequence')
                        
                        print(f"  MATCH found: '{book_title}' -> series={series_name}, pos={series_pos}")
                        return (True, series_name, series_pos)
                    
                    # Try normalized word matching (at least 75% of words match)
                    abs_normalized = abs_title.replace(':', '').replace(',', '').replace('-', ' ')
                    abs_words = set(abs_normalized.split())
                    
                    if len(title_words) > 0:
                        overlap = len(title_words & abs_words)
                        similarity = overlap / len(title_words)
                        if similarity >= 0.75:
                            # Extract series info from search result
                            series_name = None
                            series_pos = None
                            series_list = metadata.get('series', [])
                            
                            if series_list and len(series_list) > 0:
                                series_name = series_list[0].get('name')
                                series_pos = series_list[0].get('sequence')
                            
                            print(f"  MATCH found: '{book_title}' (similarity: {similarity:.2f}) -> series={series_name}, pos={series_pos}")
                            return (True, series_name, series_pos)
        
        return (False, None, None)
    except Exception as e:
        print(f"Error checking Audiobookshelf: {e}")
        return (False, None, None)


def get_all_authors_from_audiobookshelf() -> List[str]:
    """Get all unique authors from Audiobookshelf library"""
    # Get settings from database
    db = get_db()
    settings_dict = {}
    settings_rows = db.execute('SELECT key, value FROM settings').fetchall()
    for row in settings_rows:
        settings_dict[row['key']] = row['value']
    db.close()
    
    abs_url = settings_dict.get('audiobookshelf_url') or AUDIOBOOKSHELF_URL
    abs_token = settings_dict.get('audiobookshelf_token') or AUDIOBOOKSHELF_TOKEN
    
    if not abs_url or not abs_token:
        print("ABS URL or token not configured")
        return []
    
    try:
        headers = {'Authorization': f'Bearer {abs_token}'}
        response = requests.get(
            f"{abs_url}/api/libraries",
            headers=headers,
            timeout=10
        )
        
        if response.status_code != 200:
            print(f"Failed to get libraries: {response.status_code}")
            return []
        
        libraries = response.json().get('libraries', [])
        authors = set()
        
        print(f"Found {len(libraries)} libraries")
        
        # Get items from each library
        for library in libraries:
            library_id = library['id']
            print(f"Fetching items from library: {library.get('name')}")
            
            # Get all items (paginated)
            page = 0
            limit = 100
            total_processed = 0
            
            while True:
                items_response = requests.get(
                    f"{abs_url}/api/libraries/{library_id}/items",
                    params={'limit': limit, 'page': page},
                    headers=headers,
                    timeout=30
                )
                
                if items_response.status_code != 200:
                    print(f"Failed to get items: {items_response.status_code}")
                    break
                
                data = items_response.json()
                items = data.get('results', [])
                
                if not items:
                    break
                
                print(f"Processing {len(items)} items (page {page}, items {total_processed}-{total_processed + len(items)})")
                
                for item in items:
                    media = item.get('media', {})
                    metadata = media.get('metadata', {})
                    author_name = metadata.get('authorName', '')
                    
                    if author_name:
                        print(f"Raw author name: {author_name}")
                        
                        # Split on common separators
                        # Handle: "Author A, Author B", "Author A & Author B", "Author A and Author B"
                        separators = [' & ', ' and ', ', ']
                        author_list = [author_name]
                        
                        for sep in separators:
                            new_list = []
                            for name in author_list:
                                if sep in name:
                                    split_names = [n.strip() for n in name.split(sep)]
                                    print(f"  Split '{name}' by '{sep}' into: {split_names}")
                                    new_list.extend(split_names)
                                else:
                                    new_list.append(name)
                            author_list = new_list
                        
                        # Add all extracted authors
                        for author in author_list:
                            if author and len(author) > 1:  # Skip empty or single-char names
                                print(f"  Adding author: {author}")
                                authors.add(author)
                
                total_processed += len(items)
                
                # Check if we've fetched all items
                total = data.get('total', 0)
                if total_processed >= total:
                    print(f"Finished: processed {total_processed} of {total} items")
                    break
                
                page += 1
        
        print(f"Found {len(authors)} unique authors")
        return sorted(list(authors))
    except Exception as e:
        print(f"Error getting authors from Audiobookshelf: {e}")
        import traceback
        traceback.print_exc()
        return []


def search_prowlarr(book_title: str, author_name: str) -> Optional[str]:
    """Search for book via Prowlarr and return search URL"""
    if not PROWLARR_URL or not PROWLARR_API_KEY:
        return None
    
    try:
        search_query = f"{book_title} {author_name}"
        headers = {'X-Api-Key': PROWLARR_API_KEY}
        
        response = requests.get(
            f"{PROWLARR_URL}/api/v1/search",
            params={'query': search_query, 'type': 'book'},
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            results = response.json()
            if results:
                # Return search performed successfully
                return f"{PROWLARR_URL}/search?query={search_query}"
        
        return None
    except Exception as e:
        print(f"Error searching Prowlarr: {e}")
        return None


def get_settings_from_db() -> Dict[str, str]:
    """Helper function to get all settings from database"""
    db = get_db()
    settings_dict = {}
    settings_rows = db.execute('SELECT key, value FROM settings').fetchall()
    for row in settings_rows:
        settings_dict[row['key']] = row['value']
    db.close()
    return settings_dict


def search_prowlarr_api(query: str) -> List[Dict]:
    """Search Prowlarr API and return results"""
    settings = get_settings_from_db()
    prowlarr_url = settings.get('prowlarr_url') or PROWLARR_URL
    prowlarr_key = settings.get('prowlarr_api_key') or PROWLARR_API_KEY
    
    if not prowlarr_url or not prowlarr_key:
        print("Prowlarr not configured")
        return []
    
    try:
        headers = {'X-Api-Key': prowlarr_key}
        response = requests.get(
            f"{prowlarr_url}/api/v1/search",
            params={'query': query, 'type': 'book'},
            headers=headers,
            timeout=30
        )
        
        if response.status_code != 200:
            print(f"Prowlarr search failed: {response.status_code}")
            return []
        
        results = response.json()
        parsed_results = []
        
        for item in results:
            parsed_results.append({
                'title': item.get('title', ''),
                'source': 'Prowlarr',
                'type': 'Usenet',
                'size': item.get('size', 0),
                'indexer': item.get('indexer', ''),
                'download_url': item.get('downloadUrl', ''),
                'guid': item.get('guid', ''),
                'seeders': 0,  # Usenet doesn't have seeders
                'publish_date': item.get('publishDate', '')
            })
        
        print(f"Found {len(parsed_results)} Prowlarr results")
        return parsed_results
        
    except Exception as e:
        print(f"Error searching Prowlarr API: {e}")
        import traceback
        traceback.print_exc()
        return []


def search_jackett_api(query: str) -> List[Dict]:
    """Search Jackett API and return results"""
    settings = get_settings_from_db()
    jackett_url = settings.get('jackett_url')
    jackett_key = settings.get('jackett_api_key')
    
    if not jackett_url or not jackett_key:
        print("Jackett not configured")
        return []
    
    try:
        # Don't filter by category - let Jackett return all results and filter by query
        response = requests.get(
            f"{jackett_url}/api/v2.0/indexers/all/results",
            params={
                'apikey': jackett_key,
                'Query': query,
                # Removed Category filtering - Jackett will search all categories
            },
            timeout=30
        )
        
        if response.status_code != 200:
            print(f"Jackett search failed: {response.status_code}")
            return []
        
        data = response.json()
        results = data.get('Results', [])
        print(f"Jackett returned {len(results)} total results from API")
        
        # Debug: print first few result titles
        for i, item in enumerate(results[:5]):
            print(f"  Jackett result {i+1}: {item.get('Title', 'NO TITLE')}")
        
        parsed_results = []
        
        for item in results:
            parsed_results.append({
                'title': item.get('Title', ''),
                'source': 'Jackett',
                'type': 'Torrent',
                'size': item.get('Size', 0),
                'indexer': item.get('Tracker', ''),
                'download_url': item.get('Link', ''),
                'magnet_url': item.get('MagnetUri', ''),
                'guid': item.get('Guid', ''),
                'seeders': item.get('Seeders', 0),
                'leechers': item.get('Peers', 0),
                'publish_date': item.get('PublishDate', '')
            })
        
        print(f"Found {len(parsed_results)} Jackett results")
        return parsed_results
        
    except Exception as e:
        print(f"Error searching Jackett API: {e}")
        import traceback
        traceback.print_exc()
        return []


def unified_search(query: str) -> List[Dict]:
    """Search both Prowlarr and Jackett and merge results"""
    print(f"Performing unified search for: {query}")
    
    # Search both APIs
    prowlarr_results = search_prowlarr_api(query)
    jackett_results = search_jackett_api(query)
    
    # Merge results
    all_results = prowlarr_results + jackett_results
    
    # Sort by seeders (for torrents) and date
    def sort_key(item):
        # Prioritize items with seeders, then by size
        return (item.get('seeders', 0), item.get('size', 0))
    
    all_results.sort(key=sort_key, reverse=True)
    
    print(f"Total unified results: {len(all_results)}")
    return all_results


def send_to_sabnzbd(download_url: str, title: str) -> bool:
    """Send NZB to SABnzbd"""
    settings = get_settings_from_db()
    sabnzbd_url = settings.get('sabnzbd_url')
    sabnzbd_key = settings.get('sabnzbd_api_key')
    
    if not sabnzbd_url or not sabnzbd_key:
        print("SABnzbd not configured")
        return False
    
    try:
        # First, download the NZB file from Prowlarr
        print(f"Downloading NZB from: {download_url}")
        nzb_response = requests.get(download_url, timeout=30, allow_redirects=True)
        
        if nzb_response.status_code != 200:
            print(f"Failed to download NZB: {nzb_response.status_code}")
            return False
        
        nzb_content = nzb_response.content
        
        # Check if we actually got an NZB file (should start with <?xml)
        if not nzb_content.startswith(b'<?xml'):
            print(f"Downloaded content is not an NZB file. First 200 bytes: {nzb_content[:200]}")
            # Try using addurl mode instead
            print(f"Trying addurl mode as fallback...")
            response = requests.get(
                f"{sabnzbd_url}/api",
                params={
                    'mode': 'addurl',
                    'name': download_url,
                    'nzbname': title,
                    'apikey': sabnzbd_key,
                    'output': 'json'
                },
                timeout=10
            )
            if response.status_code == 200:
                result = response.json()
                if result.get('status'):
                    print(f"Successfully sent to SABnzbd via addurl: {title}")
                    return True
            return False
        
        # Now send the NZB content directly to SABnzbd
        response = requests.post(
            f"{sabnzbd_url}/api",
            params={
                'mode': 'addfile',
                'apikey': sabnzbd_key,
                'output': 'json',
                'nzbname': title
            },
            files={'nzbfile': (f'{title}.nzb', nzb_content, 'application/x-nzb')},
            timeout=10
        )
        
        if response.status_code == 200:
            result = response.json()
            print(f"SABnzbd response: {result}")
            if result.get('status'):
                print(f"Successfully sent to SABnzbd: {title}")
                return True
            else:
                print(f"SABnzbd returned error: {result}")
                return False
        
        print(f"Failed to send to SABnzbd: {response.status_code}")
        print(f"Response: {response.text}")
        return False
        
    except Exception as e:
        print(f"Error sending to SABnzbd: {e}")
        import traceback
        traceback.print_exc()
        return False


def send_to_qbittorrent(download_url: str, title: str) -> bool:
    """Send torrent to qBittorrent"""
    settings = get_settings_from_db()
    qbt_url = settings.get('torrent_client_url')
    qbt_user = settings.get('torrent_client_username')
    qbt_pass = settings.get('torrent_client_password')
    
    if not qbt_url:
        print("qBittorrent not configured")
        return False
    
    try:
        # Login to qBittorrent
        session = requests.Session()
        login_response = session.post(
            f"{qbt_url}/api/v2/auth/login",
            data={'username': qbt_user, 'password': qbt_pass},
            timeout=10
        )
        
        if login_response.status_code != 200 or login_response.text != 'Ok.':
            print(f"qBittorrent login failed")
            return False
        
        # Add torrent
        add_response = session.post(
            f"{qbt_url}/api/v2/torrents/add",
            data={'urls': download_url},
            timeout=10
        )
        
        if add_response.status_code == 200 and add_response.text == 'Ok.':
            print(f"Successfully sent to qBittorrent: {title}")
            return True
        
        print(f"Failed to add torrent to qBittorrent")
        return False
        
    except Exception as e:
        print(f"Error sending to qBittorrent: {e}")
        return False


def send_to_transmission(download_url: str, title: str) -> bool:
    """Send torrent to Transmission"""
    settings = get_settings_from_db()
    trans_url = settings.get('torrent_client_url')
    trans_user = settings.get('torrent_client_username')
    trans_pass = settings.get('torrent_client_password')
    
    if not trans_url:
        print("Transmission not configured")
        return False
    
    try:
        session = requests.Session()
        if trans_user and trans_pass:
            session.auth = (trans_user, trans_pass)
        
        # Transmission RPC
        rpc_url = f"{trans_url}/transmission/rpc"
        
        # Get session ID
        response = session.post(rpc_url, timeout=10)
        session_id = response.headers.get('X-Transmission-Session-Id')
        
        if not session_id:
            print("Failed to get Transmission session ID")
            return False
        
        headers = {'X-Transmission-Session-Id': session_id}
        
        # Add torrent
        payload = {
            'method': 'torrent-add',
            'arguments': {'filename': download_url}
        }
        
        add_response = session.post(rpc_url, json=payload, headers=headers, timeout=10)
        
        if add_response.status_code == 200:
            result = add_response.json()
            if result.get('result') == 'success':
                print(f"Successfully sent to Transmission: {title}")
                return True
        
        print(f"Failed to add torrent to Transmission")
        return False
        
    except Exception as e:
        print(f"Error sending to Transmission: {e}")
        return False


def send_to_deluge(download_url: str, title: str) -> bool:
    """Send torrent to Deluge"""
    settings = get_settings_from_db()
    deluge_url = settings.get('torrent_client_url')
    deluge_pass = settings.get('torrent_client_password')
    
    if not deluge_url:
        print("Deluge not configured")
        return False
    
    try:
        # Deluge Web API
        session = requests.Session()
        
        # Login
        login_response = session.post(
            f"{deluge_url}/json",
            json={'method': 'auth.login', 'params': [deluge_pass], 'id': 1},
            timeout=10
        )
        
        if login_response.status_code != 200:
            print(f"Deluge login failed: {login_response.status_code}")
            print(f"Response: {login_response.text}")
            return False
        
        login_result = login_response.json()
        if not login_result.get('result'):
            print(f"Deluge authentication failed: {login_result}")
            return False
        
        # Add torrent - Deluge uses core.add_torrent_url for URLs
        add_response = session.post(
            f"{deluge_url}/json",
            json={
                'method': 'core.add_torrent_url',
                'params': [download_url, {}],
                'id': 2
            },
            timeout=10
        )
        
        if add_response.status_code == 200:
            result = add_response.json()
            print(f"Deluge add response: {result}")
            if result.get('result'):
                print(f"Successfully sent to Deluge: {title}")
                return True
            else:
                print(f"Deluge returned error: {result.get('error')}")
                return False
        
        print(f"Failed to add torrent to Deluge: {add_response.status_code}")
        print(f"Response: {add_response.text}")
        return False
        
    except Exception as e:
        print(f"Error sending to Deluge: {e}")
        import traceback
        traceback.print_exc()
        return False


def send_to_rtorrent(download_url: str, title: str) -> bool:
    """Send torrent to rTorrent/ruTorrent"""
    settings = get_settings_from_db()
    rtorrent_url = settings.get('torrent_client_url')
    rtorrent_user = settings.get('torrent_client_username')
    rtorrent_pass = settings.get('torrent_client_password')
    
    if not rtorrent_url:
        print("rTorrent not configured")
        return False
    
    try:
        import xmlrpc.client
        
        # Setup auth if provided
        if rtorrent_user and rtorrent_pass:
            url_parts = rtorrent_url.split('//')
            authenticated_url = f"{url_parts[0]}//{rtorrent_user}:{rtorrent_pass}@{url_parts[1]}"
        else:
            authenticated_url = rtorrent_url
        
        server = xmlrpc.client.ServerProxy(authenticated_url)
        
        # Add torrent
        server.load.start('', download_url)
        print(f"Successfully sent to rTorrent: {title}")
        return True
        
    except Exception as e:
        print(f"Error sending to rTorrent: {e}")
        return False


def send_to_download_client(download_url: str, title: str, result_type: str) -> bool:
    """Route download to appropriate client based on type"""
    settings = get_settings_from_db()
    
    if result_type == 'Usenet':
        # Send to SABnzbd
        return send_to_sabnzbd(download_url, title)
    else:
        # Send to torrent client based on configured type
        client_type = settings.get('torrent_client_type', 'qbittorrent')
        
        if client_type == 'qbittorrent':
            return send_to_qbittorrent(download_url, title)
        elif client_type == 'transmission':
            return send_to_transmission(download_url, title)
        elif client_type == 'deluge':
            return send_to_deluge(download_url, title)
        elif client_type == 'rtorrent':
            return send_to_rtorrent(download_url, title)
        else:
            print(f"Unknown torrent client type: {client_type}")
            return False


@app.route('/')
def index():
    """Home page showing authors"""
    db = get_db()
    authors = db.execute('SELECT * FROM authors WHERE active = 1 ORDER BY name').fetchall()
    db.close()
    return render_template('index.html', authors=authors)


@app.route('/authors/add', methods=['POST'])
def add_author():
    """Add a new author to watchlist"""
    author_name = request.form.get('author_name', '').strip()
    
    if not author_name:
        flash('Author name required', 'danger')
        return redirect(url_for('index'))
    
    db = get_db()
    try:
        db.execute('INSERT INTO authors (name) VALUES (?)', (author_name,))
        db.commit()
        author_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        db.close()
        flash(f'Added {author_name} to watchlist', 'success')
        return redirect(url_for('scan_author', author_id=author_id))
    except sqlite3.IntegrityError:
        db.close()
        flash(f'{author_name} already in watchlist', 'warning')
        return redirect(url_for('index'))


@app.route('/authors/bulk-import', methods=['POST'])
def bulk_import_authors():
    """Bulk import authors from Audiobookshelf"""
    print("Starting bulk import from Audiobookshelf...")
    authors_list = get_all_authors_from_audiobookshelf()
    
    if not authors_list:
        flash('Could not retrieve authors from Audiobookshelf. Check your settings.', 'danger')
        return redirect(url_for('index'))
    
    db = get_db()
    added = 0
    skipped = 0
    
    for author_name in authors_list:
        try:
            # Check if author already exists
            existing = db.execute('SELECT id FROM authors WHERE name = ?', (author_name,)).fetchone()
            if not existing:
                db.execute('INSERT INTO authors (name) VALUES (?)', (author_name,))
                added += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"Error adding {author_name}: {e}")
            skipped += 1
    
    db.commit()
    db.close()
    
    flash(f'Imported {added} new authors from Audiobookshelf ({skipped} already existed)', 'success')
    return redirect(url_for('index'))


@app.route('/authors/<int:author_id>/delete', methods=['POST'])
def delete_author(author_id):
    """Remove author from watchlist"""
    db = get_db()
    db.execute('UPDATE authors SET active = 0 WHERE id = ?', (author_id,))
    db.commit()
    db.close()
    flash('Author removed from watchlist', 'success')
    return redirect(url_for('index'))


@app.route('/authors/<int:author_id>/edit', methods=['POST'])
def edit_author(author_id):
    """Edit author name"""
    new_name = request.form.get('author_name', '').strip()
    
    if not new_name:
        flash('Author name cannot be empty', 'danger')
        return redirect(url_for('view_author', author_id=author_id))
    
    db = get_db()
    try:
        db.execute('UPDATE authors SET name = ? WHERE id = ?', (new_name, author_id))
        db.commit()
        db.close()
        flash(f'Author name updated to "{new_name}"', 'success')
    except sqlite3.IntegrityError:
        db.close()
        flash(f'Author "{new_name}" already exists', 'warning')
    
    return redirect(url_for('view_author', author_id=author_id))


@app.route('/authors/<int:author_id>/add-book', methods=['POST'])
def add_book_manually(author_id):
    """Manually add a book to an author"""
    db = get_db()
    
    # Verify author exists
    author = db.execute('SELECT id FROM authors WHERE id = ?', (author_id,)).fetchone()
    if not author:
        db.close()
        flash('Author not found', 'danger')
        return redirect(url_for('index'))
    
    # Get form data
    title = request.form.get('title', '').strip()
    subtitle = request.form.get('subtitle', '').strip() or None
    series = request.form.get('series', '').strip() or None
    series_position = request.form.get('series_position', '').strip() or None
    isbn = request.form.get('isbn', '').strip() or None
    isbn13 = request.form.get('isbn13', '').strip() or None
    asin = request.form.get('asin', '').strip() or None
    release_date = request.form.get('release_date', '').strip() or None
    format_type = request.form.get('format', '').strip() or None
    cover_url = request.form.get('cover_url', '').strip() or None
    description = request.form.get('description', '').strip() or None
    have_it = 1 if request.form.get('have_it') else 0
    
    if not title:
        db.close()
        flash('Book title is required', 'danger')
        return redirect(url_for('view_author', author_id=author_id))
    
    try:
        db.execute('''
            INSERT INTO books (author_id, title, subtitle, isbn, isbn13, asin, 
                              release_date, format, source, cover_url, description, 
                              series, series_position, have_it)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            author_id, title, subtitle, isbn, isbn13, asin, 
            release_date, format_type, 'Manual Entry', cover_url, description,
            series, series_position, have_it
        ))
        db.commit()
        db.close()
        flash(f'Successfully added "{title}"', 'success')
    except Exception as e:
        db.close()
        flash(f'Error adding book: {str(e)}', 'danger')
    
    return redirect(url_for('view_author', author_id=author_id))


@app.route('/books/<int:book_id>/edit', methods=['POST'])
def edit_book(book_id):
    """Edit an existing book's details"""
    db = get_db()
    
    # Verify book exists
    book = db.execute('SELECT id FROM books WHERE id = ?', (book_id,)).fetchone()
    if not book:
        db.close()
        return jsonify({'success': False, 'error': 'Book not found'}), 404
    
    # Get form data
    title = request.form.get('title', '').strip()
    subtitle = request.form.get('subtitle', '').strip() or None
    series = request.form.get('series', '').strip() or None
    series_position = request.form.get('series_position', '').strip() or None
    isbn = request.form.get('isbn', '').strip() or None
    isbn13 = request.form.get('isbn13', '').strip() or None
    asin = request.form.get('asin', '').strip() or None
    release_date = request.form.get('release_date', '').strip() or None
    format_type = request.form.get('format', '').strip() or None
    cover_url = request.form.get('cover_url', '').strip() or None
    description = request.form.get('description', '').strip() or None
    
    if not title:
        db.close()
        return jsonify({'success': False, 'error': 'Book title is required'}), 400
    
    try:
        db.execute('''
            UPDATE books 
            SET title = ?, subtitle = ?, series = ?, series_position = ?,
                isbn = ?, isbn13 = ?, asin = ?, release_date = ?, format = ?,
                cover_url = ?, description = ?
            WHERE id = ?
        ''', (
            title, subtitle, series, series_position,
            isbn, isbn13, asin, release_date, format_type,
            cover_url, description, book_id
        ))
        db.commit()
        db.close()
        return jsonify({'success': True, 'message': 'Book updated successfully'})
    except Exception as e:
        db.close()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/books/<int:book_id>/search-metadata', methods=['POST'])
def search_book_metadata(book_id):
    """Search for metadata for a book across multiple APIs"""
    db = get_db()
    
    # Verify book exists
    book = db.execute('SELECT id FROM books WHERE id = ?', (book_id,)).fetchone()
    if not book:
        db.close()
        return jsonify({'success': False, 'error': 'Book not found'}), 404
    
    data = request.get_json()
    title = data.get('title', '').strip()
    author = data.get('author', '').strip()
    
    if not title or not author:
        db.close()
        return jsonify({'success': False, 'error': 'Title and author required'}), 400
    
    results = []
    
    try:
        # Search Open Library
        ol_books = query_openlibrary(author, language_filter='all')
        for book_data in ol_books:
            if book_data['title'].lower().find(title.lower()) >= 0 or title.lower().find(book_data['title'].lower()) >= 0:
                results.append({
                    'source': 'OpenLibrary',
                    'title': book_data['title'],
                    'subtitle': book_data.get('subtitle'),
                    'isbn': book_data.get('isbn'),
                    'isbn13': book_data.get('isbn13'),
                    'asin': book_data.get('asin'),
                    'release_date': book_data.get('release_date'),
                    'format': book_data.get('format'),
                    'cover_url': book_data.get('cover_url'),
                    'description': book_data.get('description'),
                    'series': book_data.get('series'),
                    'series_position': book_data.get('series_position')
                })
        
        # Search Google Books
        gb_books = query_google_books(author, language_filter='all')
        for book_data in gb_books:
            if book_data['title'].lower().find(title.lower()) >= 0 or title.lower().find(book_data['title'].lower()) >= 0:
                results.append({
                    'source': 'GoogleBooks',
                    'title': book_data['title'],
                    'subtitle': book_data.get('subtitle'),
                    'isbn': book_data.get('isbn'),
                    'isbn13': book_data.get('isbn13'),
                    'asin': book_data.get('asin'),
                    'release_date': book_data.get('release_date'),
                    'format': book_data.get('format'),
                    'cover_url': book_data.get('cover_url'),
                    'description': book_data.get('description'),
                    'series': book_data.get('series'),
                    'series_position': book_data.get('series_position')
                })
        
        # Search Audnexus
        audnexus_books = query_audnexus(author, language_filter='all')
        for book_data in audnexus_books:
            if book_data['title'].lower().find(title.lower()) >= 0 or title.lower().find(book_data['title'].lower()) >= 0:
                results.append({
                    'source': 'Audnexus',
                    'title': book_data['title'],
                    'subtitle': book_data.get('subtitle'),
                    'isbn': book_data.get('isbn'),
                    'isbn13': book_data.get('isbn13'),
                    'asin': book_data.get('asin'),
                    'release_date': book_data.get('release_date'),
                    'format': book_data.get('format', 'audiobook'),
                    'cover_url': book_data.get('cover_url'),
                    'description': book_data.get('description'),
                    'series': book_data.get('series'),
                    'series_position': book_data.get('series_position')
                })
        
        db.close()
        return jsonify({'success': True, 'results': results})
    except Exception as e:
        db.close()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/books/<int:book_id>/apply-metadata', methods=['POST'])
def apply_book_metadata(book_id):
    """Apply selected metadata to a book"""
    db = get_db()
    
    # Verify book exists
    book = db.execute('SELECT id FROM books WHERE id = ?', (book_id,)).fetchone()
    if not book:
        db.close()
        return jsonify({'success': False, 'error': 'Book not found'}), 404
    
    data = request.get_json()
    
    try:
        # Update book with selected metadata
        # Only update fields that are provided and not empty
        update_fields = []
        update_values = []
        
        if data.get('title'):
            update_fields.append('title = ?')
            update_values.append(data['title'])
        
        if data.get('subtitle'):
            update_fields.append('subtitle = ?')
            update_values.append(data['subtitle'])
        
        if data.get('series'):
            update_fields.append('series = ?')
            update_values.append(data['series'])
        
        if data.get('series_position'):
            update_fields.append('series_position = ?')
            update_values.append(data['series_position'])
        
        if data.get('isbn'):
            update_fields.append('isbn = ?')
            update_values.append(data['isbn'])
        
        if data.get('isbn13'):
            update_fields.append('isbn13 = ?')
            update_values.append(data['isbn13'])
        
        if data.get('asin'):
            update_fields.append('asin = ?')
            update_values.append(data['asin'])
        
        if data.get('release_date'):
            update_fields.append('release_date = ?')
            update_values.append(data['release_date'])
        
        if data.get('format'):
            update_fields.append('format = ?')
            update_values.append(data['format'])
        
        if data.get('cover_url'):
            update_fields.append('cover_url = ?')
            update_values.append(data['cover_url'])
        
        if data.get('description'):
            update_fields.append('description = ?')
            update_values.append(data['description'])
        
        if update_fields:
            update_values.append(book_id)
            query = f"UPDATE books SET {', '.join(update_fields)} WHERE id = ?"
            db.execute(query, update_values)
            db.commit()
        
        db.close()
        return jsonify({'success': True, 'message': 'Metadata applied successfully'})
    except Exception as e:
        db.close()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/authors/<int:author_id>/scan')
def scan_author(author_id):
    """Scan for books by specific author"""
    db = get_db()
    author = db.execute('SELECT * FROM authors WHERE id = ?', (author_id,)).fetchone()
    
    if not author:
        db.close()
        return jsonify({'error': 'Author not found'}), 404
    
    author_name = author['name']
    
    # Get language filter from settings
    settings = get_settings_from_db()
    language_filter = settings.get('language_filter', 'all')
    
    # Query all sources
    print(f"Scanning for books by {author_name}... (language filter: {language_filter})")
    openlibrary_books = query_openlibrary(author_name, language_filter)
    google_books = query_google_books(author_name, language_filter)
    audnexus_books = query_audnexus(author_name, language_filter)
    
    # Merge results
    all_books = merge_books([openlibrary_books, google_books, audnexus_books])
    
    # Check against Audiobookshelf and get series info
    for book in all_books:
        has_it, abs_series, abs_series_pos = check_audiobookshelf(book['title'], author_name)
        book['have_it'] = 1 if has_it else 0
        
        # If Audiobookshelf has series info, use it (it's most accurate)
        if abs_series:
            book['series'] = abs_series
            book['series_position'] = abs_series_pos
        # If book not in library and no series from title parsing, query Audible API directly
        elif not has_it and not book.get('series'):
            audible_series, audible_pos = search_audible_metadata_direct(book['title'], author_name)
            if audible_series:
                book['series'] = audible_series
                book['series_position'] = audible_pos
    
    # Store in database
    new_books = 0
    updated_books = 0
    for book in all_books:
        # Check if book already exists - prioritize ISBN/ASIN over title
        # Also check if it was deleted - skip deleted books entirely
        existing = None
        
        # First try to find by ISBN13
        if book.get('isbn13'):
            existing = db.execute(
                'SELECT id, deleted FROM books WHERE author_id = ? AND isbn13 = ?',
                (author_id, book['isbn13'])
            ).fetchone()
        
        # Then try ISBN
        if not existing and book.get('isbn'):
            existing = db.execute(
                'SELECT id, deleted FROM books WHERE author_id = ? AND isbn = ?',
                (author_id, book['isbn'])
            ).fetchone()
        
        # Then try ASIN
        if not existing and book.get('asin'):
            existing = db.execute(
                'SELECT id, deleted FROM books WHERE author_id = ? AND asin = ?',
                (author_id, book['asin'])
            ).fetchone()
        
        # Finally fall back to title matching
        if not existing:
            existing = db.execute(
                'SELECT id, deleted FROM books WHERE author_id = ? AND title = ?',
                (author_id, book['title'])
            ).fetchone()
        
        # Skip if book was previously deleted (merged or intentionally removed)
        if existing and existing['deleted']:
            continue
        
        if not existing:
            db.execute('''
                INSERT INTO books (author_id, title, subtitle, isbn, isbn13, asin, 
                                  release_date, format, source, cover_url, description, 
                                  series, series_position, have_it)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                author_id, book['title'], book.get('subtitle'), book.get('isbn'),
                book.get('isbn13'), book.get('asin'), book.get('release_date'),
                book.get('format'), json.dumps(book['source']) if isinstance(book['source'], list) else book['source'],
                book.get('cover_url'), book.get('description'),
                book.get('series'), book.get('series_position'), book.get('have_it', 0)
            ))
            new_books += 1
        else:
            # Update existing book with new info (cover, series, have_it status, etc.)
            # Use COALESCE to preserve existing data - only update empty fields
            # This protects manually edited books from being overwritten during rescan
            db.execute('''
                UPDATE books 
                SET have_it = ?, 
                    series = COALESCE(series, ?),
                    series_position = COALESCE(series_position, ?),
                    cover_url = COALESCE(cover_url, ?),
                    subtitle = COALESCE(subtitle, ?),
                    description = COALESCE(description, ?),
                    asin = COALESCE(asin, ?),
                    isbn = COALESCE(isbn, ?),
                    isbn13 = COALESCE(isbn13, ?)
                WHERE id = ?
            ''', (
                book.get('have_it', 0), 
                book.get('series'), 
                book.get('series_position'),
                book.get('cover_url'),
                book.get('subtitle'),
                book.get('description'),
                book.get('asin'),
                book.get('isbn'),
                book.get('isbn13'),
                existing['id']
            ))
            updated_books += 1
    
    print(f"Scan complete: {new_books} new books, {updated_books} existing books updated")
    
    # Update scan history
    db.execute(
        'UPDATE authors SET last_scanned = ? WHERE id = ?',
        (datetime.now(), author_id)
    )
    db.execute(
        'INSERT INTO scan_history (author_id, books_found, new_books) VALUES (?, ?, ?)',
        (author_id, len(all_books), new_books)
    )
    
    db.commit()
    db.close()
    
    return redirect(url_for('view_author', author_id=author_id))


@app.route('/authors/<int:author_id>')
def view_author(author_id):
    """View books for specific author"""
    show_missing_only = request.args.get('missing', 'false').lower() == 'true'
    
    db = get_db()
    author = db.execute('SELECT * FROM authors WHERE id = ?', (author_id,)).fetchone()
    
    if show_missing_only:
        books = db.execute(
            'SELECT * FROM books WHERE author_id = ? AND have_it = 0 AND (deleted IS NULL OR deleted = 0) ORDER BY series, series_position, release_date DESC',
            (author_id,)
        ).fetchall()
    else:
        books = db.execute(
            'SELECT * FROM books WHERE author_id = ? AND (deleted IS NULL OR deleted = 0) ORDER BY series, series_position, release_date DESC',
            (author_id,)
        ).fetchall()
    
    # Group books by series
    books_by_series = {}
    for book in books:
        series_key = book['series'] if book['series'] else 'Standalone'
        if series_key not in books_by_series:
            books_by_series[series_key] = []
        books_by_series[series_key].append(dict(book))
    
    db.close()
    
    return render_template('author.html', author=author, books=books, 
                         books_by_series=books_by_series, show_missing_only=show_missing_only)


@app.route('/authors/<int:author_id>/duplicates')
def view_duplicates(author_id):
    """View and manage duplicate books for an author"""
    db = get_db()
    author = db.execute('SELECT * FROM authors WHERE id = ?', (author_id,)).fetchone()
    
    if not author:
        db.close()
        return "Author not found", 404
    
    # Find potential duplicates using fuzzy title matching
    all_books = db.execute(
        'SELECT * FROM books WHERE author_id = ? AND (deleted IS NULL OR deleted = 0) ORDER BY title',
        (author_id,)
    ).fetchall()
    
    # Group books that might be duplicates
    duplicate_groups = []
    processed = set()
    
    for i, book1 in enumerate(all_books):
        if book1['id'] in processed:
            continue
            
        group = [dict(book1)]
        processed.add(book1['id'])
        
        for book2 in all_books[i+1:]:
            if book2['id'] in processed:
                continue
            
            # Check if books are duplicates
            is_duplicate = False
            
            # Same ISBN13, ISBN, or ASIN
            if book1['isbn13'] and book2['isbn13'] and book1['isbn13'] == book2['isbn13']:
                is_duplicate = True
            elif book1['isbn'] and book2['isbn'] and book1['isbn'] == book2['isbn']:
                is_duplicate = True
            elif book1['asin'] and book2['asin'] and book1['asin'] == book2['asin']:
                is_duplicate = True
            # Very similar titles (normalize and compare)
            else:
                title1 = book1['title'].lower().strip()
                title2 = book2['title'].lower().strip()
                # Remove common punctuation for comparison
                for char in [':', ',', '-', '!', '?', '.']:
                    title1 = title1.replace(char, ' ')
                    title2 = title2.replace(char, ' ')
                # Remove extra spaces
                title1 = ' '.join(title1.split())
                title2 = ' '.join(title2.split())
                
                # Check if titles are very similar (one contains the other or 90% word overlap)
                if title1 in title2 or title2 in title1:
                    is_duplicate = True
                else:
                    words1 = set(title1.split())
                    words2 = set(title2.split())
                    if len(words1) > 0 and len(words2) > 0:
                        overlap = len(words1 & words2)
                        similarity = overlap / max(len(words1), len(words2))
                        if similarity >= 0.9:
                            is_duplicate = True
            
            if is_duplicate:
                group.append(dict(book2))
                processed.add(book2['id'])
        
        # Only add groups with 2+ books
        if len(group) > 1:
            duplicate_groups.append(group)
    
    db.close()
    
    return render_template('duplicates.html', author=author, duplicate_groups=duplicate_groups)


@app.route('/books/merge', methods=['POST'])
def merge_duplicate_books():
    """Merge multiple books into one, keeping the primary and deleting others"""
    primary_id = request.form.get('primary_id', type=int)
    duplicate_ids = request.form.getlist('duplicate_ids[]', type=int)
    
    if not primary_id or not duplicate_ids:
        return jsonify({'error': 'Missing required fields'}), 400
    
    db = get_db()
    
    try:
        # Get primary book
        primary = db.execute('SELECT * FROM books WHERE id = ?', (primary_id,)).fetchone()
        if not primary:
            db.close()
            return jsonify({'error': 'Primary book not found'}), 404
        
        # Merge data from duplicates into primary
        for dup_id in duplicate_ids:
            if dup_id == primary_id:
                continue
            
            duplicate = db.execute('SELECT * FROM books WHERE id = ?', (dup_id,)).fetchone()
            if not duplicate:
                continue
            
            # Update primary with any missing fields from duplicate
            update_fields = []
            update_values = []
            
            for field in ['subtitle', 'isbn', 'isbn13', 'asin', 'cover_url', 'description', 'series', 'series_position']:
                if not primary[field] and duplicate[field]:
                    update_fields.append(f'{field} = ?')
                    update_values.append(duplicate[field])
            
            # Keep have_it if any duplicate has it
            if duplicate['have_it'] and not primary['have_it']:
                update_fields.append('have_it = ?')
                update_values.append(1)
            
            if update_fields:
                update_values.append(primary_id)
                db.execute(f"UPDATE books SET {', '.join(update_fields)} WHERE id = ?", update_values)
            
            # Delete the duplicate
            db.execute('DELETE FROM books WHERE id = ?', (dup_id,))
        
        db.commit()
        db.close()
        
        return jsonify({'success': True, 'message': f'Merged {len(duplicate_ids)} books'})
        
    except Exception as e:
        db.close()
        return jsonify({'error': str(e)}), 500


@app.route('/books/delete', methods=['POST'])
def delete_books():
    """Mark multiple books as deleted (soft delete)"""
    book_ids = request.form.getlist('book_ids[]', type=int)
    
    if not book_ids:
        return jsonify({'error': 'No books specified'}), 400
    
    db = get_db()
    
    try:
        for book_id in book_ids:
            # Mark as deleted instead of actually deleting
            # This prevents them from being re-added during rescans
            db.execute('UPDATE books SET deleted = 1 WHERE id = ?', (book_id,))
        
        db.commit()
        db.close()
        
        return jsonify({'success': True, 'message': f'Deleted {len(book_ids)} books'})
        
    except Exception as e:
        db.close()
        return jsonify({'error': str(e)}), 500


@app.route('/books/<int:book_id>/toggle-owned', methods=['POST'])
def toggle_book_owned(book_id):
    """Toggle the have_it status of a book"""
    db = get_db()
    book = db.execute('SELECT have_it, author_id FROM books WHERE id = ?', (book_id,)).fetchone()
    
    if not book:
        db.close()
        return jsonify({'error': 'Book not found'}), 404
    
    # Toggle the status
    new_status = 0 if book['have_it'] else 1
    db.execute('UPDATE books SET have_it = ? WHERE id = ?', (new_status, book_id))
    db.commit()
    db.close()
    
    return jsonify({'success': True, 'have_it': new_status})


@app.route('/books/<int:book_id>/search-prowlarr', methods=['POST'])
def search_book_prowlarr(book_id):
    """Search for a specific book via Prowlarr"""
    db = get_db()
    book = db.execute('SELECT * FROM books WHERE id = ?', (book_id,)).fetchone()
    
    if not book:
        db.close()
        return jsonify({'error': 'Book not found'}), 404
    
    author = db.execute('SELECT name FROM authors WHERE id = ?', (book['author_id'],)).fetchone()
    db.close()
    
    search_url = search_prowlarr(book['title'], author['name'])
    
    if search_url:
        return jsonify({'success': True, 'url': search_url})
    else:
        return jsonify({'error': 'Prowlarr search failed'}), 500


@app.route('/scan-all')
def scan_all():
    """Scan all active authors"""
    db = get_db()
    authors = db.execute('SELECT id FROM authors WHERE active = 1').fetchall()
    db.close()
    
    for author in authors:
        scan_author(author['id'])
    
    return redirect(url_for('index'))


@app.route('/unified-search')
def unified_search_page():
    """Unified search page"""
    return render_template('unified_search.html')


@app.route('/api/unified-search', methods=['POST'])
def api_unified_search():
    """API endpoint for unified search"""
    query = request.json.get('query', '').strip()
    
    if not query:
        return jsonify({'error': 'Query required'}), 400
    
    results = unified_search(query)
    
    # Format size for display
    for result in results:
        size_bytes = result.get('size', 0)
        if size_bytes > 1073741824:  # > 1GB
            result['size_display'] = f"{size_bytes / 1073741824:.2f} GB"
        elif size_bytes > 1048576:  # > 1MB
            result['size_display'] = f"{size_bytes / 1048576:.2f} MB"
        elif size_bytes > 1024:  # > 1KB
            result['size_display'] = f"{size_bytes / 1024:.2f} KB"
        else:
            result['size_display'] = f"{size_bytes} B"
    
    return jsonify({'results': results, 'count': len(results)})


@app.route('/api/download', methods=['POST'])
def api_download():
    """API endpoint to send item to download client"""
    data = request.json
    download_url = data.get('download_url', '')
    title = data.get('title', '')
    result_type = data.get('type', 'Torrent')
    
    if not download_url or not title:
        return jsonify({'error': 'Missing required fields'}), 400
    
    success = send_to_download_client(download_url, title, result_type)
    
    if success:
        return jsonify({'success': True, 'message': f'Successfully sent to download client'})
    else:
        return jsonify({'error': 'Failed to send to download client'}), 500


@app.route('/settings', methods=['GET', 'POST'])
def settings():
    """Settings page for API configuration"""
    if request.method == 'POST':
        db = get_db()
        
        settings_to_save = [
            'language_filter',
            'audiobookshelf_url',
            'audiobookshelf_token',
            'prowlarr_url',
            'prowlarr_api_key',
            'jackett_url',
            'jackett_api_key',
            'sabnzbd_url',
            'sabnzbd_api_key',
            'torrent_client_type',
            'torrent_client_url',
            'torrent_client_username',
            'torrent_client_password'
        ]
        
        for setting in settings_to_save:
            value = request.form.get(setting, '')
            db.execute(
                'INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)',
                (setting, value)
            )
        
        db.commit()
        db.close()
        
        flash('Settings saved successfully!', 'success')
        return redirect(url_for('index'))
    
    # GET request - show settings
    db = get_db()
    settings_dict = {}
    settings_rows = db.execute('SELECT key, value FROM settings').fetchall()
    for row in settings_rows:
        settings_dict[row['key']] = row['value']
    db.close()
    
    return render_template('settings.html', settings=settings_dict)


if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)
