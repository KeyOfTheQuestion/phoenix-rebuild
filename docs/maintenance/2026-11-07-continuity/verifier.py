"""Frozen, stdlib-only v2-to-v2 continuity checks using the supplied HTTP client."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time
from urllib.parse import quote


class FixtureFailure(Exception):
    """Only verifier-authored messages from this exception enter public evidence."""


def ensure(condition, message):
    if not condition:
        raise FixtureFailure(message)


def same_json(actual, expected, message):
    # JSON booleans, integers and floats compare differently from Python values.
    ensure(json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True), message)


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def utc_timestamp(value):
    if not isinstance(value, str):
        return False
    try:
        instant = datetime.fromisoformat(value[:-1] + '+00:00' if value.endswith('Z') else value)
        return instant.utcoffset() == timedelta(0)
    except ValueError:
        return False


def rows(database):
    """Read one committed snapshot, independent of physical column order or indexes."""
    queries = {
        'inboxes': 'SELECT id,name,token_sha256,created_at FROM inboxes',
        'events': 'SELECT id,inbox_id,external_id,received_at,headers_json,body,body_sha256 FROM events',
        'policies': 'SELECT inbox_id,max_age_seconds FROM retention_policies',
    }
    with closing(sqlite3.connect(database)) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        result = {'version': db.execute('PRAGMA user_version').fetchone()[0]}
        for table, query in queries.items():
            result[table] = {row[0]: dict(row) for row in db.execute(query)}
        return result


def expect(subject, method, route, status=200, body=None, headers=None, admin=True):
    actual, data, response_headers = subject.request(method, route, body, headers, admin)
    ensure(actual == status, '%s expected HTTP %d, received %d' % (method, status, actual))
    media = next((v for k, v in response_headers.items() if k.lower() == 'content-type'), '')
    ensure(media.partition(';')[0].strip().lower() == 'application/json', 'Expected JSON content type')
    if status >= 400:
        ensure(isinstance(data, dict) and isinstance(data.get('error'), str), 'Expected a JSON error string')
    return data


def inbox_route(inbox, endpoint):
    return '/api/inboxes/%s/%s' % (quote(inbox['id'], safe=''), endpoint)


def hook(inbox, token=None):
    return '/hooks/%s/%s' % (quote(inbox['id'], safe=''), quote(inbox['token'] if token is None else token, safe=''))


def detail(subject, identifier, status=200):
    return expect(subject, 'GET', '/api/events/' + quote(identifier, safe=''), status)


def schema_two(subject):
    same_json(expect(subject, 'GET', '/health', admin=False), {'status': 'ok', 'schema_version': 2},
              'Expected a schema version 2 service')


class Fixture:
    policies = {'active': 3600, 'disabled': None, 'longer': 86400}

    def __init__(self, predecessor, candidate):
        self.predecessor = predecessor
        self.candidate = candidate
        self.database = predecessor.database
        self.inboxes = {}
        self.events = {}
        self.before = None

    def new_event(self, subject, role, key, body):
        inbox = self.inboxes[role]
        headers = {'x-webhook-id': key, 'x-event-type': 'continuity.' + role,
                   'content-type': 'text/plain; charset=utf-8'}
        received = expect(subject, 'POST', hook(inbox), 201, body, headers, admin=False)
        identifier = received.get('id') if isinstance(received, dict) else None
        ensure(isinstance(identifier, str) and identifier and identifier not in self.events,
               'Expected a distinct event ID')
        same_json(received, {'id': identifier, 'duplicate': False}, 'First delivery must create a new event')
        response = detail(subject, identifier)
        timestamp = response.get('received_at') if isinstance(response, dict) else None
        ensure(utc_timestamp(timestamp), 'Expected a UTC event timestamp')
        event = {'id': identifier, 'inbox_id': inbox['id'], 'external_id': key,
                 'received_at': timestamp, 'headers': headers, 'body': body, 'body_sha256': digest(body)}
        same_json(response, event, 'Delivery details differ from the HTTP input')
        self.events[identifier] = event
        return identifier

    def assert_policies(self, subject):
        for role, age in self.policies.items():
            same_json(expect(subject, 'GET', inbox_route(self.inboxes[role], 'retention')),
                      {'max_age_seconds': age}, 'Persisted retention response changed')

    def assert_details(self, subject):
        listed = [{k: v for k, v in inbox.items() if k != 'token'} for inbox in self.inboxes.values()]
        listed.sort(key=lambda item: (item['created_at'], item['id']), reverse=True)
        same_json(expect(subject, 'GET', '/api/inboxes'), {'inboxes': listed}, 'Inbox listing changed')
        for inbox in self.inboxes.values():
            summaries = [{k: v for k, v in event.items() if k not in ('body', 'headers')}
                         for event in self.events.values() if event['inbox_id'] == inbox['id']]
            summaries.sort(key=lambda item: (item['received_at'], item['id']), reverse=True)
            same_json(expect(subject, 'GET', inbox_route(inbox, 'events')), {'events': summaries},
                      'Event listing changed or hid an event')
        for identifier, event in self.events.items():
            same_json(detail(subject, identifier), event, 'Event detail changed')

    def assert_retries(self, subject):
        for inbox in self.inboxes.values():
            for event in self.events.values():
                if event['inbox_id'] != inbox['id']:
                    continue
                headers = {'X-Webhook-Id': event['external_id']}
                duplicate = expect(subject, 'POST', hook(inbox), 200, event['body'], headers, admin=False)
                same_json(duplicate, {'id': event['id'], 'duplicate': True}, 'Duplicate lost its original event ID')
                expect(subject, 'POST', hook(inbox), 409, 'conflicting delivery', headers, admin=False)
            expect(subject, 'POST', hook(inbox, 'invalid-fixture-token'), 404, 'invalid credential', admin=False)

    def assert_storage(self, snapshot):
        ensure(snapshot['version'] == 2, 'Expected SQLite user_version 2')
        inboxes = {i['id']: {'id': i['id'], 'name': i['name'], 'created_at': i['created_at'],
                             'token_sha256': digest(i['token'])} for i in self.inboxes.values()}
        ensure(snapshot['inboxes'] == inboxes, 'Stored inbox rows differ from the fixture')
        policies = {self.inboxes[role]['id']: {'inbox_id': self.inboxes[role]['id'], 'max_age_seconds': age}
                    for role, age in self.policies.items() if age is not None}
        ensure(snapshot['policies'] == policies, 'Stored retention rows differ from the fixture')
        ensure(snapshot['events'].keys() == self.events.keys(), 'Stored event IDs differ from the fixture')
        for identifier, expected in self.events.items():
            row = dict(snapshot['events'][identifier])
            row['headers'] = json.loads(row.pop('headers_json'))
            same_json(row, expected, 'Stored event row differs from the fixture')

    def seed(self):
        old = self.predecessor
        old.start()
        schema_two(old)
        for role, age in self.policies.items():
            name = 'Continuity ' + role
            inbox = expect(old, 'POST', '/api/inboxes', 201, {'name': name})
            ensure(isinstance(inbox, dict) and set(inbox) == {'id', 'name', 'created_at', 'token'}
                   and isinstance(inbox['id'], str) and inbox['id'] and inbox['name'] == name
                   and isinstance(inbox['token'], str) and inbox['token'] and utc_timestamp(inbox['created_at']),
                   'Expected complete predecessor inbox credentials and metadata')
            ensure(all(i['id'] != inbox['id'] and i['token'] != inbox['token'] for i in self.inboxes.values()),
                   'Expected distinct predecessor inboxes and credentials')
            self.inboxes[role] = inbox
            for value in ((3600, None) if role == 'disabled' else (age,)):
                policy = {'max_age_seconds': value}
                same_json(expect(old, 'PUT', inbox_route(inbox, 'retention'), body=policy), policy,
                          'Predecessor could not set the requested retention policy')
        # Insert the younger active row first. An insertion-order limit must not pass V06.
        younger = self.new_event(old, 'active', 'second', ' second delivery 二 🔥\n')
        self.oldest = self.new_event(old, 'active', 'shared', ' oldest delivery 一 🔥\n')
        disabled = self.new_event(old, 'disabled', 'shared', ' disabled delivery 三 🔥\n')
        longer = self.new_event(old, 'longer', 'shared', ' longer delivery 四 🔥\n')
        old.stop()
        self.assert_storage(rows(self.database))
        # Only received_at is edited, while stopped. All four ages share one clock reading;
        # both active events are eligible, and the other two are younger than one day.
        anchor = datetime.now(timezone.utc).replace(microsecond=0)
        with closing(sqlite3.connect(self.database)) as db, db:
            for identifier, hours, use_z in ((younger, 2, False), (self.oldest, 4, True),
                                             (disabled, 4, True), (longer, 4, False)):
                stamp = (anchor - timedelta(hours=hours)).isoformat()
                if use_z:
                    stamp = stamp.replace('+00:00', 'Z')
                changed = db.execute('UPDATE events SET received_at=? WHERE id=?', (stamp, identifier))
                ensure(changed.rowcount == 1, 'Synthetic aging did not update the intended event')
                self.events[identifier]['received_at'] = stamp
        aged = rows(self.database)
        self.assert_storage(aged)
        old.start()
        schema_two(old)
        self.assert_policies(old)
        self.assert_details(old)
        self.assert_retries(old)
        old.stop()
        self.before = rows(self.database)
        ensure(self.before == aged, 'Predecessor restart or HTTP reads/retries changed the aged fixture')

    def boot(self):
        self.candidate.start()
        schema_two(self.candidate)
        ensure(rows(self.database) == self.before, 'Candidate startup changed schema version or predecessor rows')

    def policies_survive(self):
        self.assert_policies(self.candidate)

    def details_survive(self):
        self.assert_details(self.candidate)
        self.assert_retries(self.candidate)
        ensure(rows(self.database) == self.before, 'Candidate HTTP reads or retries changed predecessor rows')

    def restart(self):
        self.candidate.stop()
        self.boot()
        self.policies_survive()
        self.details_survive()

    def expire_one(self):
        subject = self.candidate
        result = expect(subject, 'POST', inbox_route(self.inboxes['active'], 'expire'), body={'limit': 1})
        same_json(result, {'deleted': 1}, 'Bounded expiry must report one deletion')
        expected_events = dict(self.before['events'])
        del expected_events[self.oldest]
        self.after_expiry = dict(self.before, events=expected_events)
        ensure(rows(self.database) == self.after_expiry, 'Expiry must delete only the oldest active row')
        del self.events[self.oldest]
        detail(subject, self.oldest, 404)
        self.assert_details(subject)

    def protected(self):
        for role in ('disabled', 'longer'):
            result = expect(self.candidate, 'POST', inbox_route(self.inboxes[role], 'expire'))
            same_json(result, {'deleted': 0}, 'Protected inbox must report zero deletions')
        self.policies_survive()
        self.assert_details(self.candidate)
        ensure(rows(self.database) == self.after_expiry, 'Protected expiry or reads changed surviving rows')

    def replace(self):
        subject = self.candidate
        new = self.new_event(subject, 'active', 'shared', ' replacement delivery 五 🔥\n')
        ensure(new != self.oldest, 'Replacement reused the expired event ID')
        written = rows(self.database)
        self.assert_storage(written)
        survivors = dict(written['events'])
        del survivors[new]
        ensure(dict(written, events=survivors) == self.after_expiry, 'Replacement changed a surviving row')
        self.assert_retries(subject)
        subject.stop()
        subject.start()
        schema_two(subject)
        self.policies_survive()
        self.assert_details(subject)
        self.assert_retries(subject)
        detail(subject, self.oldest, 404)
        ensure(rows(self.database) == written, 'Restart or retries changed the replacement or surviving rows')


def evaluate(directory, client):
    """Run checks in order on frozen trusted sources; stop on the first unmet prerequisite."""
    started = time.monotonic()
    report = {'started_at': datetime.now(timezone.utc).isoformat(), 'execution_runtime': client.runtime_identity(),
              'expected_total': 8, 'checks': []}
    directory = Path(directory)
    with tempfile.TemporaryDirectory(prefix='phoenix-continuity-db-') as temporary:
        old = client.Subject(directory / 'predecessor.py', temporary)
        candidate = client.Subject(directory / 'server.py', temporary)
        candidate.token = old.token
        fixture = Fixture(old, candidate)
        checks = (
            ('V01', 'Create, age and reopen the HTTP-written v2 predecessor fixture', fixture.seed),
            ('V02', 'Start the candidate at version 2 and preserve every named row', fixture.boot),
            ('V03', 'Preserve enabled, explicitly disabled and longer retention policies', fixture.policies_survive),
            ('V04', 'Preserve event details, hook credentials, duplicate IDs and conflicts', fixture.details_survive),
            ('V05', 'Restart without implicit expiry or changes to rows, policies and retries', fixture.restart),
            ('V06', 'Delete only the oldest eligible active event with limit one', fixture.expire_one),
            ('V07', 'Protect disabled and longer-policy events during explicit expiry', fixture.protected),
            ('V08', 'Reuse the expired key and preserve the replacement and policies across restart', fixture.replace),
        )
        try:
            for identifier, title, callback in checks:
                before = time.monotonic()
                check = {'id': identifier, 'title': title, 'passed': False}
                try:
                    callback()
                    check['passed'] = True
                except FixtureFailure as error:
                    check['error'] = str(error)
                except sqlite3.Error:
                    check['error'] = 'Unable to inspect or age the expected SQLite schema'
                except Exception:
                    # Transport, startup and parsing failures can contain paths, credentials
                    # or candidate-controlled text. None of those belong in public reports.
                    check['error'] = 'Unable to complete the fixture operation or read its response'
                check['duration_ms'] = round((time.monotonic() - before) * 1000, 2)
                report['checks'].append(check)
                if not check['passed']:
                    break
        finally:
            try:
                old.stop()
            finally:
                candidate.stop()
    report['duration_seconds'] = round(time.monotonic() - started, 3)
    report['passed'] = sum(c['passed'] for c in report['checks'])
    report['total'] = len(report['checks'])
    report['status'] = 'passed' if report['passed'] == report['expected_total'] else 'failed'
    return report
