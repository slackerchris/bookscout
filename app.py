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
app.config['DATABASE'] = 'bookscout.db'
app.secret_key = os.getenv('SECRET_KEY', 'bookscout-secret-key-change-in-production')

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
    db.commit()
    db.close()


def query_openlibrary(author_name: str) -> List[Dict]:
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
            book = {
                'title': doc.get('title', ''),
                'subtitle': doc.get('subtitle', ''),
                'isbn': doc.get('isbn', [None])[0] if doc.get('isbn') else None,
                'isbn13': next((isbn for isbn in doc.get('isbn', []) if len(isbn) == 13), None),
                'release_date': str(doc.get('first_publish_year', '')),
                'cover_url': f"https://covers.openlibrary.org/b/id/{doc.get('cover_i')}-M.jpg" if doc.get('cover_i') else None,
                'source': 'OpenLibrary'
            }
            if book['title']:
                books.append(book)
        
        return books
    except Exception as e:
        print(f"Error querying OpenLibrary: {e}")
        return []


def query_google_books(author_name: str) -> List[Dict]:
    """Query Google Books API for books by author"""
    try:
        response = requests.get(
            GOOGLE_BOOKS_API,
            params={'q': f'inauthor:"{author_name}"', 'maxResults': 40},
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        
        books = []
        for item in data.get('items', []):
            vol_info = item.get('volumeInfo', {})
            identifiers = {id_info['type']: id_info['identifier'] 
                          for id_info in vol_info.get('industryIdentifiers', [])}
            
            book = {
                'title': vol_info.get('title', ''),
                'subtitle': vol_info.get('subtitle', ''),
                'isbn': identifiers.get('ISBN_10'),
                'isbn13': identifiers.get('ISBN_13'),
                'release_date': vol_info.get('publishedDate', ''),
                'cover_url': vol_info.get('imageLinks', {}).get('thumbnail'),
                'description': vol_info.get('description', ''),
                'source': 'GoogleBooks'
            }
            if book['title']:
                books.append(book)
        
        return books
    except Exception as e:
        print(f"Error querying Google Books: {e}")
        return []


def query_audnexus(author_name: str) -> List[Dict]:
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
            book = {
                'title': item.get('title', ''),
                'subtitle': item.get('subtitle', ''),
                'asin': item.get('asin', ''),
                'release_date': item.get('releaseDate', ''),
                'cover_url': item.get('image'),
                'format': 'audiobook',
                'source': 'Audnexus'
            }
            if book['title']:
                books.append(book)
        
        return books
    except Exception as e:
        print(f"Error querying Audnexus: {e}")
        return []


def merge_books(books_lists: List[List[Dict]]) -> List[Dict]:
    """Merge and deduplicate books from multiple sources"""
    merged = {}
    
    for books in books_lists:
        for book in books:
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


def check_audiobookshelf(book_title: str, author_name: str) -> bool:
    """Check if book exists in Audiobookshelf library"""
    if not AUDIOBOOKSHELF_URL or not AUDIOBOOKSHELF_TOKEN:
        return False
    
    try:
        headers = {'Authorization': f'Bearer {AUDIOBOOKSHELF_TOKEN}'}
        response = requests.get(
            f"{AUDIOBOOKSHELF_URL}/api/libraries",
            headers=headers,
            timeout=10
        )
        
        if response.status_code != 200:
            return False
        
        libraries = response.json().get('libraries', [])
        
        # Search each library
        for library in libraries:
            search_response = requests.get(
                f"{AUDIOBOOKSHELF_URL}/api/libraries/{library['id']}/search",
                params={'q': book_title},
                headers=headers,
                timeout=10
            )
            
            if search_response.status_code == 200:
                results = search_response.json()
                for item in results.get('book', []):
                    if book_title.lower() in item.get('title', '').lower():
                        return True
        
        return False
    except Exception as e:
        print(f"Error checking Audiobookshelf: {e}")
        return False


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
        response = requests.get(
            f"{jackett_url}/api/v2.0/indexers/all/results",
            params={
                'apikey': jackett_key,
                'Query': query,
                'Category[]': [7000, 7020, 8000, 8010]  # Books, Audiobooks, Ebooks
            },
            timeout=30
        )
        
        if response.status_code != 200:
            print(f"Jackett search failed: {response.status_code}")
            return []
        
        data = response.json()
        results = data.get('Results', [])
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
                print(f"Successfully sent to SABnzbd: {title}")
                return True
        
        print(f"Failed to send to SABnzbd: {response.status_code}")
        return False
        
    except Exception as e:
        print(f"Error sending to SABnzbd: {e}")
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
            print("Deluge login failed")
            return False
        
        # Add torrent
        add_response = session.post(
            f"{deluge_url}/json",
            json={
                'method': 'web.add_torrents',
                'params': [[{'path': download_url, 'options': {}}]],
                'id': 2
            },
            timeout=10
        )
        
        if add_response.status_code == 200:
            print(f"Successfully sent to Deluge: {title}")
            return True
        
        print(f"Failed to add torrent to Deluge")
        return False
        
    except Exception as e:
        print(f"Error sending to Deluge: {e}")
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


@app.route('/authors/<int:author_id>/scan')
def scan_author(author_id):
    """Scan for books by specific author"""
    db = get_db()
    author = db.execute('SELECT * FROM authors WHERE id = ?', (author_id,)).fetchone()
    
    if not author:
        db.close()
        return jsonify({'error': 'Author not found'}), 404
    
    author_name = author['name']
    
    # Query all sources
    print(f"Scanning for books by {author_name}...")
    openlibrary_books = query_openlibrary(author_name)
    google_books = query_google_books(author_name)
    audnexus_books = query_audnexus(author_name)
    
    # Merge results
    all_books = merge_books([openlibrary_books, google_books, audnexus_books])
    
    # Check against Audiobookshelf
    for book in all_books:
        book['have_it'] = check_audiobookshelf(book['title'], author_name)
    
    # Store in database
    new_books = 0
    for book in all_books:
        # Check if book already exists
        existing = db.execute(
            'SELECT id FROM books WHERE author_id = ? AND title = ?',
            (author_id, book['title'])
        ).fetchone()
        
        if not existing:
            db.execute('''
                INSERT INTO books (author_id, title, subtitle, isbn, isbn13, asin, 
                                  release_date, format, source, cover_url, description, have_it)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                author_id, book['title'], book.get('subtitle'), book.get('isbn'),
                book.get('isbn13'), book.get('asin'), book.get('release_date'),
                book.get('format'), json.dumps(book['source']) if isinstance(book['source'], list) else book['source'],
                book.get('cover_url'), book.get('description'), book.get('have_it', 0)
            ))
            new_books += 1
    
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
            'SELECT * FROM books WHERE author_id = ? AND have_it = 0 ORDER BY release_date DESC',
            (author_id,)
        ).fetchall()
    else:
        books = db.execute(
            'SELECT * FROM books WHERE author_id = ? ORDER BY release_date DESC',
            (author_id,)
        ).fetchall()
    
    db.close()
    
    return render_template('author.html', author=author, books=books, show_missing_only=show_missing_only)


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
