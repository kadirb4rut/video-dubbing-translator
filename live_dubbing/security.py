"""Loopback origin validation, one-use pairing and explicit source policy."""
from __future__ import annotations

import hmac
import re
import secrets
import time
from urllib.parse import urlparse


def validated_glossary(value):
    """Bound explicitly supplied terminology before any model startup."""
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 50:
        raise ValueError('Glossary must be a JSON object with at most 50 terms')
    result = {}
    for source, target in value.items():
        if (not isinstance(source, str) or not isinstance(target, str)
                or not source.strip() or not target.strip()
                or max(len(source), len(target)) > 160
                or any(ord(char) < 32 for char in source + target)):
            raise ValueError('Glossary terms must be nonempty single-line strings of at most 160 characters')
        source, target = source.strip(), target.strip()
        if source in result:
            raise ValueError('Glossary contains duplicate terms after removing surrounding spaces')
        result[source] = target
    return result


class Credentials:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.cookie = secrets.token_urlsafe(32)
        self.pairings = {}
        self.tickets = {}
        self.extension_origins = set()

    def issue_pairing(self):
        # No 6-digit code brute-force surface on localhost.
        code = secrets.token_urlsafe(24)
        self.pairings[code] = self.clock() + 120
        self._prune()
        return code

    def _prune(self):
        now = self.clock()
        self.pairings = {key: expiry for key, expiry in self.pairings.items() if expiry > now}
        self.tickets = {key: value for key, value in self.tickets.items() if value[1] > now}

    def issue_ticket(self, origin):
        self._prune()
        ticket = secrets.token_urlsafe(32)
        self.tickets[ticket] = (origin, self.clock() + 120)
        return ticket

    def redeem(self, code, origin):
        self._prune()
        if not re.fullmatch(r'chrome-extension://[a-p]{32}', origin or ''):
            raise ValueError('Pairing accepts only an explicit Chrome/Edge extension origin')
        expiry = self.pairings.pop(code, None)
        if expiry is None or expiry <= self.clock():
            raise ValueError('Pairing code expired or already used')
        self.extension_origins.add(origin)
        return self.issue_ticket(origin)

    def consume_ticket(self, ticket, origin):
        self._prune()
        item = self.tickets.pop(ticket, None)
        return bool(item and hmac.compare_digest(item[0], origin) and item[1] > self.clock())

    def valid_cookie(self, value):
        return bool(value and hmac.compare_digest(value, self.cookie))


def source_policy(source_url, source_kind, *, user_authorized=False):
    if source_kind not in ('browser_tab', 'display', 'local_fixture'):
        return False, 'Unsupported source type'
    parsed = urlparse(source_url or '')
    host = (parsed.hostname or '').lower()
    youtube = host == 'youtu.be' or host == 'youtube.com' or host.endswith('.youtube.com') or host.endswith('youtube-nocookie.com')
    if youtube:
        # The API alternate-track prohibition is not evidence that *all*
        # browser capture is forbidden. This route never uses a YouTube API,
        # player modification, extracted stream URL or ad/DRM workaround.
        # User authorization is a required scope declaration, not a legal
        # certification or an override of platform/content restrictions.
        if parsed.path.startswith('/embed/') or host.endswith('youtube-nocookie.com'):
            return False, 'YouTube embedded/API-player capture is unsupported; use the normal selected source tab'
        if source_kind != 'browser_tab' or user_authorized is not True:
            return False, 'YouTube requires explicit authorization and the selected-tab browser integration; API/stream-extraction routes are unsupported.'
        return True, 'Experimental user-authorized full-tab capture; YouTube APIs are not used. Platform and content permissions still apply.'
    if parsed.scheme not in ('http', 'https', 'file'):
        return False, 'Select a permitted normal web tab or local fixture'
    return True, 'User-selected permitted source; no platform API or stream extraction'
