#!/usr/bin/env python3
"""
ClaudeWidget (macOS) — menu-bar app showing Anthropic API rate-limit stats.
Requires: rumps  (pip install rumps)
Click the menu-bar item to see details. Refreshes every 10 seconds.
"""

import rumps
import urllib.request
import json
import os
import threading
from datetime import datetime, timezone

_KEY_FILE   = os.path.expanduser('~/.config/claude-widget/api_key')
_CREDS_FILE = os.path.expanduser('~/.claude/.credentials.json')

REFRESH_SECS = 10


def _load_auth():
    # 1. Claude Code credentials file (Linux / older versions)
    try:
        with open(_CREDS_FILE) as f:
            creds = json.load(f)
        token = creds.get('claudeAiOauth', {}).get('accessToken', '').strip()
        if token:
            return token, True
    except Exception:
        pass
    # 2. macOS Keychain (where claude CLI stores credentials on Mac)
    try:
        import subprocess
        raw = subprocess.check_output(
            ['security', 'find-generic-password', '-s', 'Claude Code-credentials', '-w'],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        creds = json.loads(raw)
        token = creds.get('claudeAiOauth', {}).get('accessToken', '').strip()
        if token:
            return token, True
    except Exception:
        pass
    # 3. Explicit env var
    k = os.environ.get('ANTHROPIC_API_KEY', '').strip()
    if k:
        return k, False
    # 4. Local config file
    try:
        with open(_KEY_FILE) as f:
            return f.read().strip(), False
    except FileNotFoundError:
        return '', False


def _bar(pct, width=10):
    filled = round(pct * width)
    return '█' * filled + '░' * (width - filled)


def _pct_str(pct):
    return f'{pct * 100:.1f}%'


class ClaudeWidget(rumps.App):
    def __init__(self):
        super().__init__('Claude', title='◆', quit_button=None)

        self.api_key, self.is_oauth = _load_auth()

        self.item_5h     = rumps.MenuItem('5h:  —')
        self.item_7d     = rumps.MenuItem('7d:  —')
        self.item_reset  = rumps.MenuItem('')
        self.item_status = rumps.MenuItem('')
        self.item_last   = rumps.MenuItem('')
        self.item_refresh = rumps.MenuItem('Refresh now', callback=self._on_refresh)
        self.item_quit   = rumps.MenuItem('Quit', callback=rumps.quit_application)

        self.menu = [
            self.item_5h,
            self.item_7d,
            None,
            self.item_reset,
            self.item_status,
            self.item_last,
            None,
            self.item_refresh,
            self.item_quit,
        ]

        self._fetch()

    @rumps.timer(REFRESH_SECS)
    def _tick(self, _):
        threading.Thread(target=self._fetch, daemon=True).start()

    def _on_refresh(self, _):
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self):
        if not self.api_key:
            self._apply_error(f'No key — add to {_KEY_FILE}')
            return
        try:
            payload = json.dumps({
                'model':      'claude-haiku-4-5-20251001',
                'max_tokens': 1,
                'messages':   [{'role': 'user', 'content': '.'}],
            }).encode()
            auth_headers = (
                {'Authorization': f'Bearer {self.api_key}'}
                if self.is_oauth else
                {'x-api-key': self.api_key}
            )
            req = urllib.request.Request(
                'https://api.anthropic.com/v1/messages',
                data=payload,
                headers={
                    **auth_headers,
                    'anthropic-version': '2023-06-01',
                    'content-type':      'application/json',
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                h = resp.headers
                def _f(k): return float(h.get(k) or 0)
                def _i(k): return int(h.get(k) or 0)
                data = {
                    'util_5h':  _f('anthropic-ratelimit-unified-5h-utilization'),
                    'util_7d':  _f('anthropic-ratelimit-unified-7d-utilization'),
                    'reset_5h': _i('anthropic-ratelimit-unified-5h-reset'),
                    'reset_7d': _i('anthropic-ratelimit-unified-7d-reset'),
                    'status':    h.get('anthropic-ratelimit-unified-status', ''),
                }
            self._apply(data)
        except Exception as exc:
            self._apply_error(str(exc)[:60])

    def _apply(self, d):
        util_5h = d.get('util_5h', 0.0)
        util_7d = d.get('util_7d', 0.0)
        status  = d.get('status', '')
        reset_5h = d.get('reset_5h', 0)

        def col(pct):
            if pct < 0.5:  return '🟢'
            if pct < 0.8:  return '🟡'
            return '🔴'

        self.item_5h.title    = f'5h  {col(util_5h)} {_bar(util_5h)}  {_pct_str(util_5h)}'
        self.item_7d.title    = f'7d  {col(util_7d)} {_bar(util_7d)}  {_pct_str(util_7d)}'

        if reset_5h:
            reset_dt = datetime.fromtimestamp(reset_5h, tz=timezone.utc).astimezone()
            self.item_reset.title = f'5h resets {reset_dt.strftime("%H:%M")}'
        else:
            self.item_reset.title = ''

        self.item_status.title = f'Status: {status}' if status and status != 'allowed' else ''
        self.item_last.title   = f'Updated {datetime.now().strftime("%H:%M:%S")}'

        # Menu-bar title: dominant utilisation
        pct = max(util_5h, util_7d)
        self.title = f'{col(pct)} {_pct_str(pct)}'

    def _apply_error(self, msg):
        self.title = '⚠️'
        self.item_5h.title  = f'Error: {msg}'
        self.item_7d.title  = ''
        self.item_last.title = datetime.now().strftime('%H:%M:%S')


if __name__ == '__main__':
    ClaudeWidget().run()
