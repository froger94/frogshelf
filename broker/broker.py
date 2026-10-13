#!/usr/bin/env python3
"""
Frogshelf request broker.

Sits between the Frogshelf app and LazyLibrarian so the LazyLibrarian API key never leaves the server:
- Users are Audiobookshelf accounts. Callers send their Audiobookshelf token; the broker asks
  Audiobookshelf who they are (GET /api/me) and whether they're an admin.
- Admin book requests go straight to LazyLibrarian. Other users' requests wait for admin approval.
- People without an account can ask for one. Once an admin approves, they choose a password and the
  broker creates the Audiobookshelf account with it. Passwords are never stored.
- New book and account requests are announced through ntfy.

Standard library only, so it runs on a stock python image. Configuration comes from a JSON file
(BROKER_CONFIG, default /config/config.json); see config.example.json.
"""

import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = 1
log = logging.getLogger('broker')

# ---------------------------------------------------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------------------------------------------------

DEFAULTS = {
    'port': 8080,
    'db_path': '/data/broker.db',
    'abs_url': '',
    'abs_api_key': '',
    'll_url': '',
    'll_api_key': '',
    'ntfy_url': '',
    'ntfy_token': '',
    'signup_enabled': True,
    # Signup limits: per client address per day, and how many can wait for approval at once
    'signup_per_ip_per_day': 3,
    'signup_max_pending': 25,
    'signup_expiry_days': 14,
    # Permissions for accounts created through signup (merged over Audiobookshelf's "user" defaults)
    'friend_permissions': {
        'download': True,
        'update': False,
        'delete': False,
        'upload': False,
        'createEreader': False,
        'accessAllLibraries': True,
        'accessAllTags': True,
        'accessExplicitContent': True
    }
}


def load_config():
    path = os.environ.get('BROKER_CONFIG', '/config/config.json')
    config = dict(DEFAULTS)
    if os.path.exists(path):
        with open(path) as f:
            config.update(json.load(f))
    for key in ('abs_url', 'll_url', 'ntfy_url'):
        config[key] = (config.get(key) or '').rstrip('/')
    missing = [k for k in ('abs_url', 'abs_api_key', 'll_url', 'll_api_key') if not config.get(k)]
    if missing:
        raise SystemExit(f'Missing config values: {", ".join(missing)} (config file {path})')
    return config


CONFIG = {}

# ---------------------------------------------------------------------------------------------------------------------
# Small HTTP client
# ---------------------------------------------------------------------------------------------------------------------


class UpstreamError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def http_call(method, url, headers=None, body=None, timeout=15):
    """Returns (status, text). Never raises for HTTP error statuses, only for connection problems."""
    data = None
    headers = dict(headers or {})
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode()
            headers.setdefault('Content-Type', 'application/json')
        else:
            data = body.encode() if isinstance(body, str) else body
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UpstreamError(f'Unable to reach {urllib.parse.urlsplit(url).netloc}: {e}')


# ---------------------------------------------------------------------------------------------------------------------
# Audiobookshelf
# ---------------------------------------------------------------------------------------------------------------------

_auth_cache = {}
_auth_lock = threading.Lock()
AUTH_CACHE_SECONDS = 60


def abs_identify(token):
    """Who owns this Audiobookshelf token. Returns {id, username, isAdmin} or None if the token is rejected."""
    key = hashlib.sha256(token.encode()).hexdigest()
    now = time.time()
    with _auth_lock:
        cached = _auth_cache.get(key)
        if cached and now - cached[0] < AUTH_CACHE_SECONDS:
            return cached[1]
    status, text = http_call('GET', f"{CONFIG['abs_url']}/api/me", {'Authorization': f'Bearer {token}'})
    if status in (401, 403):
        return None
    if status != 200:
        raise UpstreamError(f'Audiobookshelf returned HTTP {status}')
    me = json.loads(text)
    if not me.get('isActive', True):
        return None
    user = {'id': me['id'], 'username': me['username'], 'isAdmin': me.get('type') in ('root', 'admin')}
    with _auth_lock:
        _auth_cache[key] = (now, user)
        if len(_auth_cache) > 500:
            for k in [k for k, v in _auth_cache.items() if now - v[0] > AUTH_CACHE_SECONDS]:
                _auth_cache.pop(k, None)
    return user


def abs_admin(method, path, body=None):
    status, text = http_call(method, f"{CONFIG['abs_url']}{path}", {'Authorization': f"Bearer {CONFIG['abs_api_key']}"}, body)
    return status, text


def abs_username_taken(username):
    status, text = abs_admin('GET', '/api/users')
    if status != 200:
        raise UpstreamError(f'Audiobookshelf returned HTTP {status} listing users')
    users = json.loads(text).get('users', [])
    return any(u.get('username', '').lower() == username.lower() for u in users)


def abs_create_user(username, password):
    body = {
        'username': username,
        'password': password,
        'type': 'user',
        # Audiobookshelf creates accounts disabled unless isActive is sent
        'isActive': True,
        'permissions': CONFIG['friend_permissions']
    }
    status, text = abs_admin('POST', '/api/users', body)
    if status != 200:
        raise UpstreamError(text.strip() or f'Audiobookshelf returned HTTP {status}', 400 if status == 400 else 502)


# ---------------------------------------------------------------------------------------------------------------------
# LazyLibrarian (same behaviour as the app's old direct client)
# ---------------------------------------------------------------------------------------------------------------------

AUDIO_STATUS_MAP = {
    'Open': 'library',
    'Have': 'library',
    'Wanted': 'requested',
    'Snatched': 'downloading',
    'Seeding': 'downloading',
    'Processed': 'downloading'
}


def ll_request(cmd, params=None, timeout=15):
    query = urllib.parse.urlencode({**(params or {}), 'cmd': cmd, 'apikey': CONFIG['ll_api_key']})
    status, text = http_call('GET', f"{CONFIG['ll_url']}/api?{query}", timeout=timeout)
    if status >= 400:
        raise UpstreamError(f'LazyLibrarian returned HTTP {status}')
    try:
        data = json.loads(text)
    except ValueError:
        # Action commands answer in plain text ("OK", "Invalid id: ...")
        data = text.strip()
        if not data or data.startswith('<'):
            raise UpstreamError('Unexpected response from LazyLibrarian')
    if isinstance(data, dict) and data.get('Success') is False:
        raise UpstreamError((data.get('Error') or {}).get('Message') or 'LazyLibrarian request failed')
    return data


def ll_command(cmd, params=None, timeout=15):
    data = ll_request(cmd, params, timeout)
    if data != 'OK':
        raise UpstreamError(data if isinstance(data, str) else f'LazyLibrarian {cmd} failed')


def ll_cover_url(book_img):
    if not book_img or 'nocover' in book_img:
        return None
    if re.match(r'^https?://', book_img):
        return book_img
    # Covers are served from LazyLibrarian; friends can't reach it, so the app proxies through /api/covers
    return f"/api/covers/{urllib.parse.quote(book_img.lstrip('/'), safe='/')}"


def ll_books():
    books = ll_request('getAllBooks')
    return books if isinstance(books, list) else []


def ll_raw_statuses(books=None):
    books = ll_books() if books is None else books
    return {b['BookID']: AUDIO_STATUS_MAP[b['AudioStatus']] for b in books if b.get('BookID') and b.get('AudioStatus') in AUDIO_STATUS_MAP}


def effective_status(status, book_id, active_ids):
    # LazyLibrarian can leave a book "Snatched" after its torrent vanished from the download client (e.g. the add
    # failed, or a cleanup removed it). Nothing will ever finish, so show it as wanted, where "Search again" applies.
    if status == 'downloading' and book_id not in active_ids:
        return 'requested'
    return status


def ll_statuses():
    raw = ll_raw_statuses()
    active = {d['BookID'] for d in ll_downloads()} if 'downloading' in raw.values() else set()
    return {book_id: effective_status(status, book_id, active) for book_id, status in raw.items()}


def ll_downloads():
    data = ll_request('getDownloadProgress')
    return [d for d in (data.get('Data') or []) if d.get('AuxInfo') == 'AudioBook' and d.get('BookID')] if isinstance(data, dict) else []


def ll_add_and_search(book_id):
    # addBook returns false both for failures and for books LazyLibrarian already has, so success is
    # checked indirectly: queueBook rejects ids that aren't in its database
    ll_request('addBook', {'id': book_id, 'wait': 1}, timeout=60)
    ll_command('queueBook', {'id': book_id, 'type': 'AudioBook'})
    ll_command('searchBook', {'id': book_id, 'type': 'AudioBook'})


def ll_try_another_release(book_id):
    """Marks the current release failed (web UI endpoint; the API has no equivalent) and searches again.
    Returns 'new', 'same' (the same torrent came back) or 'none'."""
    download = next((d for d in ll_downloads() if d['BookID'] == book_id), None)
    if not download or download.get('rowid') is None:
        raise UpstreamError('This download is no longer active in LazyLibrarian', 409)
    old_id = download.get('DownloadID')
    old_rowid = urllib.parse.quote(str(download['rowid']))
    status, _ = http_call('GET', f"{CONFIG['ll_url']}/markhistory?rowid={old_rowid}")
    if status >= 400:
        raise UpstreamError(f'LazyLibrarian returned HTTP {status}')
    if ll_statuses().get(book_id) != 'requested':
        raise UpstreamError("LazyLibrarian didn't mark the release as failed. If its user accounts are on, mark it failed on its History page instead.")
    result = ll_request('searchBook', {'id': book_id, 'type': 'AudioBook', 'wait': 1}, timeout=120)
    if isinstance(result, str) and result != 'OK':
        raise UpstreamError(result)
    new = ll_clear_stale_history(book_id)
    if not new:
        return 'none'
    return 'same' if old_id and new.get('DownloadID') == old_id else 'new'


def ll_clear_stale_history(book_id):
    """Drops Failed/Aborted history rows for the torrent the book is now downloading, and returns that download.
    A search can pick a torrent that failed before (another indexer's link to it), and with DEL_FAILED on,
    LazyLibrarian's cleanup of those old rows deletes the torrent from the client, killing the live download."""
    download = next((d for d in ll_downloads() if d['BookID'] == book_id), None)
    download_id = (download or {}).get('DownloadID')
    if not download_id:
        return download
    query = urllib.parse.urlencode({'iDisplayStart': 0, 'iDisplayLength': 100, 'sSearch': download_id})
    status, text = http_call('GET', f"{CONFIG['ll_url']}/get_history?{query}")
    try:
        rows = json.loads(text).get('aaData') or [] if status < 400 else []
    except ValueError:
        rows = []
    # Columns: 6 status, 8 download id, 10 rowid
    for row in rows:
        if len(row) > 10 and row[8] == download_id and row[6] in ('Failed', 'Aborted'):
            log.info('Dropping stale %s history row %s for %s', row[6], row[10], book_id)
            http_call('GET', f"{CONFIG['ll_url']}/deletehistory?rowid={urllib.parse.quote(str(row[10]))}")
    return download


def ll_search_and_clear(book_id):
    try:
        ll_request('searchBook', {'id': book_id, 'type': 'AudioBook', 'wait': 1}, timeout=180)
        ll_clear_stale_history(book_id)
    except UpstreamError as e:
        log.warning('Search for %s failed: %s', book_id, e)


# ---------------------------------------------------------------------------------------------------------------------
# ntfy
# ---------------------------------------------------------------------------------------------------------------------


def notify(title, message, tags='books'):
    if not CONFIG.get('ntfy_url'):
        return

    def send():
        headers = {'Title': title.encode('utf-8').decode('latin-1', 'replace'), 'Tags': tags, 'Priority': 'default'}
        if CONFIG.get('ntfy_token'):
            headers['Authorization'] = f"Bearer {CONFIG['ntfy_token']}"
        try:
            status, _ = http_call('POST', CONFIG['ntfy_url'], headers, message, timeout=10)
            if status >= 400:
                log.warning('ntfy returned HTTP %s', status)
        except UpstreamError as e:
            log.warning('ntfy failed: %s', e)

    threading.Thread(target=send, daemon=True).start()


# ---------------------------------------------------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id TEXT NOT NULL,
  title TEXT NOT NULL,
  author TEXT NOT NULL DEFAULT '',
  cover_url TEXT,
  user_id TEXT NOT NULL,
  username TEXT NOT NULL,
  status TEXT NOT NULL,              -- pending | approved | denied | cancelled
  created_at REAL NOT NULL,
  decided_at REAL,
  decided_by TEXT
);
CREATE INDEX IF NOT EXISTS requests_book ON requests(book_id);
CREATE INDEX IF NOT EXISTS requests_user ON requests(user_id);

CREATE TABLE IF NOT EXISTS signups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL,
  display_name TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  token_hash TEXT NOT NULL,          -- the requester's secret; only its hash is kept
  client_ip TEXT NOT NULL,
  status TEXT NOT NULL,              -- pending | approved | denied | completed | expired
  created_at REAL NOT NULL,
  decided_at REAL,
  decided_by TEXT
);
"""

_db_local = threading.local()


def db():
    conn = getattr(_db_local, 'conn', None)
    if conn is None:
        conn = sqlite3.connect(CONFIG['db_path'], timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        _db_local.conn = conn
    return conn


def init_db():
    os.makedirs(os.path.dirname(CONFIG['db_path']) or '.', exist_ok=True)
    db().executescript(SCHEMA)
    db().commit()


def expire_signups():
    cutoff = time.time() - CONFIG['signup_expiry_days'] * 86400
    db().execute("UPDATE signups SET status='expired' WHERE status IN ('pending','approved') AND created_at < ?", (cutoff,))
    db().commit()


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------------------------------------------------------------
# HTTP handling
# ---------------------------------------------------------------------------------------------------------------------


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


ROUTES = []


def route(method, pattern):
    regex = re.compile('^' + re.sub(r'\{(\w+)\}', r'(?P<\1>[^/]+)', pattern) + '$')

    def wrap(fn):
        ROUTES.append((method, regex, fn))
        return fn
    return wrap


USERNAME_RE = re.compile(r'^[A-Za-z0-9_.-]{3,32}$')


def request_row_json(row):
    return {
        'id': row['id'],
        'bookId': row['book_id'],
        'title': row['title'],
        'author': row['author'],
        'coverUrl': row['cover_url'],
        'username': row['username'],
        'status': row['status'],
        'createdAt': int(row['created_at'] * 1000)
    }


@route('GET', '/api/info')
def info(ctx):
    return {'app': 'frogshelf-broker', 'version': VERSION, 'signup': bool(CONFIG['signup_enabled'])}


@route('GET', '/api/me')
def me(ctx):
    return ctx.require_user()


@route('GET', '/api/books/status')
def book_statuses(ctx):
    """Audiobook status per Open Library work id, for the Discover sheet. Includes the caller's pending requests
    (every pending request for admins) so the app can show "Waiting for approval"."""
    user = ctx.require_user()
    statuses = {}
    query = "SELECT book_id FROM requests WHERE status='pending'" + ('' if user['isAdmin'] else ' AND user_id=?')
    for row in db().execute(query, () if user['isAdmin'] else (user['id'],)):
        statuses[row['book_id']] = 'pending'
    statuses.update(ll_statuses())
    return {'statuses': statuses}


@route('POST', '/api/requests')
def create_request(ctx):
    user = ctx.require_user()
    body = ctx.json()
    book_id = str(body.get('bookId') or '').strip()
    title = str(body.get('title') or '').strip()[:300]
    if not re.match(r'^OL\d+W$', book_id) or not title:
        raise ApiError(400, 'bookId (an Open Library work id) and title are required')
    author = str(body.get('author') or '').strip()[:300]
    cover_url = str(body.get('coverUrl') or '').strip()[:500] or None

    existing = ll_statuses().get(book_id)
    if existing:
        return {'status': existing}

    now = time.time()
    if user['isAdmin']:
        ll_add_and_search(book_id)
        db().execute("""INSERT INTO requests (book_id,title,author,cover_url,user_id,username,status,created_at,decided_at,decided_by)
                        VALUES (?,?,?,?,?,?, 'approved', ?, ?, ?)""", (book_id, title, author, cover_url, user['id'], user['username'], now, now, user['username']))
        db().commit()
        return {'status': 'requested'}

    pending = db().execute("SELECT id FROM requests WHERE book_id=? AND user_id=? AND status='pending'", (book_id, user['id'])).fetchone()
    if not pending:
        db().execute("""INSERT INTO requests (book_id,title,author,cover_url,user_id,username,status,created_at)
                        VALUES (?,?,?,?,?,?, 'pending', ?)""", (book_id, title, author, cover_url, user['id'], user['username'], now))
        db().commit()
        notify(f'Book request from {user["username"]}', f'{title}' + (f' by {author}' if author else ''), 'books')
    return {'status': 'pending'}


@route('GET', '/api/requests')
def list_requests(ctx):
    user = ctx.require_user()
    expire_signups()
    books_raw = ll_books()
    raw = ll_raw_statuses(books_raw)
    downloads = ll_downloads() if 'downloading' in raw.values() else []
    active = {d['BookID'] for d in downloads}
    progress = {d['BookID']: max(0, min(100, int(float(d.get('progress') or 0)))) for d in downloads}
    statuses = {book_id: effective_status(status, book_id, active) for book_id, status in raw.items()}

    if not user['isAdmin']:
        rows = db().execute('SELECT * FROM requests WHERE user_id=? ORDER BY created_at DESC LIMIT 200', (user['id'],)).fetchall()
        items = []
        for row in rows:
            item = request_row_json(row)
            if row['status'] == 'approved':
                item['state'] = statuses.get(row['book_id'], 'requested')
            else:
                item['state'] = row['status']
            if item['state'] == 'downloading':
                item['progress'] = progress.get(row['book_id'], 0)
            items.append(item)
        return {'isAdmin': False, 'requests': items}

    pending = [request_row_json(r) for r in db().execute("SELECT * FROM requests WHERE status='pending' ORDER BY created_at")]
    signups = [{
        'id': r['id'], 'username': r['username'], 'displayName': r['display_name'], 'note': r['note'],
        'createdAt': int(r['created_at'] * 1000)
    } for r in db().execute("SELECT * FROM signups WHERE status='pending' ORDER BY created_at")]

    requested_by = {}
    for r in db().execute("SELECT book_id, username FROM requests WHERE status='approved' ORDER BY created_at"):
        requested_by[r['book_id']] = r['username']
    books = []
    for b in books_raw:
        status = statuses.get(b.get('BookID'))
        if not status:
            continue
        books.append({
            'id': b['BookID'],
            'title': b.get('BookName') or '',
            'author': b.get('AuthorName') or '',
            'coverUrl': ll_cover_url(b.get('BookImg')),
            'status': status,
            'libraryDate': b.get('AudioLibrary') or None,
            'progress': progress.get(b['BookID']),
            'requestedBy': requested_by.get(b['BookID'])
        })
    return {'isAdmin': True, 'pending': pending, 'signups': signups, 'books': books}


def get_request_row(request_id):
    row = db().execute('SELECT * FROM requests WHERE id=?', (request_id,)).fetchone()
    if not row:
        raise ApiError(404, 'Request not found')
    return row


@route('POST', '/api/requests/{id}/approve')
def approve_request(ctx, id):
    admin = ctx.require_admin()
    row = get_request_row(id)
    if row['status'] != 'pending':
        raise ApiError(409, f"Request is already {row['status']}")
    ll_add_and_search(row['book_id'])
    now = time.time()
    # Approving one approves every pending request for the same book
    db().execute("UPDATE requests SET status='approved', decided_at=?, decided_by=? WHERE book_id=? AND status='pending'", (now, admin['username'], row['book_id']))
    db().commit()
    return {'status': 'approved'}


@route('POST', '/api/requests/{id}/deny')
def deny_request(ctx, id):
    admin = ctx.require_admin()
    row = get_request_row(id)
    if row['status'] != 'pending':
        raise ApiError(409, f"Request is already {row['status']}")
    db().execute("UPDATE requests SET status='denied', decided_at=?, decided_by=? WHERE id=?", (time.time(), admin['username'], row['id']))
    db().commit()
    return {'status': 'denied'}


@route('POST', '/api/requests/{id}/cancel')
def cancel_request(ctx, id):
    user = ctx.require_user()
    row = get_request_row(id)
    if row['user_id'] != user['id'] and not user['isAdmin']:
        raise ApiError(403, 'Not your request')
    if row['status'] != 'pending':
        raise ApiError(409, f"Request is already {row['status']}")
    db().execute("UPDATE requests SET status='cancelled', decided_at=? WHERE id=?", (time.time(), row['id']))
    db().commit()
    return {'status': 'cancelled'}


def valid_book_id(book_id):
    if not re.match(r'^OL\d+W$', book_id):
        raise ApiError(400, 'Invalid book id')
    return book_id


@route('POST', '/api/books/{book_id}/search')
def search_again(ctx, book_id):
    ctx.require_admin()
    book_id = valid_book_id(book_id)
    # Back to Wanted first: books stuck as Snatched with no download wouldn't be picked up otherwise
    ll_command('queueBook', {'id': book_id, 'type': 'AudioBook'})
    # The search can take a minute; the app picks up the result on its next refresh
    threading.Thread(target=ll_search_and_clear, args=(book_id,), daemon=True).start()
    return {'ok': True}


@route('POST', '/api/books/{book_id}/try-another-release')
def try_another_release(ctx, book_id):
    ctx.require_admin()
    return {'outcome': ll_try_another_release(valid_book_id(book_id))}


@route('POST', '/api/books/{book_id}/cancel')
def cancel_book(ctx, book_id):
    ctx.require_admin()
    ll_command('unqueueBook', {'id': valid_book_id(book_id), 'type': 'AudioBook'})
    return {'ok': True}


@route('POST', '/api/signup')
def signup(ctx):
    if not CONFIG['signup_enabled']:
        raise ApiError(403, 'Account requests are turned off')
    expire_signups()
    body = ctx.json()
    username = str(body.get('username') or '').strip()
    display_name = str(body.get('displayName') or '').strip()[:60]
    note = str(body.get('note') or '').strip()[:300]
    if not USERNAME_RE.match(username):
        raise ApiError(400, 'Usernames are 3-32 letters, numbers, dots, dashes or underscores')

    day_ago = time.time() - 86400
    recent = db().execute('SELECT COUNT(*) FROM signups WHERE client_ip=? AND created_at > ?', (ctx.client_ip, day_ago)).fetchone()[0]
    if recent >= CONFIG['signup_per_ip_per_day']:
        raise ApiError(429, 'Too many account requests from this device today, try again tomorrow')
    waiting = db().execute("SELECT COUNT(*) FROM signups WHERE status='pending'").fetchone()[0]
    if waiting >= CONFIG['signup_max_pending']:
        raise ApiError(429, 'Too many account requests are waiting for approval right now')
    taken = db().execute("SELECT 1 FROM signups WHERE lower(username)=lower(?) AND status IN ('pending','approved')", (username,)).fetchone()
    if taken or abs_username_taken(username):
        raise ApiError(409, 'That username is already taken')

    token = secrets.token_urlsafe(24)
    cur = db().execute("""INSERT INTO signups (username, display_name, note, token_hash, client_ip, status, created_at)
                          VALUES (?,?,?,?,?, 'pending', ?)""", (username, display_name, note, hash_token(token), ctx.client_ip, time.time()))
    db().commit()
    notify(f'Account request: {username}', (display_name + (f' - {note}' if note else '')) or 'No note', 'bust_in_silhouette')
    return {'id': cur.lastrowid, 'token': token, 'status': 'pending'}


def get_signup_for_requester(signup_id, token):
    row = db().execute('SELECT * FROM signups WHERE id=?', (signup_id,)).fetchone()
    if not row or not token or not secrets.compare_digest(row['token_hash'], hash_token(token)):
        raise ApiError(404, 'Account request not found')
    return row


@route('GET', '/api/signup/{id}')
def signup_status(ctx, id):
    expire_signups()
    row = get_signup_for_requester(id, ctx.query.get('token', [''])[0])
    return {'status': row['status'], 'username': row['username']}


@route('POST', '/api/signup/{id}/complete')
def signup_complete(ctx, id):
    body = ctx.json()
    row = get_signup_for_requester(id, str(body.get('token') or ''))
    if row['status'] != 'approved':
        raise ApiError(409, f"Account request is {row['status']}")
    password = str(body.get('password') or '')
    if len(password) < 8:
        raise ApiError(400, 'Passwords need at least 8 characters')
    # The password goes straight to Audiobookshelf and is never stored here
    abs_create_user(row['username'], password)
    db().execute("UPDATE signups SET status='completed' WHERE id=?", (row['id'],))
    db().commit()
    notify(f'New account ready: {row["username"]}', f'{row["username"]} set their password and can sign in now.', 'white_check_mark')
    return {'status': 'completed', 'username': row['username']}


def decide_signup(ctx, signup_id, status):
    admin = ctx.require_admin()
    row = db().execute('SELECT * FROM signups WHERE id=?', (signup_id,)).fetchone()
    if not row:
        raise ApiError(404, 'Account request not found')
    if row['status'] != 'pending':
        raise ApiError(409, f"Account request is already {row['status']}")
    db().execute('UPDATE signups SET status=?, decided_at=?, decided_by=? WHERE id=?', (status, time.time(), admin['username'], row['id']))
    db().commit()
    return {'status': status}


@route('POST', '/api/signups/{id}/approve')
def approve_signup(ctx, id):
    return decide_signup(ctx, id, 'approved')


@route('POST', '/api/signups/{id}/deny')
def deny_signup(ctx, id):
    return decide_signup(ctx, id, 'denied')


COVER_PATH_RE = re.compile(r'^cache/[\w./-]+\.(jpg|jpeg|png|webp)$', re.I)


class Handler(BaseHTTPRequestHandler):
    server_version = 'frogshelf-broker'
    protocol_version = 'HTTP/1.1'

    # -- request context helpers --
    @property
    def client_ip(self):
        return self.client_address[0]

    def json(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length > 64 * 1024:
            raise ApiError(413, 'Request too large')
        raw = self.rfile.read(length) if length else b''
        try:
            data = json.loads(raw or b'{}')
        except ValueError:
            raise ApiError(400, 'Invalid JSON')
        if not isinstance(data, dict):
            raise ApiError(400, 'Expected a JSON object')
        return data

    def require_user(self):
        auth = self.headers.get('Authorization', '')
        if not auth.startswith('Bearer ') or len(auth) < 15:
            raise ApiError(401, 'Sign in to Audiobookshelf first')
        user = abs_identify(auth[7:].strip())
        if not user:
            raise ApiError(401, 'Audiobookshelf session expired')
        return user

    def require_admin(self):
        user = self.require_user()
        if not user['isAdmin']:
            raise ApiError(403, 'Only admins can do that')
        return user

    # -- plumbing --
    def send_json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_cors()
        self.end_headers()
        self.wfile.write(body)

    def send_cors(self):
        # Requests carry a bearer token, never cookies, so allowing any origin is safe (used by the dev server)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors()
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        self.dispatch('GET')

    def do_POST(self):
        self.dispatch('POST')

    def serve_cover(self, path):
        # No sign-in check: the app loads these in <img> tags, which can't send a token. Only image files from
        # LazyLibrarian's cover cache are served; LazyLibrarian itself stays unreachable for friends.
        cover = path[len('/api/covers/'):]
        if not COVER_PATH_RE.match(cover) or '..' in cover:
            raise ApiError(404, 'Not found')
        req = urllib.request.Request(f"{CONFIG['ll_url']}/{cover}")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read(5 * 1024 * 1024)
                content_type = resp.headers.get('Content-Type', 'image/jpeg')
        except (urllib.error.URLError, OSError):
            raise ApiError(404, 'Not found')
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'private, max-age=86400')
        self.send_cors()
        self.end_headers()
        self.wfile.write(data)

    def dispatch(self, method):
        parsed = urllib.parse.urlsplit(self.path)
        self.query = urllib.parse.parse_qs(parsed.query)
        try:
            if method == 'GET' and parsed.path.startswith('/api/covers/'):
                return self.serve_cover(parsed.path)
            for route_method, regex, fn in ROUTES:
                match = regex.match(parsed.path)
                if match and route_method == method:
                    return self.send_json(200, fn(self, **match.groupdict()))
            raise ApiError(404, 'Not found')
        except ApiError as e:
            self.send_json(e.status, {'error': str(e)})
        except UpstreamError as e:
            log.warning('%s %s: %s', method, parsed.path, e)
            self.send_json(e.status, {'error': str(e)})
        except Exception:
            log.exception('%s %s failed', method, parsed.path)
            self.send_json(500, {'error': 'Something went wrong on the server'})

    def log_message(self, fmt, *args):
        # Paths only: query strings can hold signup tokens
        log.info('%s "%s" %s', self.client_ip, self.requestline.split('?')[0], args[1] if len(args) > 1 else '')


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    CONFIG.update(load_config())
    init_db()
    server = ThreadingHTTPServer(('0.0.0.0', int(CONFIG['port'])), Handler)
    log.info('frogshelf-broker v%s listening on port %s', VERSION, CONFIG['port'])
    server.serve_forever()


if __name__ == '__main__':
    main()
