"""Contacts on Calendar's Radicale (humanitylabs-org/wizard-calendar#61): push, pull, phone delete, Calendar's switch
going off (one last read, nothing lost), a reset server, and no grant = nothing changes.
Run: python3 -m unittest tests/test_radicale.py

A small stand-in for Calendar's /service/dav/ (the real one is tested with a real Radicale in wizard-calendar
qa/radicale.py): one address book with etags and a ctag, the grant check and the X-Wizard-Radicale header.
"""
import hashlib
import importlib
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'app'))


class FakeCalendar:
    def __init__(self):
        self.cards, self.on, self.grant, self.version = {}, True, 'g-contacts', 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def send(self, code, body=b'', head=None):
                self.send_response(code)
                for k, v in (head or {}).items():
                    self.send_header(k, v)
                self.send_header('X-Wizard-Radicale', 'on' if outer.on else 'off')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def handle_any(self):
                if self.headers.get('X-Wizard-Grant') != outer.grant:
                    return self.send(403)
                if not outer.on and self.command not in ('GET', 'PROPFIND', 'REPORT'):
                    return self.send(404)
                name = unquote(self.path[len('/service/dav/'):])
                size = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(size).decode() if size else ''
                if self.command == 'PROPFIND':
                    xml = f'<d:multistatus xmlns:d="DAV:" xmlns:cs="http://calendarserver.org/ns/"><d:response><d:href>/service/dav/</d:href><d:propstat><d:prop><cs:getctag>{outer.version}</cs:getctag></d:prop></d:propstat></d:response></d:multistatus>'
                    return self.send(207, xml.encode())
                if self.command == 'REPORT':
                    rows = ''.join(f'<d:response><d:href>/service/dav/{n}</d:href><d:propstat><d:prop><d:getetag>"{e}"</d:getetag>'
                                   f'<card:address-data>{escape(t)}</card:address-data></d:prop></d:propstat></d:response>' for n, (e, t) in outer.cards.items())
                    return self.send(207, f'<d:multistatus xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">{rows}</d:multistatus>'.encode())
                if self.command == 'PUT':
                    have = outer.cards.get(name)
                    if (self.headers.get('If-None-Match') == '*' and have) or (self.headers.get('If-Match') and (not have or self.headers['If-Match'] != f'"{have[0]}"')):
                        return self.send(412)
                    outer.put(name, body)
                    return self.send(201 if not have else 204, head={'ETag': f'"{outer.cards[name][0]}"'})
                if self.command == 'DELETE':
                    outer.cards.pop(name, None)
                    outer.version += 1
                    return self.send(204)
                return self.send(405)
            do_PROPFIND = do_REPORT = do_PUT = do_DELETE = do_GET = handle_any

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]

    def put(self, name, text):
        """Also what a phone does."""
        self.version += 1
        self.cards[name] = (hashlib.sha1((text + str(self.version)).encode()).hexdigest()[:12], text)


class RadicaleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cal = FakeCalendar()
        self.grants = Path(self.tmp.name) / 'grants.json'
        self.grants.write_text(json.dumps({'outgoing': {'wizard-calendar': 'g-contacts'},
                                           'provider_endpoints': {'wizard-calendar': f'http://127.0.0.1:{self.cal.port}'}}))
        os.environ['DATA_DIR'] = self.tmp.name
        os.environ['WIZARD_GRANTS_FILE'] = str(self.grants)
        import server
        self.s = importlib.reload(server)

    def tearDown(self):
        self.cal.server.shutdown()
        os.environ.pop('WIZARD_GRANTS_FILE', None)
        self.tmp.cleanup()

    def names(self):
        return sorted(c['name'] for c in self.s.list_contacts()['contacts'])

    def test_existing_contacts_migrate_and_phone_changes_come_back(self):
        s = self.s
        a = s.add_contact({'name': 'Ana Lima', 'phones': ['+351 900 000 001'], 'accounts': [{'network': 'telegram', 'value': '42'}]})['contact']
        s.add_contact({'name': 'Bo Chen', 'emails': ['bo@example.com']})
        st = s.rad_sync()
        self.assertTrue(st['active'], st)
        self.assertEqual(len(self.cal.cards), 2)
        self.assertIn('FN:Ana Lima', self.cal.cards[a['id'] + '.vcf'][1])
        self.assertIn('X-WIZARD-ACCOUNT;NETWORK=telegram:42', self.cal.cards[a['id'] + '.vcf'][1])
        # The phone adds one, edits Ana and deletes Bo.
        self.cal.put('phone-1.vcf', 'BEGIN:VCARD\r\nVERSION:3.0\r\nUID:phone-1\r\nFN:Cy Phone\r\nTEL:+1 555 0100\r\nEND:VCARD\r\n')
        self.cal.put(a['id'] + '.vcf', self.cal.cards[a['id'] + '.vcf'][1].replace('FN:Ana Lima', 'FN:Ana Lima Souza'))
        bo = next(n for n, (_, t) in self.cal.cards.items() if 'Bo Chen' in t)
        self.cal.cards.pop(bo)
        s.rad_sync()
        self.assertEqual(self.names(), ['Ana Lima Souza', 'Cy Phone'])
        ana = s.get_contact(a['id'])['contact']
        self.assertEqual(ana['accounts'][0]['value'], '42')  # what the phone doesn't know about is kept
        # A change here reaches the phone; nothing is pushed twice.
        s.update_contact(a['id'], {'notes': 'met in Lisbon'})
        version = self.cal.version
        s.rad_sync()
        self.assertIn('NOTE:met in Lisbon', self.cal.cards[a['id'] + '.vcf'][1])
        self.assertEqual(self.cal.version, version + 1)
        s.rad_sync()
        self.assertEqual(self.cal.version, version + 1)
        # A delete here deletes it there.
        cy = next(c for c in s.list_contacts()['contacts'] if c['name'] == 'Cy Phone')
        s.delete_contact(cy['id'])
        s.rad_sync()
        self.assertNotIn('phone-1.vcf', self.cal.cards)

    def test_calendar_switch_off_takes_the_last_changes_and_stops(self):
        s = self.s
        s.add_contact({'name': 'Ana Lima'})
        s.rad_sync()
        self.cal.put('phone-2.vcf', 'BEGIN:VCARD\r\nVERSION:3.0\r\nUID:p2\r\nFN:Last Minute\r\nEND:VCARD\r\n')
        self.cal.on = False
        st = s.rad_sync()
        self.assertFalse(st['active'])
        self.assertEqual(self.names(), ['Ana Lima', 'Last Minute'])  # nothing lost
        s.add_contact({'name': 'While Off'})
        before = dict(self.cal.cards)
        s.rad_sync()
        self.assertEqual(self.cal.cards, before)  # off: nothing is sent
        self.cal.on = True
        s.rad_sync()
        self.assertTrue(any('While Off' in t for _, t in self.cal.cards.values()))

    def test_no_grant_or_no_calendar_changes_nothing(self):
        s = self.s
        s.add_contact({'name': 'Ana Lima'})
        self.grants.write_text('{}')
        st = s.rad_sync()
        self.assertFalse(st['active'])
        self.assertEqual(self.cal.cards, {})
        self.grants.write_text(json.dumps({'outgoing': {'wizard-calendar': 'wrong'}, 'provider_endpoints': {'wizard-calendar': f'http://127.0.0.1:{self.cal.port}'}}))
        st = s.rad_sync()
        self.assertFalse(st['active'])
        self.assertTrue(st['error'])
        self.assertEqual(self.names(), ['Ana Lima'])
        self.assertFalse(s.sync_status()['radicale']['active'])

    def test_a_reset_server_never_empties_contacts(self):
        s = self.s
        for n in ('A One', 'B Two', 'C Three'):
            s.add_contact({'name': n})
        s.rad_sync()
        self.cal.cards.clear()
        self.cal.version += 1
        s.rad_sync()
        self.assertEqual(self.names(), ['A One', 'B Two', 'C Three'])
        self.assertEqual(len(self.cal.cards), 3)


if __name__ == '__main__':
    unittest.main()
