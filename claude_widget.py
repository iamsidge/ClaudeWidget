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

_KEY_FILE = os.path.expanduser('~/.config/claude-widget/api_key')

def _load_api_key():
    # 1. env var wins
    k = os.environ.get('ANTHROPIC_API_KEY', '').strip()
    if k:
        return k
    # 2. fall back to local config file (never committed to git)
    try:
        with open(_KEY_FILE) as f:
            return f.read().strip()
    except FileNotFoundError:
        return ''

API_KEY      = _load_api_key()
REFRESH_SECS = 60
WIN_W, WIN_H = 240, 260
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

        # Drag state
        self._drag = None
        self.connect('button-press-event',   self._on_press)
        self.connect('motion-notify-event',  self._on_motion)
        self.connect('button-release-event', lambda *_: setattr(self, '_drag', None))
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK |
            Gdk.EventMask.BUTTON1_MOTION_MASK |
            Gdk.EventMask.BUTTON_RELEASE_MASK
        )

        # Data
        self.data   = {}
        self.error  = 'Loading…'
        self.status = ''

        area = Gtk.DrawingArea()
        area.connect('draw', self._draw)
        self.add(area)
        self.show_all()

        # Place bottom-right of primary monitor
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        geo = monitor.get_geometry()
        self.move(geo.x + geo.width - WIN_W - 20, geo.y + geo.height - WIN_H - 60)

        threading.Thread(target=self._fetch, daemon=True).start()
        GLib.timeout_add_seconds(REFRESH_SECS, self._schedule_fetch)

    # ── Data fetch ────────────────────────────────────────────────────────────

    def _schedule_fetch(self):
        threading.Thread(target=self._fetch, daemon=True).start()
        return True

    def _fetch(self):
        if not API_KEY:
            GLib.idle_add(self._apply, None, f'Add key to\n{_KEY_FILE}')
            return
        try:
            payload = json.dumps({
                'model':      'claude-haiku-4-5-20251001',
                'max_tokens': 1,
                'messages':   [{'role': 'user', 'content': '.'}],
            }).encode()
            req = urllib.request.Request(
                'https://api.anthropic.com/v1/messages',
                data=payload,
                headers={
                    'x-api-key':         API_KEY,
                    'anthropic-version': '2023-06-01',
                    'content-type':      'application/json',
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                h = resp.headers
                def _i(k): return int(h.get(k) or 0)
                data = {
                    'tok_lim':  _i('anthropic-ratelimit-tokens-limit'),
                    'tok_rem':  _i('anthropic-ratelimit-tokens-remaining'),
                    'req_lim':  _i('anthropic-ratelimit-requests-limit'),
                    'req_rem':  _i('anthropic-ratelimit-requests-remaining'),
                    'reset':     h.get('anthropic-ratelimit-tokens-reset', ''),
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
        _text_center(cr, 'CLAUDE TOKENS', cx, 26, 10, TEXT2, bold=True)

        if self.error:
            col = RED if 'API_KEY' not in self.error else ORANGE
            _text_center(cr, self.error, cx, h // 2 + 6, 9, col)
            _text_center(cr, self.status, cx, h - 14, 8, TEXT2)
            return

        d = self.data
        tok_pct = d.get('tok_rem', 0) / max(d.get('tok_lim', 1), 1)
        req_pct = d.get('req_rem', 0) / max(d.get('req_lim', 1), 1)

        # Main token donut
        donut_r = 54
        donut_cx = cx
        donut_cy = 108
        ring_w = 11
        tok_col = GREEN if tok_pct > 0.5 else (ORANGE if tok_pct > 0.2 else RED)
        _donut(cr, donut_cx, donut_cy, donut_r, donut_r - ring_w, tok_pct, tok_col, CARD)

        # Percentage inside donut
        pct_str = f'{tok_pct * 100:.0f}%'
        _text_center(cr, pct_str, donut_cx, donut_cy + 9, 20, tok_col, bold=True)
        _text_center(cr, 'TOKENS', donut_cx, donut_cy + 24, 8, TEXT2)

        # Separator
        _set(cr, SEP)
        cr.set_line_width(1)
        y_sep = donut_cy + donut_r + 14
        cr.move_to(16, y_sep)
        cr.line_to(w - 16, y_sep)
        cr.stroke()

        # Stats rows
        pad = 20
        row_y = y_sep + 20

        def stat_row(label, rem, lim, pct, col, y):
            _text_left(cr, label, pad, y, 9, TEXT2)
            val = f'{rem:,} / {lim:,}'
            cr.set_font_size(9)
            cr.select_font_face('Ubuntu Mono', cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            ext = cr.text_extents(val)
            _set(cr, TEXT)
            cr.move_to(w - pad - ext.width - ext.x_bearing, y)
            cr.show_text(val)
            _pill_bar(cr, pad, y + 4, w - pad * 2, 5, pct, col, CARD)

        stat_row('Tokens', d.get('tok_rem', 0), d.get('tok_lim', 0), tok_pct, tok_col, row_y)
        stat_row('Requests', d.get('req_rem', 0), d.get('req_lim', 0), req_pct, BLUE, row_y + 32)

        # Reset info
        reset = d.get('reset', '')
        if reset:
            reset_lbl = 'Resets ' + reset[11:19] if len(reset) > 19 else reset
            _text_center(cr, reset_lbl, cx, row_y + 70, 8, TEXT2)

        # Timestamp
        _text_center(cr, f'↻  {self.status}', cx, h - 12, 8, TEXT2)

    # ── Input ─────────────────────────────────────────────────────────────────

    def _on_press(self, _widget, event):
        if event.button == 1:
            wx, wy = self.get_position()
            self._drag = (int(event.x_root), int(event.y_root), wx, wy)
        elif event.button == 3:
            Gtk.main_quit()

    def _on_motion(self, _widget, event):
        if self._drag:
            ox, oy, wx, wy = self._drag
            self.move(wx + int(event.x_root) - ox, wy + int(event.y_root) - oy)


if __name__ == '__main__':
    win = ClaudeWidget()
    win.connect('destroy', Gtk.main_quit)
    Gtk.main()
