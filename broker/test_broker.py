"""
Tests for broker.py against in-process fakes of Audiobookshelf, LazyLibrarian and ntfy.
Run with: python3 -m unittest broker/test_broker.py
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(__file__))
import broker  # noqa: E402

ADMIN_TOKEN = 'admin-token-123456'
FRIEND_TOKEN = 'friend-token-123456'
OTHER_TOKEN = 'other-token-1234567'
ABS_KEY = 'abs-api-key-123456'
LL_KEY = 'll-key'


class Fakes:
    def __init__(self):
        self.abs_users = [{'id': 'u-admin', 'username': 'frogbooks'}, {'id': 'u-friend', 'username': 'dave'}]
        self.created_users = []
        self.ll_calls = []
        self.ll_books = {}            # book id -> AudioStatus
        self.ll_downloads = []
        self.ntfy = []
        self.markhistory = []


def serve(handler_cls):
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def make_abs(fakes):
    tokens = {
        ADMIN_TOKEN: {'id': 'u-admin', 'username': 'frogbooks', 'type': 'root', 'isActive': True},
        FRIEND_TOKEN: {'id': 'u-friend', 'username': 'dave', 'type': 'user', 'isActive': True},
        OTHER_TOKEN: {'id': 'u-other', 'username': 'erin', 'type': 'user', 'isActive': True},
        ABS_KEY: {'id': 'u-admin', 'username': 'frogbooks', 'type': 'root', 'isActive': True}
    }

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, status, body):
            data = (json.dumps(body) if not isinstance(body, str) else body).encode()
            self.send_response(status)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def caller(self):
            return tokens.get(self.headers.get('Authorization', '')[7:])

        def do_GET(self):
            user = self.caller()
            if not user:
                return self.reply(401, 'Unauthorized')
            if self.path == '/api/me':
                return self.reply(200, user)
            if self.path == '/api/users':
                return self.reply(200, {'users': fakes.abs_users})
            self.reply(404, 'nope')

        def do_POST(self):
            user = self.caller()
            if not user or user['type'] not in ('root', 'admin'):
                return self.reply(403, 'Forbidden')
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.path == '/api/users':
                if any(u['username'].lower() == body['username'].lower() for u in fakes.abs_users):
                    return self.reply(400, 'Username already taken')
                fakes.created_users.append(body)
                fakes.abs_users.append({'id': 'u-new', 'username': body['username']})
                return self.reply(200, {'user': {'username': body['username']}})
            self.reply(404, 'nope')

    return serve(H)


def make_ll(fakes):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, status, body, content_type='text/html'):
            data = (json.dumps(body) if not isinstance(body, (str, bytes)) else body)
            data = data.encode() if isinstance(data, str) else data
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
            if parsed.path == '/markhistory':
                fakes.markhistory.append(q['rowid'])
                for d in fakes.ll_downloads:
                    if str(d['rowid']) == q['rowid']:
                        fakes.ll_books[d['BookID']] = 'Wanted'
                fakes.ll_downloads = [d for d in fakes.ll_downloads if str(d['rowid']) != q['rowid']]
                return self.reply(200, '')
            if parsed.path.startswith('/cache/'):
                return self.reply(200, b'\x89PNGfake', 'image/png')
            if parsed.path != '/api' or q.get('apikey') != LL_KEY:
                return self.reply(200, {'Success': False, 'Error': {'Code': 401, 'Message': 'Incorrect API key'}})
            cmd = q.pop('cmd')
            q.pop('apikey')
            fakes.ll_calls.append((cmd, q))
            if cmd == 'getAllBooks':
                return self.reply(200, [{'BookID': k, 'BookName': f'Book {k}', 'AuthorName': 'Author', 'BookImg': f'cache/book/{k}.jpg',
                                         'AudioStatus': v, 'AudioLibrary': None} for k, v in fakes.ll_books.items()])
            if cmd == 'getDownloadProgress':
                return self.reply(200, {'Success': True, 'Data': fakes.ll_downloads})
            if cmd == 'addBook':
                fakes.ll_books.setdefault(q['id'], 'Skipped')
                return self.reply(200, 'true')
            if cmd == 'queueBook':
                if q['id'] not in fakes.ll_books:
                    return self.reply(200, f"Invalid id: {q['id']}")
                fakes.ll_books[q['id']] = 'Wanted'
                return self.reply(200, 'OK')
            if cmd == 'searchBook':
                if q.get('wait') and fakes.next_release:
                    fakes.ll_books[q['id']] = 'Snatched'
                    fakes.ll_downloads.append({'BookID': q['id'], 'AuxInfo': 'AudioBook', 'rowid': 99, 'DownloadID': fakes.next_release, 'progress': 0})
                return self.reply(200, 'OK')
            if cmd == 'unqueueBook':
                fakes.ll_books[q['id']] = 'Skipped'
                return self.reply(200, 'OK')
            self.reply(200, 'OK')

    return serve(H)


def make_ntfy(fakes):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            fakes.ntfy.append((self.headers.get('Title'), self.rfile.read(int(self.headers['Content-Length'])).decode()))
            self.send_response(200)
            self.send_header('Content-Length', '0')
            self.end_headers()

    return serve(H)


class BrokerTest(unittest.TestCase):
    def setUp(self):
        self.fakes = Fakes()
        self.fakes.next_release = None
        self.abs = make_abs(self.fakes)
        self.ll = make_ll(self.fakes)
        self.ntfy = make_ntfy(self.fakes)
        self.tmp = tempfile.TemporaryDirectory()
        broker.CONFIG.clear()
        broker.CONFIG.update(broker.DEFAULTS)
        broker.CONFIG.update({
            'db_path': os.path.join(self.tmp.name, 'broker.db'),
            'abs_url': f'http://127.0.0.1:{self.abs.server_port}',
            'abs_api_key': ABS_KEY,
            'll_url': f'http://127.0.0.1:{self.ll.server_port}',
            'll_api_key': LL_KEY,
            'ntfy_url': f'http://127.0.0.1:{self.ntfy.server_port}/topic'
        })
        broker._auth_cache.clear()
        broker._db_local.conn = None
        broker.init_db()
        self.server = serve(broker.Handler)
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        for s in (self.server, self.abs, self.ll, self.ntfy):
            s.shutdown()
        self.tmp.cleanup()

    def call(self, method, path, body=None, token=None):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = f'Bearer {token}'
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read() or b'{}')
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b'{}')

    def wait_ntfy(self, count):
        for _ in range(50):
            if len(self.fakes.ntfy) >= count:
                return
            time.sleep(0.05)
        self.fail(f'expected {count} ntfy messages, got {self.fakes.ntfy}')

    # -- identity --
    def test_info_and_me(self):
        self.assertEqual(self.call('GET', '/api/info')[1]['app'], 'frogshelf-broker')
        self.assertEqual(self.call('GET', '/api/me', token=ADMIN_TOKEN)[1], {'id': 'u-admin', 'username': 'frogbooks', 'isAdmin': True})
        self.assertEqual(self.call('GET', '/api/me', token=FRIEND_TOKEN)[1]['isAdmin'], False)
        self.assertEqual(self.call('GET', '/api/me')[0], 401)
        self.assertEqual(self.call('GET', '/api/me', token='bogus-token-xxxxx')[0], 401)

    # -- book requests --
    def test_friend_request_waits_then_admin_approves(self):
        book = {'bookId': 'OL24156W', 'title': 'Jekyll and Hyde', 'author': 'Stevenson'}
        status, body = self.call('POST', '/api/requests', book, FRIEND_TOKEN)
        self.assertEqual((status, body), (200, {'status': 'pending'}))
        self.assertFalse(any(c[0] in ('addBook', 'queueBook', 'searchBook') for c in self.fakes.ll_calls), 'friend request must not reach LazyLibrarian')
        self.wait_ntfy(1)
        self.assertIn('dave', self.fakes.ntfy[0][0])

        # Repeating the request doesn't duplicate it or notify again
        self.call('POST', '/api/requests', book, FRIEND_TOKEN)
        time.sleep(0.2)
        self.assertEqual(len(self.fakes.ntfy), 1)

        self.assertEqual(self.call('GET', '/api/books/status', token=FRIEND_TOKEN)[1]['statuses'], {'OL24156W': 'pending'})
        self.assertEqual(self.call('GET', '/api/books/status', token=OTHER_TOKEN)[1]['statuses'], {}, 'others do not see your pending request')

        admin_view = self.call('GET', '/api/requests', token=ADMIN_TOKEN)[1]
        self.assertEqual(len(admin_view['pending']), 1)
        request_id = admin_view['pending'][0]['id']

        self.assertEqual(self.call('POST', f'/api/requests/{request_id}/approve', token=FRIEND_TOKEN)[0], 403)
        self.assertEqual(self.call('POST', f'/api/requests/{request_id}/approve', token=ADMIN_TOKEN)[1], {'status': 'approved'})
        cmds = [c[0] for c in self.fakes.ll_calls]
        self.assertEqual([c for c in cmds if c in ('addBook', 'queueBook', 'searchBook')], ['addBook', 'queueBook', 'searchBook'])
        self.assertEqual(dict(self.fakes.ll_calls[cmds.index('queueBook')][1]), {'id': 'OL24156W', 'type': 'AudioBook'})

        mine = self.call('GET', '/api/requests', token=FRIEND_TOKEN)[1]
        self.assertEqual(mine['requests'][0]['state'], 'requested')
        self.assertEqual(self.call('POST', f'/api/requests/{request_id}/approve', token=ADMIN_TOKEN)[0], 409)

    def test_admin_request_goes_straight_through(self):
        status, body = self.call('POST', '/api/requests', {'bookId': 'OL1W', 'title': 'Admin book'}, ADMIN_TOKEN)
        self.assertEqual(body, {'status': 'requested'})
        self.assertIn('searchBook', [c[0] for c in self.fakes.ll_calls])
        time.sleep(0.2)
        self.assertEqual(self.fakes.ntfy, [], 'admins are not notified about their own requests')

    def test_request_for_book_already_in_library(self):
        self.fakes.ll_books['OL5W'] = 'Open'
        self.assertEqual(self.call('POST', '/api/requests', {'bookId': 'OL5W', 'title': 'Owned'}, FRIEND_TOKEN)[1], {'status': 'library'})

    def test_deny_and_cancel(self):
        self.call('POST', '/api/requests', {'bookId': 'OL7W', 'title': 'Seven'}, FRIEND_TOKEN)
        self.call('POST', '/api/requests', {'bookId': 'OL8W', 'title': 'Eight'}, FRIEND_TOKEN)
        pending = {r['bookId']: r['id'] for r in self.call('GET', '/api/requests', token=ADMIN_TOKEN)[1]['pending']}
        self.assertEqual(self.call('POST', f"/api/requests/{pending['OL7W']}/deny", token=ADMIN_TOKEN)[1], {'status': 'denied'})
        self.assertEqual(self.call('POST', f"/api/requests/{pending['OL8W']}/cancel", token=OTHER_TOKEN)[0], 403)
        self.assertEqual(self.call('POST', f"/api/requests/{pending['OL8W']}/cancel", token=FRIEND_TOKEN)[1], {'status': 'cancelled'})
        states = {r['bookId']: r['state'] for r in self.call('GET', '/api/requests', token=FRIEND_TOKEN)[1]['requests']}
        self.assertEqual(states, {'OL7W': 'denied', 'OL8W': 'cancelled'})

    def test_bad_book_id(self):
        self.assertEqual(self.call('POST', '/api/requests', {'bookId': '../etc', 'title': 'x'}, FRIEND_TOKEN)[0], 400)

    # -- admin-only LazyLibrarian actions --
    def test_admin_actions_are_admin_only(self):
        for path in ('/api/books/OL1W/search', '/api/books/OL1W/try-another-release', '/api/books/OL1W/cancel'):
            self.assertEqual(self.call('POST', path, token=FRIEND_TOKEN)[0], 403, path)

    def test_try_another_release_reports_same_torrent(self):
        self.fakes.ll_books['OL9W'] = 'Snatched'
        self.fakes.ll_downloads = [{'BookID': 'OL9W', 'AuxInfo': 'AudioBook', 'rowid': 21, 'DownloadID': 'deadbeef', 'progress': 0}]
        self.fakes.next_release = 'deadbeef'
        self.assertEqual(self.call('POST', '/api/books/OL9W/try-another-release', token=ADMIN_TOKEN)[1], {'outcome': 'same'})
        self.assertEqual(self.fakes.markhistory, ['21'])

    def test_try_another_release_finds_new_torrent(self):
        self.fakes.ll_books['OL9W'] = 'Snatched'
        self.fakes.ll_downloads = [{'BookID': 'OL9W', 'AuxInfo': 'AudioBook', 'rowid': 21, 'DownloadID': 'deadbeef', 'progress': 0}]
        self.fakes.next_release = 'cafef00d'
        self.assertEqual(self.call('POST', '/api/books/OL9W/try-another-release', token=ADMIN_TOKEN)[1], {'outcome': 'new'})

    def test_admin_view_lists_books_with_progress_and_requester(self):
        self.call('POST', '/api/requests', {'bookId': 'OL3W', 'title': 'Three'}, FRIEND_TOKEN)
        pending_id = self.call('GET', '/api/requests', token=ADMIN_TOKEN)[1]['pending'][0]['id']
        self.call('POST', f'/api/requests/{pending_id}/approve', token=ADMIN_TOKEN)
        self.fakes.ll_books['OL3W'] = 'Snatched'
        self.fakes.ll_downloads = [{'BookID': 'OL3W', 'AuxInfo': 'AudioBook', 'rowid': 5, 'DownloadID': 'x', 'progress': '42'}]
        books = self.call('GET', '/api/requests', token=ADMIN_TOKEN)[1]['books']
        self.assertEqual(books[0]['status'], 'downloading')
        self.assertEqual(books[0]['progress'], 42)
        self.assertEqual(books[0]['requestedBy'], 'dave')
        self.assertEqual(books[0]['coverUrl'], '/api/covers/cache/book/OL3W.jpg')

    def test_cover_proxy(self):
        req = urllib.request.urlopen(self.base + '/api/covers/cache/book/OL3W.jpg', timeout=5)
        self.assertEqual(req.read(), b'\x89PNGfake')
        for bad in ('/api/covers/../config.ini', '/api/covers/cache/../../etc/passwd.jpg', '/api/covers/api?cmd=x'):
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(self.base + bad, timeout=5)

    # -- account signup --
    def test_signup_flow(self):
        status, body = self.call('POST', '/api/signup', {'username': 'newfriend', 'displayName': 'New Friend', 'note': 'hi'})
        self.assertEqual(status, 200)
        signup_id, token = body['id'], body['token']
        self.wait_ntfy(1)
        self.assertIn('newfriend', self.fakes.ntfy[0][0])

        self.assertEqual(self.call('GET', f'/api/signup/{signup_id}?token={token}')[1]['status'], 'pending')
        self.assertEqual(self.call('GET', f'/api/signup/{signup_id}?token=wrong')[0], 404)
        # Can't set a password before approval
        self.assertEqual(self.call('POST', f'/api/signup/{signup_id}/complete', {'token': token, 'password': 'longenough'})[0], 409)

        admin_view = self.call('GET', '/api/requests', token=ADMIN_TOKEN)[1]
        self.assertEqual([s['username'] for s in admin_view['signups']], ['newfriend'])
        self.assertEqual(self.call('POST', f'/api/signups/{signup_id}/approve', token=FRIEND_TOKEN)[0], 403)
        self.assertEqual(self.call('POST', f'/api/signups/{signup_id}/approve', token=ADMIN_TOKEN)[1], {'status': 'approved'})
        self.assertEqual(self.call('GET', f'/api/signup/{signup_id}?token={token}')[1]['status'], 'approved')

        self.assertEqual(self.call('POST', f'/api/signup/{signup_id}/complete', {'token': token, 'password': 'short'})[0], 400)
        status, body = self.call('POST', f'/api/signup/{signup_id}/complete', {'token': token, 'password': 'longenough'})
        self.assertEqual((status, body), (200, {'status': 'completed', 'username': 'newfriend'}))
        created = self.fakes.created_users[0]
        self.assertEqual(created['username'], 'newfriend')
        self.assertEqual(created['type'], 'user')
        self.assertIs(created['isActive'], True)
        self.assertIs(created['permissions']['delete'], False)
        self.assertIs(created['permissions']['upload'], False)

        # Password isn't kept anywhere in the database
        dump = '\n'.join(broker.db().iterdump())
        self.assertNotIn('longenough', dump)
        self.assertNotIn(token, dump)
        # Completed signups can't be reused
        self.assertEqual(self.call('POST', f'/api/signup/{signup_id}/complete', {'token': token, 'password': 'longenough2'})[0], 409)

    def test_signup_validation_and_limits(self):
        self.assertEqual(self.call('POST', '/api/signup', {'username': 'a b'})[0], 400)
        self.assertEqual(self.call('POST', '/api/signup', {'username': 'Dave'})[0], 409, 'existing Audiobookshelf user')
        self.assertEqual(self.call('POST', '/api/signup', {'username': 'first'})[0], 200)
        self.assertEqual(self.call('POST', '/api/signup', {'username': 'FIRST'})[0], 409, 'pending username')
        self.call('POST', '/api/signup', {'username': 'second'})
        self.call('POST', '/api/signup', {'username': 'third'})
        self.assertEqual(self.call('POST', '/api/signup', {'username': 'fourth'})[0], 429, 'per-address daily limit')

    def test_denied_signup_cannot_complete(self):
        body = self.call('POST', '/api/signup', {'username': 'nope'})[1]
        self.call('POST', f"/api/signups/{body['id']}/deny", token=ADMIN_TOKEN)
        self.assertEqual(self.call('POST', f"/api/signup/{body['id']}/complete", {'token': body['token'], 'password': 'longenough'})[0], 409)
        self.assertEqual(self.fakes.created_users, [])

    def test_signup_disabled(self):
        broker.CONFIG['signup_enabled'] = False
        self.assertEqual(self.call('POST', '/api/signup', {'username': 'someone'})[0], 403)


if __name__ == '__main__':
    unittest.main()
