#!/usr/bin/env python3
"""
ClaudeWidget — floating GTK desktop widget showing Anthropic API rate-limit stats.
Requires: python3-gi, ANTHROPIC_API_KEY env var.
Left-drag to move. Right-click to quit.
"""

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, GLib
import urllib.request
import json
import os
import threading
import math
import cairo
from datetime import datetime

_KEY_FILE   = os.path.expanduser('~/.config/claude-widget/api_key')
_CREDS_FILE = os.path.expanduser('~/.claude/.credentials.json')

def _load_auth():
    """Return (token, is_oauth).  OAuth token → Bearer header; API key → x-api-key."""
    # 1. Claude Code OAuth credentials (same bucket Claude Code uses)
    try:
        import json as _json
        with open(_CREDS_FILE) as f:
            creds = _json.load(f)
        token = creds.get('claudeAiOauth', {}).get('accessToken', '').strip()
        if token:
            return token, True
    except Exception:
        pass
    # 2. Explicit env var
    k = os.environ.get('ANTHROPIC_API_KEY', '').strip()
    if k:
        return k, False
    # 3. Local config file
    try:
        with open(_KEY_FILE) as f:
            return f.read().strip(), False
    except FileNotFoundError:
        return '', False

API_KEY, IS_OAUTH = _load_auth()
REFRESH_SECS = 10
WIN_W, WIN_H = 260, 310
CORNER_R     = 14

# macOS-style dark palette
BG       = (0.102, 0.102, 0.110, 0.93)
CARD     = (0.145, 0.145, 0.157, 1.0)
TEXT     = (0.949, 0.949, 0.969, 1.0)
TEXT2    = (0.557, 0.557, 0.596, 1.0)
BLUE     = (0.039, 0.518, 1.000, 1.0)
GREEN    = (0.196, 0.843, 0.294, 1.0)
ORANGE   = (1.000, 0.624, 0.039, 1.0)
RED      = (1.000, 0.271, 0.227, 1.0)
SEP      = (0.227, 0.227, 0.243, 1.0)


def _set(cr, rgba):
    cr.set_source_rgba(*rgba)


def _rounded_rect(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + r,     y + r,     r, math.pi,       1.5 * math.pi)
    cr.arc(x + w - r, y + r,     r, -0.5 * math.pi, 0)
    cr.arc(x + w - r, y + h - r, r, 0,              0.5 * math.pi)
    cr.arc(x + r,     y + h - r, r, 0.5 * math.pi,  math.pi)
    cr.close_path()


def _donut(cr, cx, cy, outer_r, inner_r, pct, val_col, bg_col):
    # 270° arc: start at 225° (7-o'clock), sweep CW to 315° (5-o'clock) via top
    # In Cairo: 0=3-o'clock, angles CW in screen coords
    start = 0.75 * math.pi   # 135° screen = 7-o'clock
    span  = 1.5 * math.pi    # 270°

    # Track
    _set(cr, bg_col)
    cr.set_line_width(outer_r - inner_r)
    cr.arc(cx, cy, (outer_r + inner_r) / 2, start, start + span)
    cr.stroke()

    # Value
    if pct > 0:
        _set(cr, val_col)
        cr.arc(cx, cy, (outer_r + inner_r) / 2, start, start + span * max(0.0, min(1.0, pct)))
        cr.stroke()


def _text_center(cr, txt, cx, y, size, rgba, bold=False):
    cr.set_font_size(size)
    cr.select_font_face(
        "Ubuntu Mono",
        cairo.FONT_SLANT_NORMAL,
        cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL,
    )
    ext = cr.text_extents(txt)
    _set(cr, rgba)
    cr.move_to(cx - ext.width / 2 - ext.x_bearing, y)
    cr.show_text(txt)


def _text_left(cr, txt, x, y, size, rgba, bold=False):
    cr.set_font_size(size)
    cr.select_font_face(
        "Ubuntu Mono",
        cairo.FONT_SLANT_NORMAL,
        cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL,
    )
    _set(cr, rgba)
    cr.move_to(x, y)
    cr.show_text(txt)


def _pill_bar(cr, x, y, w, h, pct, fg, bg):
    r = h / 2
    # background
    _set(cr, bg)
    _rounded_rect(cr, x, y, w, h, r)
    cr.fill()
    # fill
    if pct > 0:
        fw = max(h, int(w * min(1.0, pct)))
        _set(cr, fg)
        _rounded_rect(cr, x, y, fw, h, r)
        cr.fill()


class ClaudeWidget(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_title("Claude")
        self.set_default_size(WIN_W, WIN_H)
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_resizable(False)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.set_app_paintable(True)

        # Data
        self.data         = {}
        self.error        = 'Loading…'
        self.status       = ''
        self.session_used = 0       # tokens consumed since widget started
        self.prev_rem     = None    # tok_rem from last successful poll
        # Connect drag/click events to the DrawingArea — it fills the window
        # and absorbs pointer events before they reach the Window.
        area = Gtk.DrawingArea()
        area.connect('draw', self._draw)
        area.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK |
            Gdk.EventMask.BUTTON_RELEASE_MASK
        )
        area.connect('button-press-event', self._on_press)
        self.add(area)

        # Realize before positioning so move() takes effect
        self.realize()
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        geo = monitor.get_geometry()
        self.move(geo.x + geo.width - WIN_W - 20, geo.y + geo.height - WIN_H - 60)
        self.show_all()

        threading.Thread(target=self._fetch, daemon=True).start()
        GLib.timeout_add_seconds(REFRESH_SECS, self._schedule_fetch)

    # ── Data fetch ────────────────────────────────────────────────────────────

    def _schedule_fetch(self):
        threading.Thread(target=self._fetch, daemon=True).start()
        return True

    def _fetch(self):
        if not API_KEY:
            GLib.idle_add(self._apply, None, f'No key found. Add to {_KEY_FILE}')
            return
        try:
            payload = json.dumps({
                'model':      'claude-haiku-4-5-20251001',
                'max_tokens': 1,
                'messages':   [{'role': 'user', 'content': '.'}],
            }).encode()
            auth_headers = (
                {'Authorization': f'Bearer {API_KEY}'}
                if IS_OAUTH else
                {'x-api-key': API_KEY}
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
                    'util_5h':    _f('anthropic-ratelimit-unified-5h-utilization'),
                    'util_7d':    _f('anthropic-ratelimit-unified-7d-utilization'),
                    'reset_5h':   _i('anthropic-ratelimit-unified-5h-reset'),
                    'reset_7d':   _i('anthropic-ratelimit-unified-7d-reset'),
                    'status':      h.get('anthropic-ratelimit-unified-status', ''),
                    'claim':       h.get('anthropic-ratelimit-unified-representative-claim', ''),
                }
            GLib.idle_add(self._apply, data, None)
        except Exception as exc:
            GLib.idle_add(self._apply, None, str(exc)[:50])

    def _apply(self, data, error):
        self.data   = data or {}
        self.error  = error
        self.status = datetime.now().strftime('%H:%M:%S')
        self.get_child().queue_draw()

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _draw(self, widget, cr):
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        cx = w / 2

        cr.set_operator(cairo.OPERATOR_SOURCE)

        # Window background
        _set(cr, BG)
        _rounded_rect(cr, 0, 0, w, h, CORNER_R)
        cr.fill()

        cr.set_operator(cairo.OPERATOR_OVER)

        # Title
        _text_center(cr, 'CLAUDE TOKENS', cx, 28, 12, TEXT2, bold=True)

        if self.error:
            col = RED if 'API_KEY' not in self.error else ORANGE
            _text_center(cr, self.error, cx, h // 2 + 6, 11, col)
            _text_center(cr, self.status, cx, h - 14, 10, TEXT2)
            return

        d = self.data
        util_5h = d.get('util_5h', 0.0)
        util_7d = d.get('util_7d', 0.0)
        status  = d.get('status', '')

        # Colour based on 5h utilisation
        col_5h = GREEN if util_5h < 0.5 else (ORANGE if util_5h < 0.8 else RED)

        # Main donut — 5-hour budget used
        donut_r  = 54
        donut_cx = cx
        donut_cy = 105
        ring_w   = 11
        _donut(cr, donut_cx, donut_cy, donut_r, donut_r - ring_w, util_5h, col_5h, CARD)
        _text_center(cr, f'{util_5h * 100:.0f}%', donut_cx, donut_cy + 10, 22, col_5h, bold=True)
        _text_center(cr, '5H BUDGET', donut_cx, donut_cy + 26, 11, TEXT2)

        # Separator
        _set(cr, SEP)
        cr.set_line_width(1)
        y_sep = donut_cy + donut_r + 14
        cr.move_to(16, y_sep)
        cr.line_to(w - 16, y_sep)
        cr.stroke()

        pad   = 20
        row_y = y_sep + 18

        def util_row(label, pct, col, y):
            _text_left(cr, label, pad, y, 11, TEXT2)
            val = f'{pct * 100:.1f}%'
            cr.set_font_size(11)
            cr.select_font_face('Ubuntu Mono', cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            ext = cr.text_extents(val)
            _set(cr, col)
            cr.move_to(w - pad - ext.width - ext.x_bearing, y)
            cr.show_text(val)
            _pill_bar(cr, pad, y + 4, w - pad * 2, 5, pct, col, CARD)

        col_7d = GREEN if util_7d < 0.5 else (ORANGE if util_7d < 0.8 else RED)
        util_row('5h used',  util_5h, col_5h, row_y)
        util_row('7d used',  util_7d, col_7d, row_y + 28)

        # Reset time for 5h window
        reset_5h = d.get('reset_5h', 0)
        if reset_5h:
            from datetime import timezone
            reset_dt = datetime.fromtimestamp(reset_5h, tz=timezone.utc).astimezone()
            reset_lbl = '5h resets ' + reset_dt.strftime('%H:%M')
            _text_center(cr, reset_lbl, cx, row_y + 56, 10, TEXT2)

        # Status badge
        if status and status != 'allowed':
            _text_center(cr, status.upper(), cx, row_y + 72, 10, RED)

        # Timestamp
        _text_center(cr, f'↻  {self.status}', cx, h - 12, 10, TEXT2)

    # ── Input ─────────────────────────────────────────────────────────────────

    def _on_press(self, _widget, event):
        if event.button == 1:
            self.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
        elif event.button == 2:
            # Middle-click: force immediate refresh
            threading.Thread(target=self._fetch, daemon=True).start()
        elif event.button == 3:
            Gtk.main_quit()


if __name__ == '__main__':
    win = ClaudeWidget()
    win.connect('destroy', Gtk.main_quit)
    Gtk.main()
