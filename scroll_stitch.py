#!/usr/bin/env python3
import sys
import ctypes
from ctypes import POINTER, c_int, c_uint, c_short, c_ulong, c_char_p, c_void_p, c_ubyte, c_size_t, Structure, byref, CFUNCTYPE, cast
from ctypes.util import find_library
import os
from pathlib import Path
import webbrowser
import shutil
import re
import subprocess
import shlex
import logging
from logging.handlers import QueueHandler
import queue
import collections
from collections import Counter
import threading
import asyncio
from enum import Enum, IntFlag, auto
import abc
from datetime import datetime
import time
import math
import bisect
import select
import configparser
import argparse
import cv2
import numpy as np
import cairo
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Gdk, GLib, GObject, Pango, PangoCairo, GdkPixbuf
IS_WAYLAND = False
try:
    gdk_display = Gdk.Display.get_default()
    if gdk_display:
        IS_WAYLAND = "wayland" in gdk_display.get_name().lower()
    else:
        IS_WAYLAND = "wayland" in os.environ.get('XDG_SESSION_TYPE', '').lower()
except Exception:
    IS_WAYLAND = "wayland" in os.environ.get('XDG_SESSION_TYPE', '').lower()
if not IS_WAYLAND:
    from Xlib import display, X, protocol, XK
    from Xlib.ext import xtest, record, shape
    from Xlib.protocol import rq
    Gst = GstVideo = Gio = None
else:
    display = X = protocol = XK = xtest = record = rq = shape = None
    try:
        gi.require_version('Gst', '1.0')
        gi.require_version('GstVideo', '1.0')
        gi.require_version('Gio', '2.0')
        from gi.repository import Gst, GstVideo, Gio
    except (ImportError, ValueError) as e:
        logging.error("Wayland 环境下无法加载 GStreamer 依赖: {e}")
        sys.exit(1)
EVDEV_AVAILABLE = False
try:
    import evdev
    from evdev import UInput, ecodes as e, AbsInfo
    EVDEV_AVAILABLE = True
except ImportError:
    pass
UINPUT_AVAILABLE = os.access('/dev/uinput', os.R_OK | os.W_OK)
INPUT_AVAILABLE = False
try:
    INPUT_AVAILABLE = any(os.access(p, os.R_OK) for p in Path('/dev/input').glob('event*'))
except OSError:
    pass
GTK_LAYER_SHELL_AVAILABLE = False
try:
    gi.require_version('GtkLayerShell', '0.1')
    from gi.repository import GtkLayerShell
    GTK_LAYER_SHELL_AVAILABLE = True
except (ImportError, ValueError):
    pass

GLOBAL_OVERLAY = None
hotkey_manager = None

class HotkeyModifiers(IntFlag):
    NONE = 0
    SHIFT = auto()
    CTRL = auto()
    ALT = auto()
    SUPER = auto()

    def __str__(self):
        if self == self.__class__.NONE:
            return "NONE"
        parts = [f"<{mod.name.lower()}>" for mod in self.__class__ if mod != self.__class__.NONE and (self & mod)]
        return '+'.join(parts)

class HotkeyDefinition:
    _STD_TO_ALIASES = {
        'ctrl': ['ctrl', 'control'],
        'super': ['super', 'win', 'meta'],
        'enter': ['enter', 'return'],
        'esc': ['esc', 'escape'],
        'page_up': ['page_up', 'pageup', 'pgup'],
        'page_down': ['page_down', 'pagedown', 'pgdn'],
        'insert': ['insert', 'ins'],
        'delete': ['delete', 'del'],
        'print': ['print', 'printscreen', 'prtsc']
    }
    _ALIAS_TO_STD = {alias: std for std, aliases in _STD_TO_ALIASES.items() for alias in aliases}

    _MOD_MASK_MAP = {
        HotkeyModifiers.SHIFT: {'gdk': 'SHIFT_MASK', 'x11': 'ShiftMask', 'evdev': ['LEFTSHIFT', 'RIGHTSHIFT']},
        HotkeyModifiers.CTRL: {'gdk': 'CONTROL_MASK', 'x11': 'ControlMask', 'evdev': ['LEFTCTRL', 'RIGHTCTRL']},
        HotkeyModifiers.ALT: {'gdk': 'MOD1_MASK', 'x11': 'Mod1Mask', 'evdev': ['LEFTALT', 'RIGHTALT']},
        HotkeyModifiers.SUPER: {'gdk': 'SUPER_MASK', 'x11': 'Mod4Mask', 'evdev': ['LEFTMETA', 'RIGHTMETA']}
    }
    _STR_TO_MODIFIER = {mod.name.lower(): mod for mod in HotkeyModifiers if mod != HotkeyModifiers.NONE}
    EVDEV_CODE_TO_MODIFIER = {f"KEY_{code}": mod for mod, backends in _MOD_MASK_MAP.items() for code in backends['evdev']}

    _MAIN_KEY_MAP = {
        'space': {'gdk': ['space'], 'x11': ['space'], 'evdev': ['SPACE']},
        'enter': {'gdk': ['Return'], 'x11': ['Return'], 'evdev': ['ENTER']},
        'tab': {'gdk': ['Tab'], 'x11': ['Tab'], 'evdev': ['TAB']},
        'backspace': {'gdk': ['BackSpace'], 'x11': ['BackSpace'], 'evdev': ['BACKSPACE']},
        'esc': {'gdk': ['Escape'], 'x11': ['Escape'], 'evdev': ['ESC']},
        'minus': {'gdk': ['minus'], 'x11': ['minus'], 'evdev': ['MINUS']},
        'equal': {'gdk': ['equal'], 'x11': ['equal'], 'evdev': ['EQUAL']},
        'up': {'gdk': ['Up'], 'x11': ['Up'], 'evdev': ['UP']},
        'down': {'gdk': ['Down'], 'x11': ['Down'], 'evdev': ['DOWN']},
        'left': {'gdk': ['Left'], 'x11': ['Left'], 'evdev': ['LEFT']},
        'right': {'gdk': ['Right'], 'x11': ['Right'], 'evdev': ['RIGHT']},
        'page_up': {'gdk': ['Page_Up'],   'x11': ['Page_Up', 'Prior'], 'evdev': ['PAGEUP']},
        'page_down': {'gdk': ['Page_Down'], 'x11': ['Page_Down', 'Next'], 'evdev': ['PAGEDOWN']},
        'home': {'gdk': ['Home'], 'x11': ['Home'], 'evdev': ['HOME']},
        'end': {'gdk': ['End'], 'x11': ['End'], 'evdev': ['END']},
        'insert': {'gdk': ['Insert'], 'x11': ['Insert'], 'evdev': ['INSERT']},
        'delete': {'gdk': ['Delete'], 'x11': ['Delete'], 'evdev': ['DELETE']},
        'print': {'gdk': ['Print'], 'x11': ['Print'], 'evdev': ['SYSRQ']},
        'shift': {'gdk': ['Shift_L', 'Shift_R'], 'x11': ['Shift_L', 'Shift_R'], 'evdev': ['LEFTSHIFT', 'RIGHTSHIFT']},
        'ctrl': {'gdk': ['Control_L', 'Control_R'], 'x11': ['Control_L', 'Control_R'], 'evdev': ['LEFTCTRL', 'RIGHTCTRL']},
        'alt': {'gdk': ['Alt_L', 'Alt_R'], 'x11': ['Alt_L', 'Alt_R'], 'evdev': ['LEFTALT', 'RIGHTALT']},
        'super': {'gdk': ['Super_L', 'Super_R'], 'x11': ['Super_L', 'Super_R'], 'evdev': ['LEFTMETA', 'RIGHTMETA']}
    }
    for char_code in range(ord('a'), ord('z') + 1):
        char = chr(char_code)
        _MAIN_KEY_MAP[char] = {'gdk': [char], 'x11': [char], 'evdev': [char.upper()]}
    for i in range(10):
        k = str(i)
        _MAIN_KEY_MAP[k] = {'gdk': [k], 'x11': [k], 'evdev': [k]}
    for i in range(1, 13):
        _MAIN_KEY_MAP[f'f{i}'] = {'gdk': [f'F{i}'], 'x11': [f'F{i}'], 'evdev': [f'F{i}']}
    _GDK_TO_STD = {gdk_key: std_key for std_key, backends in _MAIN_KEY_MAP.items() for gdk_key in backends.get('gdk', [])}

    def __init__(self, modifiers: HotkeyModifiers, main_key):
        self.modifiers = modifiers
        self.main_key = main_key

    def __hash__(self):
        return hash((self.modifiers, self.main_key))

    def __eq__(self, other):
        if not isinstance(other, HotkeyDefinition):
            return False
        return self.modifiers == other.modifiers and self.main_key == other.main_key

    @classmethod
    def from_string(cls, hotkey_str: str):
        if not hotkey_str:
            return cls(HotkeyModifiers.NONE, None)
        parts = [p.strip().lower().replace('<', '').replace('>', '') for p in hotkey_str.split('+') if p.strip()]
        modifiers = HotkeyModifiers.NONE
        main_key = None
        for part in parts:
            std_name = cls._ALIAS_TO_STD.get(part, part)
            if std_name in cls._STR_TO_MODIFIER:
                modifiers |= cls._STR_TO_MODIFIER[std_name]
            else:
                main_key = std_name
        if main_key is None and len(parts) == 1 and modifiers != HotkeyModifiers.NONE:
            std_name = cls._ALIAS_TO_STD.get(parts[0], parts[0])
            main_key = std_name
            modifiers = HotkeyModifiers.NONE
        return cls(modifiers, main_key)

    @classmethod
    def from_gdk_event(cls, event):
        modifiers = HotkeyModifiers.NONE
        for mod_flag, data in cls._MOD_MASK_MAP.items():
            gdk_mask = getattr(Gdk.ModifierType, data['gdk'])
            if event.state & gdk_mask:
                modifiers |= mod_flag
        keymap = Gdk.Keymap.get_for_display(Gdk.Display.get_default())
        success, entries = keymap.get_entries_for_keyval(event.keyval)
        effective_keyval = event.keyval
        if success and entries:
            keycode = entries[0].keycode
            success_lookup, key_entries, keyvals = keymap.get_entries_for_keycode(keycode)
            if success_lookup and key_entries and keyvals:
                for i, entry in enumerate(key_entries):
                    if entry.group == 0 and entry.level == 0:
                        effective_keyval = keyvals[i]
                        break
        key_name = Gdk.keyval_name(effective_keyval)
        if not key_name:
            return cls(HotkeyModifiers.NONE, None)
        main_key = cls._GDK_TO_STD.get(key_name, key_name)
        if main_key in cls._STR_TO_MODIFIER:
            mod_flag = cls._STR_TO_MODIFIER[main_key]
            if modifiers & mod_flag:
                modifiers &= ~mod_flag
            if modifiers != HotkeyModifiers.NONE:
                modifiers |= mod_flag
                main_key = None
        return cls(modifiers, main_key)

    def is_valid(self) -> bool:
        return self.main_key is not None and self.main_key in self._MAIN_KEY_MAP

    def is_modifier_only(self) -> bool:
        return self.modifiers == HotkeyModifiers.NONE and self.main_key in self._STR_TO_MODIFIER

    def to_string(self) -> str:
        if self.main_key is None and self.modifiers == HotkeyModifiers.NONE:
            return "未知按键"
        parts = []
        if self.modifiers != HotkeyModifiers.NONE:
            parts.append(str(self.modifiers))
        if self.main_key:
            if self.main_key in self._STR_TO_MODIFIER:
                parts.append(f"<{self.main_key}>")
            else:
                parts.append(self.main_key)
        return '+'.join(parts)

    def to_x11(self):
        x11_masks = []
        for mod in HotkeyModifiers:
            if mod != HotkeyModifiers.NONE and (self.modifiers & mod):
                x11_masks.append(self._MOD_MASK_MAP[mod]['x11'])
        key_strs = self._MAIN_KEY_MAP[self.main_key]['x11'] if self.is_valid() else []
        return x11_masks, key_strs

    def to_evdev(self):
        if self.is_valid():
            return [f"KEY_{code}" for code in self._MAIN_KEY_MAP[self.main_key]['evdev']]
        return []

class Config(GObject.Object):
    __gsignals__ = {
        'setting-changed': (GObject.SignalFlags.RUN_FIRST, None, (str, str, object)),
    }

    DEFAULT_CSS = {
        'info_panel': """
.info-panel { padding: 5px; border: 1px solid #505070; border-radius: 8px; background-color: rgba(43, 42, 51, 0.8); }
.info-panel label { font-weight: bold; color: #e0e0e0; }
.info-panel #label_dimensions { font-size: 26px; color: #948bc1; }
.info-panel #label_count { font-size: 24px; opacity: 0.9; }
.info-panel #label_mode { font-size: 23px; opacity: 0.9; }
""".strip(),
        'button': """
.overlay-button { padding: 4px; border: 1px solid #d1d5db; border-radius: 4px; background-color: #f5f5f5; background-image: none; font-size: 24px; color: #374151; }
.overlay-button:hover { border-color: #9ca3af; background-image: none; }
.overlay-button:active { background-color: #e5e7eb; background-image: none; }
.overlay-button:disabled { border-color: #e5e7eb; background-color: #f3f4f6; background-image: none; color: #9ca3af; opacity: 1; }
""".strip(),
        'instruction_panel': """
.instruction-panel { padding: 10px; border: 1px solid #555; border-radius: 6px; background-color: rgba(30, 30, 30, 0.85); font-size: 18px; color: #f0f0f0; }
.instruction-panel label { margin-bottom: 2px; }
.key-label { margin-right: 10px; font-weight: bold; color: #8be9fd; }
.desc-label { color: #f8f8f2; }
.key-label-inactive { margin-right: 10px; font-weight: bold; color: #8be9fd; opacity: 0.55; }
.desc-label-inactive { color: #f8f8f2; opacity: 0.55; }
""".strip(),
        'simulated_window': """
.simulated-window { border: 1px solid #b0b0b0; border-radius: 8px; background-color: #fdfdfd; box-shadow: 0 3px 10px rgba(0,0,0,0.2); font-size: 26px; color: #2e3436; }
.window-header { padding: 6px 10px; border-bottom: 1px solid #dcdcdc; border-radius: 8px 8px 0 0; background-color: #f2f2f2; }
.window-title { font-size: 28px; font-weight: bold; color: #333333; }
""".strip(),
        'preview_panel': """
@define-color preview_bg #1a1a1a;
@define-color preview_text #cccccc;
@define-color preview_mask rgba(26, 26, 26, 0.6);
@define-color preview_matched_seam #3380ff;
@define-color preview_unmatched_seam #b34dcc;
@define-color preview_drawing_border #ffffff;
@define-color preview_static_border #e6e6e6;
@define-color preview_delete_line #ff1a1a;
""".strip(),
        'config_panel': """
.config-container { margin: 20px; }
.config-section { margin: 12px; }
.config-sidebar { border-right: 1px solid alpha(@theme_fg_color, 0.15); }
.config-sidebar list row label { color: #2e3436; }
.config-sidebar list row:selected label { color: #ffffff; }
.log-view { margin: 5px; font-family: monospace; font-size: 22px; }
.log-view text { background-color: #ffffff; }
@define-color log_debug #708090;
@define-color log_info #2b2b2b;
@define-color log_warning #e69138;
@define-color log_error #cc0000;
""".strip(),
        'notification': """
.notification-panel { padding: 24px; border: 1px solid rgba(255,255,255,0.2); border-radius: 12px; background-color: rgba(40, 40, 45, 0.98); box-shadow: 0 2px 5px rgba(0,0,0,0.5); color: white; }
.notification-critical .notif-title { border-bottom-color: #ff5555; color: #ff5555; }
.notification-warning .notif-title { border-bottom-color: #ec9028; color: #ec9028; }
.notification-success .notif-title { border-bottom-color: #78c93f; color: #78c93f; }
.notif-title { padding-bottom: 8px; border-bottom: 1px solid rgba(255,255,255,0.5); font-size: 26px; font-weight: bold; color: #f5f5f5; }
.notif-msg { font-size: 20px; color: #f8f8f2; }
.notif-btn { min-width: 80px; margin-left: 4px; margin-right: 4px; padding: 6px 20px; border-radius: 6px; font-size: 22px; font-weight: bold; }
""".strip(),
        'mask': """
.mask-layer { background-color: rgba(0, 0, 0, 0.6); }
""".strip(),
        'dialog': """
.embedded-dialog { border: 1px solid #b0b0b0; border-radius: 8px; background-color: #f6f6f6; box-shadow: 0 2px 5px rgba(0,0,0,0.2); color: #2e3436; }
.dialog-header { padding: 12px; border-bottom: 1px solid #dcdcdc; border-radius: 11px 11px 0 0; background-color: #e8e8e8; }
.dialog-title { font-size: 26px; font-weight: bold; color: #333333; }
.dialog-content-area { padding: 15px; }
.dialog-message { font-size: 24px; color: #444444; }
.dialog-action-area { padding: 0 15px 10px 10px; }
.dialog-btn { padding: 8px 15px; border-radius: 6px; font-size: 22px; font-weight: bold; }
.dialog-list-box { padding: 4px; border: 1px solid #bfbfbf; border-radius: 4px; background-color: #f3f3f3; }
.dialog-list-row { margin-bottom: 5px; padding: 10px; border: 1px solid #cccccc; border-radius: 4px; background-color: #ffffff; }
.dialog-list-row:last-child { margin-bottom: 0; }
.dialog-list-row:selected { border: 1px solid #4A90E2; color: #000000; }
""".strip(),
        'feedback_widget': """
.feedback-panel { border: 1px solid rgba(255, 255, 255, 0.15); border-radius: 12px; background-color: rgba(30, 32, 35, 0.92); }
.feedback-label { margin-left: 10px; font-size: 20px; font-weight: bold; color: #f0f0f0; }
.feedback-spinner { min-width: 36px; min-height: 36px; color: #cccccc; }
.feedback-progress trough { min-height: 5px; border: none; border-radius: 3px; background-color: rgba(255,255,255,0.1); background-image: none; }
.feedback-progress progress { min-height: 5px; border: none; border-radius: 3px; background-color: #5e81ac; }
""".strip()
        }
    CONFIG_SCHEMA = {
        'Behavior': {
            'copy_to_clipboard_on_finish': ('bool', 'true'),
            'capture_with_cursor': ('bool', 'false'),
            'enable_free_scroll_matching': ('bool', 'true'),
            'auto_scroll_ticks_per_step': ('int', '2'),
            'auto_scroll_interval_ms': ('int', '300'),
            'scroll_method': ('str', 'move_user_cursor'),
            'reuse_invisible_cursor': ('bool', 'false'),
            'grid_scroll_interval_ms': ('int', '200'),
            'forward_action': ('str', 'capture_scroll'),
            'backward_action': ('str', 'scroll_delete'),
            'grid_scroll_ticks_formula': ('str', 'max(1, int(0.7 * {ticks}))'),
            'calibration_samples': ('int', '4'),
            'hotkey_debounce_time': ('float', '0.25'),
        },
        'Interface.Components': {
            'enable_buttons': ('bool', 'true'),
            'enable_side_panel': ('bool', 'true'),
            'show_instruction_panel_on_start': ('bool', 'true'),
            'show_preview_on_start': ('bool', 'true'),
            'enable_auto_scroll_buttons': ('bool', 'true'),
            'enable_grid_action_buttons': ('bool', 'true'),
            'show_capture_count': ('bool', 'true'),
            'show_total_dimensions': ('bool', 'true'),
            'show_current_mode': ('bool', 'true'),
        },
        # 逻辑px {
        'Interface.Layout': {
            'border_width': ('int', '4'),
            'button_panel_width': ('int', '100'),
            'side_panel_width': ('int', '150'),
            'preview_panel_height': ('int', '750'),
            'config_panel_height': ('int', '800'),
            'notification_width': ('int', '520'),
            'feedback_widget_width': ('int', '200'),
            'file_chooser_height': ('int', '800'),
            'min_selection_size': ('int', '40'),
        },
        'Interface.Theme': {
            'border_color': ('color', '0.73, 0.25, 0.25, 1.00'),
            'static_bar_color': ('color', '0.60, 0.76, 0.95, 1.00'),
            'info_panel_css': ('css', DEFAULT_CSS['info_panel']),
            'button_css': ('css', DEFAULT_CSS['button']),
            'instruction_panel_css': ('css', DEFAULT_CSS['instruction_panel']),
            'simulated_window_css': ('css', DEFAULT_CSS['simulated_window']),
            'preview_panel_css': ('css', DEFAULT_CSS['preview_panel']),
            'config_panel_css': ('css', DEFAULT_CSS['config_panel']),
            'notification_css': ('css', DEFAULT_CSS['notification']),
            'mask_css': ('css', DEFAULT_CSS['mask']),
            'dialog_css': ('css', DEFAULT_CSS['dialog']),
            'feedback_widget_css': ('css', DEFAULT_CSS['feedback_widget']),
        },
        # 逻辑px }
        'Output': {
            'save_directory': ('path', ''),
            'save_format': ('str', 'PNG'),
            'jpeg_quality': ('int', '80'),
            'filename_template': ('str', '长截图 {timestamp}'),
            'filename_timestamp_format': ('str', '%Y-%m-%d %H-%M-%S'),
        },
        'System': {
            'max_viewer_dimension': ('int', '32767'), # 缓冲区px
            'large_image_opener': ('str', 'default_browser'),
            'sound_theme': ('str', 'freedesktop'),
            'capture_sound': ('str', 'screen-capture'),
            'undo_sound': ('str', 'bell'),
            'finalize_sound': ('str', 'complete'),
            'warning_sound': ('str', 'dialog-warning'),
            'log_file': ('path', '~/.scroll_stitch.log'),
            'temp_directory': ('path', '/tmp/scroll_stitch_{pid}'),
        },
        'Preview': {
            'preview_cache_size': ('int', '20'),
            'preview_drag_sensitivity': ('float', '2.0'),
            'preview_autoscroll_sensitivity': ('float', '1.0'),
            'preview_zoom_factor': ('float', '1.26'),
            'preview_min_zoom': ('float', '0.25'),
            'preview_max_zoom': ('float', '4.0'),
            'preview_resize_handle_size': ('int', '10'), # 逻辑px
        },
        'Performance': {
            # 缓冲区px {
            'max_scroll_per_tick': ('int', '230'),
            'min_scroll_per_tick': ('int', '30'),
            # 缓冲区px }
            'thres_score': ('float', '5.0'),
            'thres_texture': ('float', '3.0'),
        },
        'Hotkeys': {
            'capture': ('hotkey', 'space'),
            'finalize': ('hotkey', 'enter'),
            'undo': ('hotkey', 'backspace'),
            'cancel': ('hotkey', 'esc'),
            'auto_scroll_start': ('hotkey', 's'),
            'auto_scroll_stop': ('hotkey', 'e'),
            'grid_forward': ('hotkey', 'f'),
            'grid_backward': ('hotkey', 'b'),
            'configure_scroll_unit': ('hotkey', 'c'),
            'toggle_grid_mode': ('hotkey', '<shift>'),
            'toggle_config_panel': ('hotkey', 'g'),
            'toggle_preview': ('hotkey', 'w'),
            'toggle_hotkeys_enabled': ('hotkey', 'f4'),
            'toggle_instruction_panel': ('hotkey', 'f1'),
            'preview_zoom_in': ('hotkey', '<ctrl>+equal'),
            'preview_zoom_out': ('hotkey', '<ctrl>+minus'),
            'dialog_confirm': ('hotkey', 'space'),
            'dialog_cancel': ('hotkey', 'esc'),
        },
        'ApplicationScrollUnits': {}
    }

    def __init__(self, custom_path=None):
        super().__init__()
        self.parser = configparser.ConfigParser(interpolation=None)
        self._save_timer_id = None
        default_config_path = Path.home() / ".config" / "scroll_stitch" / "config.ini"
        script_dir_config_path = Path(__file__).resolve().parent / "config.ini"
        path_to_load = None
        if custom_path and custom_path.is_file():
            path_to_load = custom_path
        elif script_dir_config_path.is_file():
            path_to_load = script_dir_config_path
        elif default_config_path.is_file():
            path_to_load = default_config_path
        if path_to_load:
            self.config_path = path_to_load
            self.parser.read(str(self.config_path), encoding='utf-8')
        else:
            self.config_path = default_config_path
            self._create_default_config()
            self.parser.read(str(self.config_path), encoding='utf-8')
        self._load_settings()

    def _load_settings(self):
        for section, items in self.CONFIG_SCHEMA.items():
            for key, _ in items.items():
                raw_str = self.get_raw_string(section, key)
                val = self.parse_string_to_value(section, key, raw_str)
                attr_name = f"HOTKEY_{key.upper()}" if section == 'Hotkeys' else key.upper()
                setattr(self, attr_name, val)

    def parse_string_to_value(self, section: str, key: str, raw_string: str):
        if section == 'ApplicationScrollUnits':
            parts = [p.strip() for p in raw_string.split(',')]
            try:
                unit = int(float(parts[0]))
                enabled = parts[1].lower() == 'true' if len(parts) > 1 else True
                return (unit, enabled)
            except (ValueError, IndexError):
                return (0, False)
        if section not in self.CONFIG_SCHEMA or key not in self.CONFIG_SCHEMA[section]:
            return None
        type_str = self.CONFIG_SCHEMA[section][key][0]
        try:
            if type_str == 'bool':
                return raw_string.strip().lower() == 'true'
            elif type_str == 'int':
                return int(float(raw_string.strip()))
            elif type_str == 'float':
                return float(raw_string.strip())
            elif type_str == 'color':
                return tuple(float(c.strip()) for c in raw_string.split(','))
            elif type_str == 'path':
                val = raw_string.strip()
                if not val: return None
                if '{pid}' in val:
                    val = val.replace('{pid}', str(os.getpid()))
                return Path(val).expanduser()
            elif type_str == 'hotkey':
                return HotkeyDefinition.from_string(raw_string)
            elif type_str == 'css':
                return raw_string.strip()
            else:
                return raw_string
        except Exception as e:
            logging.warning(f"配置解析失败 [{section}] {key}='{raw_string}': {e}，将使用默认值")
            default_str = self.CONFIG_SCHEMA[section][key][1]
            if raw_string != default_str:
                return self.parse_string_to_value(section, key, default_str)
            return None

    def is_restart_required(self, key: str) -> bool:
        restart_keys = {'log_file', 'temp_directory'}
        if IS_WAYLAND:
            restart_keys.add('capture_with_cursor')
        return key in restart_keys

    def get_section_items(self, section: str):
        if self.parser.has_section(section):
            return self.parser.items(section)
        return []

    def get_raw_string(self, section: str, key: str) -> str:
        if self.parser.has_option(section, key):
            return self.parser.get(section, key)
        return self.get_default_string(section, key)

    def get_default_string(self, section: str, key: str) -> str:
        if section in self.CONFIG_SCHEMA and key in self.CONFIG_SCHEMA[section]:
            return self.CONFIG_SCHEMA[section][key][1]
        return ""

    def get_default_css_color(self, css_key: str, color_name: str, fallback: str = "#000000") -> str:
        css_str = self.DEFAULT_CSS.get(css_key, "")
        if css_str:
            match = re.search(fr'@define-color\s+{re.escape(color_name)}\s+([^;]+);', css_str)
            if match:
                return match.group(1).strip()
        return fallback

    def remove_value(self, section: str, key: str):
        if self.parser.has_option(section, key):
            self.parser.remove_option(section, key)
            self._schedule_save()
            self.emit('setting-changed', section, key, None)

    def set_value(self, section: str, key: str, value):
        str_value = str(value)
        if not self.parser.has_section(section):
            self.parser.add_section(section)
        try:
            current_str = self.parser.get(section, key, fallback=None)
            if current_str == str_value:
                return
        except:
            pass
        self.parser.set(section, key, str_value)
        parsed_val = self.parse_string_to_value(section, key, str_value)
        attr_name = f"HOTKEY_{key.upper()}" if section == 'Hotkeys' else key.upper()
        if hasattr(self, attr_name) and not self.is_restart_required(key):
            setattr(self, attr_name, parsed_val)
        self._schedule_save()
        self.emit('setting-changed', section, key, str_value)
        logging.debug(f"配置已更新: [{section}] {key} = {str_value}")

    def _perform_save_to_disk(self):
        try:
            with open(self.config_path, 'w', encoding='utf-8') as configfile:
                self.parser.write(configfile)
        except Exception as e:
            logging.error(f"保存配置文件失败: {e}")
        self._save_timer_id = None
        return False

    def flush_save(self):
        if self._save_timer_id:
            GLib.source_remove(self._save_timer_id)
            self._perform_save_to_disk()

    def _schedule_save(self):
        if self._save_timer_id:
            GLib.source_remove(self._save_timer_id)
        self._save_timer_id = GLib.timeout_add(1000, self._perform_save_to_disk)

    def _create_default_config(self):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temp_parser = configparser.ConfigParser(interpolation=None)
        for section, items in self.CONFIG_SCHEMA.items():
            temp_parser.add_section(section)
            for key, (type_str, default_val) in items.items():
                temp_parser.set(section, key, default_val)
        with open(self.config_path, 'w', encoding='utf-8') as f:
            temp_parser.write(f)
        logging.info(f"已在 {self.config_path} 目录下创建默认配置文件")

class CoordSys(str, Enum):
    GLOBAL = "global"
    WINDOW = "window"
    MONITOR = "monitor"

class InvisibleCursorScroller:
    def __init__(self, min_x, min_y, max_x, max_y, park_x, park_y, config_obj: Config):
        self.config = config_obj
        # 缓冲区px全局坐标
        self.bounds = (min_x, min_y, max_x, max_y)
        self.park_pos = (park_x, park_y)
        self.master_id = None
        self.unique_name = "scroll-stitch-cursor"
        self.ui_mouse = None
        self.virtual_mouse_name = f"VirtualMouse-{self.unique_name}"
        self.is_ready = False

    def _get_all_master_ids(self, master_name):
        ids = []
        try:
            output = subprocess.check_output(['xinput', 'list']).decode()
            pattern = re.compile(fr'{re.escape(master_name)} pointer\s+id=(\d+)')
            matches = pattern.findall(output)
            ids = [int(match) for match in matches]
            logging.debug(f"找到 {len(ids)} 个名为 '{master_name}' 的主指针设备: {ids}")
        except (subprocess.CalledProcessError, FileNotFoundError, ValueError) as e:
            logging.error(f"查找主设备 ID 时出错: {e}")
        return ids

    def _remove_master_device(self, master_id):
        if master_id is None:
            return
        try:
            result = subprocess.run(
                ['xinput', 'remove-master', str(master_id)],
                check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, timeout=1
            )
            if result.returncode == 0:
                logging.debug(f"成功移除主设备 ID: {master_id}")
            else:
                logging.warning(f"尝试移除主设备 ID {master_id} 未成功: {result.stderr.strip()}")
        except subprocess.TimeoutExpired:
            logging.warning(f"移除主设备 ID {master_id} 超时")
        except Exception as e:
            logging.warning(f"尝试移除主设备 ID {master_id} 时发生异常: {e}")

    def _wait_for_device(self, device_name, timeout=3):
        """轮询 'xinput list' 直到找到指定的设备或超时"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                output = subprocess.check_output(['xinput', 'list']).decode()
                if device_name in output:
                    logging.debug(f"设备 '{device_name}' 已被 X Server 识别")
                    return
            except subprocess.CalledProcessError:
                pass
            time.sleep(0.1)
        raise TimeoutError(f"等待虚拟设备 '{device_name}' 超时（{timeout}秒）")

    def _create_virtual_devices(self):
        min_x, min_y, max_x, max_y = self.bounds
        mouse_caps = {
            e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT],
            e.EV_REL: [e.REL_WHEEL],
            e.EV_ABS: [
                (e.ABS_X, AbsInfo(value=min_x, min=min_x, max=max_x, fuzz=0, flat=0, resolution=0)),
                (e.ABS_Y, AbsInfo(value=min_y, min=min_y, max=max_y, fuzz=0, flat=0, resolution=0)),
            ],
        }
        self.ui_mouse = UInput(mouse_caps, name=self.virtual_mouse_name)

    def setup(self):
        try:
            existing_master_ids = self._get_all_master_ids(self.unique_name)
            master_id_to_use = None
            if not self.config.REUSE_INVISIBLE_CURSOR:
                if existing_master_ids:
                    logging.info("隐形光标设备配置为不复用，尝试清理所有检测到的旧主设备...")
                    for old_id in existing_master_ids:
                        self._remove_master_device(old_id)
                    existing_master_ids = []
            else:
                if not existing_master_ids:
                    logging.info("配置为复用，但未找到现有设备，将创建新设备")
                else:
                    master_id_to_use = existing_master_ids[0]
                    if len(existing_master_ids) > 1:
                        logging.warning(f"配置为复用，但检测到多个同名主设备: {existing_master_ids}。将复用第一个 ID: {existing_master_ids[0]}")
            if master_id_to_use is None:
                logging.info(f"创建新的主指针设备 '{self.unique_name}'")
                ids_before = self._get_all_master_ids(self.unique_name)
                subprocess.check_call(['xinput', 'create-master', self.unique_name])
                time.sleep(0.1)
                new_master_id = None
                for _ in range(10):
                    ids_after = self._get_all_master_ids(self.unique_name)
                    diff_ids = list(set(ids_after) - set(ids_before))
                    if diff_ids:
                        new_master_id = diff_ids[0]
                        if len(diff_ids) > 1:
                            logging.warning(f"检测到多个新设备 ID: {diff_ids}，将使用第一个: {new_master_id}")
                        else:
                            logging.debug(f"成功识别新创建的主设备 ID: {new_master_id}")
                        break
                    time.sleep(0.1)
                else:
                    ids_now = self._get_all_master_ids(self.unique_name)
                    raise RuntimeError(f"创建主设备后无法识别其 ID。创建前: {ids_before}, 当前: {ids_now}")
                self.master_id = new_master_id
                self._create_virtual_devices()
                self._wait_for_device(self.virtual_mouse_name)
                logging.debug(f"将新虚拟设备附加到主设备 ID {self.master_id}")
                subprocess.check_call(['xinput', 'reattach', self.virtual_mouse_name, str(self.master_id)])
            else:
                self.master_id = master_id_to_use
                try:
                    self._create_virtual_devices()
                    subprocess.check_call(['xinput', 'reattach', self.virtual_mouse_name, str(self.master_id)])
                    logging.debug(f"已重新附加虚拟设备到 Master ID: {self.master_id}")
                except Exception as e_reopen:
                    logging.warning(f"复用设备 (Master ID: {self.master_id}) 时失败: {e_reopen}。滚动功能可能无效")
                    GLib.idle_add(send_notification, "隐形光标复用失败", "无法重新连接虚拟设备，滚动功能可能无效", "warning", config.WARNING_SOUND)
            self.park()
            logging.debug(f"隐形光标设置完成")
            self.is_ready = True
        except Exception as e:
            logging.error(f"创建/设置隐形光标失败: {e}")
            self.cleanup()
            GLib.idle_add(send_notification, "隐形光标初始化失败", f"无法创建虚拟设备: {e}", "warning", config.WARNING_SOUND)

    def move(self, x, y):
        # x, y: 缓冲区px全局坐标
        self.ui_mouse.write(e.EV_ABS, e.ABS_X, int(x))
        self.ui_mouse.write(e.EV_ABS, e.ABS_Y, int(y))
        self.ui_mouse.syn()

    def park(self):
        self.move(*self.park_pos)
        logging.debug(f"隐形光标已停放至全局坐标 {self.park_pos} 处")

    def scroll_discrete(self, num_clicks):
        if num_clicks == 0:
            return
        value = -1 if num_clicks > 0 else 1
        for _ in range(abs(num_clicks)):
            self.ui_mouse.write(e.EV_REL, e.REL_WHEEL, value)
            self.ui_mouse.syn()
            time.sleep(0.01)

    def cleanup(self):
        logging.info("清理隐形光标资源")
        if self.master_id is not None:
            self._remove_master_device(self.master_id)
            self.master_id = None
        if self.ui_mouse:
            self.ui_mouse.close()
            self.ui_mouse = None
        self.is_ready = False

class EvdevWheelScroller:
    """虚拟鼠标，用于触发滚轮事件"""
    def __init__(self):
        self.REL_WHEEL_HI_RES = getattr(e, 'REL_WHEEL_HI_RES', 0x08)
        capabilities = {
            e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT],
            e.EV_REL: [e.REL_WHEEL, self.REL_WHEEL_HI_RES],
        }
        self.ui_device = UInput(capabilities, name='scroll-stitch-wheel-mouse', version=0x1)
        logging.debug("EvdevWheelScroller 初始化成功，虚拟滚轮鼠标已创建")
        def _trigger_refresh():
            global hotkey_manager
            if hotkey_manager and hotkey_manager.listener and hasattr(hotkey_manager.listener, 'refresh_devices'):
                hotkey_manager.listener.refresh_devices()
        threading.Timer(0.5, _trigger_refresh).start()

    def scroll_discrete(self, num_clicks):
        if num_clicks == 0:
            return
        value = -1 if num_clicks > 0 else 1
        hi_res_value = value * 120
        for _ in range(abs(num_clicks)):
            self.ui_device.write(e.EV_REL, e.REL_WHEEL, value)
            self.ui_device.write(e.EV_REL, self.REL_WHEEL_HI_RES, hi_res_value)
            self.ui_device.syn()
            time.sleep(0.01)

    def close(self):
        if self.ui_device:
            self.ui_device.close()
            logging.debug("虚拟滚轮鼠标已关闭")

class EvdevAbsoluteMouse:
    """在 Wayland 下使用绝对定位设备来移动鼠标"""
    def __init__(self, min_x, min_y, max_x, max_y):
        # min_x, min_y, max_x, max_y: 缓冲区px全局坐标
        caps = {
            e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE],
            e.EV_ABS: [
                (e.ABS_X, AbsInfo(value=min_x, min=min_x, max=max_x, fuzz=0, flat=0, resolution=0)),
                (e.ABS_Y, AbsInfo(value=min_y, min=min_y, max=max_y, fuzz=0, flat=0, resolution=0)),
            ]
        }
        self.device = UInput(caps, name='scroll-stitch-mover', version=0x1)
        logging.debug(f"EvdevAbsoluteMouse 已创建: 全局坐标范围 ({min_x},{min_y}) -> ({max_x},{max_y})")

    def move(self, x, y):
        # x, y: 缓冲区px全局坐标
        self.device.write(e.EV_ABS, e.ABS_X, int(x))
        self.device.write(e.EV_ABS, e.ABS_Y, int(y))
        self.device.syn()

    def close(self):
        if self.device:
            self.device.close()

class StreamToLogger:
    def __init__(self, logger, level):
        self.logger = logger
        self.level = level
        self.linebuf = ''

    def write(self, buf):
        self.linebuf += buf
        while '\n' in self.linebuf:
            line, self.linebuf = self.linebuf.split('\n', 1)
            if line.rstrip():
                self.logger.log(self.level, line.rstrip())

    def flush(self):
        if self.linebuf.rstrip():
            self.logger.log(self.level, self.linebuf.rstrip())
        self.linebuf = ''

class SystemInteraction:
    _sound_themes = None
    MARKER_FILENAME = ".scroll_stitch_owned"

    @classmethod
    def ensure_temp_directory(cls, target_dir: Path):
        if target_dir.exists():
            return
        highest_new_dir = target_dir
        for parent in target_dir.parents:
            if parent.exists():
                break
            highest_new_dir = parent
        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            marker_path = highest_new_dir / cls.MARKER_FILENAME
            marker_path.touch()
            logging.debug(f"已在顶层新建目录 {highest_new_dir} 放置安全删除标记")
        except Exception as e:
            logging.error(f"创建安全删除标记文件失败: {e}")

    @classmethod
    def get_sound_themes(cls):
        if cls._sound_themes is not None:
            return cls._sound_themes
        sound_base_path = Path("/usr/share/sounds")
        themes = {}
        if not sound_base_path.is_dir():
            logging.warning(f"声音目录 {sound_base_path} 不存在，无法扫描主题")
            return themes
        for theme_path in sound_base_path.iterdir():
            stereo_path = theme_path / "stereo"
            if theme_path.is_dir() and stereo_path.is_dir():
                theme_name = theme_path.name
                sounds = {}
                for sound_file in stereo_path.iterdir():
                    if sound_file.is_file() and sound_file.suffix in ['.oga', '.wav', '.ogg']:
                        if sound_file.stem not in sounds:
                            sounds[sound_file.stem] = str(sound_file)
                if sounds:
                    themes[theme_name] = dict(sorted(sounds.items()))
        logging.debug(f"发现 {len(themes)} 个声音主题")
        cls._sound_themes = themes
        return themes

    @classmethod
    def play_sound(cls, sound_name: str, theme_name: str = None):
        if not sound_name:
            return
        effective_theme = theme_name if theme_name is not None else config.SOUND_THEME
        if not effective_theme:
            logging.warning("播放声音失败：未指定有效的主题")
            return
        themes = cls.get_sound_themes()
        sounds_in_theme = themes.get(effective_theme, {})
        sound_file_path = sounds_in_theme.get(sound_name)
        if not sound_file_path:
            logging.warning(f"在主题 '{effective_theme}' 中未找到声音文件: {sound_name}")
            return
        try:
            subprocess.Popen(["paplay", sound_file_path])
            logging.debug(f"正在播放声音: {sound_file_path}")
        except FileNotFoundError:
            logging.warning(f"播放命令 'paplay' 未找到，请确保已安装")

    @classmethod
    def copy_to_clipboard(cls, image_path: Path) -> tuple:
        copy_start_time = time.perf_counter()
        str_path = str(image_path)
        def _copy_path_fallback(path_str, fallback_msg):
            try:
                clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
                clipboard.set_text(path_str, -1)
                return True, fallback_msg
            except Exception as e:
                combined_error = f"{fallback_msg}，复制路径也失败了: {e}"
                logging.error(combined_error)
                return False, combined_error
        try:
            file_info, w, h = GdkPixbuf.Pixbuf.get_file_info(str_path)
            if file_info:
                raw_size = w * h * 4
                SAFE_LIMIT = 500 * 1024 * 1024
                if raw_size > SAFE_LIMIT:
                    logging.warning(f"图片原始数据过大（{raw_size/1024/1024:.0f} MB），跳过位图复制")
                    return _copy_path_fallback(str_path, "图片过大（>500MB），已改为复制路径")
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(str_path)
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clipboard.set_image(pixbuf)
            copy_duration = time.perf_counter() - copy_start_time
            logging.info(f"图片 {image_path} 已通过 GTK 复制到剪贴板，耗时: {copy_duration:.3f} 秒")
            return True, "并已复制到剪贴板"
        except Exception as e:
            logging.error(f"复制到剪贴板发生异常: {e}")
            return _copy_path_fallback(str_path, f"写入剪贴板出错，已改为复制路径 ({e})")

    @classmethod
    def cleanup_directory(cls, path: Path, known_files=None):
        if path and path.exists():
            current = path.resolve()
            while current != current.parent:
                if (current / cls.MARKER_FILENAME).is_file():
                    try:
                        shutil.rmtree(current)
                        logging.debug(f"已清理带有安全删除标记的整个目录树: {current}")
                    except OSError as e:
                        logging.error(f"清理目录树 {current} 失败: {e}")
                    break
                current = current.parent
            else:
                logging.debug(f"目录 {path} 及其父目录均无安全删除标记，执行保守清理")
                if known_files:
                    count = 0
                    for f in known_files:
                        fp = Path(f)
                        if fp.exists() and fp.is_file():
                            try: fp.unlink(); count += 1
                            except OSError: pass
                    logging.debug(f"已删除 {count} 个已知文件")

    @classmethod
    def cleanup_temp_dirs(cls, config_obj, is_exiting=False, known_files=None):
        try:
            raw_template = config_obj.get_raw_string('System', 'temp_directory')
            if '{pid}' not in raw_template:
                if is_exiting:
                    cls.cleanup_directory(config_obj.TEMP_DIRECTORY, known_files)
                else:
                    logging.debug("临时目录模板不包含 {pid}，跳过旧目录清理")
                return
            template_path = Path(raw_template).expanduser()
            base_dir = Path(template_path.anchor)
            pid_part = ""
            for part in template_path.parts:
                if '{pid}' in part:
                    pid_part = part
                    break
                if part != template_path.anchor:
                    base_dir = base_dir / part
            if not base_dir.is_dir():
                return
            escaped_part = re.escape(pid_part)
            pattern_str = "^" + escaped_part.replace(r'\{pid\}', r'(\d+)') + "$"
            regex = re.compile(pattern_str)
            current_pid = os.getpid()
            for item in base_dir.iterdir():
                if not item.is_dir(): continue
                match = regex.match(item.name)
                if not match: continue
                try:
                    pids = match.groups()
                    if not all(p == pids[0] for p in pids):
                        continue
                    pid = int(pids[0])
                except (ValueError, IndexError): continue
                is_current_process = (pid == current_pid)
                if is_current_process and not is_exiting:
                    continue
                if not is_current_process and Path(f"/proc/{pid}").exists():
                    continue
                cls.cleanup_directory(item, known_files if is_current_process else None)
        except Exception as e:
            logging.error(f"执行残留目录清理时发生未知错误: {e}")

    @classmethod
    def check_dependencies(cls):
        optional_deps = {
            'paplay': '用于播放音效',
            'xdg-open': '用于打开文件或目录',
        }
        if not IS_WAYLAND:
            optional_deps['xinput'] = '用于 X11 下“隐形光标”滚动模式'
        missing_optional = []
        for dep, purpose in optional_deps.items():
            if not shutil.which(dep):
                missing_optional.append(f"可选依赖 '{dep}' ({purpose}) 缺失")
        if missing_optional:
            logging.warning("检测到缺少可选依赖项，部分功能将无法使用或表现异常")
            GLib.idle_add(
                send_notification,
                "功能受限警告",
                f"检测到可选依赖缺失，部分功能将不可用\n请查看日志以获取详细信息",
                "warning", config.WARNING_SOUND, 2
            )
            for item in missing_optional:
                logging.warning(item)

    @classmethod
    def open_file(cls, path: Path, opener_command: str = "xdg-open"):
        if not path or not path.exists(): return
        try:
            if opener_command == 'default_browser':
                webbrowser.open(path.as_uri())
            elif opener_command == 'xdg-open' or not opener_command:
                subprocess.Popen(['xdg-open', str(path)])
            else:
                command_str = opener_command.replace('{filepath}', str(path))
                subprocess.Popen(shlex.split(command_str))
        except Exception as e:
            logging.error(f"打开文件失败 (cmd={opener_command}): {e}")

    @classmethod
    def open_directory(cls, path: Path):
        if not path: return
        try:
            target = path if path.is_dir() else path.parent
            subprocess.Popen(['xdg-open', str(target)])
        except Exception as e:
            logging.error(f"打开目录失败: {e}")

    @classmethod
    def load_library(cls, names, fallback_name=None):
        for name in names:
            try:
                return ctypes.CDLL(name)
            except OSError:
                continue
        if fallback_name:
            lib_path = find_library(fallback_name)
            if lib_path:
                try:
                    return ctypes.CDLL(lib_path)
                except OSError:
                    pass
        return None

    @classmethod
    def setup_logging(cls, config_obj):
        logging.raiseExceptions = False
        root_logger = logging.getLogger()
        if root_logger.hasHandlers():
            for handler in root_logger.handlers[:]:
                root_logger.removeHandler(handler)
        root_logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(levelname)-7s - %(message)s')
        file_handler = logging.FileHandler(config_obj.LOG_FILE, mode='w', encoding='utf-8')
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
        log_queue = queue.Queue()
        queue_handler = QueueHandler(log_queue)
        root_logger.addHandler(queue_handler)
        logging.getLogger('asyncio').setLevel(logging.WARNING)
        stdout_logger = logging.getLogger('STDOUT')
        sys.stdout = StreamToLogger(stdout_logger, logging.INFO)
        stderr_logger = logging.getLogger('STDERR')
        sys.stderr = StreamToLogger(stderr_logger, logging.ERROR)
        logging.debug("标准输出和标准错误已被重定向到日志系统")
        return log_queue

class EmbeddedWidget(Gtk.EventBox):
    """嵌入式组件基类"""
    def __init__(self, css_class=None):
        super().__init__()
        self.set_visible_window(True)
        if css_class:
            self.get_style_context().add_class(css_class)

class NotificationWidget(EmbeddedWidget):
    def __init__(self, overlay, title, message, level="normal", action_config=None):
        super().__init__(css_class="notification-panel")
        self.overlay = overlay
        self.action_config = action_config
        self.action_path = Path(action_config['path']) if action_config and action_config.get('path') else None
        # 逻辑px {
        self.set_size_request(config.NOTIFICATION_WIDTH, -1)
        if level and level != "normal":
            self.get_style_context().add_class(f"notification-{level}")
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        vbox.set_margin_top(8)
        vbox.set_margin_bottom(8)
        self.add(vbox)
        lbl_title = Gtk.Label(label=title)
        lbl_title.set_halign(Gtk.Align.CENTER)
        lbl_title.get_style_context().add_class("notif-title")
        vbox.pack_start(lbl_title, False, False, 0)
        lbl_msg = Gtk.Label(label=message)
        lbl_msg.set_halign(Gtk.Align.CENTER)
        lbl_msg.set_justify(Gtk.Justification.CENTER)
        lbl_msg.set_line_wrap(True)
        lbl_msg.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl_msg.get_style_context().add_class("notif-msg")
        vbox.pack_start(lbl_msg, False, False, 0)
        hbox_btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        hbox_btns.set_halign(Gtk.Align.CENTER)
        vbox.pack_start(hbox_btns, False, False, 5)
        # 逻辑px }
        self.btn_open_file = Gtk.Button(label="打开文件")
        self.btn_open_file.get_style_context().add_class("notif-btn")
        self.btn_open_file.connect("clicked", self._on_open_file)
        hbox_btns.pack_start(self.btn_open_file, False, False, 0)
        self.btn_open_dir = Gtk.Button(label="打开目录")
        self.btn_open_dir.get_style_context().add_class("notif-btn")
        self.btn_open_dir.connect("clicked", self._on_open_dir)
        hbox_btns.pack_start(self.btn_open_dir, False, False, 0)
        self.btn_close = Gtk.Button(label="关闭")
        self.btn_close.get_style_context().add_class("notif-btn")
        self.btn_close.connect("clicked", lambda w: self.close())
        hbox_btns.pack_start(self.btn_close, False, False, 0)
        if not self.action_path or not self.action_path.exists():
            self.btn_open_file.set_no_show_all(True)
            self.btn_open_file.hide()
            self.btn_open_dir.set_no_show_all(True)
            self.btn_open_dir.hide()
        elif self.action_path.is_dir():
            self.btn_open_file.set_no_show_all(True)
            self.btn_open_file.hide()
        self.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK | Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK)
        self.connect("realize", self._on_realize)
        self.connect("button-press-event", lambda w, e: True)
        self.connect("button-release-event", lambda w, e: True)
        self.connect("destroy", self._on_destroy)

    def do_get_preferred_width(self):
        return config.NOTIFICATION_WIDTH, config.NOTIFICATION_WIDTH # 逻辑px

    def _on_realize(self, widget):
        if self.get_window() and self.overlay:
            self.get_window().set_cursor(self.overlay.cursors['default'])

    def _on_open_file(self, widget):
        if not self.action_path: return
        opener_to_use = "xdg-open"
        is_large = False
        w = self.action_config.get('width', 0) if self.action_config else 0
        h = self.action_config.get('height', 0) if self.action_config else 0
        if config.MAX_VIEWER_DIMENSION >= 0 and w > 0 and h > 0:
            if max(w, h) > config.MAX_VIEWER_DIMENSION:
                is_large = True
        if is_large:
            opener_to_use = config.LARGE_IMAGE_OPENER
        SystemInteraction.open_file(self.action_path, opener_command=opener_to_use)
        self.close()

    def _on_open_dir(self, widget):
        if not self.action_path: return
        SystemInteraction.open_directory(self.action_path)
        self.close()

    def close(self):
        if self.overlay and self.overlay.overlay_manager:
            self.overlay.overlay_manager.dismiss(self)

    def _on_destroy(self, widget):
        if self.action_config:
            logging.info("通知关闭且处于完成状态，触发最终退出")
            self.overlay.controller.perform_cleanup()

def send_notification(title, message, level="normal", sound_name=None, timeout=None, action_config=None):
    try:
        if GLOBAL_OVERLAY:
            alloc = GLOBAL_OVERLAY.get_allocation()
            if not GLOBAL_OVERLAY.session.screen_rect or alloc.width <= 1:
                logging.debug(f"窗口尚未布局，推迟显示通知: {title}")
                GLib.timeout_add(500, send_notification, title, message, level, sound_name, timeout, action_config)
                return
            GLOBAL_OVERLAY.overlay_manager.dismiss_by_type(NotificationWidget)
            if sound_name:
                SystemInteraction.play_sound(sound_name)
            widget = NotificationWidget(GLOBAL_OVERLAY, title, message, level, action_config)
            if timeout is None:
                default_timeouts = {"normal": 3, "warning": 8, "success": 8, "critical": 0}
                timeout_sec = default_timeouts.get(level, 3)
            else:
                timeout_sec = timeout
            GLOBAL_OVERLAY.overlay_manager.show(widget, anchor='top-center', layer=OverlayManager.LAYER_TOP, auto_dismiss=timeout_sec)
        else:
            logging.warning("无法找到主窗口覆盖层，通知仅记录日志: " + message)
    except Exception as e:
        logging.error(f"发送内嵌通知失败: {e}")
        if action_config and GLOBAL_OVERLAY and GLOBAL_OVERLAY.controller:
            GLib.idle_add(GLOBAL_OVERLAY.controller.perform_cleanup)

class FrameGrabber(abc.ABC):
    @property
    @abc.abstractmethod
    def target_coords(self) -> CoordSys:
        pass

    @abc.abstractmethod
    def prepare(self):
        pass

    @abc.abstractmethod
    def capture(self, x, y, w, h, filepath: Path, scale: float = 1.0, include_cursor: bool = False) -> bool:
        pass

    @abc.abstractmethod
    def cleanup(self):
        pass

class XImageFuncs(Structure):
    _fields_ = [
        ("create_image", c_void_p),
        ("destroy_image", c_void_p),
        ("get_pixel", c_void_p),
        ("put_pixel", c_void_p),
        ("sub_image", c_void_p),
        ("add_pixel", c_void_p),
    ]

# 缓冲区px {
class XImage(Structure):
    _fields_ = [
        ("width", c_int), ("height", c_int), ("xoffset", c_int), ("format", c_int),
        ("data", c_void_p), ("byte_order", c_int), ("bitmap_unit", c_int),
        ("bitmap_bit_order", c_int), ("bitmap_pad", c_int), ("depth", c_int),
        ("bytes_per_line", c_int), ("bits_per_pixel", c_int),
        ("red_mask", c_ulong), ("green_mask", c_ulong), ("blue_mask", c_ulong),
        ("obdata", c_void_p), ("f", XImageFuncs),
    ]

class XShmSegmentInfo(Structure):
    _fields_ = [("shmseg", c_ulong), ("shmid", c_int), ("shmaddr", c_void_p), ("readOnly", c_int)]

class XFixesCursorImage(Structure):
    _fields_ = [
        ('x', c_short), # 全局坐标
        ('y', c_short),
        ('width', c_short),
        ('height', c_short),
        ('xhot', c_short),
        ('yhot', c_short),
        ('cursor_serial', c_ulong),
        ('pixels', POINTER(c_ulong)),
        ('atom', c_ulong),
        ('name', c_char_p),
    ]

IPC_PRIVATE = 0
IPC_CREAT = 0o1000
IPC_RMID = 0
ZPixmap = 2
ALL_PLANES = c_ulong(-1).value

class X11FrameGrabber(FrameGrabber):
    def __init__(self):
        super().__init__()
        self.libx11 = SystemInteraction.load_library(['libX11.so.6', 'libX11.so'], 'X11')
        self.libxext = SystemInteraction.load_library(['libXext.so.6', 'libXext.so'], 'Xext')
        self.libxfixes = SystemInteraction.load_library(['libXfixes.so.3', 'libXfixes.so'], 'Xfixes')
        self.libc = SystemInteraction.load_library(['libc.so.6', 'libc.so'], 'c')
        self.dpy = None
        self.shminfo = None
        self.img = None
        self.root_w = 0
        self.root_h = 0
        self.has_xfixes = False
        self._setup_x11_funcs()

    def _setup_x11_funcs(self):
        if not self.libx11 or not self.libxext or not self.libc:
            logging.error("无法加载 X11/Xext/Libc 库，XShm 截图不可用")
            return
        self.libx11.XOpenDisplay.restype = c_void_p
        self.libx11.XOpenDisplay.argtypes = [c_char_p]
        self.libx11.XCloseDisplay.argtypes = [c_void_p]
        self.libx11.XDefaultScreen.restype = c_int
        self.libx11.XDefaultScreen.argtypes = [c_void_p]
        self.libx11.XDefaultRootWindow.restype = c_ulong
        self.libx11.XDefaultRootWindow.argtypes = [c_void_p]
        self.libx11.XGetGeometry.restype = c_int
        self.libx11.XGetGeometry.argtypes = [c_void_p, c_ulong, POINTER(c_ulong), POINTER(c_int), POINTER(c_int), POINTER(c_uint), POINTER(c_uint), POINTER(c_uint), POINTER(c_uint)]
        self.libx11.XSync.argtypes = [c_void_p, c_int]
        self.libx11.XDefaultVisual.restype = c_void_p
        self.libx11.XDefaultVisual.argtypes = [c_void_p, c_int]
        self.libx11.XDefaultDepth.restype = c_int
        self.libx11.XDefaultDepth.argtypes = [c_void_p, c_int]
        self.libx11.XFree.argtypes = [c_void_p]
        self.libx11.XQueryExtension.restype = c_int
        self.libx11.XQueryExtension.argtypes = [c_void_p, c_char_p, POINTER(c_int), POINTER(c_int), POINTER(c_int)]
        self.libxext.XShmQueryExtension.restype = c_int
        self.libxext.XShmQueryExtension.argtypes = [c_void_p]
        self.libxext.XShmCreateImage.restype = POINTER(XImage)
        self.libxext.XShmCreateImage.argtypes = [c_void_p, c_void_p, c_uint, c_int, c_char_p, POINTER(XShmSegmentInfo), c_uint, c_uint]
        self.libxext.XShmAttach.restype = c_int
        self.libxext.XShmAttach.argtypes = [c_void_p, POINTER(XShmSegmentInfo)]
        self.libxext.XShmDetach.restype = c_int
        self.libxext.XShmDetach.argtypes = [c_void_p, POINTER(XShmSegmentInfo)]
        self.libxext.XShmGetImage.restype = c_int
        self.libxext.XShmGetImage.argtypes = [c_void_p, c_ulong, POINTER(XImage), c_int, c_int, c_ulong]
        self.libc.shmget.restype = c_int
        self.libc.shmget.argtypes = [c_int, c_size_t, c_int]
        self.libc.shmat.restype = c_void_p
        self.libc.shmat.argtypes = [c_int, c_void_p, c_int]
        self.libc.shmdt.restype = c_int
        self.libc.shmdt.argtypes = [c_void_p]
        self.libc.shmctl.restype = c_int
        self.libc.shmctl.argtypes = [c_int, c_int, c_void_p]
        if self.libxfixes:
            self.libxfixes.XFixesGetCursorImage.restype = POINTER(XFixesCursorImage)
            self.libxfixes.XFixesGetCursorImage.argtypes = [c_void_p]

    @property
    def target_coords(self) -> CoordSys:
        return CoordSys.GLOBAL

    def prepare(self):
        if self.dpy: return True
        self.dpy = self.libx11.XOpenDisplay(None)
        if not self.dpy:
            logging.error("无法打开 Display")
            return False
        if not self.libxext.XShmQueryExtension(self.dpy):
            logging.error("当前环境不支持 SHM 扩展")
            self.cleanup()
            return False
        dummy1, dummy2, dummy3 = c_int(), c_int(), c_int()
        if self.libx11.XQueryExtension(self.dpy, b"XFIXES", byref(dummy1), byref(dummy2), byref(dummy3)):
            self.has_xfixes = True
        else:
            self.has_xfixes = False
            logging.warning("XFIXES 扩展不可用，将无法捕获光标")
        self.screen = self.libx11.XDefaultScreen(self.dpy)
        self.root = self.libx11.XDefaultRootWindow(self.dpy)
        self.visual = self.libx11.XDefaultVisual(self.dpy, self.screen)
        self.depth = self.libx11.XDefaultDepth(self.dpy, self.screen)
        self._init_shm_image()
        return True

    def _get_root_geometry(self):
        if not self.dpy: return 0, 0
        root_return = c_ulong()
        x_return = c_int()
        y_return = c_int()
        width_return = c_uint()
        height_return = c_uint()
        border_width_return = c_uint()
        depth_return = c_uint()
        status = self.libx11.XGetGeometry(
            self.dpy, self.root,
            byref(root_return), byref(x_return), byref(y_return),
            byref(width_return), byref(height_return),
            byref(border_width_return), byref(depth_return)
        )
        if status == 0:
            logging.warning("XGetGeometry 失败")
            return 0, 0
        return width_return.value, height_return.value

    def _init_shm_image(self):
        self.root_w, self.root_h = self._get_root_geometry()
        if self.root_w == 0 or self.root_h == 0:
            raise RuntimeError("无法获取根窗口尺寸")
        self.shminfo = XShmSegmentInfo()
        self.img = self.libxext.XShmCreateImage(
            self.dpy, self.visual, self.depth, ZPixmap, None,
            byref(self.shminfo), self.root_w, self.root_h
        )
        if not self.img: raise RuntimeError("XShmCreateImage 失败")
        size = self.img.contents.height * self.img.contents.bytes_per_line
        self.shminfo.shmid = self.libc.shmget(IPC_PRIVATE, size, IPC_CREAT | 0o600)
        if self.shminfo.shmid < 0: raise RuntimeError("shmget 失败")
        try:
            addr = self.libc.shmat(self.shminfo.shmid, None, 0)
            if addr == c_void_p(-1).value: raise RuntimeError("shmat 失败")
            self.shminfo.shmaddr = addr
            self.shminfo.readOnly = 0
            self.img.contents.data = addr
            if not self.libxext.XShmAttach(self.dpy, byref(self.shminfo)):
                raise RuntimeError("XShmAttach 失败")
        finally:
            self.libc.shmctl(self.shminfo.shmid, IPC_RMID, None)
        self.libx11.XSync(self.dpy, 0)
        logging.debug(f"XShm 初始化完成，根窗口：{self.root_w} x {self.root_h} 缓冲区px")

    def capture(self, x, y, w, h, filepath: Path, scale: float = 1.0, include_cursor: bool = False) -> bool:
        """使用 XShm 从根窗口截取指定区域并保存到文件"""
        # x, y: 全局坐标; x, y, w, h: 逻辑px
        try:
            if not self.prepare(): return False
            cur_w, cur_h = self._get_root_geometry()
            if cur_w > 0 and cur_h > 0 and (cur_w != self.root_w or cur_h != self.root_h):
                logging.debug(f"检测到根窗口变更（{self.root_w} x {self.root_h} -> {cur_w} x {cur_h}），正在重新初始化 XShm...")
                self.cleanup()
                if not self.prepare(): return False
            cursor_info = None
            if include_cursor:
                cursor_info = self._get_cursor_image()
            # 逻辑px -> 缓冲区px
            g_x_buf = math.ceil(x * scale)
            g_y_buf = math.ceil(y * scale)
            g_x_end_buf = int((x + w) * scale)
            g_y_end_buf = int((y + h) * scale)
            w_buf = g_x_end_buf - g_x_buf
            h_buf = g_y_end_buf - g_y_buf
            if g_x_buf < 0: g_x_buf = 0
            if g_y_buf < 0: g_y_buf = 0
            if g_x_buf + w_buf > self.root_w: w_buf = self.root_w - g_x_buf
            if g_y_buf + h_buf > self.root_h: h_buf = self.root_h - g_y_buf
            if w_buf <= 0 or h_buf <= 0:
                logging.warning(f"截图区域无效")
                return False
            if not self.libxext.XShmGetImage(self.dpy, self.root, self.img, 0, 0, ALL_PLANES):
                logging.error("XShmGetImage 失败")
                return False
            h_total = self.img.contents.height
            stride = self.img.contents.bytes_per_line
            buf_len = h_total * stride
            buf_ptr = cast(self.img.contents.data, POINTER(c_ubyte * buf_len))
            raw_view = np.ctypeslib.as_array(buf_ptr.contents).reshape(h_total, stride)
            bpp = self.img.contents.bits_per_pixel // 8
            roi_raw = raw_view[g_y_buf : g_y_buf + h_buf, g_x_buf * bpp : (g_x_buf + w_buf) * bpp]
            src_bgra = roi_raw.reshape(h_buf, w_buf, 4)
            final_bgr = cv2.cvtColor(src_bgra, cv2.COLOR_BGRA2BGR)
            if cursor_info:
                final_bgr = self._blend_cursor(final_bgr, cursor_info, g_x_buf, g_y_buf)
            success = cv2.imwrite(str(filepath), final_bgr)
            if not success:
                logging.error(f"XShm 截图写入文件 {filepath} 失败")
                return False
            return True
        except Exception as e:
            logging.error(f"XShm 截图失败: {e}")
            return False

    def _get_cursor_image(self):
        if not self.dpy or not self.libxfixes or not self.has_xfixes: return None
        try:
            cursor_ptr = self.libxfixes.XFixesGetCursorImage(self.dpy)
            if not cursor_ptr: return None
            c = cursor_ptr.contents
            width, height = c.width, c.height
            if width <= 0 or height <= 0:
                self.libx11.XFree(cursor_ptr)
                return None
            count = width * height
            raw_data = np.ctypeslib.as_array(c.pixels, shape=(count,))
            raw_data = raw_data.astype(np.uint32)
            bgra = raw_data.view(np.uint8)
            result = {
                'image': bgra.reshape((height, width, 4)),
                'x': c.x, 'y': c.y, 'xhot': c.xhot, 'yhot': c.yhot,
                'width': width, 'height': height
            }
            self.libx11.XFree(cursor_ptr)
            return result
        except Exception as e:
            logging.warning(f"获取光标图像失败: {e}")
            return None

    def _blend_cursor(self, screenshot_array, cursor_info, cap_g_x, cap_g_y):
        """将光标图像混合到截图中"""
        # cap_g_x, cap_g_y: 缓冲区px全局坐标
        try:
            cursor_x = cursor_info['x'] - cursor_info['xhot'] - cap_g_x
            cursor_y = cursor_info['y'] - cursor_info['yhot'] - cap_g_y
            cursor_img = cursor_info['image']
            cursor_h, cursor_w = cursor_img.shape[:2]
            shot_h, shot_w = screenshot_array.shape[:2]
            dst_x = max(0, cursor_x)
            dst_y = max(0, cursor_y)
            dst_x_end = min(shot_w, cursor_x + cursor_w)
            dst_y_end = min(shot_h, cursor_y + cursor_h)
            src_x = max(0, -cursor_x)
            src_y = max(0, -cursor_y)
            src_x_end = src_x + (dst_x_end - dst_x)
            src_y_end = src_y + (dst_y_end - dst_y)
            if dst_x >= dst_x_end or dst_y >= dst_y_end:
                return screenshot_array
            cursor_region = cursor_img[src_y:src_y_end, src_x:src_x_end]
            screenshot_region = screenshot_array[dst_y:dst_y_end, dst_x:dst_x_end]
            alpha = cursor_region[:, :, 3:4] / 255.0
            blended = screenshot_region[:, :, :3] * (1 - alpha) + cursor_region[:, :, :3] * alpha
            screenshot_array[dst_y:dst_y_end, dst_x:dst_x_end, :3] = blended.astype(np.uint8)
            return screenshot_array
        except Exception as e:
            logging.error(f'混合光标图像失败: {e}')
            return screenshot_array
# 缓冲区px }

    def cleanup(self):
        if self.dpy:
            if self.shminfo and self.shminfo.shmaddr:
                self.libxext.XShmDetach(self.dpy, byref(self.shminfo))
                self.libx11.XSync(self.dpy, 0)
            if self.img:
                destroy_func = ctypes.CFUNCTYPE(c_int, POINTER(XImage))(self.img.contents.f.destroy_image)
                destroy_func(self.img)
                self.img = None
            if self.shminfo:
                if self.shminfo.shmaddr: self.libc.shmdt(self.shminfo.shmaddr)
                self.shminfo = None
            self.libx11.XCloseDisplay(self.dpy)
            self.dpy = None

class WaylandFrameGrabber(FrameGrabber):
    def __init__(self):
        Gst.init(None)
        self.state = "IDLE"
        self.session_handle = None
        self.pipewire_node_id = None
        self.pipeline = None
        self.appsink = None
        self.latest_frame = None
        self.frame_lock = threading.Lock()
        self.connection = None
        self.portal = None
        self.init_loop = None
        self.last_error = None
        self.user_cancelled = False
        self._setup_dbus()

    def _setup_dbus(self):
        try:
            self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self.portal = Gio.DBusProxy.new_sync(
                self.connection, Gio.DBusProxyFlags.NONE, None,
                'org.freedesktop.portal.Desktop',
                '/org/freedesktop/portal/desktop',
                'org.freedesktop.portal.ScreenCast', None
            )
        except Exception as e:
            logging.error(f"DBus 连接失败: {e}")

    @property
    def target_coords(self) -> CoordSys:
        return CoordSys.MONITOR

    def prepare(self):
        if self.state != "IDLE": return True
        logging.info("正在请求屏幕录制权限...")
        self.init_loop = GLib.MainLoop()
        self._start_portal_request()
        if self.state == "ERROR":
            logging.warning("WaylandFrameGrabber: 初始化请求失败，跳过事件循环")
            self.init_loop = None
            return True
        try:
            self.init_loop.run()
        except KeyboardInterrupt:
            self.user_cancelled = True
            return False
        self.init_loop = None
        if self.user_cancelled:
            logging.info("WaylandFrameGrabber: 用户取消了授权")
            return False
        if self.state == "STREAMING":
            logging.info("WaylandFrameGrabber: 授权成功，后台流已启动")
        else:
            logging.warning(f"WaylandFrameGrabber: 未进入流状态 (state: {self.state})")
        return True

    def _start_portal_request(self):
        self.state = "REQUESTING"
        self.connection.signal_subscribe(
            'org.freedesktop.portal.Desktop', 'org.freedesktop.portal.Request',
            'Response', None, None, Gio.DBusSignalFlags.NONE,
            self._on_portal_response, None
        )
        request_token = f"ss_{os.getpid()}_{int(time.time()*1000)}"
        options = {
            'handle_token': GLib.Variant('s', request_token),
            'session_handle_token': GLib.Variant('s', f"session_{request_token}")
        }
        try:
            self.portal.call_sync('CreateSession', GLib.Variant('(a{sv})', (options,)), Gio.DBusCallFlags.NONE, -1, None)
        except Exception as e:
            err_msg = f"CreateSession 失败:\n{e}"
            logging.error(err_msg)
            self.state = "ERROR"
            self.last_error = err_msg
            if self.init_loop: self.init_loop.quit()

    def _on_portal_response(self, connection, sender, path, iface, signal, params, user_data):
        response_code, results = params.unpack()
        if response_code == 1:
            logging.info("Portal 请求被用户取消 (code=1)")
            self.user_cancelled = True
            self.state = "CANCELLED"
            if self.init_loop: self.init_loop.quit()
            return
        elif response_code != 0:
            logging.error(f"Portal 请求失败 (code={response_code})")
            self.last_error = f"屏幕录制请求失败 (code: {response_code})"
            self.state = "ERROR"
            if self.init_loop: self.init_loop.quit()
            return
        result_dict = dict(results)
        if self.state == "REQUESTING":
            if 'session_handle' in result_dict:
                self.session_handle = result_dict['session_handle']
                cursor_mode_val = 2 if config.CAPTURE_WITH_CURSOR else 1
                opts = {
                    'handle_token': GLib.Variant('s', f"sel_{os.getpid()}"),
                    'types': GLib.Variant('u', 1),
                    'multiple': GLib.Variant('b', False),
                    'cursor_mode': GLib.Variant('u', cursor_mode_val)
                }
                self.portal.call_sync('SelectSources', GLib.Variant('(oa{sv})', (self.session_handle, opts)), Gio.DBusCallFlags.NONE, -1, None)
                self.state = "SELECTING"
        elif self.state == "SELECTING":
            self.state = "STARTING"
            opts = {'handle_token': GLib.Variant('s', f"start_{os.getpid()}")}
            self.portal.call_sync('Start', GLib.Variant('(osa{sv})', (self.session_handle, '', opts)), Gio.DBusCallFlags.NONE, -1, None)
        elif self.state == "STARTING":
            if 'streams' in result_dict and len(result_dict['streams']) > 0:
                self.pipewire_node_id = result_dict['streams'][0][0]
                self.state = "STREAMING"
                self._start_pipeline()
                if self.init_loop: self.init_loop.quit()

    def _start_pipeline(self):
        pipeline_str = (
            f"pipewiresrc path={self.pipewire_node_id} do-timestamp=true ! "
            f"videoconvert ! video/x-raw,format=BGRx ! "
            f"appsink name=mysink emit-signals=true drop=true max-buffers=1 sync=false"
        )
        try:
            self.pipeline = Gst.parse_launch(pipeline_str)
            self.appsink = self.pipeline.get_by_name('mysink')
            self.appsink.connect('new-sample', self._on_new_sample)
            bus = self.pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message", self._on_bus_message)
            self.pipeline.set_state(Gst.State.PLAYING)
        except Exception as e:
            err_str = f"Pipeline 启动失败:\n{e}"
            logging.error(err_str)
            self.last_error = err_str

    def _on_bus_message(self, bus, message):
        if message.type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            self.last_error = f"GStreamer 错误: {err.message}\n(debug: {debug})"
            logging.error(self.last_error)

    def _on_new_sample(self, appsink):
        # 缓冲区px
        sample = appsink.emit('pull-sample')
        if sample is None: return Gst.FlowReturn.ERROR
        buffer = sample.get_buffer()
        caps = sample.get_caps()
        success, map_info = buffer.map(Gst.MapFlags.READ)
        if not success: return Gst.FlowReturn.ERROR
        try:
            info = GstVideo.VideoInfo.new_from_caps(caps)
            w, h = info.width, info.height
            stride = info.stride[0]
            if map_info.size >= h * stride:
                raw_bgra = np.ndarray(shape=(h, w, 4), dtype=np.uint8, buffer=map_info.data, strides=(stride, 4, 1))
                frame_bgr = cv2.cvtColor(raw_bgra, cv2.COLOR_BGRA2BGR)
                with self.frame_lock:
                    self.latest_frame = frame_bgr
        finally:
            buffer.unmap(map_info)
        return Gst.FlowReturn.OK

    def wait_for_valid_frame(self, timeout=2.0):
        start_time = time.time()
        while time.time() - start_time < timeout:
            with self.frame_lock:
                if self.latest_frame is not None:
                    h, w, _ = self.latest_frame.shape
                    if w > 0 and h > 0:
                        return w, h
            if self.last_error or self.state == "ERROR":
                raise RuntimeError(self.last_error or "GStreamer 发生未知错误")
            time.sleep(0.05)
        raise TimeoutError("等待视频流初始化超时")

    def capture(self, x, y, w, h, filepath: Path, scale: float = 1.0, include_cursor: bool = False) -> bool:
        # x, y: 显示器坐标; x, y, w, h: 逻辑px
        with self.frame_lock:
            if self.latest_frame is None: return False
            # 逻辑px -> 缓冲区px
            x_buf = math.ceil(x * scale)
            y_buf = math.ceil(y * scale)
            x_end_buf = int((x + w) * scale)
            y_end_buf = int((y + h) * scale)
            w_buf = x_end_buf - x_buf
            h_buf = y_end_buf - y_buf
            img_h, img_w, _ = self.latest_frame.shape # 缓冲区px
            x1 = max(0, x_buf)
            y1 = max(0, y_buf)
            x2 = min(img_w, x_buf + w_buf)
            y2 = min(img_h, y_buf + h_buf)
            crop = self.latest_frame[y1:y2, x1:x2]
        try:
            success = cv2.imwrite(str(filepath), crop)
            if not success:
                logging.error(f"Wayland 截图写入文件 {filepath} 失败")
                return False
            return True
        except Exception as e:
            logging.error(f"Wayland 截图保存失败: {e}")
            return False

    def cleanup(self):
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
            self.appsink = None
        if self.session_handle and self.connection:
            try:
                self.connection.call_sync(
                    'org.freedesktop.portal.Desktop',
                    self.session_handle,
                    'org.freedesktop.portal.Session',
                    'Close',
                    None, None, Gio.DBusCallFlags.NONE, -1, None
                )
            except Exception as e:
                logging.warning(f"关闭 session 失败: {e}")
        self.session_handle = None
        self.pipewire_node_id = None
        self.state = "IDLE"

class ImageMatcher:
    THRES_ABS_GOOD, THRES_ABS_VALID, THRES_ABS_BAD = 0.005, 0.02, 0.03
    THRES_SQ_GOOD, THRES_SQ_VALID = 0.01, 0.15
    THRES_CC_GOOD, THRES_CC_VALID = 0.95, 0.80
    THRES_STATIC_ABS = 0.005
    THRES_SCORE = 5.0
    THRES_TEXTURE = 3.0

    @classmethod
    def configure(cls, config_obj):
        cls.THRES_SCORE = config_obj.THRES_SCORE
        cls.THRES_TEXTURE = config_obj.THRES_TEXTURE

    @staticmethod
    def _compute_similarity_metrics(region1, region2):
        if region1.shape != region2.shape:
            return False, 0
        mean_bgr = cv2.mean(cv2.absdiff(region1, region2))
        s_abs = (mean_bgr[0] + mean_bgr[1] + mean_bgr[2]) / (3 * 255.0)
        if s_abs > ImageMatcher.THRES_ABS_BAD:
            return False, 0
        res_sq = cv2.matchTemplate(region1, region2, cv2.TM_SQDIFF_NORMED)
        s_sq = res_sq[0][0]
        res_cc = cv2.matchTemplate(region1, region2, cv2.TM_CCOEFF_NORMED)
        s_cc = res_cc[0][0]
        is_valid = (s_cc >= ImageMatcher.THRES_CC_VALID and s_sq <= ImageMatcher.THRES_SQ_VALID and s_abs <= ImageMatcher.THRES_ABS_VALID)
        points = 0
        if s_cc > ImageMatcher.THRES_CC_GOOD: points += 1
        if s_sq < ImageMatcher.THRES_SQ_GOOD: points += 1
        if s_abs < ImageMatcher.THRES_ABS_GOOD: points += 1
        return is_valid, points

    @staticmethod
    def detect_static_bars(img_top, img_bottom, prev_y=0, curr_y=0):
        h_top, w_top = img_top.shape[:2]
        h_bot, w_bot = img_bottom.shape[:2]
        box_shift = curr_y - prev_y
        delta_h = h_bot - h_top
        def_bot_h_header = max(0, -box_shift)
        def_bot_h_footer = max(0, box_shift + delta_h)
        y_start_intersect = max(prev_y, curr_y)
        y_end_intersect = min(prev_y + h_top, curr_y + h_bot)
        h_header_found = 0
        h_footer_found = 0
        w_left = 0
        w_right = 0
        if y_end_intersect > y_start_intersect:
            t_y1 = y_start_intersect - prev_y
            t_y2 = y_end_intersect - prev_y
            b_y1 = y_start_intersect - curr_y
            b_y2 = y_end_intersect - curr_y
            strip_top = img_top[t_y1:t_y2, :]
            strip_bot = img_bottom[b_y1:b_y2, :]
            intersect_h = t_y2 - t_y1
            max_header_search = intersect_h
            for y in range(max_header_search):
                row_top = strip_top[y:y+1, :]
                row_bot = strip_bot[y:y+1, :]
                mb = cv2.mean(cv2.absdiff(row_top, row_bot))
                mae = (mb[0] + mb[1] + mb[2]) / (3 * 255.0)
                if mae <= ImageMatcher.THRES_STATIC_ABS: h_header_found = y + 1
                else: break
            max_footer_search = intersect_h - h_header_found
            for y in range(max_footer_search):
                row_top = strip_top[intersect_h - 1 - y : intersect_h - y, :]
                row_bot = strip_bot[intersect_h - 1 - y : intersect_h - y, :]
                mb = cv2.mean(cv2.absdiff(row_top, row_bot))
                mae = (mb[0] + mb[1] + mb[2]) / (3 * 255.0)
                if mae <= ImageMatcher.THRES_STATIC_ABS: h_footer_found = y + 1
                else: break
            max_left_search = w_bot
            for x in range(max_left_search):
                col_top = strip_top[:, x:x+1]
                col_bot = strip_bot[:, x:x+1]
                mb = cv2.mean(cv2.absdiff(col_top, col_bot))
                mae = (mb[0] + mb[1] + mb[2]) / (3 * 255.0)
                if mae <= ImageMatcher.THRES_STATIC_ABS: w_left = x + 1
                else: break
            max_right_search = w_bot - w_left
            for x in range(max_right_search):
                col_top = strip_top[:, w_top - 1 - x : w_top - x]
                col_bot = strip_bot[:, w_bot - 1 - x : w_bot - x]
                mb = cv2.mean(cv2.absdiff(col_top, col_bot))
                mae = (mb[0] + mb[1] + mb[2]) / (3 * 255.0)
                if mae <= ImageMatcher.THRES_STATIC_ABS: w_right = x + 1
                else: break
        final_bot_header = def_bot_h_header + h_header_found
        final_bot_footer = def_bot_h_footer + h_footer_found
        if final_bot_header + final_bot_footer >= 0.6 * h_bot:
            final_bot_header = def_bot_h_header
            final_bot_footer = def_bot_h_footer
        if w_left + w_right >= 0.6 * w_bot:
            w_left = 0
            w_right = 0
        return final_bot_header, final_bot_footer, w_left, w_right

    @staticmethod
    def verify_region(img_top, img_bottom, shift, match_y_bot, static_bars, box_shift=0):
        h_top = img_top.shape[0]
        h_bot = img_bottom.shape[0]
        w_bot = img_bottom.shape[1]
        bot_h_header, bot_h_footer, w_left, w_right = static_bars
        match_y_top = match_y_bot + shift
        if match_y_top < 0:
            match_y_bot -= match_y_top
            match_y_top = 0
        delta_h = h_bot - h_top
        top_h_footer = bot_h_footer - (box_shift + delta_h)
        if match_y_top >= h_top - top_h_footer:
            top_h_footer = 0
        max_h_valid_top = h_top - top_h_footer - match_y_top
        max_h_valid_bot = h_bot - bot_h_footer - match_y_bot
        check_h = min(max_h_valid_top, max_h_valid_bot)
        if check_h <= 0:
            return float('-inf'), match_y_bot
        valid_w_end = w_bot - w_right
        region_top = img_top[match_y_top : match_y_top + check_h, w_left : valid_w_end]
        region_bot = img_bottom[match_y_bot : match_y_bot + check_h, w_left : valid_w_end]
        rows, cols = 6, 6
        if check_h < 2 * rows: rows = 1
        h_step = check_h // rows
        w_step = region_bot.shape[1] // cols
        score = 0.0
        best_row_score = float('-inf')
        best_row_idx = 0
        debug_grid = [["" for _ in range(cols)] for _ in range(rows)]
        for r in range(rows):
            row_score = 0.0
            y1, y2 = r*h_step, (r+1)*h_step
            for c in range(cols):
                x1, x2 = c*w_step, (c+1)*w_step
                b_top = region_top[y1:y2, x1:x2]
                b_bot = region_bot[y1:y2, x1:x2]
                mean_bgr = cv2.mean(cv2.absdiff(b_top, b_bot))
                mae = (mean_bgr[0] + mean_bgr[1] + mean_bgr[2]) / (3 * 255.0)
                delta = 0.0
                if mae > ImageMatcher.THRES_ABS_BAD:
                    delta = -1.5
                    debug_grid[r][c] = "X"
                elif mae < ImageMatcher.THRES_ABS_GOOD:
                    std_top = cv2.meanStdDev(cv2.cvtColor(b_top, cv2.COLOR_BGR2GRAY))[1][0][0]
                    std_bot = cv2.meanStdDev(cv2.cvtColor(b_bot, cv2.COLOR_BGR2GRAY))[1][0][0]
                    if std_top > ImageMatcher.THRES_TEXTURE and std_bot > ImageMatcher.THRES_TEXTURE:
                        delta = 1.0
                        debug_grid[r][c] = "O"
                    else:
                        delta = 0.0
                        debug_grid[r][c] = "-"
                else:
                    delta = 0.0
                    debug_grid[r][c] = "."
                score += delta
                row_score += delta
            if row_score > best_row_score:
                best_row_score = row_score
                best_row_idx = r
        log_msg = [f"\n  score={score:.1f}（shift={shift}，top: y=[{match_y_top}:{match_y_top + check_h}]，bot: y=[{match_y_bot}:{match_y_bot + check_h}]）"]
        for r in range(rows):
            row_str = "  " + " ".join([f"[{char}]" for char in debug_grid[r]])
            log_msg.append(row_str)
        logging.debug("\n".join(log_msg))
        best_cut_y = match_y_bot + (best_row_idx * h_step) + (h_step // 2)
        return score, best_cut_y

    @staticmethod
    def _search_in_row(img_top, row_img, row_rect, static_bars, box_shift, delta_h, min_shift, max_shift):
        rx, ry, rw, rh = row_rect
        bot_h_header, bot_h_footer, w_left, w_right = static_bars
        top_h_header = bot_h_header + box_shift
        top_h_footer = bot_h_footer - (box_shift + delta_h)
        valid_rw = rw - w_left - w_right
        num_cols = max(1, round(valid_rw ** 0.5 / 3.5))
        col_w = valid_rw // num_cols
        blocks = []
        row_gray = cv2.cvtColor(row_img[:, w_left : rw - w_right], cv2.COLOR_BGR2GRAY)
        for c in range(num_cols):
            cx = c * col_w
            cw = col_w
            if cx + cw > rw: cw = rw - cx
            block_roi = row_gray[:, cx:cx+cw]
            tex_score = cv2.meanStdDev(block_roi)[1][0][0]
            if tex_score > ImageMatcher.THRES_TEXTURE:
                blocks.append({
                    'tex_score': tex_score,
                    'rect': (rx + w_left + cx, ry, cw, rh),
                    'block_bgr': row_img[:, w_left + cx : w_left + cx + cw]
                })
        blocks.sort(key=lambda x: x['tex_score'], reverse=True)
        target_blocks = blocks[:4]
        if not target_blocks: return None
        candidates = []
        h_top = img_top.shape[0]
        strip_start_y = max(top_h_header, ry + min_shift)
        if top_h_footer == bot_h_footer:
            limit_y = h_top - top_h_footer
        else:
            limit_y = h_top
        strip_end_y = min(limit_y, ry + max_shift + rh)
        search_area_h = strip_end_y - strip_start_y
        if search_area_h < rh:
            return None
        scale_factor = max(0.08, (8.0 / search_area_h) ** 0.5)
        fine_radius = max(3, int(0.8 / scale_factor))
        for b in target_blocks:
            bx, by, bw, bh = b['rect']
            strip_top = img_top[strip_start_y : strip_end_y, bx : bx + bw]
            if strip_top.shape[0] < bh: continue
            small_strip = cv2.resize(strip_top, (0,0), fx=scale_factor, fy=scale_factor, interpolation=cv2.INTER_AREA)
            small_block = cv2.resize(b['block_bgr'], (0,0), fx=scale_factor, fy=scale_factor, interpolation=cv2.INTER_AREA)
            if small_strip.shape[0] < small_block.shape[0]: continue
            res = cv2.matchTemplate(small_strip, small_block, cv2.TM_SQDIFF_NORMED)
            _, _, min_loc, _ = cv2.minMaxLoc(res)
            found_y_small = min_loc[1]
            center_y = int(found_y_small / scale_factor)
            fy_start = max(0, center_y - fine_radius)
            fy_end = min(strip_top.shape[0] - bh, center_y + fine_radius + 1)
            if fy_end <= fy_start: continue
            roi_fine = strip_top[fy_start : fy_end+bh, :]
            res_fine = cv2.matchTemplate(roi_fine, b['block_bgr'], cv2.TM_SQDIFF_NORMED)
            _, _, min_loc_fine, _ = cv2.minMaxLoc(res_fine)
            best_y_in_strip = fy_start + min_loc_fine[1]
            y_top_global = strip_start_y + best_y_in_strip
            shift = y_top_global - by
            match_region = strip_top[best_y_in_strip : best_y_in_strip+bh, :]
            is_valid, points = ImageMatcher._compute_similarity_metrics(match_region, b['block_bgr'])
            if is_valid:
                candidates.append({'shift': shift, 'points': points})
        if not candidates: return None
        candidates.sort(key=lambda x: x['shift'])
        clusters = []
        curr_cluster = [candidates[0]]
        for i in range(1, len(candidates)):
            if abs(candidates[i]['shift'] - curr_cluster[-1]['shift']) <= 3:
                curr_cluster.append(candidates[i])
            else:
                clusters.append(curr_cluster)
                curr_cluster = [candidates[i]]
        clusters.append(curr_cluster)
        best_cluster = max(clusters, key=lambda cl: sum(c['points'] for c in cl))
        best_cluster.sort(key=lambda x: (-x['points'], x['shift']))
        best_candidate = best_cluster[0]
        return int(best_candidate['shift'])

    @staticmethod
    def detect_visual_shift(img_top, img_bottom, static_bars, box_shift, min_shift, max_shift):
        h_bot, w_bot = img_bottom.shape[:2]
        h_top = img_top.shape[0]
        bot_h_header, bot_h_footer, w_left, w_right = static_bars
        y_start_scan = bot_h_header if box_shift == 0 else 0
        y_end_scan = h_bot - bot_h_footer
        search_h_bot = y_end_scan - y_start_scan
        num_rows = max(1, round(search_h_bot ** 0.5 / 3.5))
        row_h = search_h_bot // num_rows
        best_score = float('-inf')
        best_shift = h_top
        best_cut = 0
        for r in range(num_rows):
            y_start = y_start_scan + r * row_h
            y_end = y_start + row_h
            if y_end > y_end_scan: break
            row_img = img_bottom[y_start:y_end, :]
            valid_row_img = row_img[:, w_left : w_bot - w_right]
            gray_row = cv2.cvtColor(valid_row_img, cv2.COLOR_BGR2GRAY)
            tex_score = cv2.meanStdDev(gray_row)[1][0][0]
            if tex_score < ImageMatcher.THRES_TEXTURE:
                continue
            delta_h = h_bot - h_top
            shift_candidate = ImageMatcher._search_in_row(img_top, row_img, (0, y_start, w_bot, row_h), static_bars, box_shift, delta_h, min_shift, max_shift)
            if shift_candidate is not None:
                score, verified_cut_y = ImageMatcher.verify_region(img_top, img_bottom, shift_candidate, y_start, static_bars, box_shift)
                if score > ImageMatcher.THRES_SCORE:
                    return shift_candidate, verified_cut_y, score
                if score > best_score:
                    best_score = score
                    best_shift = shift_candidate
                    best_cut = verified_cut_y
        return best_shift, best_cut, best_score

    @staticmethod
    def detect_micro_overlap(img_top, img_bottom, static_bars, row_h, box_shift):
        h_top, w_top = img_top.shape[:2]
        h_bot, w_bot = img_bottom.shape[:2]
        bot_h_header, bot_h_footer, w_left, w_right = static_bars
        delta_h = h_bot - h_top
        top_h_footer = bot_h_footer - (box_shift + delta_h)
        candidates_top = []
        if h_top >= row_h:
            candidates_top.append({'img': img_top[h_top - row_h : h_top, :], 'base_y': h_top - row_h})
        if top_h_footer > 0 and h_top >= top_h_footer + row_h:
            base_y = h_top - top_h_footer - row_h
            candidates_top.append({'img': img_top[base_y : base_y + row_h, :], 'base_y': base_y})
        candidates_bot = []
        if h_bot >= row_h:
            candidates_bot.append({'img': img_bottom[0 : row_h, :], 'base_y': 0})
        if bot_h_header > 0 and h_bot >= bot_h_header + row_h:
            candidates_bot.append({'img': img_bottom[bot_h_header : bot_h_header + row_h, :], 'base_y': bot_h_header})
        valid_w_slice = slice(w_left, w_bot - w_right if w_right > 0 else None)
        best_candidate = None
        for item_top in candidates_top:
            gray_top = cv2.cvtColor(item_top['img'][:, valid_w_slice], cv2.COLOR_BGR2GRAY)
            if cv2.meanStdDev(gray_top)[1][0][0] < ImageMatcher.THRES_TEXTURE:
                continue
            for item_bot in candidates_bot:
                gray_bot = cv2.cvtColor(item_bot['img'][:, valid_w_slice], cv2.COLOR_BGR2GRAY)
                if cv2.meanStdDev(gray_bot)[1][0][0] < ImageMatcher.THRES_TEXTURE:
                    continue
                chosen_h = -1
                min_mae = 1.0
                for h in range(row_h, 0, -1):
                    slice_top = item_top['img'][row_h - h :, :]
                    slice_bot = item_bot['img'][0 : h, :]
                    mb = cv2.mean(cv2.absdiff(slice_top, slice_bot))
                    mae = (mb[0] + mb[1] + mb[2]) / (3 * 255.0)
                    if mae < ImageMatcher.THRES_ABS_GOOD:
                        chosen_h = h
                        min_mae = mae
                        break
                    if mae < min_mae:
                        chosen_h = h
                        min_mae = mae
                if chosen_h > 0 and min_mae < ImageMatcher.THRES_ABS_VALID:
                    shift = (item_top['base_y'] + row_h - chosen_h) - item_bot['base_y']
                    match_y_bot = item_bot['base_y']
                    score, cut_y = ImageMatcher.verify_region(img_top, img_bottom, shift, match_y_bot, static_bars, box_shift)
                    if score > ImageMatcher.THRES_SCORE:
                        if best_candidate is None or score > best_candidate['score'] or (abs(score - best_candidate['score']) < 0.1 and shift < best_candidate['shift']):
                            best_candidate = {'score': score, 'shift': shift, 'cut_y': cut_y}
        if best_candidate:
            return best_candidate['shift'], best_candidate['cut_y']
        return None

def stitch_images_in_memory_from_model(render_plan: list, image_width: int, total_height: int, progress_callback=None):
    if not render_plan:
        return None
    num_pieces = len(render_plan)
    logging.debug(f"开始从 {num_pieces} 个渲染片段拼接图像，最终尺寸: {image_width}x{total_height}")
    current_img_path = None
    try:
        stitched_image = np.zeros((total_height, image_width, 3), dtype=np.uint8)
        for i, piece in enumerate(render_plan):
            filepath = piece['filepath']
            src_y = piece['src_y']
            height = piece['height']
            dest_y = piece['render_y_start']
            try:
                if filepath != current_img_path:
                    current_img = cv2.imread(str(filepath))
                    if current_img is None:
                        raise ValueError(f"cv2 无法读取图片: {filepath}")
                    current_img_path = filepath
                img_h, img_w = current_img.shape[:2]
                if img_w != image_width:
                    logging.warning(f"图片片段 {filepath} 宽度 {img_w} 与预期 {image_width} 不符")
                copy_h = min(height, img_h - src_y)
                copy_w = min(image_width, img_w)
                stitched_image[dest_y:dest_y + copy_h, :copy_w] = current_img[src_y:src_y + copy_h, :copy_w]
            except Exception as e_load:
                logging.error(f"拼接图片失败 {filepath}：{e_load}")
            if progress_callback:
                progress_callback((i + 1) / num_pieces)
        logging.info("图像拼接完成")
        return stitched_image
    except Exception as e:
        msg = f"拼接图像时发生错误: {e}"
        logging.error(msg)
        GLib.idle_add(send_notification, "拼接失败", msg, "critical", "dialog-error")
        return None
# 缓冲区px }

class StitchModel(GObject.Object):
    """管理拼接数据的模型，支持异步更新和信号通知"""
    # 缓冲区px
    __gsignals__ = {
        'model-updated': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'modification-stack-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'image-ready': (GObject.SignalFlags.RUN_FIRST, None, (str, object)),
    }
    def __init__(self):
        super().__init__()
        self.entries = []
        self.image_width = 0
        self.total_virtual_height = 0
        self.modifications = []
        self.redo_stack = []
        self.render_plan = [] # 渲染层
        self.merged_del_regions = []
        self.surface_cache = collections.OrderedDict()
        self.CACHE_SIZE = config.PREVIEW_CACHE_SIZE
        self._load_queue = []
        self._loading_set = set()
        self._roi_filepaths = set()
        self._queue_lock = threading.Lock()
        self._worker_condition = threading.Condition(self._queue_lock)
        self._worker_running = True
        self._loader_thread = threading.Thread(target=self._image_loader_worker, daemon=True, name="ImageLoader")
        self._loader_thread.start()

    @property
    def capture_count(self) -> int:
        return len(self.entries)

    def update_roi(self, roi_set):
        with self._queue_lock:
            self._roi_filepaths = roi_set

    def request_image(self, filepath):
        if filepath in self.surface_cache:
            self.surface_cache.move_to_end(filepath)
            return self.surface_cache[filepath]
        with self._queue_lock:
            if filepath in self._loading_set:
                return None
            self._loading_set.add(filepath)
            self._load_queue.append(filepath)
            self._worker_condition.notify()
        return None

    def _image_loader_worker(self):
        while True:
            filepath_to_load = None
            with self._worker_condition:
                while not self._load_queue and self._worker_running:
                    self._worker_condition.wait()
                if not self._worker_running:
                    break
                candidate = self._load_queue.pop()
                is_needed = candidate in self._roi_filepaths
                if is_needed:
                    filepath_to_load = candidate
                else:
                    if candidate in self._loading_set:
                        self._loading_set.remove(candidate)
                    continue
            if filepath_to_load:
                if not os.path.exists(filepath_to_load):
                    GLib.idle_add(self._on_image_loaded_ui, filepath_to_load, None)
                    continue
                img_bgra = None
                try:
                    img_bgr = cv2.imread(str(filepath_to_load))
                    if img_bgr is not None:
                        img_bgra = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2BGRA)
                except Exception as e:
                    logging.warning(f"异步加载图片失败 {filepath_to_load}: {e}")
                GLib.idle_add(self._on_image_loaded_ui, filepath_to_load, img_bgra)

    def _on_image_loaded_ui(self, filepath, img_bgra):
        with self._queue_lock:
            if filepath in self._loading_set:
                self._loading_set.remove(filepath)
        if img_bgra is not None:
            surface = cairo.ImageSurface.create_for_data(img_bgra, cairo.FORMAT_ARGB32, img_bgra.shape[1], img_bgra.shape[0], img_bgra.strides[0])
            self.surface_cache[filepath] = (surface, img_bgra)
            self.surface_cache.move_to_end(filepath)
            self._prune_cache()
            self.emit('image-ready', filepath, surface)

    def update_cache_limit(self, new_size):
        self.CACHE_SIZE = new_size
        self._prune_cache()

    def _prune_cache(self):
        excess = len(self.surface_cache) - self.CACHE_SIZE
        if excess <= 0: return
        keys_to_evict = []
        for key in self.surface_cache:
            if key not in self._roi_filepaths:
                keys_to_evict.append(key)
                if len(keys_to_evict) == excess:
                    break
        for key in keys_to_evict:
            del self.surface_cache[key]

    def _regenerate_plans(self):
        """生成渲染层并通知视图更新"""
        self.render_plan = []
        current_r_y = 0
        restored_seams = {mod['seam_index'] for mod in self.modifications if mod['type'] == 'restore'}
        raw_deletes = [(mod['y_start_abs'], mod['y_end_abs']) for mod in self.modifications if mod['type'] == 'delete']
        raw_deletes.sort(key=lambda x: x[0])
        merged_deletes = []
        for start, end in raw_deletes:
            if merged_deletes and start <= merged_deletes[-1][1]:
                merged_deletes[-1] = (merged_deletes[-1][0], max(merged_deletes[-1][1], end))
            else:
                merged_deletes.append((start, end))
        self.merged_del_regions = merged_deletes
        for i, entry in enumerate(self.entries):
            c_top = entry['crop_top']
            c_bottom = entry['crop_bottom']
            original_h = entry['height']
            if i in restored_seams:
                c_bottom = original_h
            if (i - 1) in restored_seams:
                c_top = 0
            entry_abs_origin = entry['absolute_y_start']
            valid_abs_start = entry_abs_origin + c_top
            valid_abs_end = entry_abs_origin + c_bottom
            if valid_abs_start >= valid_abs_end:
                continue
            visible_intervals = [(valid_abs_start, valid_abs_end)]
            for del_start, del_end in self.merged_del_regions:
                if not visible_intervals:
                    break
                new_intervals = []
                for vis_start, vis_end in visible_intervals:
                    if del_start < vis_end and del_end > vis_start:
                        if vis_start < del_start:
                            new_intervals.append((vis_start, del_start))
                        if vis_end > del_end:
                            new_intervals.append((del_end, vis_end))
                    else:
                        new_intervals.append((vis_start, vis_end))
                visible_intervals = new_intervals
            for abs_start, abs_end in visible_intervals:
                height = abs_end - abs_start
                if height < 1:
                    continue
                src_y = abs_start - entry_abs_origin
                self.render_plan.append({
                    'entry_index': i,
                    'filepath': entry['filepath'],
                    'absolute_y_start': abs_start,
                    'absolute_y_end': abs_end,
                    'render_y_start': current_r_y,
                    'height': height,
                    'src_y': src_y,
                })
                current_r_y += height
        self.total_virtual_height = current_r_y
        GLib.idle_add(self.emit, 'model-updated')

    def undo(self):
        if not self.modifications:
            logging.debug("StitchModel: 撤销栈为空，无操作")
            return
        mod = self.modifications.pop()
        self.redo_stack.append(mod)
        logging.info(f"StitchModel: 撤销操作 {mod.get('type')}")
        GLib.idle_add(self._regenerate_plans)
        GLib.idle_add(self.emit, 'modification-stack-changed')

    def redo(self):
        if not self.redo_stack:
            logging.debug("StitchModel: 重做栈为空，无操作")
            return
        mod = self.redo_stack.pop()
        self.modifications.append(mod)
        logging.info(f"StitchModel: 重做操作 {mod.get('type')}")
        GLib.idle_add(self._regenerate_plans)
        GLib.idle_add(self.emit, 'modification-stack-changed')

    def add_modification(self, mod: dict):
        logging.debug(f"StitchModel: 添加新修改: {mod}")
        self.modifications.append(mod)
        if self.redo_stack:
            logging.debug("StitchModel: 新修改导致重做栈被清空")
            self.redo_stack.clear()
        GLib.idle_add(self._regenerate_plans)
        GLib.idle_add(self.emit, 'modification-stack-changed')

    def add_entry(self, filepath: str, width: int, height: int, shift: int, cut_y: int, box_y: int, thumb_data, full_img_data):
        logging.debug(f"StitchModel: 收到添加请求: {Path(filepath).name}, h={height}, shift={shift}, cut_y={cut_y}")
        thumb_bundle = None
        if thumb_data:
            try:
                t_array, t_w, t_h, t_stride = thumb_data
                thumb_surface = cairo.ImageSurface.create_for_data(t_array, cairo.FORMAT_ARGB32, t_w, t_h, t_stride)
                thumb_bundle = (thumb_surface, t_array)
            except Exception as e:
                logging.warning(f"创建缩略图失败: {e}")
        preloaded_bundle = None
        if full_img_data:
            try:
                f_array, f_w, f_h, f_stride = full_img_data
                preloaded_surface = cairo.ImageSurface.create_for_data(f_array, cairo.FORMAT_ARGB32, f_w, f_h, f_stride)
                preloaded_bundle = (preloaded_surface, f_array)
            except Exception as e:
                logging.warning(f"创建预加载全图失败: {e}")
        if not self.entries:
            self.image_width = width
            self.entries.append({'filepath': filepath, 'height': height, 'crop_top': 0, 'crop_bottom': height, 'shift': 0, 'box_y': box_y, 'absolute_y_start': 0, 'thumb': thumb_bundle})
        else:
            prev_entry = self.entries[-1]
            new_abs_start = prev_entry['absolute_y_start'] + prev_entry['height']
            calculated_bottom = cut_y + shift
            if calculated_bottom < prev_entry['crop_top']:
                logging.debug(f"StitchModel: 修正负高度重叠，entry {len(self.entries)-1} 修正后高度为 0")
                prev_entry['crop_bottom'] = prev_entry['crop_top']
                cut_y = prev_entry['crop_bottom'] - shift
            else:
                prev_entry['crop_bottom'] = calculated_bottom
            self.entries.append({'filepath': filepath, 'height': height, 'crop_top': cut_y, 'crop_bottom': height, 'shift': shift, 'box_y': box_y, 'absolute_y_start': new_abs_start, 'thumb': thumb_bundle})
            logging.info(f"添加第 {len(self.entries)} 张截图. prev_bottom: {prev_entry['crop_bottom']}, curr_top: {cut_y}, shift: {shift}")
        if preloaded_bundle:
            self.surface_cache[filepath] = preloaded_bundle
            self.surface_cache.move_to_end(filepath)
            self._prune_cache()
        GLib.idle_add(self._regenerate_plans)

    def pop_entry(self):
        if not self.entries:
            return
        logging.debug("StitchModel: 收到移除最后一个条目的请求")
        last_entry_index = len(self.entries) - 1
        last_entry = self.entries[last_entry_index]
        entry_abs_start = last_entry['absolute_y_start']
        entry_abs_end = last_entry['absolute_y_start'] + last_entry['height']
        seam_index_to_remove = last_entry_index - 1
        logging.debug(f"正在清理与截图 {last_entry_index} (abs_y: [{entry_abs_start}, {entry_abs_end}], seam_idx: {seam_index_to_remove}) 相关的修改")
        new_modifications = []
        removed_count = 0
        for mod in self.modifications:
            mod_applies = False
            if mod['type'] == 'delete':
                mod_start = mod['y_start_abs']
                mod_end = mod['y_end_abs']
                if max(entry_abs_start, mod_start) < min(entry_abs_end, mod_end):
                    mod_applies = True
                    logging.debug(f"删除操作 {mod} 与被删除截图重叠，将被移除")
            elif mod['type'] == 'restore':
                if mod['seam_index'] == seam_index_to_remove:
                    mod_applies = True
                    logging.debug(f"恢复操作 {mod} 与被删除截图相关，将被移除")
            if mod_applies:
                removed_count += 1
            else:
                new_modifications.append(mod)
        if removed_count > 0:
            self.modifications = new_modifications
            logging.debug(f"已移除 {removed_count} 个与被删除截图相关的修改")
            if self.redo_stack:
                self.redo_stack.clear()
                logging.debug("由于删除了截图，重做栈已清空")
            GLib.idle_add(self.emit, 'modification-stack-changed')
        popped_entry = self.entries.pop()
        if popped_entry['filepath'] in self.surface_cache:
            del self.surface_cache[popped_entry['filepath']]
            logging.debug(f"从缓存中移除 {popped_entry['filepath']}")
        try:
            filepath_to_remove = Path(popped_entry['filepath'])
            if filepath_to_remove.exists():
                os.remove(filepath_to_remove)
                logging.debug(f"已删除文件: {filepath_to_remove}")
        except OSError as e:
            logging.error(f"删除文件失败 {popped_entry['filepath']}: {e}")
        if self.entries:
            self.entries[-1]['crop_bottom'] = self.entries[-1]['height']
            last_entry = self.entries[-1]
        else:
            self.image_width = 0
            logging.info("所有截图已移除")
        GLib.idle_add(self._regenerate_plans)

    def cleanup(self):
        self._worker_running = False
        with self._queue_lock:
            self._worker_condition.notify_all()
        if self._loader_thread.is_alive():
            self._loader_thread.join(timeout=0.5)

class CaptureMode(str, Enum):
    FREE = "自由模式"
    GRID = "整格模式"
    AUTO = "自动模式"

class Context(str, Enum):
    BASE = "base"
    SELECTING = "selecting"
    DIALOG = "dialog"

class CaptureSession(GObject.Object):
    """管理会话的数据和状态"""
    __gsignals__ = {
        'state-changed': (GObject.SignalFlags.RUN_FIRST, None, (str, object)),
        'context-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'mode-changed': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'geometry-changed': (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        'static-bars-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'grid-config-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'screen-config-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
    def __init__(self):
        super().__init__()
        self.current_mode = CaptureMode.FREE
        self.scroll_stat_history = collections.defaultdict(lambda: collections.deque(maxlen=5))
        self.scale = 1.0
        # 逻辑px
        self.geometry: dict = {} # 窗口坐标
        self.screen_rect = None # 全局坐标
        self.monitor_offset_x = 0
        self.monitor_offset_y = 0
        self.static_bars = (0, 0, 0, 0)
        self.grid_app_class = None
        self.grid_unit = 0 # 缓冲区px
        self.grid_matching_enabled = False
        self.is_horizontally_locked = False
        self.is_selection_done = False
        self.is_exiting = False
        self.is_finished = False
        self.context_stack = []
        self.context_lock = threading.Lock()

    def push_context(self, context: Context):
        with self.context_lock:
            self.context_stack.append(context)
            logging.debug(f"上下文 {context.value} 入栈，当前栈: {[c.value for c in self.context_stack]}")
        self.emit('context-changed')

    def pop_context(self, context: Context = None):
        with self.context_lock:
            if not self.context_stack: return
            if context and self.context_stack[-1] != context:
                logging.warning(f"尝试弹出的上下文 {context.value} 与栈顶 {self.context_stack[-1].value} 不匹配，强制弹出栈顶")
            popped = self.context_stack.pop()
            logging.debug(f"上下文 {popped.value} 出栈，当前栈: {[c.value for c in self.context_stack]}")
        self.emit('context-changed')

    def clear_context(self):
        with self.context_lock:
            logging.debug(f"清空当前上下文栈: {[c.value for c in self.context_stack]}")
            self.context_stack.clear()
        self.emit('context-changed')

    def get_current_context(self):
        with self.context_lock:
            return self.context_stack[-1] if self.context_stack else None

    def set_geometry(self, new_geometry):
        """更新捕获区域的几何信息"""
        # new_geometry: 逻辑px窗口坐标
        clean_geometry = {key: float(value) for key, value in new_geometry.items()}
        if self.geometry != clean_geometry:
            self.geometry = clean_geometry
            self.emit('geometry-changed', self.geometry)

    def set_selection_done(self, done: bool):
        if self.is_selection_done != done:
            self.is_selection_done = done
            self.emit('state-changed', 'is_selection_done', done)

    def set_exiting(self, exiting: bool):
        if self.is_exiting != exiting:
            self.is_exiting = exiting
            self.emit('state-changed', 'is_exiting', exiting)

    def set_finished(self, finished: bool):
        if self.is_finished != finished:
            self.is_finished = finished
            self.emit('state-changed', 'is_finished', finished)

    def set_mode(self, mode: CaptureMode):
        if self.current_mode != mode:
            self.current_mode = mode
            self.emit('mode-changed', mode.value)

    def set_grid_config(self, app_class: str, unit: int, matching_enabled: bool):
        if self.grid_app_class != app_class or self.grid_unit != unit or self.grid_matching_enabled != matching_enabled:
            self.grid_app_class = app_class
            self.grid_unit = unit
            self.grid_matching_enabled = matching_enabled
            self.emit('grid-config-changed')

    def set_screen_config(self, rect=None, scale=None, offset_x=None, offset_y=None):
        changed = False
        if rect is not None and self.screen_rect != rect:
            self.screen_rect = rect
            changed = True
        if scale is not None and self.scale != scale:
            self.scale = scale
            changed = True
        if offset_x is not None and self.monitor_offset_x != offset_x:
            self.monitor_offset_x = offset_x
            changed = True
        if offset_y is not None and self.monitor_offset_y != offset_y:
            self.monitor_offset_y = offset_y
            changed = True
        if changed:
            self.emit('screen-config-changed')

    def set_static_bars(self, header_buf, footer_buf, left_buf, right_buf):
        s = self.scale
        new_bars = (header_buf / s, footer_buf / s, left_buf / s, right_buf / s) # 缓冲区px -> 逻辑px
        if self.static_bars != new_bars:
            self.static_bars = new_bars
            logging.debug(f"更新静态栏指示: T={header_buf}, B={footer_buf}, L={left_buf}, R={right_buf}")
            self.emit('static-bars-changed')

# 逻辑px {
class FeedbackWidget(EmbeddedWidget):
    def __init__(self, parent_overlay, text, show_progress=False):
        super().__init__(css_class="feedback-panel")
        self.overlay = parent_overlay
        self.show_progress = show_progress
        self.set_size_request(config.FEEDBACK_WIDGET_WIDTH, -1)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(vbox)
        top_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        top_hbox.set_margin_top(10)
        top_hbox.set_margin_start(10)
        top_hbox.set_margin_end(10)
        top_hbox.set_margin_bottom(10)
        self.spinner = Gtk.Spinner()
        self.spinner.start()
        self.spinner.get_style_context().add_class("feedback-spinner")
        self.label = Gtk.Label()
        self.label.set_justify(Gtk.Justification.CENTER)
        self.label.set_line_wrap(True)
        self.label.get_style_context().add_class("feedback-label")
        self.set_text(text)
        top_hbox.pack_start(self.spinner, False, False, 0)
        top_hbox.pack_start(self.label, True, True, 0)
        vbox.pack_start(top_hbox, True, True, 0)
        self.progress_bar = None
        if show_progress:
            self.progress_bar = Gtk.ProgressBar()
            self.progress_bar.get_style_context().add_class("feedback-progress")
            self.progress_bar.set_fraction(0.0)
            self.progress_bar.set_margin_start(10)
            self.progress_bar.set_margin_end(10)
            self.progress_bar.set_margin_bottom(15)
            vbox.pack_start(self.progress_bar, False, False, 0)

    def set_text(self, text):
        self.label.set_text(text)

    def set_progress(self, fraction):
        if self.progress_bar:
            self.progress_bar.set_fraction(fraction)

class EmbeddedDialog(EmbeddedWidget):
    __gsignals__ = {
        'response': (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }
    def __init__(self, title, css_class="embedded-dialog"):
        super().__init__(css_class=css_class)
        self.set_can_focus(True)
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(self.main_box)
        self.header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.header_box.get_style_context().add_class("dialog-header")
        self.main_box.pack_start(self.header_box, False, False, 0)
        self.lbl_title = Gtk.Label(label=title)
        self.lbl_title.get_style_context().add_class("dialog-title")
        self.header_box.pack_start(self.lbl_title, True, True, 0)
        self.content_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        self.content_area.get_style_context().add_class("dialog-content-area")
        self.main_box.pack_start(self.content_area, True, True, 0)
        self.action_area = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
        self.action_area.set_halign(Gtk.Align.CENTER)
        self.action_area.get_style_context().add_class("dialog-action-area")
        self.main_box.pack_end(self.action_area, False, False, 0)

    def add_button(self, text, response_id, css_class="dialog-btn"):
        btn = Gtk.Button(label=text)
        if css_class:
            btn.get_style_context().add_class(css_class)
        btn.connect("clicked", lambda w: self.emit("response", response_id))
        self.action_area.pack_start(btn, False, False, 0)
        return btn

    def create_default_buttons(self):
        confirm_key_name = config.HOTKEY_DIALOG_CONFIRM.to_string()
        cancel_key_name = config.HOTKEY_DIALOG_CANCEL.to_string()
        confirm_text = f"确定 ({confirm_key_name})"
        cancel_text = f"取消 ({cancel_key_name})"
        self.add_button(confirm_text, Gtk.ResponseType.OK)
        self.add_button(cancel_text, Gtk.ResponseType.CANCEL)

    def get_result(self):
        return None

class QuitDialog(EmbeddedDialog):
    def __init__(self, capture_count):
        title = "确认放弃截图？"
        super().__init__(title)
        msg = f"您已经截取了 {capture_count} 张图片\n确定要放弃它们吗？"
        lbl_msg = Gtk.Label(label=msg)
        lbl_msg.set_line_wrap(True)
        lbl_msg.set_justify(Gtk.Justification.CENTER)
        lbl_msg.set_xalign(0.5)
        lbl_msg.get_style_context().add_class("dialog-message")
        self.content_area.pack_start(lbl_msg, False, False, 0)
        self.create_default_buttons()

class AppConfigDialog(EmbeddedDialog):
    """应用配置选择/输入对话框"""
    def __init__(self, config_obj, allow_new_entry=False):
        title = "请输入应用标识符" if allow_new_entry else "请选择应用配置"
        super().__init__(title)
        self.allow_new_entry = allow_new_entry
        self.selected_app = None
        label_text = "无法自动检测底层应用\n请输入名称以保存配置：" if allow_new_entry else "请选择一个已校准的配置："
        label = Gtk.Label(label=label_text)
        label.set_line_wrap(True)
        label.set_justify(Gtk.Justification.CENTER)
        label.set_xalign(0.5)
        label.get_style_context().add_class("dialog-message")
        label.set_margin_bottom(5)
        self.content_area.pack_start(label, False, False, 0)
        self.entry = None
        if allow_new_entry:
            self.entry = Gtk.Entry()
            self.entry.set_placeholder_text("例如: firefox, vscode...")
            self.entry.connect("activate", lambda w: self.emit("response", Gtk.ResponseType.OK))
            self.content_area.pack_start(self.entry, False, False, 0)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_size_request(-1, 150)
        self.listbox = Gtk.ListBox()
        self.listbox.get_style_context().add_class("dialog-list-box")
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-selected", self._on_row_selected)
        scrolled.add(self.listbox)
        self.content_area.pack_start(scrolled, True, True, 0)
        items = config_obj.get_section_items('ApplicationScrollUnits')
        if not items:
            empty_lbl = Gtk.Label(label="无已保存的配置")
            empty_lbl.get_style_context().add_class("dim-label")
            empty_lbl.show()
            self.listbox.set_placeholder(empty_lbl)
        else:
            for key, _ in items:
                row = Gtk.ListBoxRow()
                row.get_style_context().add_class("dialog-list-row")
                lbl = Gtk.Label(label=key, xalign=0)
                row.add(lbl)
                row.app_key = key
                self.listbox.add(row)
        self.create_default_buttons()

    def _on_row_selected(self, box, row):
        if row:
            self.selected_app = row.app_key
            if self.entry:
                self.entry.set_text(self.selected_app)

    def get_result(self):
        if self.allow_new_entry and self.entry:
            text = self.entry.get_text().strip().lower()
            return text if text else None
        return self.selected_app
# 逻辑px }

class GridModeController:
    def __init__(self, config_obj: Config, session: CaptureSession, view: 'CaptureOverlay'):
        self.config = config_obj
        self.session = session
        self.view = view
        self.calibration_state = None
        if not IS_WAYLAND:
            try:
                self.x_display = display.Display()
            except Exception as e:
                self.x_display = None
                logging.error(f"无法连接到 X Display，整格模式功能将不可用: {e}")
        else:
            logging.info("Wayland 下 Xlib 窗口检测将不可用")
            self.x_display = None

    @property
    def is_active(self):
        return self.session.current_mode == CaptureMode.GRID

    def _get_window_at_coords(self, d, x, y):
        """X11 在不移动鼠标的情况下，获取指定全局坐标下的窗口 ID 和 WM_CLASS"""
        # x, y: 逻辑px全局坐标
        if IS_WAYLAND or not d:
            return None, None
        scale = self.session.scale
        # 逻辑px -> 缓冲区px
        buf_x = round(x * scale)
        buf_y = round(y * scale)
        try:
            root = d.screen().root
            stacking_atom = d.intern_atom('_NET_CLIENT_LIST_STACKING')
            prop = root.get_full_property(stacking_atom, X.AnyPropertyType)
            if not prop or not prop.value:
                window_ids = [win.id for win in root.query_tree().children]
                window_ids.reverse()
            else:
                window_ids = prop.value
            for win_id in reversed(window_ids):
                try:
                    win_obj = d.create_resource_object('window', win_id)
                    if not win_obj:
                        continue
                    attrs = win_obj.get_attributes()
                    if attrs.map_state != X.IsViewable:
                        continue
                    state_atom = d.intern_atom('_NET_WM_STATE')
                    hidden_atom = d.intern_atom('_NET_WM_STATE_HIDDEN')
                    state_prop = win_obj.get_full_property(state_atom, X.AnyPropertyType)
                    if state_prop and state_prop.value:
                        if hidden_atom in state_prop.value:
                            continue
                    geom = win_obj.get_geometry()
                    translated = root.translate_coords(win_obj, 0, 0)
                    client_x, client_y = translated.x, translated.y
                    extents_atom = d.intern_atom('_NET_FRAME_EXTENTS')
                    prop_extents = win_obj.get_full_property(extents_atom, X.AnyPropertyType)
                    border_left = 0
                    border_right = 0
                    border_top = 0
                    border_bottom = 0
                    if prop_extents and prop_extents.value and len(prop_extents.value) >= 4:
                        border_left = prop_extents.value[0]
                        border_right = prop_extents.value[1]
                        border_top = prop_extents.value[2]
                        border_bottom = prop_extents.value[3]
                    abs_x = client_x - border_left # 全局坐标
                    abs_y = client_y - border_top
                    # 缓冲区px
                    win_w = geom.width + border_left + border_right
                    win_h = geom.height + border_top + border_bottom
                    if not (abs_x <= buf_x < abs_x + win_w and
                            abs_y <= buf_y < abs_y + win_h):
                        continue
                    wm_class = win_obj.get_wm_class()
                    if wm_class and 'Scroll_stitch.py' not in wm_class[1]:
                        app_class = wm_class[1].lower()
                        logging.debug(
                            f"定位成功，id={win_obj.id}，class={app_class}，scale={scale:.3f}，"
                            f"geom=({abs_x},{abs_y},{win_w},{win_h})，point=({buf_x},{buf_y})"
                        )
                        return win_obj.id, app_class
                except Exception as e:
                    logging.error(f"错误： {e}")
        except Exception as e:
            logging.error(f"使用 python-xlib 查找窗口时发生错误: {e}")
        return None, None

    def _get_app_class_at_center(self):
        # 逻辑px
        if IS_WAYLAND:
            return None
        center_x = self.session.geometry['x'] + self.session.geometry['w'] / 2 # 窗口坐标
        center_y = self.session.geometry['y'] + self.session.geometry['h'] / 2
        g_center_x, g_center_y = self.view.coord_manager.map_point(center_x, center_y, source=CoordSys.WINDOW, target=CoordSys.GLOBAL)
        _, app_class = self._get_window_at_coords(self.x_display, g_center_x, g_center_y)
        if app_class:
            logging.info(f"检测到底层应用: {app_class}")
        return app_class

    def _prompt_for_app_class(self, allow_new_entry):
        dialog = AppConfigDialog(self.config, allow_new_entry=allow_new_entry)
        response, result = self.view.overlay_manager.run_modal(
            dialog, anchor='center', layer=OverlayManager.LAYER_HIGH, mask=False
        )
        if response == Gtk.ResponseType.OK and result:
            return result
        return None

    def toggle(self):
        """切换整格模式"""
        # 缓冲区px
        if self.view.controller.is_auto_scrolling:
            logging.debug("自动滚动模式下忽略切换整格模式请求")
            return
        if self.is_active:
            self.session.set_grid_config(None, 0, False)
            self.session.set_mode(CaptureMode.FREE)
            logging.info("整格模式已关闭")
            send_notification("整格模式已关闭", "已恢复自由模式", "normal")
            return
        app_class = self._get_app_class_at_center()
        if not app_class:
            app_class = self._prompt_for_app_class(allow_new_entry=False)
        if not app_class:
            send_notification("切换整格模式失败", "无法检测到底层应用程序", "warning")
            return
        val_str = self.config.get_raw_string('ApplicationScrollUnits', app_class)
        grid_unit_from_config, matching_enabled = self.config.parse_string_to_value('ApplicationScrollUnits', app_class, val_str)
        if grid_unit_from_config > 0:
            self.session.set_grid_config(app_class, grid_unit_from_config, matching_enabled)
            match_status = "启用" if matching_enabled else "禁用"
            self.session.set_mode(CaptureMode.GRID)
            scale = self.session.scale
            logging.info(f"为应用 '{app_class}' 启用整格模式，滚动单位: {grid_unit_from_config}px, 模板匹配: {match_status}")
            send_notification("整格模式已启用", f"应用: {app_class}, 滚动单位: {grid_unit_from_config}px, 误差修正: {match_status}", "normal")
            if not matching_enabled:
                self.snap_current_height()
        else:
            logging.warning(f"应用 '{app_class}' 未在配置中找到滚动单位，无法启用整格模式")
            send_notification("切换整格模式失败", f"'{app_class}' 的滚动单位未配置", "warning")

    def snap_current_height(self):
        """将当前选区的高度对齐到最近的整数个滚动单位"""
        if not self.is_active or self.session.grid_unit == 0:
            return
        scale = self.session.scale
        geo = self.session.geometry.copy()
        current_h = geo['h'] # 逻辑px
        _, cap_y = self.view.coord_manager.map_point(geo['x'], geo['y'], source=CoordSys.WINDOW, target=self.view.frame_grabber.target_coords)
        # 逻辑px -> 缓冲区px
        y_buf = math.ceil(cap_y * scale)
        current_h_buf = int((cap_y + current_h) * scale) - y_buf
        ticks = round(current_h_buf / self.session.grid_unit)
        if ticks < 1: ticks = 1
        target_h_buf = ticks * self.session.grid_unit # 缓冲区px
        snapped_h = (y_buf + target_h_buf + 0.01) / scale - cap_y # 缓冲区px -> 逻辑px
        if abs(geo['h'] - snapped_h) > 1e-5:
            geo['h'] = snapped_h
            self.session.set_geometry(geo)
            logging.debug(f"高度对齐: {target_h_buf} 缓冲区px, {snapped_h:.1f} 逻辑px (scale={scale:.3f})")

    def start_calibration(self):
        """启动滚动单位自动校准"""
        if self.view.controller.is_auto_scrolling:
            logging.debug("自动滚动模式下忽略配置滚动单位请求")
            return
        app_class = self._get_app_class_at_center()
        if not app_class:
            app_class = self._prompt_for_app_class(allow_new_entry=True)
        if not app_class:
            send_notification("配置失败", "无法检测到底层应用程序", "warning")
            return
        logging.info(f"为应用 '{app_class}' 启动自动校准...")
        # 逻辑px窗口坐标
        dialog_text = f"正在为 {app_class} 自动校准...\n请勿操作"
        panel = FeedbackWidget(self.view, text=dialog_text, show_progress=False)
        self.view.overlay_manager.show(panel, anchor='top-left', layer=OverlayManager.LAYER_LOW, mask=False)
        scale = self.session.scale
        self.calibration_state = {
            "app_class": app_class,
            "num_samples": self.config.CALIBRATION_SAMPLES,
            "measured_units": [],
            "panel": panel,
            "ticks_to_scroll": max(1, int(int(self.session.geometry['h'] * scale) / self.config.MAX_SCROLL_PER_TICK)) # 逻辑px -> 缓冲区px
        }
        thread = threading.Thread(target=self._calibration_thread_func, daemon=True)
        thread.start()

    def _calibration_thread_func(self):
        state = self.calibration_state
        try:
            # 逻辑px窗口坐标
            h = self.session.geometry['h']
            w = self.session.geometry['w']
            shot_x = self.session.geometry['x']
            shot_y = self.session.geometry['y']
            scale = self.session.scale
            buf_h = int(h * scale) # 逻辑px -> 缓冲区px
            ticks_to_scroll = state['ticks_to_scroll']
            num_samples = state['num_samples']
            cap_x, cap_y = self.view.coord_manager.map_point(shot_x, shot_y, source=CoordSys.WINDOW, target=self.view.frame_grabber.target_coords)
            time.sleep(0.4)
            def safe_capture(path):
                result = [False]
                event = threading.Event()
                def task():
                    try:
                        result[0] = self.view.frame_grabber.capture(cap_x, cap_y, w, h, path, scale, include_cursor=False)
                    except Exception as e:
                        logging.error(f"校准截图异常: {e}")
                    finally:
                        event.set()
                GLib.idle_add(task)
                event.wait()
                return result[0]
            logging.debug(f"校准参数: 截图区高度={h:.1f} 逻辑px, 约 {buf_h} 缓冲区px, 每次滚动格数={ticks_to_scroll}, 采样次数={num_samples}")
            state["filepath_before"] = config.TEMP_DIRECTORY / "cal_before.png"
            if not safe_capture(state["filepath_before"]):
                logging.error("校准初始截图失败")
                GLib.idle_add(self._finalize_calibration, False)
                return
            self.view.controller.scroll_manager.scroll_discrete(ticks_to_scroll)
            time.sleep(0.4)
            for step in range(1, num_samples + 1):
                filepath_after = config.TEMP_DIRECTORY / "cal_after.png"
                if not safe_capture(filepath_after):
                    logging.error(f"第 {step} 次采样截图失败，中止校准")
                    GLib.idle_add(self._finalize_calibration, False)
                    return
                # 缓冲区px
                img_top = cv2.imread(str(state["filepath_before"]))
                img_bottom = cv2.imread(str(filepath_after))
                if img_top is not None and img_bottom is not None:
                    h_buf, _, _ = img_top.shape
                    min_scroll_buf = self.config.MIN_SCROLL_PER_TICK
                    min_shift = ticks_to_scroll * min_scroll_buf
                    max_shift = ticks_to_scroll * self.config.MAX_SCROLL_PER_TICK
                    cal_bars = ImageMatcher.detect_static_bars(img_top, img_bottom)
                    h_header, _, _, _ = cal_bars
                    shift, cut_y, score = ImageMatcher.detect_visual_shift(img_top, img_bottom, cal_bars, 0, min_shift, max_shift)
                    if score > ImageMatcher.THRES_SCORE and shift > 0:
                        scroll_dist_px = shift
                        unit = scroll_dist_px / state['ticks_to_scroll']
                        state["measured_units"].append(unit)
                        logging.debug(f"采样 {step} 成功，滚动单位 ≈ {unit:.2f} 缓冲区px/格")
                    else:
                        logging.warning(f"采样 {step} 匹配失败")
                        score_check, _ = ImageMatcher.verify_region(img_top, img_bottom, 0, h_header, cal_bars)
                        if score_check > ImageMatcher.THRES_SCORE:
                            logging.warning(f"校准过程中检测到底部，提前中止采样")
                            GLib.idle_add(self._finalize_calibration, True)
                            return
                else:
                    logging.error("无法读取图片文件进行匹配")
                    GLib.idle_add(self._finalize_calibration, False)
                    return
                if os.path.exists(state["filepath_before"]):
                    os.remove(state["filepath_before"])
                os.rename(filepath_after, state["filepath_before"])
                if step < num_samples:
                    self.view.controller.scroll_manager.scroll_discrete(state['ticks_to_scroll'])
                    time.sleep(0.4)
            GLib.idle_add(self._finalize_calibration, True)
        except Exception as e:
            logging.error(f"校准线程发生错误: {e}")
            GLib.idle_add(self._finalize_calibration, False)

    def _finalize_calibration(self, success):
        state = self.calibration_state
        self.view.overlay_manager.dismiss(state["panel"])
        self.view.update_input_shape()
        if os.path.exists(state.get("filepath_before", "")):
            os.remove(state["filepath_before"])
        MIN_VALID_SAMPLES = max(2, state["num_samples"] // 2)
        if not success or not state["measured_units"] or len(state["measured_units"]) < MIN_VALID_SAMPLES:
            msg = f"为 '{state['app_class']}' 校准失败\n有效采样数据不足，请在内容更丰富的区域操作或确保界面有足够的滚动空间"
            send_notification("配置失败", msg, "warning")
            logging.warning(msg.replace('\n', ' '))
            return
        units = sorted(state["measured_units"])
        logging.debug(f"开始聚类分析，原始数据: {[round(u, 2) for u in units]}")
        if not units:
            self._finalize_calibration(success=False)
            return
        TOLERANCE = 5
        clusters = []
        for unit in units:
            for cluster in clusters:
                if abs(unit - np.mean(cluster)) < TOLERANCE:
                    cluster.append(unit)
                    break
            else:
                clusters.append([unit])
        if not clusters:
            self._finalize_calibration(success=False)
            return
        largest_cluster = max(clusters, key=len)
        logging.debug(f"聚类结果: {[[round(u, 2) for u in c] for c in clusters]}。选择的最大集群: {[round(u, 2) for u in largest_cluster]}")
        if len(largest_cluster) < MIN_VALID_SAMPLES:
            msg = f"为 '{state['app_class']}' 校准失败。\n采样数据一致性过差，无法找到共识值"
            send_notification("配置失败", msg, "warning")
            logging.warning(msg.replace('\n', ' '))
            return
        raw_mean = float(np.mean(largest_cluster))
        final_avg_unit = round(raw_mean)
        final_std_dev = np.std(largest_cluster)
        matching_enabled = final_std_dev >= 0.05 or (abs(raw_mean - final_avg_unit) >= 0.1)
        logging.info(f"最终分析: 原始平均值 {raw_mean:.2f} 取整后滚动单位= {final_avg_unit} 缓冲区px, 标准差={final_std_dev:.3f}, 开启误差修正={matching_enabled}")
        value_to_save = f"{final_avg_unit}, {str(matching_enabled).lower()}"
        config.set_value('ApplicationScrollUnits', state["app_class"], value_to_save)
        if self.is_active:
            self.session.set_grid_config(state["app_class"], final_avg_unit, matching_enabled)
            if not matching_enabled:
                self.snap_current_height()
        status_str = "启用" if matching_enabled else "禁用"
        msg = f"已为 '{state['app_class']}' 保存滚动单位: {final_avg_unit} 缓冲区px\n误差修正: {status_str}"
        send_notification("配置成功", msg, level="success", timeout=4)

class ScrollManager:
    def __init__(self, config_obj: Config, session: CaptureSession, view: 'CaptureOverlay'):
        self.config = config_obj
        self.session = session
        self.view = view
        self.gdk_display = Gdk.Display.get_default()
        self.gdk_seat = self.gdk_display.get_default_seat()
        self.gdk_pointer = self.gdk_seat.get_pointer()
        self.gdk_screen = self.gdk_display.get_default_screen()
        self.evdev_abs_mouse = None
        self.evdev_wheel_scroller = None
        self.invisible_scroller = None

    def _start_invisible_cursor_thread(self, screen_rect, scale):
        g_min_x, g_min_y, g_max_x, g_max_y = self.view.coord_manager.get_all_monitors_geometry()
        rect_w = screen_rect.width
        rect_h = screen_rect.height
        # 逻辑px -> 缓冲区px
        g_min_x_buf = math.ceil(g_min_x * scale)
        g_min_y_buf = math.ceil(g_min_y * scale)
        g_max_x_buf = int(g_max_x * scale)
        g_max_y_buf = int(g_max_y * scale)
        # 计算停放位置
        park_x, park_y = self.view.coord_manager.map_point(rect_w - 1, rect_h - 1, source=CoordSys.MONITOR, target=CoordSys.GLOBAL)
        # 逻辑px -> 缓冲区px
        park_x_buf = int(park_x * scale)
        park_y_buf = int(park_y * scale)
        try:
            self.invisible_scroller = InvisibleCursorScroller(g_min_x_buf, g_min_y_buf, g_max_x_buf, g_max_y_buf, park_x_buf, park_y_buf, self.config)
            threading.Thread(target=self.invisible_scroller.setup, daemon=True).start()
            logging.info("正在后台初始化隐形光标设备...")
        except Exception as e:
            logging.error(f"初始化隐形光标失败: {e}")
            self.invisible_scroller = None

    def init_devices(self, screen_rect, scale):
        can_evdev_simulate_input = EVDEV_AVAILABLE and UINPUT_AVAILABLE
        if not can_evdev_simulate_input:
            missing = []
            if not EVDEV_AVAILABLE: missing.append("未安装 evdev 库")
            if not UINPUT_AVAILABLE: missing.append("缺少 /dev/uinput 读写权限")
            evdev_sim_err_msg = "、".join(missing)
        try:
            if IS_WAYLAND:
                logging.info("Wayland 下 'invisible_cursor' 模式不可用")
                self.invisible_scroller = None
                if can_evdev_simulate_input:
                    self.evdev_wheel_scroller = EvdevWheelScroller()
                    if screen_rect.width > 0:
                        g_min_x, g_min_y, g_max_x, g_max_y = self.view.coord_manager.get_all_monitors_geometry()
                        # 逻辑px -> 缓冲区px
                        self.evdev_abs_mouse = EvdevAbsoluteMouse(
                            math.ceil(g_min_x * scale), math.ceil(g_min_y * scale),
                            int(g_max_x * scale), int(g_max_y * scale)
                        )
                    else:
                        logging.error("无法获取分辨率，Wayland 下鼠标移动功能将无法工作")
                        self.evdev_abs_mouse = None
                else:
                    logging.error(f"{evdev_sim_err_msg}，Wayland 下鼠标移动及滚动功能将无法工作")
                    self.evdev_wheel_scroller = None
                    self.evdev_abs_mouse = None
            else:
                if can_evdev_simulate_input:
                    if self.config.SCROLL_METHOD == 'invisible_cursor':
                        self._start_invisible_cursor_thread(screen_rect, scale)
                    else:
                        self.evdev_wheel_scroller = EvdevWheelScroller()
                else:
                    logging.debug(f"{evdev_sim_err_msg}，X11 下默认使用 XTest 进行滚动")
                    self.evdev_wheel_scroller = None
                    self.invisible_scroller = None
        except Exception as err:
            logging.error(f"创建虚拟设备失败: {err}")
            send_notification("设备错误", f"无法创建虚拟设备: {err}，基于 evdev 的滚动功能将不可用", "critical")
            self.evdev_wheel_scroller = None
            self.invisible_scroller = None
            self.evdev_abs_mouse = None

    def update_scroll_method(self):
        if IS_WAYLAND: return
        target_method = self.config.SCROLL_METHOD
        if target_method == 'invisible_cursor':
            if self.invisible_scroller: return
            if self.session.screen_rect.width > 0:
                self._start_invisible_cursor_thread(self.session.screen_rect, self.session.scale)

    def get_pointer_position(self, target: CoordSys = CoordSys.GLOBAL):
        """获取当前鼠标指针位置"""
        # 逻辑px
        def _get_pos_impl():
            try:
                if IS_WAYLAND:
                    win = self.view.get_window()
                    if win:
                        _, wx, wy, _ = win.get_device_position(self.gdk_pointer)
                        return self.view.coord_manager.map_point(wx, wy, source=CoordSys.WINDOW, target=target)
                    return (0, 0)
                else:
                    _, x, y = self.gdk_pointer.get_position()
                    return self.view.coord_manager.map_point(x, y, source=CoordSys.GLOBAL, target=target)
            except Exception as e:
                logging.error(f"获取鼠标位置失败: {e}")
                return (0, 0)
        if threading.current_thread() is threading.main_thread():
            return _get_pos_impl()
        else:
            result = [(0, 0)]
            event = threading.Event()
            def task():
                result[0] = _get_pos_impl()
                event.set()
            GLib.idle_add(task)
            event.wait()
            return result[0]

    def set_pointer_position(self, x, y):
        """设置鼠标指针位置"""
        # x, y: 逻辑px全局坐标
        def do_warp():
            try:
                if IS_WAYLAND:
                    if self.evdev_abs_mouse:
                        scale = self.session.scale
                        # 逻辑px -> 缓冲区px
                        buf_x = round(x * scale)
                        buf_y = round(y * scale)
                        self.evdev_abs_mouse.move(buf_x, buf_y)
                    else:
                        logging.warning("Wayland 下缺少 EvdevAbsoluteMouse，无法移动鼠标")
                else:
                    self.gdk_pointer.warp(self.gdk_screen, round(x), round(y))
                    self.gdk_display.flush()
            except Exception as e:
                logging.error(f"设置鼠标位置失败: {e}")
        if threading.current_thread() is threading.main_thread():
            do_warp()
            time.sleep(0.01)
        else:
            event = threading.Event()
            def task():
                do_warp()
                event.set()
            GLib.idle_add(task)
            event.wait()
            time.sleep(0.01)

    def scroll_discrete(self, ticks, return_cursor=False):
        if ticks == 0:
            return
        # 逻辑px窗口坐标
        shot_x = self.session.geometry['x']
        shot_y = self.session.geometry['y']
        center_x = shot_x + self.session.geometry['w'] / 2
        center_y = shot_y + self.session.geometry['h'] / 2
        g_center_x, g_center_y = self.view.coord_manager.map_point(center_x, center_y, source=CoordSys.WINDOW, target=CoordSys.GLOBAL)
        if self.config.SCROLL_METHOD == 'invisible_cursor' and self.invisible_scroller:
            logging.debug(f"使用隐形光标执行离散滚动: {ticks} 格")
            scale = self.session.scale
            # 逻辑px -> 缓冲区px
            g_center_x_buf = round(g_center_x * scale)
            g_center_y_buf = round(g_center_y * scale)
            scroller = self.invisible_scroller
            try:
                scroller.move(g_center_x_buf, g_center_y_buf)
                time.sleep(0.05)
                scroller.scroll_discrete(ticks)
            finally:
                time.sleep(0.05)
                scroller.park()
        else:
            original_pos = self.get_pointer_position(target=CoordSys.GLOBAL) # 逻辑px
            self.set_pointer_position(g_center_x + 1, g_center_y + 1)
            self.set_pointer_position(g_center_x, g_center_y)
            time.sleep(0.05)
            try:
                scrolled_via_xtest = False
                if not IS_WAYLAND:
                    logging.debug(f"使用 XTest 执行离散滚动: {ticks} 格")
                    try:
                        disp = display.Display()
                        button_code = 5 if ticks > 0 else 4
                        num_clicks = abs(ticks)
                        for i in range(num_clicks):
                            xtest.fake_input(disp, X.ButtonPress, button_code)
                            disp.sync()
                            time.sleep(0.01)
                            xtest.fake_input(disp, X.ButtonRelease, button_code)
                            disp.sync()
                            if i < num_clicks - 1:
                                time.sleep(0.01)
                        disp.close()
                        scrolled_via_xtest = True
                    except Exception as e:
                        logging.warning(f"使用 XTest 模拟滚动失败，尝试回退到 Evdev: {e}")
                        try: disp.close()
                        except: pass
                if not scrolled_via_xtest:
                    logging.debug(f"使用 Evdev 执行离散滚动: {ticks} 格")
                    if self.evdev_wheel_scroller:
                        try:
                            self.evdev_wheel_scroller.scroll_discrete(ticks)
                        except Exception as e:
                            logging.error(f"使用 Evdev 模拟滚动失败: {e}")
                    else:
                        logging.warning("滚动失败 Evdev 未配置")
                        GLib.idle_add(send_notification, "自动滚动不可用", "XTest 无效且未检测到 Evdev 虚拟设备", "warning", config.WARNING_SOUND)
            except Exception as e:
                logging.error(f"模拟滚动失败: {e}")
            time.sleep(0.05)
            if return_cursor:
                self.set_pointer_position(*original_pos) # 逻辑px全局坐标

    def cleanup(self):
        if self.evdev_wheel_scroller:
            self.evdev_wheel_scroller.close()
            self.evdev_wheel_scroller = None
        if self.evdev_abs_mouse:
            self.evdev_abs_mouse.close()
            self.evdev_abs_mouse = None
        if self.invisible_scroller:
            if self.config.REUSE_INVISIBLE_CURSOR and self.config.SCROLL_METHOD == 'invisible_cursor':
                logging.info("跳过隐形光标资源清理（启用复用）")
                if self.invisible_scroller.is_ready:
                    self.invisible_scroller.park()
            else:
                self.invisible_scroller.cleanup()
                self.invisible_scroller = None

class ActionController:
    def __init__(self, session: CaptureSession, view: 'CaptureOverlay', config_obj: Config, frame_grabber: FrameGrabber):
        self.session = session
        self.view = view
        self.config = config_obj
        self.frame_grabber = frame_grabber
        ImageMatcher.configure(self.config)
        self.scroll_manager = ScrollManager(self.config, self.session, self.view)
        self.grid_mode_controller = GridModeController(self.config, self.session, self.view)
        self.is_processing_movement = False
        self.auto_scroll_timer_id = None
        self.saved_cursor_pos = None # 逻辑px全局坐标
        self.pending_capture = False
        self._capture_filename_counter = 0
        self.stitch_model = StitchModel()
        self.task_queue = queue.Queue()
        self.result_queue = queue.Queue()
        self.scroll_listener = None
        if not IS_WAYLAND:
            self.scroll_listener = XlibScrollListener(self.view)
            self.scroll_listener.start()
        self.accumulated_scroll_ticks = 0
        self.stitch_worker = threading.Thread(
            target=self._stitch_worker_loop,
            args=(self.task_queue, self.result_queue, session.scroll_stat_history),
            daemon=True
        )
        self.stitch_worker_running = True
        self.stitch_worker.start()
        self.result_check_timer_id = GLib.timeout_add(100, self._check_result_queue)
        logging.info("StitchWorker 后台线程及结果检查器已启动")
        self.config_handler_id = self.config.connect('setting-changed', self._on_config_changed)
        self.stitch_model.connect('model-updated', self._on_model_updated)

    def _on_config_changed(self, config_obj, section, key, value):
        if key == 'scroll_method':
            logging.debug("检测到滚动方式变更，正在更新...")
            self.scroll_manager.update_scroll_method()
        elif key == 'preview_cache_size':
            logging.debug(f"预览缓存大小更新为 {self.config.PREVIEW_CACHE_SIZE}")
            self.stitch_model.update_cache_limit(self.config.PREVIEW_CACHE_SIZE)

    @property
    def is_auto_scrolling(self):
        return self.session.current_mode == CaptureMode.AUTO

    def _on_model_updated(self, model_instance):
        should_be_locked = self.stitch_model.capture_count > 0
        if should_be_locked and not self.session.is_horizontally_locked:
            self.session.is_horizontally_locked = True
            logging.info("第一张截图已添加到模型，边框水平位置和宽度已被锁定")
        elif not should_be_locked and self.session.is_horizontally_locked:
            self.session.is_horizontally_locked = False
            logging.info("所有截图均已移除，已解锁边框水平调整功能")

    # 缓冲区px {
    def _check_result_queue(self):
        while not self.result_queue.empty():
            try:
                result = self.result_queue.get_nowait()
                result_type = result[0]
                payload = result[1]
                if result_type == 'ADD_RESULT':
                    filepath, width, height, shift, cut_y, abs_y, thumb_data, full_img_data = payload
                    self.stitch_model.add_entry(filepath, width, height, shift, cut_y, abs_y, thumb_data, full_img_data)
                elif result_type == 'STATIC_BARS_DETECTED':
                    h_header, h_footer, w_left, w_right = payload
                    self.session.set_static_bars(h_header, h_footer, w_left, w_right)
                elif result_type == 'LEARNED_SCROLL':
                    ticks, dist_px = payload
                    self.session.scroll_stat_history[ticks].append(dist_px)
                    logging.debug(f"学习到滚动统计，滚动 {ticks} 格 -> {dist_px} px")
                elif result_type == 'POP_ACK':
                    logging.debug("收到 StitchWorker 的 POP 确认，执行模型删除")
                    self.stitch_model.pop_entry()
                elif result_type == 'BOTTOM_REACHED':
                    logging.debug("收到 StitchWorker 的 BOTTOM_REACHED 信号，停止自动滚动")
                    GLib.idle_add(self.stop_auto_scroll, "检测到页面已到达底部")
            except queue.Empty:
                break
            except Exception as e:
                logging.error(f"处理 StitchWorker 结果时出错: {e}")
        return True

    @staticmethod
    def _stitch_worker_loop(task_queue: queue.Queue, result_queue: queue.Queue, scroll_history: object):
        logging.debug("StitchWorker 线程开始运行...")
        pending_new_trend = []
        last_action_was_pop = False
        last_detected_bars = (-1, -1, -1, -1)
        cached_prev_filepath = None
        cached_prev_img = None
        while True:
            try:
                task = task_queue.get(timeout=1)
            except queue.Empty:
                continue
            if task is None or task.get('type') == 'EXIT':
                logging.debug("StitchWorker 收到退出信号")
                break
            if task.get('type') == 'ADD':
                filepath_str = task.get('filepath')
                prev_filepath_str = task.get('prev_filepath')
                current_box_y = task.get('box_y_buf', 0)
                prev_box_y = task.get('prev_box_y_buf', 0)
                should_perform_matching = task.get('should_perform_matching', False)
                is_auto_mode = task.get('is_auto_mode', False)
                ticks_scrolled = abs(task.get('ticks_scrolled', 0))
                filepath = Path(filepath_str)
                logging.debug(f"StitchWorker: 处理 ADD 任务: {filepath.name}")
                if not filepath.is_file():
                    logging.error(f"StitchWorker: 文件不存在 {filepath}")
                    task_queue.task_done()
                    continue
                w_new, h_new = None, None
                try:
                    img_new = cv2.imread(filepath_str)
                    if img_new is None: raise ValueError("cv2.imread 返回 None")
                    h_new, w_new, _ = img_new.shape
                    thumb_target_w = 32
                    thumb_scale = thumb_target_w / w_new
                    thumb_target_h = max(1, int(h_new * thumb_scale))
                    img_full_bgra = cv2.cvtColor(img_new, cv2.COLOR_BGR2BGRA)
                    img_thumb_bgra = cv2.resize(img_full_bgra, (thumb_target_w, thumb_target_h), interpolation=cv2.INTER_AREA)
                    thumb_data = (img_thumb_bgra, thumb_target_w, thumb_target_h, img_thumb_bgra.strides[0])
                    full_img_data = (img_full_bgra, w_new, h_new, img_full_bgra.strides[0])
                    shift = h_new
                    cut_y = 0
                    success = True
                    box_shift_y = current_box_y - prev_box_y
                    if ticks_scrolled > 0:
                        min_shift = ticks_scrolled * config.MIN_SCROLL_PER_TICK + box_shift_y
                        max_shift = ticks_scrolled * config.MAX_SCROLL_PER_TICK + box_shift_y
                    else:
                        min_shift = box_shift_y
                        max_shift = h_new
                    if prev_filepath_str:
                        logging.debug(f"StitchWorker: 计算 {filepath.name} 与 {Path(prev_filepath_str).name} 的重叠")
                        if prev_filepath_str == cached_prev_filepath and cached_prev_img is not None:
                            img_top = cached_prev_img
                        else:
                            img_top = cv2.imread(prev_filepath_str)
                            if img_top is None: raise ValueError(f"无法加载上一张图片 {prev_filepath_str}")
                        h_top, _, _ = img_top.shape
                        if should_perform_matching:
                            success = False
                            final_best_candidate = None
                            all_candidates = []
                            detected_bars = ImageMatcher.detect_static_bars(
                                img_top, img_new, prev_y=prev_box_y, curr_y=current_box_y
                            )
                            h_header, h_footer, _, _ = detected_bars
                            if detected_bars != last_detected_bars:
                                last_detected_bars = detected_bars
                                result_queue.put(('STATIC_BARS_DETECTED', detected_bars))
                            if not last_action_was_pop:
                                predicted_candidates = []
                                if ticks_scrolled > 0:
                                    exact_history = scroll_history.get(ticks_scrolled, [])
                                    if exact_history:
                                        common_dists = [dist for dist, count in Counter(exact_history).most_common()]
                                        predicted_candidates.extend(common_dists)
                                        logging.debug(f"StitchWorker: 历史精确匹配候选 (ticks={ticks_scrolled}): {common_dists}")
                                    all_dists = [d for sublist in scroll_history.values() for d in sublist]
                                    all_ticks_sum = sum([t * len(sublist) for t, sublist in scroll_history.items()])
                                    all_units = [d / t for t, sublist in scroll_history.items() for d in sublist if t > 0]
                                    if all_ticks_sum > 0:
                                        avg_px_per_tick = sum(all_dists) / all_ticks_sum
                                        std_dev = np.std(all_units)
                                        if std_dev * ticks_scrolled <= 2.0:
                                            inferred_dist = round(avg_px_per_tick * ticks_scrolled)
                                            is_duplicate = any(abs(inferred_dist - c) < 1 for c in predicted_candidates)
                                            if not is_duplicate:
                                                predicted_candidates.append(inferred_dist)
                                                logging.debug(f"StitchWorker: 均值推断候选 {inferred_dist}px (均值 {avg_px_per_tick:.2f}px/格, std={std_dev:.2f})")
                                elif box_shift_y != 0:
                                    predicted_candidates.append(0)
                                for cand_scroll_px in predicted_candidates:
                                    pred_shift = cand_scroll_px + box_shift_y
                                    if pred_shift >= h_top - h_footer: continue
                                    score_pred, pred_cut_y = ImageMatcher.verify_region(img_top, img_new, pred_shift, h_header, detected_bars, box_shift_y)
                                    cand = {'shift': pred_shift, 'cut_y': pred_cut_y, 'score': score_pred, 'source': 'prediction'}
                                    if score_pred > ImageMatcher.THRES_SCORE:
                                        final_best_candidate = cand
                                        logging.debug(f"StitchWorker: 预测命中 (score={score_pred:.1f})! 滚动距离 {cand_scroll_px}px")
                                        break
                                    else:
                                        all_candidates.append(cand)
                            if not final_best_candidate:
                                logging.debug("StitchWorker: 预测未达标，执行搜索")
                                s_shift, s_cut, s_score = ImageMatcher.detect_visual_shift(img_top, img_new, detected_bars, box_shift_y, min_shift, max_shift)
                                cand = {'shift': s_shift, 'cut_y': s_cut, 'score': s_score, 'source': 'search'}
                                if s_score > ImageMatcher.THRES_SCORE:
                                    final_best_candidate = cand
                                    logging.debug(f"StitchWorker: 搜索命中 (score={s_score:.1f}), shift={s_shift}")
                                else:
                                    all_candidates.append(cand)
                            if not final_best_candidate:
                                fb_shift, fb_cut, fb_score = ImageMatcher.detect_visual_shift(img_top, img_new, detected_bars, box_shift_y, 0, min_shift)
                                cand = {'shift': fb_shift, 'cut_y': fb_cut, 'score': fb_score, 'source': 'fallback'}
                                if fb_score > ImageMatcher.THRES_SCORE:
                                    final_best_candidate = cand
                                    logging.debug(f"StitchWorker: 回退搜索命中 (score={fb_score:.1f})")
                                else:
                                    all_candidates.append(cand)
                            if not final_best_candidate and all_candidates:
                                valid_candidates = [c for c in all_candidates if c['score'] >= 0]
                                if valid_candidates:
                                    final_best_candidate = max(valid_candidates, key=lambda c: (c['score'], 1 if c['source'] == 'prediction' else 0, c['shift']))
                                    logging.debug(f"StitchWorker: 无确信匹配，选出最佳候选：{final_best_candidate['source']} (score={final_best_candidate['score']:.1f})")
                            if final_best_candidate:
                                shift = final_best_candidate['shift']
                                cut_y = final_best_candidate['cut_y']
                                success = True
                                if is_auto_mode and shift == 0 and final_best_candidate['score'] > ImageMatcher.THRES_SCORE:
                                    logging.info("StitchWorker: 自动模式下检测到 shift=0，判定到达底部")
                                    result_queue.put(('STATIC_BARS_DETECTED', (0, 0, 0, 0)))
                                    result_queue.put(('BOTTOM_REACHED', None))
                                    continue
                            else:
                                y_start_scan = h_header if box_shift_y == 0 else 0
                                y_end_scan = h_new - h_footer
                                search_h_bot = y_end_scan - y_start_scan
                                num_rows = max(1, round(search_h_bot ** 0.5 / 3.5))
                                row_h = search_h_bot // num_rows
                                fallback_res = ImageMatcher.detect_micro_overlap(img_top, img_new, detected_bars, row_h, box_shift_y)
                                if fallback_res is not None:
                                    fallback_shift, fallback_cut_y = fallback_res
                                    logging.debug(f"StitchWorker: 微小重叠兜底成功，shift={fallback_shift}，cut_y={fallback_cut_y}")
                                    shift = fallback_shift
                                    cut_y = fallback_cut_y
                                    success = True
                                else:
                                    logging.debug(f"StitchWorker: 检测微小重叠失败，row_h={row_h}")
                                    success = False
                        else:
                            logging.debug("StitchWorker: 匹配已禁用，执行直接拼接")
                            shift = h_top
                            cut_y = 0
                            success = True
                        if not success:
                            logging.warning(f"StitchWorker: 图像匹配失败，回退到直接拼接")
                            shift = h_top
                            cut_y = 0
                            if is_auto_mode:
                                score_static, _ = ImageMatcher.verify_region(img_top, img_new, 0, h_header, detected_bars, box_shift_y)
                                if score_static > ImageMatcher.THRES_SCORE:
                                    logging.info("StitchWorker: 检测到底部")
                                    result_queue.put(('BOTTOM_REACHED', None))
                    if success and prev_filepath_str and should_perform_matching:
                        actual_scroll_px = shift - box_shift_y
                        is_anomaly = False
                        if ticks_scrolled > 0 and not last_action_was_pop and actual_scroll_px > 0:
                            current_unit = actual_scroll_px / ticks_scrolled
                            if scroll_history:
                                all_dists = [d for sublist in scroll_history.values() for d in sublist]
                                all_ticks = [t * len(sublist) for t, sublist in scroll_history.items()]
                                total_ticks_sum = sum(all_ticks)
                                if total_ticks_sum > 0:
                                    avg_unit = sum(all_dists) / total_ticks_sum
                                    if not (0.85 * avg_unit <= current_unit <= 1.15 * avg_unit):
                                        logging.warning(f"StitchWorker: 滚动单位异常，当前: {current_unit:.2f}px/格, 历史均值: {avg_unit:.2f}px/格")
                                        is_anomaly = True
                                    else:
                                        if pending_new_trend:
                                            pending_new_trend.clear()
                            else:
                                is_anomaly = True
                        if is_anomaly:
                            should_accept_trend = False
                            if not pending_new_trend:
                                pending_new_trend.append((ticks_scrolled, actual_scroll_px))
                            else:
                                prev_ticks, prev_dist = pending_new_trend[-1]
                                prev_unit = prev_dist / prev_ticks
                                if 0.85 * prev_unit <= current_unit <= 1.15 * prev_unit:
                                    pending_new_trend.append((ticks_scrolled, actual_scroll_px))
                                    if len(pending_new_trend) >= 3:
                                        should_accept_trend = True
                                else:
                                    pending_new_trend = [(ticks_scrolled, actual_scroll_px)]
                            if should_accept_trend:
                                logging.info(f"StitchWorker: 检测到稳定的新滚动速度 ({current_unit:.2f}px/格)，更新历史统计")
                                is_anomaly = False
                                for t, d in pending_new_trend:
                                    result_queue.put(('LEARNED_SCROLL', (t, d)))
                                pending_new_trend.clear()
                        if not is_anomaly and not last_action_was_pop and ticks_scrolled > 0 and actual_scroll_px > 0:
                            logging.debug(f"StitchWorker: 学习数据 -> {ticks_scrolled}格 = {actual_scroll_px}px")
                            result_queue.put(('LEARNED_SCROLL', (ticks_scrolled, actual_scroll_px)))
                    result_queue.put(('ADD_RESULT', (filepath_str, w_new, h_new, shift, cut_y, current_box_y, thumb_data, full_img_data)))
                    cached_prev_filepath = filepath_str
                    cached_prev_img = img_new
                except Exception as e:
                    logging.error(f"StitchWorker: 处理 ADD 任务时出错 ({filepath.name}): {e}")
                    GLib.idle_add(send_notification, "图片处理错误", f"无法处理截图 {Path(filepath_str).name}: {e}", "warning", config.WARNING_SOUND)
                    try:
                        if w_new is None or h_new is None:
                            file_info, w_fallback, h_fallback = GdkPixbuf.Pixbuf.get_file_info(str(filepath))
                            if file_info: w_new, h_new = w_fallback, h_fallback
                            else: w_new, h_new = 0, 0
                        result_queue.put(('ADD_RESULT', (filepath_str, w_new, h_new, h_new, 0, current_box_y, None, None)))
                    except Exception as fallback_e:
                        logging.error(f"StitchWorker: 获取图片尺寸失败: {fallback_e}")
                finally:
                    last_action_was_pop = False
                    task_queue.task_done()
            elif task.get('type') == 'POP':
                logging.debug("StitchWorker: 收到 POP 任务，发送确认")
                last_action_was_pop = True
                cached_prev_filepath = None
                cached_prev_img = None
                result_queue.put(('POP_ACK', None))
                task_queue.task_done()
        logging.debug("StitchWorker 线程已结束")
    # 缓冲区px }

    def handle_movement_action(self, direction: str, source: str = 'hotkey'):
        """处理前进/后退动作 (滚动, 截图, 删除). """
        if self.is_processing_movement:
            logging.debug("正在处理上一个移动动作，忽略新的请求")
            return
        if not self.grid_mode_controller.is_active:
            logging.warning("非整格模式下，前进/后退动作无效")
            send_notification("操作无效", "前进/后退操作仅在整格模式下可用", "normal")
            return
        self.is_processing_movement = True
        grid_unit_buf = self.session.grid_unit # 缓冲区px
        scale = self.session.scale
        if grid_unit_buf <= 0:
            logging.warning("整格模式滚动单位无效，无法执行操作")
            self.is_processing_movement = False
            return
        action_str = config.FORWARD_ACTION if direction == 'down' else config.BACKWARD_ACTION
        actions = action_str.lower().replace(" ", "").split('_')
        def do_scroll_action(callback):
            ticks_floor = int(int(self.session.geometry['h'] * scale) / grid_unit_buf) # 逻辑px -> 缓冲区px
            if self.session.grid_matching_enabled:
                formula = self.config.GRID_SCROLL_TICKS_FORMULA
                def eval_formula(f_str):
                    expr = f_str.replace('{ticks}', str(ticks_floor))
                    if not re.match(r'^[\d\s+\-*/().,maxminint]+$', expr):
                        raise ValueError("公式包含非法字符")
                    return int(eval(expr, {"__builtins__": None, "max": max, "min": min, "int": int}))
                try:
                    num_ticks = eval_formula(formula)
                    logging.debug(f"应用公式 '{formula}' (ticks={ticks_floor}) -> 滚动 {num_ticks} 格")
                except Exception as e:
                    default_formula = self.config.get_default_string('Behavior', 'grid_scroll_ticks_formula')
                    logging.warning(f"滚动公式 '{formula}' 解析失败: {e}，回退到默认公式 '{default_formula}'")
                    try:
                        num_ticks = eval_formula(default_formula)
                    except Exception as e_def:
                        logging.error(f"默认公式解析也失败: {e_def}，回退到 ({ticks_floor}-1)")
                        num_ticks = max(1, ticks_floor - 1)
            else:
                num_ticks = ticks_floor
            direction_sign = 1 if direction == 'down' else -1
            total_ticks = num_ticks * direction_sign
            self.accumulated_scroll_ticks += total_ticks
            self.scroll_manager.scroll_discrete(total_ticks, return_cursor=(source == 'button'))
            GLib.timeout_add(self.config.GRID_SCROLL_INTERVAL_MS, callback)
            return False
        def do_capture_action(callback):
            logging.debug("执行截图...")
            self.take_capture()
            GLib.timeout_add(50, callback)
            return False
        def do_delete_action(callback):
            logging.debug("执行删除...")
            self.delete_last_capture()
            GLib.timeout_add(50, callback)
            return False
        action_map = {
            'scroll': do_scroll_action,
            'capture': do_capture_action,
            'delete': do_delete_action
        }
        action_queue = [action_map[act] for act in actions if act in action_map]
        if not action_queue:
            logging.warning(f"为方向 '{direction}' 配置了无效的动作: '{action_str}'")
            return
        def execute_next_in_queue(index=0):
            if index >= len(action_queue):
                self.is_processing_movement = False
                return False
            action_func = action_queue[index]
            action_func(lambda: execute_next_in_queue(index + 1))
            return False
        GLib.idle_add(execute_next_in_queue, 0)

    def _get_and_reset_scroll_delta(self):
        delta = 0
        global hotkey_manager
        if hotkey_manager and hotkey_manager.listener and hasattr(hotkey_manager.listener, 'get_scroll_delta'):
            delta = hotkey_manager.listener.get_scroll_delta(reset=True)
        elif self.scroll_listener:
            delta = self.scroll_listener.get_scroll_delta(reset=True)
        if delta == 0 and self.accumulated_scroll_ticks != 0:
            delta = self.accumulated_scroll_ticks
        self.accumulated_scroll_ticks = 0
        return delta

    def _release_movement_lock(self):
        if self.is_processing_movement:
            self.is_processing_movement = False
        return False

    def take_capture(self, widget=None, automated=False):
        filepath = None
        if not automated and self.is_auto_scrolling:
            logging.debug("自动滚动模式下忽略手动截图请求")
            return False
        try:
            # 逻辑px窗口坐标
            shot_x = self.session.geometry['x']
            shot_y = self.session.geometry['y']
            w = self.session.geometry['w']
            h = self.session.geometry['h']
            is_grid = self.grid_mode_controller.is_active
            should_include_cursor = self.config.CAPTURE_WITH_CURSOR and not is_grid and not automated
            real_ticks = self._get_and_reset_scroll_delta()
            logging.debug(f"ActionController: 捕获时检测到累计滚动 {real_ticks} 格")
            self._move_cursor_out_if_needed(shot_x, shot_y, w, h, should_include_cursor)
            if IS_WAYLAND and not should_include_cursor:
                time.sleep(0.1)
            if w <= 0.5 or h <= 0.5:
                logging.warning(f"捕获区域过小，跳过截图。尺寸: {w}x{h}")
                return False
            filepath = config.TEMP_DIRECTORY / f"{self._capture_filename_counter:04d}_capture.png"
            self._capture_filename_counter += 1
            cap_x, cap_y = self.view.coord_manager.map_point(shot_x, shot_y, source=CoordSys.WINDOW, target=self.frame_grabber.target_coords)
            if self.frame_grabber.capture(cap_x, cap_y, w, h, filepath, self.session.scale, include_cursor=should_include_cursor):
                logging.info(f"已捕获截图: {filepath}")
                if not automated:
                    SystemInteraction.play_sound(config.CAPTURE_SOUND)
                prev_entry = self.stitch_model.entries[-1] if self.stitch_model.entries else None
                prev_filepath = prev_entry['filepath'] if prev_entry else None
                prev_box_y_buf = prev_entry.get('box_y', 0) if prev_entry else 0
                box_y_buf = round(cap_y * self.session.scale)
                if self.is_auto_scrolling:
                    should_match = True
                elif is_grid:
                    should_match = self.session.grid_matching_enabled
                else:
                    should_match = self.config.ENABLE_FREE_SCROLL_MATCHING
                task = {
                    'type': 'ADD',
                    'filepath': str(filepath),
                    'prev_filepath': prev_filepath,
                    'box_y_buf': box_y_buf,
                    'prev_box_y_buf': prev_box_y_buf,
                    'should_perform_matching': should_match,
                    'is_auto_mode': self.is_auto_scrolling,
                    'ticks_scrolled': real_ticks
                }
                self.task_queue.put(task)
                return True
            else:
                logging.error(f"截图失败: {filepath}")
                filepath = None
                send_notification("截图失败", "无法从屏幕获取图像，请检查日志", "warning", config.WARNING_SOUND,)
                return False
        except Exception as e:
            logging.error(f"执行截图失败: {e}")
            send_notification("截图失败", f"无法截图: {e}", "warning", config.WARNING_SOUND)
            filepath = None
            return False

    def _move_cursor_out_if_needed(self, win_x, win_y, w, h, should_include_cursor):
        """Wayland 在自动模式和整格模式下如果配置为截取鼠标指针，则将其移动到截图区域之外"""
        # win_x, win_y, w, h: 逻辑px; win_x, win_y: 窗口坐标
        if not IS_WAYLAND or not config.CAPTURE_WITH_CURSOR or should_include_cursor:
            return
        g_x_logic, g_y_logic = self.view.coord_manager.map_point(win_x, win_y, source=CoordSys.WINDOW, target=CoordSys.GLOBAL)
        target_x_logic = g_x_logic + w + 40
        target_y_logic = g_y_logic + (h // 2)
        scale = self.session.scale
        # 逻辑px -> 缓冲区px
        target_x_buf = round(target_x_logic * scale)
        target_y_buf = round(target_y_logic * scale)
        abs_mouse = self.scroll_manager.evdev_abs_mouse
        if abs_mouse:
            logging.debug(f"移动鼠标至区域外 ({target_x_buf}, {target_y_buf}) 以避开截图")
            abs_mouse.move(target_x_buf, target_y_buf)

    def delete_last_capture(self, widget=None):
        logging.info("请求删除最后一张截图...")
        if self.is_auto_scrolling:
            logging.warning("自动滚动模式下忽略撤销请求")
            return
        SystemInteraction.play_sound(config.UNDO_SOUND)
        task = {'type': 'POP'}
        self.task_queue.put(task)

    def start_auto_scroll(self, widget=None, source='hotkey'):
        if self.is_auto_scrolling:
            logging.warning("自动滚动已在运行中")
            return
        self.session.set_mode(CaptureMode.AUTO)
        global hotkey_manager
        if hotkey_manager:
            hotkey_manager.enable_mouse_click_monitor(True, lambda: GLib.idle_add(self.stop_auto_scroll, "用户点击鼠标左键停止"))
        if source == 'button' and self.config.SCROLL_METHOD == 'move_user_cursor':
            self.saved_cursor_pos = self.scroll_manager.get_pointer_position(target=CoordSys.GLOBAL)
            logging.debug(f"自动模式：记录原始光标位置 {self.saved_cursor_pos}")
        self.pending_capture = False
        if self.stitch_model.capture_count == 0:
            logging.info("自动模式：首次启动，先进行截图")
            self._auto_capture_step()
        else:
            logging.info("自动模式：继续添加截图，直接开始滚动")
            self._auto_scroll_step()
        send_notification("自动模式已启动", f"按 {config.HOTKEY_AUTO_SCROLL_STOP.to_string()} 或点击鼠标左键停止", "normal")

    def stop_auto_scroll(self, reason_message=None, level="normal"):
        if not self.is_auto_scrolling:
            return
        logging.info("正在停止自动滚动...")
        global hotkey_manager
        if hotkey_manager:
            hotkey_manager.enable_mouse_click_monitor(False)
        should_restore = self.saved_cursor_pos is not None
        notif_msg = reason_message if reason_message else "用户按快捷键停止"
        notification_info = ("自动模式已停止", notif_msg, level)
        if not self.auto_scroll_timer_id and not self.pending_capture:
            send_notification(*notification_info)
        if self.auto_scroll_timer_id:
            GLib.source_remove(self.auto_scroll_timer_id)
            self.auto_scroll_timer_id = None
            logging.debug("自动滚动定时器已移除")
            if self.pending_capture:
                GLib.timeout_add(300, self._perform_delayed_final_capture, should_restore, notification_info)
                should_restore = False
        self._release_movement_lock()
        if should_restore:
            logging.debug(f"自动模式结束：恢复原始光标位置到 {self.saved_cursor_pos}")
            self.scroll_manager.set_pointer_position(*self.saved_cursor_pos)
            self.saved_cursor_pos = None
        if self.session.grid_app_class is not None:
            self.session.set_mode(CaptureMode.GRID)
        else:
            self.session.set_mode(CaptureMode.FREE)

    def _perform_delayed_final_capture(self, should_restore_cursor=False, notification_info=None):
        if self.session.is_exiting or not self.stitch_worker_running or self.session.is_finished:
            return False
        logging.debug("自动模式：执行延迟后的最终截图")
        self.take_capture(automated=True)
        self.pending_capture = False
        if should_restore_cursor and self.saved_cursor_pos:
            logging.debug(f"延迟恢复原始光标位置到 {self.saved_cursor_pos}")
            self.scroll_manager.set_pointer_position(*self.saved_cursor_pos)
            self.saved_cursor_pos = None
        if notification_info:
            send_notification(*notification_info)
        return False

    def _auto_scroll_step(self):
        if not self.is_auto_scrolling:
            self._release_movement_lock()
            return False
        if self.is_processing_movement:
            logging.debug("自动滚动：正在处理上一动作，等待 100 ms")
            self.auto_scroll_timer_id = GLib.timeout_add(100, self._auto_scroll_step)
            return False
        ticks_to_scroll = self.config.AUTO_SCROLL_TICKS_PER_STEP
        self.scroll_manager.scroll_discrete(ticks_to_scroll, return_cursor=False)
        logging.debug(f"自动滚动: 滚动 {ticks_to_scroll} 格, 等待 {self.config.AUTO_SCROLL_INTERVAL_MS} ms 后截图")
        self.accumulated_scroll_ticks += ticks_to_scroll
        self.pending_capture = True
        self.is_processing_movement = True
        self.auto_scroll_timer_id = GLib.timeout_add(self.config.AUTO_SCROLL_INTERVAL_MS, self._auto_capture_step)
        return False

    def _auto_capture_step(self):
        if not self.is_auto_scrolling:
            self._release_movement_lock()
            return False
        if not self.take_capture(automated=True):
            self.stop_auto_scroll("截图失败", level="warning")
            return False
        self.is_processing_movement = False
        self.pending_capture = False
        self.auto_scroll_timer_id = GLib.timeout_add(50, self._auto_scroll_step)
        return False

    def finalize_and_quit(self, widget=None):
        # 缓冲区px
        if not self.config.SAVE_DIRECTORY:
            logging.warning("保存目录未配置，中止完成流程")
            send_notification("配置缺失", "请先设置图片保存目录", "warning", config.WARNING_SOUND)
            if not self.view.config_panel.get_visible():
                self.view.toggle_config_panel()
            self.view.config_panel.switch_to_page("output")
            self.view.config_panel.set_advanced_mode(True)
            return
        if self.is_auto_scrolling:
            self.stop_auto_scroll()
        if self.stitch_model.capture_count == 0:
            logging.info("未进行任何截图。正在退出")
            self.perform_cleanup()
            return
        JPEG_MAX_DIMENSION = 65500
        if self.config.SAVE_FORMAT == 'JPEG' and (self.stitch_model.image_width > JPEG_MAX_DIMENSION or self.stitch_model.total_virtual_height > JPEG_MAX_DIMENSION):
            logging.warning(f"长图尺寸 ({self.stitch_model.image_width}x{self.stitch_model.total_virtual_height}) 超过 JPEG 上限，中止完成流程")
            msg = f"当前尺寸已超出 JPEG 上限\n请切换为 PNG 格式或确保图片宽高不超过 {JPEG_MAX_DIMENSION} 像素"
            send_notification("长图尺寸过大", msg, "warning", config.WARNING_SOUND, 5)
            if not self.view.config_panel.get_visible():
                self.view.toggle_config_panel()
            self.view.config_panel.switch_to_page("output")
            return
        global hotkey_manager
        if hotkey_manager:
            hotkey_manager.set_paused(True)
        logging.debug("请求停止 StitchWorker 并等待...")
        self.task_queue.put({'type': 'EXIT'})
        self.stitch_worker.join(timeout=2.0)
        self.stitch_worker_running = False
        self._check_result_queue()
        logging.debug("StitchWorker 已停止且结果队列已清空")
        processing_dialog = FeedbackWidget(self.view, text="正在处理...", show_progress=True)
        self.view.overlay_manager.show(processing_dialog, anchor='center', layer=OverlayManager.LAYER_HIGH, mask=True)
        if self.view.preview_panel and self.view.preview_panel.get_visible():
            GLib.idle_add(self.view.preview_panel.hide)
        if self.view.config_panel and self.view.config_panel.get_visible():
            GLib.idle_add(self.view.config_panel.hide)
        render_plan_snapshot = list(self.stitch_model.render_plan)
        image_width_snapshot = self.stitch_model.image_width
        total_height_snapshot = self.stitch_model.total_virtual_height
        thread = threading.Thread(
            target=self._perform_final_stitch_and_save,
            args=(processing_dialog, render_plan_snapshot, image_width_snapshot, total_height_snapshot),
            daemon=True
        )
        thread.start()

    def _release_heavy_resources(self):
        logging.debug("正在释放重资源...")
        if self.frame_grabber:
            self.frame_grabber.cleanup()
            self.frame_grabber = None

    def _perform_final_stitch_and_save(self, feedback_widget, render_plan, image_width, total_height):
        """在后台线程中执行拼接和保存"""
        # 缓冲区px
        finalize_start_time = time.perf_counter()
        def update_progress(fraction):
            GLib.idle_add(feedback_widget.set_progress, fraction)
        def update_label_text(text):
            GLib.idle_add(feedback_widget.set_text, text)
        try:
            if not render_plan:
                logging.warning("传入的截图列表为空，退出处理")
                GLib.idle_add(self.perform_cleanup)
                return
            now = datetime.now()
            timestamp_str = now.strftime(config.FILENAME_TIMESTAMP_FORMAT)
            base_filename = config.FILENAME_TEMPLATE.replace('{timestamp}', timestamp_str)
            file_extension = 'jpg' if config.SAVE_FORMAT == 'JPEG' else 'png'
            final_filename = f"{base_filename}.{file_extension}"
            output_file = config.SAVE_DIRECTORY / final_filename
            output_file.parent.mkdir(parents=True, exist_ok=True)
            stitch_start_time = time.perf_counter()
            stitched_image = stitch_images_in_memory_from_model(
                render_plan=render_plan,
                image_width=image_width,
                total_height=total_height,
                progress_callback=update_progress
            )
            stitch_duration = time.perf_counter() - stitch_start_time
            logging.info(f"图片拼接总耗时: {stitch_duration:.3f} 秒")
            if stitched_image is not None:
                update_label_text("正在保存...")
                update_progress(1.0)
                save_start_time = time.perf_counter()
                if config.SAVE_FORMAT == 'JPEG':
                    logging.debug(f"以 JPEG 格式保存，质量为 {config.JPEG_QUALITY}")
                    success = cv2.imwrite(str(output_file), stitched_image, [int(cv2.IMWRITE_JPEG_QUALITY), config.JPEG_QUALITY])
                else:
                    logging.debug("以 PNG 格式保存")
                    success = cv2.imwrite(str(output_file), stitched_image, [int(cv2.IMWRITE_PNG_COMPRESSION), 3])
                if not success:
                    raise RuntimeError(f"无法将长图 {output_file} 写入磁盘")
                save_duration = time.perf_counter() - save_start_time
                logging.info(f"图片成功拼接并保存到: {output_file}，保存耗时: {save_duration:.3f} 秒")
                total_finalize_duration = time.perf_counter() - finalize_start_time
                logging.info(f"完成最终处理总耗时: {total_finalize_duration:.3f} 秒")
                def _on_save_success():
                    clipboard_msg = ""
                    if config.COPY_TO_CLIPBOARD_ON_FINISH:
                        update_label_text("复制到剪贴板...")
                        logging.debug("开始复制到剪贴板")
                        _, cb_msg = SystemInteraction.copy_to_clipboard(output_file)
                        clipboard_msg = f"\n{cb_msg}"
                    message = f"已保存到: {output_file}{clipboard_msg}"
                    send_notification(
                        title="长截图拼接成功",
                        message=message,
                        level="success",
                        sound_name=config.FINALIZE_SOUND,
                        timeout=8,
                        action_config={'path': output_file, 'width': image_width, 'height': total_height}
                    )
                    self._release_heavy_resources()
                    self.session.set_finished(True)
                GLib.idle_add(_on_save_success)
        except Exception as e:
            logging.error(f"最终处理时发生错误: {e}")
            GLib.idle_add(self.perform_cleanup)
        finally:
            GLib.idle_add(self.view.overlay_manager.dismiss, feedback_widget)

    def quit_and_cleanup(self, widget=None):
        """处理带确认的退出逻辑"""
        if self.is_auto_scrolling:
            self.stop_auto_scroll()
        if self.stitch_model.capture_count == 0:
            logging.info("没有截图，直接退出")
            self.perform_cleanup()
            return
        dialog = QuitDialog(self.stitch_model.capture_count)
        response, _ = self.view.overlay_manager.run_modal(dialog, anchor='center', layer=OverlayManager.LAYER_HIGH, mask=True)
        if response == Gtk.ResponseType.OK:
            logging.info("用户确认放弃截图")
            self.perform_cleanup()
        else:
            logging.info("用户取消了放弃操作")

    def perform_cleanup(self):
        """执行最终的清理工作"""
        self.session.set_exiting(True)
        self.config.flush_save()
        logging.info("正在执行清理和退出操作")
        global hotkey_manager
        if hotkey_manager:
            hotkey_manager.stop()
        if self.config_handler_id:
            self.config.disconnect(self.config_handler_id)
        if self.result_check_timer_id:
            GLib.source_remove(self.result_check_timer_id)
            self.result_check_timer_id = None
            logging.debug("结果检查定时器已移除")
        if self.stitch_worker_running:
            logging.debug("检测到 StitchWorker 仍在运行，尝试最后停止...")
            self.task_queue.put({'type': 'EXIT'})
            self.stitch_worker.join(timeout=0.5)
            self.stitch_worker_running = False
        if self.scroll_listener:
            self.scroll_listener.stop()
        known_files = [entry['filepath'] for entry in self.stitch_model.entries]
        self.stitch_model.cleanup()
        if self.frame_grabber:
            self.frame_grabber.cleanup()
            logging.debug("FrameGrabber 已清理")
        self.view.overlay_manager.cleanup()
        SystemInteraction.cleanup_temp_dirs(self.config, is_exiting=True, known_files=known_files)
        self.scroll_manager.cleanup()
        Gtk.main_quit()

class ButtonPanel(Gtk.Box):
    # 逻辑px
    __gsignals__ = {
        'grid-forward-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'grid-backward-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'auto-scroll-start-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'auto-scroll-stop-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'capture-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'undo-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'finalize-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'cancel-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        buttons_data = [
            ('btn_grid_forward', '前进', 'grid-forward-clicked'),
            ('btn_grid_backward', '后退', 'grid-backward-clicked'),
            ('btn_auto_start', '开始', 'auto-scroll-start-clicked'),
            ('btn_auto_stop', '停止', 'auto-scroll-stop-clicked'),
            ('btn_capture', '截图', 'capture-clicked'),
            ('btn_undo', '撤销', 'undo-clicked'),
            ('btn_finalize', '完成', 'finalize-clicked'),
            ('btn_cancel', '取消', 'cancel-clicked')
        ]
        for attr_name, label, signal_name in buttons_data:
            btn = Gtk.Button(label=label)
            btn.connect("clicked", lambda w, s=signal_name: self.emit(s))
            btn.set_can_focus(False)
            btn.show()
            btn.get_style_context().add_class("overlay-button")
            setattr(self, attr_name, btn)
        self.btn_undo.set_sensitive(False)
        self.pack_start(self.btn_grid_forward, True, True, 0)
        self.pack_start(self.btn_grid_backward, True, True, 0)
        self.separator_grid_auto = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        self.pack_start(self.separator_grid_auto, False, False, 2)
        self.pack_start(self.btn_auto_start, True, True, 0)
        self.pack_start(self.btn_auto_stop, True, True, 0)
        self.separator_auto_main = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        self.pack_start(self.separator_auto_main, False, False, 2)
        self.pack_start(self.btn_capture, True, True, 0)
        self.pack_start(self.btn_undo, True, True, 0)
        self.pack_start(self.btn_finalize, True, True, 0)
        self.pack_start(self.btn_cancel, True, True, 0)
        self.set_size_request(config.BUTTON_PANEL_WIDTH, -1)
        self.show()
        self.update_button_state(CaptureMode.FREE)

    def update_button_state(self, mode: CaptureMode):
        is_grid = (mode == CaptureMode.GRID)
        is_auto = (mode == CaptureMode.AUTO)
        show_grid_btns = is_grid and config.ENABLE_GRID_ACTION_BUTTONS
        show_auto_btns = (not is_grid) and config.ENABLE_AUTO_SCROLL_BUTTONS
        self.btn_grid_forward.set_visible(show_grid_btns)
        self.btn_grid_backward.set_visible(show_grid_btns)
        self.separator_grid_auto.set_visible(show_grid_btns)
        self.btn_auto_start.set_visible(show_auto_btns)
        self.btn_auto_stop.set_visible(show_auto_btns)
        self.separator_auto_main.set_visible(show_auto_btns)
        sensitive = not is_auto
        self.btn_capture.set_sensitive(sensitive)
        self.btn_auto_start.set_sensitive(sensitive)
        self.btn_grid_forward.set_sensitive(sensitive)
        self.btn_grid_backward.set_sensitive(sensitive)
        if is_auto:
            self.btn_undo.set_sensitive(False)

    def set_undo_sensitive(self, sensitive: bool):
        self.btn_undo.set_sensitive(sensitive)

    def update_visibility_by_height(self, available_height, mode: CaptureMode):
        should_show_buttons_base = config.ENABLE_BUTTONS
        if not should_show_buttons_base:
            return False
        self.update_button_state(mode)
        self.show()
        _, required_h = self.get_preferred_height()
        return available_height >= required_h

class InfoPanel(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2) # 逻辑px
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.START)
        self.get_style_context().add_class("info-panel")
        self.label_count = Gtk.Label()
        self.label_dimensions = Gtk.Label()
        self.label_mode = Gtk.Label()
        self.label_count.set_name("label_count")
        self.label_dimensions.set_name("label_dimensions")
        self.label_mode.set_name("label_mode")
        for label in [self.label_count, self.label_dimensions, self.label_mode]:
            label.set_can_focus(False)
            label.get_style_context().add_class("info-label")
            label.set_no_show_all(True)
            label.set_line_wrap(True)
            label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            label.set_justify(Gtk.Justification.CENTER)
            label.set_xalign(0.5)
            self.pack_start(label, False, False, 0)
        self.update_info(0, 0, 0, CaptureMode.FREE.value)

    def update_info(self, count: int, width: int, height: int, mode_str: str):
        # width, height: 缓冲区px
        if config.SHOW_CAPTURE_COUNT:
            self.label_count.set_text(f"截图: {count}")
            self.label_count.show()
        else:
            self.label_count.hide()
        if config.SHOW_TOTAL_DIMENSIONS:
            pango_attrs = "line_height='0.8'"
            if count > 0:
                dim_markup = f"<span {pango_attrs}>{width}\nx\n{height}</span>"
            else:
                dim_markup = f"<span {pango_attrs}>宽\nx\n高</span>"
            self.label_dimensions.set_markup(dim_markup)
            self.label_dimensions.show()
        else:
            self.label_dimensions.hide()
        self.label_mode.set_markup(mode_str)
        if config.SHOW_CURRENT_MODE:
            self.label_mode.show()
        else:
            self.label_mode.hide()

class FunctionPanel(Gtk.Box):
    __gsignals__ = {
        'toggle-grid-mode-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'toggle-preview-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'open-config-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'toggle-hotkeys-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=5) # 逻辑px
        self.set_valign(Gtk.Align.START)
        self.btn_toggle_grid = Gtk.Button(label=f"整格模式")
        self.btn_toggle_grid.connect("clicked", lambda w: self.emit('toggle-grid-mode-clicked'))
        self.btn_toggle_preview = Gtk.Button(label=f"预览面板")
        self.btn_toggle_preview.connect("clicked", lambda w: self.emit('toggle-preview-clicked'))
        self.btn_open_config = Gtk.Button(label=f"配置面板")
        self.btn_open_config.connect("clicked", lambda w: self.emit('open-config-clicked'))
        self.btn_toggle_hotkeys = Gtk.Button(label=f"热键开关")
        self.btn_toggle_hotkeys.connect("clicked", lambda w: self.emit('toggle-hotkeys-clicked'))
        buttons = [self.btn_toggle_grid, self.btn_toggle_preview, self.btn_open_config, self.btn_toggle_hotkeys]
        for btn in buttons:
            btn.set_can_focus(False)
            btn.get_style_context().add_class("overlay-button")
            self.pack_start(btn, False, False, 0)
            btn.show()
        self.show()
        self.update_button_state(CaptureMode.FREE)

    def update_button_state(self, mode: CaptureMode):
        is_auto = (mode == CaptureMode.AUTO)
        self.btn_toggle_grid.set_sensitive(not is_auto)

class SidePanel(Gtk.Box):
    # 逻辑px
    __gsignals__ = {
        'toggle-grid-mode-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'toggle-preview-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'open-config-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'toggle-hotkeys-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.info_panel = InfoPanel()
        self.info_panel.set_size_request(config.SIDE_PANEL_WIDTH, -1)
        self.pack_start(self.info_panel, False, False, 0)
        self.function_panel = FunctionPanel()
        self.function_panel.set_size_request(config.SIDE_PANEL_WIDTH, -1)
        self.pack_start(self.function_panel, False, False, 0)
        self.function_panel.connect("toggle-grid-mode-clicked", lambda w: self.emit('toggle-grid-mode-clicked'))
        self.function_panel.connect("toggle-preview-clicked", lambda w: self.emit('toggle-preview-clicked'))
        self.function_panel.connect("open-config-clicked", lambda w: self.emit('open-config-clicked'))
        self.function_panel.connect("toggle-hotkeys-clicked", lambda w: self.emit('toggle-hotkeys-clicked'))
        self.info_panel.show()
        self.function_panel.show()

    def update_visibility_by_height(self, available_height):
        should_show_info_base = config.ENABLE_SIDE_PANEL and (config.SHOW_CAPTURE_COUNT or config.SHOW_TOTAL_DIMENSIONS or config.SHOW_CURRENT_MODE)
        should_show_func_base = config.ENABLE_SIDE_PANEL
        if should_show_info_base:
            self.info_panel.show()
            _, info_nat_h = self.info_panel.get_preferred_height()
        else:
            info_nat_h = 0
        if should_show_func_base:
            self.function_panel.show()
            _, func_nat_h = self.function_panel.get_preferred_height()
        else:
            func_nat_h = 0
        can_show_info_panel = available_height >= info_nat_h
        can_show_func_panel = available_height >= info_nat_h + func_nat_h
        if should_show_info_base and can_show_info_panel:
            self.info_panel.show()
        else:
            self.info_panel.hide()
        if should_show_func_base and can_show_func_panel:
            self.function_panel.show()
        else:
            self.function_panel.hide()
        return self.info_panel.get_visible() or self.function_panel.get_visible()

class InstructionPanel(Gtk.Box):
    INSTRUCTIONS = [
        ("toggle_instruction_panel", "显隐此面板"),
        ("capture", "截图"),
        ("finalize", "完成"),
        ("undo", "撤销"),
        ("cancel", "取消"),
        ("auto_scroll_start", "启用自动模式"),
        ("auto_scroll_stop", "停止自动模式"),
        ("grid_forward", "整格前进"),
        ("grid_backward", "整格后退"),
        ("configure_scroll_unit", "配置滚动单位"),
        ("toggle_grid_mode", "切换整格模式"),
        ("toggle_config_panel", "显隐配置面板"),
        ("toggle_hotkeys_enabled", "开关热键"),
        ("toggle_preview", "显隐预览面板"),
        ("preview_zoom_in", "放大预览图"),
        ("preview_zoom_out", "缩小预览图"),
    ]

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.get_style_context().add_class("instruction-panel")
        self.grid = Gtk.Grid()
        # 逻辑px
        self.grid.set_column_spacing(10)
        self.grid.set_row_spacing(4)
        self.pack_start(self.grid, True, True, 0)
        self.reload_keys()
        self.show_all()

    def reload_keys(self):
        for child in self.grid.get_children():
            self.grid.remove(child)
        global hotkey_manager
        active_keys = hotkey_manager.active_keys if hotkey_manager else set()
        for i, (action_name, desc) in enumerate(self.INSTRUCTIONS):
            hotkey_def = getattr(config, f"HOTKEY_{action_name.upper()}")
            key_str = hotkey_def.to_string()
            lbl_key = Gtk.Label(label=key_str)
            lbl_key.set_halign(Gtk.Align.START)
            lbl_desc = Gtk.Label(label=desc)
            lbl_desc.set_halign(Gtk.Align.START)
            if action_name in active_keys:
                lbl_key.get_style_context().add_class("key-label")
                lbl_desc.get_style_context().add_class("desc-label")
            else:
                lbl_key.get_style_context().add_class("key-label-inactive")
                lbl_desc.get_style_context().add_class("desc-label-inactive")
            self.grid.attach(lbl_key, 0, i, 1, 1)
            self.grid.attach(lbl_desc, 1, i, 1, 1)
        self.grid.show_all()

class SimulatedWindow(Gtk.EventBox):
    """模拟窗口行为的基础组件，提供标题栏拖动、边缘调整大小、最大化和关闭功能"""
    # 逻辑px
    def __init__(self, parent_overlay, title="Window", css_class="simulated-window", resizable=True):
        super().__init__()
        self.parent_overlay = parent_overlay
        self.cursors = parent_overlay.cursors
        self.is_maximized = False
        self.resizable = resizable
        # 窗口坐标
        self.restore_geometry = None
        self.RESIZE_BORDER = 6
        self._resize_edge = None
        self._drag_anchor_mouse = None
        self._drag_anchor_panel_pos = None
        self._resize_start_rect = None
        self._resize_limit_w = 200
        self._resize_limit_h = 300
        self.user_has_moved = False
        self.get_style_context().add_class(css_class)
        self.main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.main_vbox.set_margin_top(self.RESIZE_BORDER)
        self.main_vbox.set_margin_bottom(self.RESIZE_BORDER)
        self.main_vbox.set_margin_start(self.RESIZE_BORDER)
        self.main_vbox.set_margin_end(self.RESIZE_BORDER)
        self.add(self.main_vbox)
        self.header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.header_box.get_style_context().add_class("window-header")
        self.main_vbox.pack_start(self.header_box, False, False, 0)
        self.title_label = Gtk.Label(label=title)
        self.title_label.get_style_context().add_class("window-title")
        self.title_label.set_margin_bottom(0)
        self.header_box.pack_start(self.title_label, True, True, 0)
        self.maximize_btn = Gtk.Button.new_from_icon_name("window-maximize-symbolic", Gtk.IconSize.MENU)
        self.maximize_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.maximize_btn.set_tooltip_text("最大化")
        self.maximize_btn.set_can_focus(False)
        self.maximize_btn.connect("clicked", self._toggle_maximize)
        if self.resizable:
            self.header_box.pack_start(self.maximize_btn, False, False, 0)
        else:
            self.maximize_btn.set_no_show_all(True)
            self.maximize_btn.hide()
        self.close_btn = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
        self.close_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.close_btn.set_can_focus(False)
        self.close_btn.connect("clicked", self.on_close_clicked)
        self.header_box.pack_end(self.close_btn, False, False, 0)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect("button-press-event", self._on_panel_press)
        self.connect("button-release-event", self._on_panel_release)
        self.connect("motion-notify-event", self._on_panel_motion)
        self.connect("realize", lambda w: w.get_window().set_cursor(self.cursors['default']))

    def add_content(self, widget, expand=True, fill=True, padding=0):
        self.main_vbox.pack_start(widget, expand, fill, padding)

    def hide(self):
        self._resize_edge = None
        self._drag_anchor_mouse = None
        self._drag_anchor_panel_pos = None
        self._resize_start_rect = None
        if self.get_window():
            self.get_window().set_cursor(self.cursors.get('default'))
        super().hide()

    def on_close_clicked(self, btn):
        if self.is_maximized:
            self._restore_panel()
        self.user_has_moved = False
        self.hide()

    def _toggle_maximize(self, btn=None):
        if self.is_maximized:
            self._restore_panel()
        else:
            self._maximize_panel()

    def _maximize_panel(self):
        curr_x, curr_y = self.parent_overlay.overlay_manager.get_widget_position(self)
        alloc = self.get_allocation()
        self.restore_geometry = (curr_x, curr_y, alloc.width, alloc.height)
        valid_screen_w, valid_screen_h = self.parent_overlay.coord_manager.get_valid_screen_size()
        self.set_size_request(valid_screen_w, valid_screen_h)
        self.parent_overlay.overlay_manager.move_widget(self, 0, 0)
        self.is_maximized = True
        image = Gtk.Image.new_from_icon_name("window-restore-symbolic", Gtk.IconSize.MENU)
        self.maximize_btn.set_image(image)
        self.maximize_btn.set_tooltip_text("还原")
        self.parent_overlay.overlay_manager.bring_to_front(self)
        self.parent_overlay.update_input_shape()

    def _restore_panel(self):
        if not self.restore_geometry:
            return
        x, y, w, h = self.restore_geometry
        self.set_size_request(w, h)
        self.parent_overlay.overlay_manager.move_widget(self, x, y)
        self.is_maximized = False
        image = Gtk.Image.new_from_icon_name("window-maximize-symbolic", Gtk.IconSize.MENU)
        self.maximize_btn.set_image(image)
        self.maximize_btn.set_tooltip_text("最大化")
        self.parent_overlay.update_input_shape()

    def _get_panel_edge(self, x, y):
        if not self.resizable:
            return None
        border = self.RESIZE_BORDER
        edges = []
        if y < border:
            edges.append('top')
        elif y > self.get_allocated_height() - border:
            edges.append('bottom')
        if x < border:
            edges.append('left')
        elif x > self.get_allocated_width() - border:
            edges.append('right')
        return '-'.join(edges) if edges else None

    def _is_on_header(self, x, y):
        if not self.header_box.get_visible(): return False
        inner_x = x - self.RESIZE_BORDER
        inner_y = y - self.RESIZE_BORDER
        alloc = self.header_box.get_allocation()
        if 0 <= inner_x <= alloc.width and 0 <= inner_y <= alloc.height:
            return True
        return False

    def _is_over_header_buttons(self, x, y):
        for btn in [self.maximize_btn, self.close_btn]:
            if not btn.get_visible(): continue
            coords = btn.translate_coordinates(self, 0, 0)
            if coords is None: continue
            wx, wy = coords
            alloc = btn.get_allocation()
            if wx <= x < wx + alloc.width and wy <= y < wy + alloc.height:
                return True
        return False

    def _on_panel_press(self, widget, event):
        if event.button == 1:
            target_widget = Gtk.get_event_widget(event)
            current_check = target_widget
            while current_check and current_check != widget:
                if isinstance(current_check, (Gtk.Editable, Gtk.TextView)):
                    return False
                current_check = current_check.get_parent()
            toplevel = self.get_toplevel()
            if toplevel and isinstance(toplevel, Gtk.Window):
                if toplevel.get_focus():
                    toplevel.set_focus(None)
            edge = self._get_panel_edge(event.x, event.y)
            self._drag_anchor_mouse = widget.translate_coordinates(self.parent_overlay, event.x, event.y)
            curr_x, curr_y = self.parent_overlay.overlay_manager.get_widget_position(self)
            if edge:
                self._resize_edge = edge
                alloc = self.get_allocation()
                self._resize_start_rect = (curr_x, curr_y, alloc.width, alloc.height)
                min_req, _ = self.main_vbox.get_preferred_size()
                self._resize_limit_w = min_req.width + 2 * self.RESIZE_BORDER
                self._resize_limit_h = min_req.height + 2 * self.RESIZE_BORDER
                self.user_has_moved = True
            elif self._is_on_header(event.x, event.y):
                self._drag_anchor_panel_pos = (curr_x, curr_y)
                self.get_window().set_cursor(self.cursors.get('grabbing'))
                self.user_has_moved = True
            return True
        return False

    def _on_panel_release(self, widget, event):
        if event.button == 1:
            if self._drag_anchor_panel_pos is not None or self._resize_edge is not None:
                self._resize_edge = None
                self._resize_start_rect = None
                self._drag_anchor_panel_pos = None
                self._drag_anchor_mouse = None
                self.parent_overlay.update_input_shape()
                self._on_panel_motion(widget, event)
            return True
        return False

    def _on_panel_motion(self, widget, event):
        if self._drag_anchor_panel_pos is not None:
            curr_win_x, curr_win_y = widget.translate_coordinates(self.parent_overlay, event.x, event.y)
            if self.is_maximized:
                max_width = self.get_allocated_width()
                mouse_ratio_x = event.x / max_width if max_width > 0 else 0.5
                if self.restore_geometry:
                    _, _, target_restored_w, _ = self.restore_geometry
                self._restore_panel()
                new_panel_x = int(curr_win_x - (target_restored_w * mouse_ratio_x))
                new_panel_y = max(0, int(curr_win_y - 15))
                self._drag_anchor_panel_pos = (new_panel_x, new_panel_y)
                self._drag_anchor_mouse = (curr_win_x, curr_win_y)
                self.parent_overlay.overlay_manager.move_widget(self, new_panel_x, new_panel_y)
                return True
            total_dx = curr_win_x - self._drag_anchor_mouse[0]
            total_dy = curr_win_y - self._drag_anchor_mouse[1]
            new_x = self._drag_anchor_panel_pos[0] + total_dx
            new_y = max(0, self._drag_anchor_panel_pos[1] + total_dy)
            self.parent_overlay.overlay_manager.move_widget(self, new_x, new_y)
            return True
        if self._resize_edge is not None:
            if self.is_maximized:
                self.is_maximized = False
                image = Gtk.Image.new_from_icon_name("window-maximize-symbolic", Gtk.IconSize.MENU)
                self.maximize_btn.set_image(image)
                self.maximize_btn.set_tooltip_text("最大化")
            curr_win_x, curr_win_y = widget.translate_coordinates(self.parent_overlay, event.x, event.y)
            dx = curr_win_x - self._drag_anchor_mouse[0]
            dy = curr_win_y - self._drag_anchor_mouse[1]
            start_x, start_y, start_w, start_h = self._resize_start_rect
            current_new_x, current_new_y = start_x, start_y
            current_new_w, current_new_h = start_w, start_h
            edge = self._resize_edge
            min_w, min_h = self._resize_limit_w, self._resize_limit_h
            if 'right' in edge:
                current_new_w = max(start_w + dx, min_w)
            elif 'left' in edge:
                fixed_right = start_x + start_w
                proposed_width = start_w - dx
                current_new_w = max(proposed_width, min_w)
                current_new_x = fixed_right - current_new_w
            if 'bottom' in edge:
                current_new_h = max(start_h + dy, min_h)
            elif 'top' in edge:
                fixed_bottom = start_y + start_h
                proposed_height = start_h - dy
                current_new_h = max(proposed_height, min_h)
                current_new_y = fixed_bottom - current_new_h
                if current_new_y < 0:
                    current_new_y = 0
                    current_new_h = max(min_h, fixed_bottom)
            self.set_size_request(int(current_new_w), int(current_new_h))
            self.parent_overlay.overlay_manager.move_widget(self, int(current_new_x), int(current_new_y))
            return True
        if event.window != widget.get_window():
            self.get_window().set_cursor(self.cursors.get('default'))
            return False
        edge = self._get_panel_edge(event.x, event.y)
        if edge:
            cursor_name = {
                'top': 'n-resize', 'bottom': 's-resize',
                'left': 'w-resize', 'right': 'e-resize',
                'top-left': 'nw-resize', 'top-right': 'ne-resize',
                'bottom-left': 'sw-resize', 'bottom-right': 'se-resize'
            }.get(edge, 'default')
            self.get_window().set_cursor(self.cursors.get(cursor_name))
        elif self._is_on_header(event.x, event.y) and not self._is_over_header_buttons(event.x, event.y):
            self.get_window().set_cursor(self.cursors.get('grab'))
        else:
            self.get_window().set_cursor(self.cursors.get('default'))
        return False

class CustomColorButton(Gtk.Button):
    __gsignals__ = {
        'color-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
    def __init__(self):
        super().__init__()
        self.rgba = Gdk.RGBA(0, 0, 0, 1)
        self.set_halign(Gtk.Align.FILL)
        self.set_valign(Gtk.Align.FILL)
        self.get_style_context().add_class("no-padding")
        self.get_style_context().add_class(Gtk.STYLE_CLASS_FLAT)
        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_halign(Gtk.Align.FILL)
        self.drawing_area.set_valign(Gtk.Align.FILL)
        self.drawing_area.connect("draw", self._on_draw)
        self.set_size_request(60, 40)
        self.add(self.drawing_area)

    def set_rgba(self, rgba):
        self.rgba = rgba
        self.drawing_area.queue_draw()
        self.emit('color-changed')

    def get_rgba(self):
        return self.rgba

    def _on_draw(self, widget, cr):
        cr.set_source_rgba(self.rgba.red, self.rgba.green, self.rgba.blue, self.rgba.alpha)
        cr.paint()
        cr.set_source_rgb(0.6, 0.6, 0.6)
        cr.set_line_width(1)
        cr.rectangle(0.5, 0.5, widget.get_allocated_width()-1, widget.get_allocated_height()-1)
        cr.stroke()
        return False

class FileChooserPanel(SimulatedWindow):
    def __init__(self, parent_overlay, config_obj, title="选择目录", action=Gtk.FileChooserAction.SELECT_FOLDER):
        super().__init__(parent_overlay, title=title, css_class="simulated-window", resizable=True)
        self.config = config_obj
        self.on_selected_callback = None
        self.chooser = Gtk.FileChooserWidget(action=action)
        self.add_content(self.chooser, expand=True, fill=True)
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btn_box.set_halign(Gtk.Align.END)
        btn_box.set_margin_top(5)
        btn_box.set_margin_bottom(5)
        btn_box.set_margin_end(5)
        btn_cancel = Gtk.Button(label="取消")
        btn_cancel.connect("clicked", lambda w: self.hide())
        btn_ok = Gtk.Button(label="选择")
        btn_ok.get_style_context().add_class("suggested-action")
        btn_ok.connect("clicked", self._on_select_clicked)
        btn_box.pack_start(btn_cancel, False, False, 0)
        btn_box.pack_start(btn_ok, False, False, 0)
        self.add_content(btn_box, expand=False, fill=False)
        self.show_all()
        _, nat_size = self.get_preferred_size()
        self.set_size_request(nat_size.width, self.config.FILE_CHOOSER_HEIGHT)
        self.hide()

    def open(self, callback, initial_path=None):
        self.on_selected_callback = callback
        if initial_path and os.path.isdir(initial_path):
            self.chooser.set_current_folder(initial_path)

    def _on_select_clicked(self, widget):
        filename = self.chooser.get_filename()
        if filename and self.on_selected_callback:
            self.on_selected_callback(filename)
        self.hide()

class ColorChooserPanel(SimulatedWindow):
    def __init__(self, parent_overlay):
        super().__init__(parent_overlay, title="选择颜色", css_class="simulated-window", resizable=False)
        self.target_button = None
        self.chooser_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add_content(self.chooser_container, expand=True, fill=True)
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btn_box.set_halign(Gtk.Align.END)
        btn_box.set_margin_top(5)
        btn_box.set_margin_bottom(5)
        btn_box.set_margin_end(5)
        btn_cancel = Gtk.Button(label="取消")
        btn_cancel.connect("clicked", lambda w: self.hide())
        btn_ok = Gtk.Button(label="确定")
        btn_ok.get_style_context().add_class("suggested-action")
        btn_ok.connect("clicked", self._on_select_clicked)
        btn_box.pack_start(btn_cancel, False, False, 0)
        btn_box.pack_start(btn_ok, False, False, 0)
        self.add_content(btn_box, expand=False, fill=False)
        self.show_all()
        _, nat_size = self.get_preferred_size()
        self.set_size_request(nat_size.width, nat_size.height)
        self.hide()

    def open_for(self, custom_color_btn):
        self.target_button = custom_color_btn
        for child in self.chooser_container.get_children():
            child.destroy()
        self.chooser = Gtk.ColorChooserWidget()
        self.chooser.set_use_alpha(True)
        self.chooser.set_rgba(custom_color_btn.get_rgba())
        self.chooser_container.pack_start(self.chooser, True, True, 0)
        self.chooser.show()

    def _on_select_clicked(self, widget):
        rgba = self.chooser.get_rgba()
        if self.target_button:
            self.target_button.set_rgba(rgba)
        _, req = self.get_preferred_size()
        panel_w, panel_h = req.width, req.height
        self.hide()

class ConfigPanel(SimulatedWindow):
    """配置面板，提供部分设置项的图形化编辑界面"""
    # 逻辑px
    def __init__(self, config_obj, parent_overlay, log_queue):
        super().__init__(parent_overlay, title="拼长图配置", css_class="simulated-window", resizable=True)
        self.config = config_obj
        self.show_advanced = False
        self._is_batch_restoring = False
        self.widget_map = {}
        self.advanced_containers = []
        self.managed_settings = [
            ('Output', 'save_directory'), ('Output', 'save_format'), ('Output', 'jpeg_quality'),
            ('Output', 'filename_template'), ('Output', 'filename_timestamp_format'),
            ('Interface.Components', 'enable_buttons'), ('Interface.Components', 'enable_side_panel'),
            ('Interface.Components', 'show_preview_on_start'), ('Interface.Components', 'show_instruction_panel_on_start'),
            ('Interface.Components', 'enable_auto_scroll_buttons'), ('Interface.Components', 'enable_grid_action_buttons'),
            ('Interface.Components', 'show_capture_count'), ('Interface.Components', 'show_total_dimensions'), ('Interface.Components', 'show_current_mode'),
            ('Interface.Layout', 'border_width'), ('Interface.Layout', 'button_panel_width'), ('Interface.Layout', 'side_panel_width'),
            ('Behavior', 'copy_to_clipboard_on_finish'), ('Behavior', 'capture_with_cursor'), ('Behavior', 'enable_free_scroll_matching'),
            ('Behavior', 'auto_scroll_ticks_per_step'), ('Behavior', 'auto_scroll_interval_ms'),
            ('Behavior', 'scroll_method'), ('Behavior', 'reuse_invisible_cursor'),
            ('Behavior', 'grid_scroll_interval_ms'), ('Behavior', 'forward_action'), ('Behavior', 'backward_action'), ('Behavior', 'grid_scroll_ticks_formula'),
            ('Interface.Theme', 'border_color'), ('Interface.Theme', 'static_bar_color'),
            ('Interface.Theme', 'info_panel_css'), ('Interface.Theme', 'button_css'), ('Interface.Theme', 'instruction_panel_css'),
            ('Interface.Theme', 'simulated_window_css'), ('Interface.Theme', 'preview_panel_css'), ('Interface.Theme', 'config_panel_css'),
            ('Interface.Theme', 'notification_css'),
            ('Interface.Theme', 'mask_css'), ('Interface.Theme', 'dialog_css'), ('Interface.Theme', 'feedback_widget_css'),
            ('System', 'max_viewer_dimension'), ('System', 'large_image_opener'),
            ('Performance', 'max_scroll_per_tick'), ('Performance', 'min_scroll_per_tick'), ('Preview', 'preview_cache_size'),
            ('System', 'sound_theme'), ('System', 'capture_sound'), ('System', 'undo_sound'), ('System', 'finalize_sound'), ('System', 'warning_sound'),
            ('System', 'log_file')
        ]
        self.SOUND_KEYS = ['capture_sound', 'undo_sound', 'finalize_sound', 'warning_sound']
        self.sound_themes = SystemInteraction.get_sound_themes()
        self.capturing_hotkey_button = None
        self.log_queue = log_queue
        self.log_text_buffer = None
        self.all_log_records = []
        self.log_tags = {}
        self.filter_checkboxes = {}
        self.log_formatter = logging.Formatter('%(asctime)s - %(levelname)-7s - %(message)s')
        self._sub_panels_state = {}
        self._setup_ui()
        self.create_log_tags()
        self._load_config_values()
        self.file_chooser_panel = FileChooserPanel(parent_overlay, self.config, title="选择目录", action=Gtk.FileChooserAction.SELECT_FOLDER)
        self.color_chooser_panel = ColorChooserPanel(parent_overlay)
        self.show_all()
        _, nat_size = self.get_preferred_size()
        self.set_size_request(nat_size.width, self.config.CONFIG_PANEL_HEIGHT)
        self.hide()
        self._bind_all_widgets()
        self.config.connect('setting-changed', self._on_config_changed_signal)
        self._update_advanced_visibility()
        GLib.timeout_add(150, self._check_log_queue)

    def hide(self):
        if hasattr(self, 'file_chooser_panel') and self.file_chooser_panel:
            self._sub_panels_state['file_chooser'] = self.file_chooser_panel.get_visible()
            self.file_chooser_panel.hide()
        if hasattr(self, 'color_chooser_panel') and self.color_chooser_panel:
            self._sub_panels_state['color_chooser'] = self.color_chooser_panel.get_visible()
            self.color_chooser_panel.hide()
        super().hide()

    def show_all(self):
        super().show_all()
        self._update_advanced_visibility()
        if self._sub_panels_state.get('file_chooser', False):
            if hasattr(self, 'file_chooser_panel') and self.file_chooser_panel:
                self.parent_overlay.overlay_manager.show(self.file_chooser_panel, anchor=None, layer=OverlayManager.LAYER_MEDIUM_UP)
        if self._sub_panels_state.get('color_chooser', False):
            if hasattr(self, 'color_chooser_panel') and self.color_chooser_panel:
                self.parent_overlay.overlay_manager.show(self.color_chooser_panel, anchor=None, layer=OverlayManager.LAYER_MEDIUM_UP)

    def on_close_clicked(self, btn):
        self._cancel_hotkey_capture()
        super().on_close_clicked(btn)
        self._sub_panels_state = {}

    def _check_log_queue(self):
        while not self.log_queue.empty():
            try:
                record = self.log_queue.get_nowait()
                self.all_log_records.append(record)
                cb = self.filter_checkboxes.get(record.levelname)
                if cb and cb.get_active():
                    self._insert_record_into_buffer(record)
            except queue.Empty:
                break
        return True

    def _insert_record_into_buffer(self, record):
        if not self.log_text_buffer: return
        message = self.log_formatter.format(record)
        tag = self.log_tags.get(record.levelname)
        end_iter = self.log_text_buffer.get_end_iter()
        if tag:
            self.log_text_buffer.insert_with_tags(end_iter, message + '\n', tag)
        else:
            self.log_text_buffer.insert(end_iter, message + '\n')
        if self.log_autoscroll_checkbutton.get_active() and not self.log_textview.is_focus():
            def do_scroll():
                if self.log_text_buffer:
                    self.log_textview.scroll_to_iter(self.log_text_buffer.get_end_iter(), 0.0, True, 0.0, 1.0)
                return False
            GLib.idle_add(do_scroll)

    def redisplay_logs(self):
        if not self.log_text_buffer: return
        self.log_text_buffer.set_text("")
        active_levels = {level for level, cb in self.filter_checkboxes.items() if cb.get_active()}
        for record in self.all_log_records:
            if record.levelname in active_levels:
                self._insert_record_into_buffer(record)

    def _get_theme_color(self, color_name):
        context = self.get_style_context()
        success, color = context.lookup_color(color_name)
        if success:
            return color.to_string()
        return self.config.get_default_css_color('config_panel', color_name, "#000000")

    def create_log_tags(self):
        """为不同的日志级别创建并配置 TextTag"""
        if not self.log_text_buffer:
            return
        tag_table = self.log_text_buffer.get_tag_table()
        def setup_tag(level_name, color_key):
            tag_name = level_name.lower()
            tag = tag_table.lookup(tag_name)
            if not tag:
                tag = Gtk.TextTag(name=tag_name)
                tag_table.add(tag)
            color_val = self._get_theme_color(color_key)
            tag.set_property("foreground", color_val)
            self.log_tags[level_name] = tag
        setup_tag('DEBUG', 'log_debug')
        setup_tag('INFO', 'log_info')
        setup_tag('WARNING', 'log_warning')
        setup_tag('ERROR', 'log_error')

    def _on_copy_log_clicked(self, button):
        if not self.all_log_records:
            return
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        full_log_text = "\n".join([self.log_formatter.format(r) for r in self.all_log_records])
        clipboard.set_text(full_log_text, -1)
        original_label = button.get_label()
        button.set_sensitive(False)
        button.set_label("日志已复制")
        def restore_button_state():
            button.set_label(original_label)
            button.set_sensitive(True)
            return False
        GLib.timeout_add(1500, restore_button_state)

    def _on_hotkey_button_clicked(self, button):
        if self.capturing_hotkey_button == button:
            self._cancel_hotkey_capture()
            return
        if self.capturing_hotkey_button:
            self._cancel_hotkey_capture()
        button.original_label = button.get_label()
        self.capturing_hotkey_button = button
        button.set_label("取消")
        self.status_label.set_text("请按下快捷键...")
        global hotkey_manager
        if hotkey_manager:
            hotkey_manager.set_paused(True)
            logging.debug("开始捕获快捷键，全局热键暂停")
        self.parent_overlay.overlay_manager.set_blocking(self, True)

    def on_key_press(self, widget, event):
        if self.capturing_hotkey_button:
            return True
        return False

    def _cancel_hotkey_capture(self):
        if self.capturing_hotkey_button:
            logging.debug("取消录制快捷键")
            self.capturing_hotkey_button.set_label(self.capturing_hotkey_button.original_label)
            self.capturing_hotkey_button = None
            self.status_label.set_text("快捷键录制已取消")
            self.parent_overlay.on_global_focus_changed(self.parent_overlay, self.parent_overlay.get_focus())
            self.parent_overlay.overlay_manager.set_blocking(self, False)

    def handle_key_release(self, widget, event):
        if not self.capturing_hotkey_button:
            return False
        incoming_hotkey = HotkeyDefinition.from_gdk_event(event)
        hotkey_str = incoming_hotkey.to_string()
        current_key = self.capturing_hotkey_button.get_name()
        original_label = self.capturing_hotkey_button.original_label
        global hotkey_manager
        if not incoming_hotkey.is_valid():
            error_msg = "组合无效"
            if incoming_hotkey.main_key is None:
                if incoming_hotkey.modifiers != HotkeyModifiers.NONE:
                    error_msg = f"{hotkey_str} 缺少主键"
                else:
                    error_msg = f"无法识别按键（keyval: {event.keyval}）"
            else:
                error_msg = f"暂不支持按键: {incoming_hotkey.main_key}"
            logging.warning(f"热键录制失败，{error_msg}")
            send_notification("设置失败", f"{error_msg}\n请尝试使用其他按键组合", "warning", config.WARNING_SOUND, 2)
            self._cancel_hotkey_capture()
            return True
        conflicting_keys = hotkey_manager.get_hotkey_conflicts(current_key, incoming_hotkey) if hotkey_manager else []
        if conflicting_keys:
            conflict_descs = [next((c[1] for c in self.hotkey_configs if c[0] == k), k) for k in conflicting_keys]
            conflicting_key_desc = ", ".join(conflict_descs)
            message = f"快捷键 '{hotkey_str}' 已被分配给 '{conflicting_key_desc}'\n请设置一个不同的快捷键"
            send_notification("快捷键冲突", message, "warning", config.WARNING_SOUND, 2)
            self.status_label.set_text("")
            self.capturing_hotkey_button.set_label(original_label)
        else:
            self.config.set_value('Hotkeys', current_key, hotkey_str)
            self.status_label.set_text(f"快捷键已设置为: {hotkey_str}")
            self.capturing_hotkey_button.set_label(hotkey_str)
        self.capturing_hotkey_button = None
        self.parent_overlay.on_global_focus_changed(self.parent_overlay, self.parent_overlay.get_focus())
        self.parent_overlay.overlay_manager.set_blocking(self, False)
        return True

    def _setup_ui(self):
        """设置主界面布局"""
        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_content(main_vbox)
        main_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        main_vbox.pack_start(main_hbox, True, True, 0)
        # 左侧边栏
        sidebar_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        sidebar_container.get_style_context().add_class("config-sidebar")
        main_hbox.pack_start(sidebar_container, False, False, 0)
        sidebar_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        sidebar_header.get_style_context().add_class("config-section")
        icon = Gtk.Image.new_from_icon_name("preferences-system", Gtk.IconSize.MENU)
        title_label = Gtk.Label(label="配置选项")
        sidebar_header.pack_start(icon, False, False, 0)
        sidebar_header.pack_start(title_label, False, False, 0)
        sidebar_container.pack_start(sidebar_header, False, False, 0)
        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sidebar_container.pack_start(separator, False, False, 0)
        self.sidebar = Gtk.StackSidebar()
        sidebar_container.pack_start(self.sidebar, True, True, 0)
        # 右侧堆栈容器
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_transition_duration(200)
        self.stack.connect("notify::visible-child", lambda w, p: self._cancel_hotkey_capture())
        self.sidebar.set_stack(self.stack)
        main_hbox.pack_start(self.stack, True, True, 0)
        # 创建各个配置页面
        self._create_output_page()
        self._create_hotkeys_page()
        self._create_components_layout_page()
        self._create_behavior_page()
        self._create_theme_appearance_page()
        self._create_system_performance_page()
        self._create_grid_calibration_page()
        self._create_log_viewer_page()
        # 底部全局操作区
        self._create_bottom_panel(main_vbox)

    def switch_to_page(self, page_name):
        self.stack.set_visible_child_name(page_name)

    def set_advanced_mode(self, enabled):
        if self.advanced_switch.get_active() != enabled:
            self.advanced_switch.set_active(enabled)

    def _create_log_viewer_page(self):
        page_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        page_vbox.get_style_context().add_class("config-container")
        # 顶部工具栏
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        page_vbox.pack_start(toolbar, False, False, 0)
        copy_button = Gtk.Button(label="复制全部日志")
        copy_button.connect("clicked", self._on_copy_log_clicked)
        toolbar.pack_start(copy_button, False, False, 0)
        filter_label = Gtk.Label(label="过滤:")
        toolbar.pack_start(filter_label, False, False, 10)
        log_levels_config = [
            ("DEBUG", False),
            ("INFO", True),
            ("WARNING", True),
            ("ERROR", True)
        ]
        for level, default_active in log_levels_config:
            checkbox = Gtk.CheckButton(label=level)
            checkbox.set_active(default_active)
            checkbox.connect("toggled", lambda w: self.redisplay_logs())
            toolbar.pack_start(checkbox, False, False, 0)
            self.filter_checkboxes[level] = checkbox
        self.log_autoscroll_checkbutton = Gtk.CheckButton(label="自动滚动到底部")
        self.log_autoscroll_checkbutton.set_active(True)
        toolbar.pack_start(self.log_autoscroll_checkbutton, False, False, 10)
        # 日志显示区域
        scrolled_window = Gtk.ScrolledWindow()
        self.log_scrolled_window = scrolled_window
        scrolled_window.set_hexpand(True)
        scrolled_window.set_vexpand(True)
        scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled_window.get_style_context().add_class("config-section")
        page_vbox.pack_start(scrolled_window, True, True, 0)
        self.log_textview = Gtk.TextView()
        self.log_textview.set_editable(False)
        self.log_textview.set_cursor_visible(False)
        self.log_textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.log_textview.get_style_context().add_class("log-view")
        self.log_text_buffer = self.log_textview.get_buffer()
        scrolled_window.add(self.log_textview)
        self.stack.add_titled(page_vbox, "log", "日志查看")

    def _create_bottom_panel(self, parent):
        bottom_frame = Gtk.Frame()
        bottom_frame.set_shadow_type(Gtk.ShadowType.IN)
        parent.pack_start(bottom_frame, False, False, 0)
        bottom_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        bottom_hbox.get_style_context().add_class("config-section")
        bottom_frame.add(bottom_hbox)
        # 高级设置开关
        advanced_label = Gtk.Label(label="显示高级设置")
        self.advanced_switch = Gtk.Switch()
        self.advanced_switch.connect("notify::active", self._on_advanced_toggle)
        bottom_hbox.pack_start(advanced_label, False, False, 0)
        bottom_hbox.pack_start(self.advanced_switch, False, False, 0)
        self.status_label = Gtk.Label(label="配置面板已就绪")
        self.status_label.set_halign(Gtk.Align.END)
        bottom_hbox.pack_end(self.status_label, False, False, 0)

    def _on_browse_button_clicked(self, widget):
        def on_dir_selected(path_str):
            if path_str:
                self.config.SAVE_DIRECTORY = Path(path_str)
                self.config.set_value('Output', 'save_directory', path_str)
                self.save_dir_entry.set_text(path_str)
        current_text = self.save_dir_entry.get_text()
        initial_path = current_text if current_text else None
        self.file_chooser_panel.open(callback=on_dir_selected, initial_path=initial_path)
        self.parent_overlay.overlay_manager.show(self.file_chooser_panel, anchor='center', layer=OverlayManager.LAYER_MEDIUM_UP)

    def _show_embedded_color_chooser(self, target_button):
        self.color_chooser_panel.open_for(target_button)
        self.parent_overlay.overlay_manager.show(self.color_chooser_panel, anchor='mouse', layer=OverlayManager.LAYER_MEDIUM_UP)

    def _update_filename_preview(self, widget=None):
        template = self.filename_entry.get_text()
        ts_format = self.timestamp_entry.get_text()
        file_format = self.format_combo.get_active_id()
        if not all([template, ts_format, file_format]):
            self.filename_preview_label.set_text("")
            return
        try:
            now = datetime.now()
            timestamp_str = now.strftime(ts_format)
            self.filename_preview_label.get_style_context().remove_class("error")
        except ValueError:
            error_msg = "<i>无效的时间戳格式</i>"
            self.filename_preview_label.set_markup(f"<span foreground='red'>{error_msg}</span>")
            self.filename_preview_label.get_style_context().add_class("error")
            return
        filename_base = template.replace('{timestamp}', timestamp_str)
        extension = 'jpg' if file_format == 'JPEG' else 'png'
        final_filename = f"{filename_base}.{extension}"
        escaped_filename = GLib.markup_escape_text(final_filename)
        self.filename_preview_label.set_markup(f"<i>{escaped_filename}</i>")

    def _add_restore_button(self, container, settings_list):
        restore_button = Gtk.Button(label="恢复本页默认设置")
        restore_button.set_halign(Gtk.Align.END)
        restore_button.set_margin_top(10)
        restore_button.connect("clicked", self._on_restore_defaults_clicked, settings_list)
        container.pack_end(restore_button, False, False, 0)

    def _create_output_page(self):
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        vbox.get_style_context().add_class("config-container")
        scrolled.add(vbox)
        output_page_settings = [
            ('Output', 'save_directory'), ('Output', 'save_format'), ('Output', 'jpeg_quality'),
            ('Output', 'filename_template'), ('Output', 'filename_timestamp_format')
        ]
        self._add_restore_button(vbox, output_page_settings)
        frame1 = Gtk.Frame(label="文件输出")
        vbox.pack_start(frame1, False, False, 0)
        grid1 = Gtk.Grid()
        grid1.get_style_context().add_class("config-section")
        grid1.set_row_spacing(15)
        grid1.set_column_spacing(15)
        frame1.add(grid1)
        # 保存目录
        label = Gtk.Label(label="保存目录:", xalign=0)
        label.set_tooltip_markup("指定拼接后图片的保存目录")
        grid1.attach(label, 0, 0, 1, 1)
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.save_dir_entry = Gtk.Entry()
        self.save_dir_entry.set_editable(False)
        self.save_dir_entry.set_placeholder_text("目录未设置 (请点击浏览按钮选择)")
        self.save_dir_entry.set_hexpand(True)
        self.save_dir_entry.set_can_focus(False)
        self.save_dir_entry.connect("notify::text", lambda w, p: w.set_can_focus(bool(w.get_text())))
        self.save_dir_entry.connect("button-press-event", lambda w, e: not bool(w.get_text()))
        self.widget_map['save_directory'] = self.save_dir_entry
        hbox.pack_start(self.save_dir_entry, True, True, 0)
        browse_button = Gtk.Button(label="浏览...")
        browse_button.connect("clicked", self._on_browse_button_clicked)
        hbox.pack_start(browse_button, False, False, 0)
        grid1.attach(hbox, 1, 0, 1, 1)
        # 文件格式
        label = Gtk.Label(label="文件类型:", xalign=0)
        label.set_tooltip_markup("选择图片的保存格式\n<b>PNG</b>: 无损压缩\n<b>JPEG</b>: 有损压缩，具有 65500 像素的尺寸上限")
        self.format_combo = Gtk.ComboBoxText()
        self.format_combo.set_tooltip_markup(label.get_tooltip_markup())
        self.format_combo.connect("scroll-event", lambda widget, event: True)
        self.format_combo.append("PNG", "PNG")
        self.format_combo.append("JPEG", "JPEG")
        self.format_combo.connect("changed", self._on_format_changed)
        self.widget_map['save_format'] = self.format_combo
        cell = self.format_combo.get_cells()[0]
        cell.set_property('xalign', 0.5)
        self.format_combo.set_halign(Gtk.Align.START)
        grid1.attach(label, 0, 1, 1, 1)
        grid1.attach(self.format_combo, 1, 1, 1, 1)
        # JPEG质量
        self.jpeg_label = Gtk.Label(label="JPEG 质量:", xalign=0)
        self.jpeg_label.set_tooltip_markup("设置 JPEG 图片的压缩质量，范围 1-100")
        self.jpeg_quality_spin = Gtk.SpinButton()
        self.jpeg_quality_spin.set_tooltip_markup(self.jpeg_label.get_tooltip_markup())
        self.jpeg_quality_spin.connect("scroll-event", lambda widget, event: True)
        self.jpeg_quality_spin.set_halign(Gtk.Align.START)
        self.jpeg_quality_spin.set_range(1, 100)
        self.jpeg_quality_spin.set_increments(1, 10)
        self.widget_map['jpeg_quality'] = self.jpeg_quality_spin
        grid1.attach(self.jpeg_label, 0, 2, 1, 1)
        grid1.attach(self.jpeg_quality_spin, 1, 2, 1, 1)
        self.output_advanced_frame = Gtk.Frame(label="高级设置")
        vbox.pack_start(self.output_advanced_frame, False, False, 0)
        self.advanced_containers.append(self.output_advanced_frame)
        grid2 = Gtk.Grid()
        grid2.get_style_context().add_class("config-section")
        grid2.set_row_spacing(15)
        grid2.set_column_spacing(15)
        self.output_advanced_frame.add(grid2)
        # 文件名格式
        label = Gtk.Label(label="文件名格式:", xalign=0)
        label.set_tooltip_markup("定义保存文件的名称模板\n变量 <b>{timestamp}</b> 会被替换为下方格式定义的时间戳")
        self.filename_entry = Gtk.Entry()
        self.filename_entry.set_tooltip_markup(label.get_tooltip_markup())
        self.widget_map['filename_template'] = self.filename_entry
        grid2.attach(label, 0, 0, 1, 1)
        grid2.attach(self.filename_entry, 1, 0, 1, 1)
        # 时间戳格式
        label = Gtk.Label(label="时间戳格式:", xalign=0)
        label.set_tooltip_markup("用于生成文件名的 Python strftime 格式字符串\n常用占位符: <b>%Y</b>(年) <b>%m</b>(月) <b>%d</b>(日) <b>%H</b>(时) <b>%M</b>(分) <b>%S</b>(秒)")
        self.timestamp_entry = Gtk.Entry()
        self.timestamp_entry.set_tooltip_markup(label.get_tooltip_markup())
        self.widget_map['filename_timestamp_format'] = self.timestamp_entry
        grid2.attach(label, 0, 1, 1, 1)
        grid2.attach(self.timestamp_entry, 1, 1, 1, 1)
        preview_title_label = Gtk.Label(label="文件名预览:", xalign=0)
        self.filename_preview_label = Gtk.Label(xalign=0)
        self.filename_preview_label.set_selectable(True)
        grid2.attach(preview_title_label, 0, 2, 1, 1)
        grid2.attach(self.filename_preview_label, 1, 2, 1, 1)
        self.stack.add_titled(scrolled, "output", "输出设置")

    def _create_hotkeys_page(self):
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        vbox.get_style_context().add_class("config-container")
        scrolled.add(vbox)
        info_label = Gtk.Label(label="点击配置项右侧的按钮，然后按下想设置的快捷键组合")
        info_label.set_line_wrap(True)
        info_label.set_xalign(0)
        vbox.pack_start(info_label, False, False, 0)
        frame = Gtk.Frame(label="快捷键设置")
        vbox.pack_start(frame, False, False, 0)
        grid = Gtk.Grid()
        grid.get_style_context().add_class("config-section")
        grid.set_row_spacing(10)
        grid.set_column_spacing(15)
        frame.add(grid)
        self.hotkey_configs = [
            ("capture", "截图"), ("finalize", "完成"),
            ("undo", "撤销"), ("cancel", "取消"),
            ("auto_scroll_start", "开始自动滚动"), ("auto_scroll_stop", "停止自动滚动"),
            ("grid_forward", "整格前进"), ("grid_backward", "整格后退"),
            ("configure_scroll_unit", "配置滚动单位"), ("toggle_grid_mode", "切换整格模式"),
            ("toggle_config_panel", "显示/隐藏配置面板"), ("toggle_preview", "显示/隐藏预览面板"),
            ("toggle_hotkeys_enabled", "启用/禁用快捷键"), ("toggle_instruction_panel", "显示/隐藏提示面板"),
            ("preview_zoom_in", "放大预览图"), ("preview_zoom_out", "缩小预览图"),
            ("dialog_confirm", "对话框确认"), ("dialog_cancel", "对话框取消")
        ]
        self.managed_settings.extend([('Hotkeys', key) for key, _ in self.hotkey_configs])
        self.hotkey_buttons = {}
        num_items = len(self.hotkey_configs)
        mid_point = (num_items + 1) // 2
        for i, (key, desc) in enumerate(self.hotkey_configs):
            row = i % mid_point
            col = (i // mid_point) * 2
            label = Gtk.Label(label=f"{desc}:", xalign=0)
            button = Gtk.Button()
            button.set_name(key)
            button.connect("clicked", self._on_hotkey_button_clicked)
            grid.attach(label, col, row, 1, 1)
            grid.attach(button, col + 1, row, 1, 1)
            self.hotkey_buttons[key] = button
            self.widget_map[key] = button
        hotkeys_page_settings = [('Hotkeys', key) for key, _ in self.hotkey_configs]
        self._add_restore_button(vbox, hotkeys_page_settings)
        self.stack.add_titled(scrolled, "hotkeys", "热键")

    def _create_components_layout_page(self):
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        vbox.get_style_context().add_class("config-container")
        scrolled.add(vbox)
        components_layout_settings = [
            ('Interface.Components', 'enable_buttons'), ('Interface.Components', 'enable_side_panel'),
            ('Interface.Components', 'show_instruction_panel_on_start'), ('Interface.Components', 'show_preview_on_start'),
            ('Interface.Components', 'enable_auto_scroll_buttons'), ('Interface.Components', 'enable_grid_action_buttons'),
            ('Interface.Components', 'show_capture_count'), ('Interface.Components', 'show_total_dimensions'), ('Interface.Components', 'show_current_mode'),
            ('Interface.Layout', 'border_width'), ('Interface.Layout', 'button_panel_width'), ('Interface.Layout', 'side_panel_width'),
        ]
        self._add_restore_button(vbox, components_layout_settings)
        core_comp_frame = Gtk.Frame(label="核心组件")
        vbox.pack_start(core_comp_frame, False, False, 0)
        core_comp_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        core_comp_vbox.get_style_context().add_class("config-section")
        core_comp_frame.add(core_comp_vbox)
        basic_component_configs = [
            ("enable_buttons", "启用按钮面板", "是否在截图区域右侧显示按钮面板"),
            ("enable_side_panel", "启用侧边栏", "是否在截图区域旁显示包括信息面板和功能面板的侧边栏"),
            ("show_preview_on_start", "启动时显示预览面板", "是否在截图会话开始时自动打开预览面板"),
            ("show_instruction_panel_on_start", "启动时显示提示面板", "每次启动截图会话时，是否在左下角显示一个包含快捷键的提示面板")
        ]
        for key, desc, tooltip in basic_component_configs:
            checkbox = Gtk.CheckButton(label=desc)
            checkbox.set_tooltip_markup(tooltip)
            core_comp_vbox.pack_start(checkbox, False, False, 0)
            self.widget_map[key] = checkbox
        sub_comp_frame = Gtk.Frame(label="组件微调")
        vbox.pack_start(sub_comp_frame, False, False, 0)
        self.advanced_containers.append(sub_comp_frame)
        sub_comp_grid = Gtk.Grid()
        sub_comp_grid.get_style_context().add_class("config-section")
        sub_comp_grid.set_row_spacing(10)
        sub_comp_grid.set_column_spacing(15)
        sub_comp_frame.add(sub_comp_grid)
        sub_component_configs = [
            ("enable_auto_scroll_buttons", "启用开始/停止按钮", "控制在<b>自由/自动模式</b>下是否显示“开始”和“停止”按钮"),
            ("enable_grid_action_buttons", "启用前进/后退按钮", "控制在<b>整格模式</b>下是否显示“前进”和“后退”按钮"),
            ("show_capture_count", "显示已截图数量", "是否在侧边栏信息面板中显示当前已截取的图片数量"),
            ("show_total_dimensions", "显示最终图片总尺寸", "是否在侧边栏信息面板中显示拼接后图片的宽度和总高度"),
            ("show_current_mode", "显示当前模式", "是否在侧边栏信息面板中显示当前所处的模式（自由/整格/自动）"),
        ]
        for i, (key, desc, tooltip) in enumerate(sub_component_configs):
            checkbox = Gtk.CheckButton(label=desc)
            checkbox.set_tooltip_markup(tooltip)
            sub_comp_grid.attach(checkbox, i % 2, i // 2, 1, 1)
            self.widget_map[key] = checkbox
        layout_frame = Gtk.Frame(label="布局显示")
        vbox.pack_start(layout_frame, False, False, 0)
        self.advanced_containers.append(layout_frame)
        layout_grid = Gtk.Grid()
        layout_grid.get_style_context().add_class("config-section")
        layout_grid.set_row_spacing(15)
        layout_grid.set_column_spacing(15)
        layout_frame.add(layout_grid)
        layout_configs = [
            ("border_width", "边框宽度", (1, 20), (1, 5), "截图区域边框的宽度，单位：逻辑px"),
            ("button_panel_width", "按钮面板宽度", (80, 200), (5, 20), "右侧按钮面板的宽度，单位：逻辑px"),
            ("side_panel_width", "侧边栏宽度", (120, 200), (5, 20), "功能面板和信息面板的宽度，单位：逻辑px")
        ]
        for i, (key, desc, (min_val, max_val), (step, page), tooltip) in enumerate(layout_configs):
            label = Gtk.Label(label=f"{desc}:", xalign=0)
            label.set_tooltip_markup(tooltip)
            spin = Gtk.SpinButton()
            spin.set_tooltip_markup(tooltip)
            spin.connect("scroll-event", lambda widget, event: True)
            spin.set_range(min_val, max_val)
            spin.set_increments(step, page)
            spin.set_halign(Gtk.Align.START)
            layout_grid.attach(label, 0, i, 1, 1)
            layout_grid.attach(spin, 1, i, 1, 1)
            self.widget_map[key] = spin
        self.stack.add_titled(scrolled, "components_layout", "组件布局")

    def _create_behavior_page(self):
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        vbox.get_style_context().add_class("config-container")
        scrolled.add(vbox)
        behavior_page_settings = [
            ('Behavior', 'copy_to_clipboard_on_finish'), ('Behavior', 'capture_with_cursor'), ('Behavior', 'enable_free_scroll_matching'),
            ('Behavior', 'auto_scroll_ticks_per_step'), ('Behavior', 'auto_scroll_interval_ms'),
            ('Behavior', 'scroll_method'), ('Behavior', 'reuse_invisible_cursor'),
            ('Behavior', 'grid_scroll_interval_ms'), ('Behavior', 'forward_action'), ('Behavior', 'backward_action'), ('Behavior', 'grid_scroll_ticks_formula'),
        ]
        self._add_restore_button(vbox, behavior_page_settings)
        frame1 = Gtk.Frame(label="常用交互")
        vbox.pack_start(frame1, False, False, 0)
        vbox1 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox1.get_style_context().add_class("config-section")
        frame1.add(vbox1)
        common_configs = [
            ('copy_to_clipboard_on_finish', '完成后复制到剪贴板', "拼接完成后，是否自动将最终生成的图片复制到系统剪贴板\n若图片过大则只会复制路径"),
            ('capture_with_cursor', '截取鼠标指针', "自由模式下截图时是否将鼠标指针也一并截取下来"),
            ('enable_free_scroll_matching', '自由模式启用误差修正', "在<b>自由模式</b>下，使用模板匹配来修正误差\n启用后，请确保每次滚动有重叠部分，否则无法修正")
        ]
        for key, desc, tooltip in common_configs:
            cb = Gtk.CheckButton(label=desc)
            cb.set_tooltip_markup(tooltip)
            vbox1.pack_start(cb, False, False, 0)
            self.widget_map[key] = cb
        frame2 = Gtk.Frame(label="自动模式")
        vbox.pack_start(frame2, False, False, 0)
        grid2 = Gtk.Grid()
        grid2.get_style_context().add_class("config-section")
        grid2.set_row_spacing(15)
        grid2.set_column_spacing(15)
        frame2.add(grid2)
        auto_configs = [
            ("auto_scroll_ticks_per_step", "滚动步长 (格)", (1, 8), (1, 2), "自动模式下，每一步滚动几格"),
            ("auto_scroll_interval_ms", "滚动间隔 (ms)", (50, 800), (50, 100), "自动模式下，每次滚动完后等待截图的间隔时间")
        ]
        for i, (key, desc, (min_val, max_val), (step, page), tooltip) in enumerate(auto_configs):
            label = Gtk.Label(label=f"{desc}:", xalign=0)
            label.set_tooltip_markup(tooltip)
            spin = Gtk.SpinButton()
            spin.set_range(min_val, max_val)
            spin.set_increments(step, page)
            spin.set_tooltip_markup(tooltip)
            spin.connect("scroll-event", lambda widget, event: True)
            grid2.attach(label, 0, i, 1, 1)
            grid2.attach(spin, 1, i, 1, 1)
            self.widget_map[key] = spin
        frame3 = Gtk.Frame(label="隐形光标")
        vbox.pack_start(frame3, False, False, 0)
        self.advanced_containers.append(frame3)
        grid3 = Gtk.Grid()
        grid3.get_style_context().add_class("config-section")
        grid3.set_row_spacing(15)
        grid3.set_column_spacing(15)
        frame3.add(grid3)
        label = Gtk.Label(label="滚动方式:", xalign=0)
        label.set_tooltip_markup("<b>移动用户光标</b>: 将用户鼠标移动到截图区域中心来滚动，兼容性好但有干扰\n<b>使用隐形光标</b>: 创建另一个光标来滚动，无干扰但退出时可能导致界面卡顿")
        method_combo = Gtk.ComboBoxText()
        method_combo.append("move_user_cursor", "移动用户光标")
        method_combo.append("invisible_cursor", "使用隐形光标（实验性）")
        method_combo.connect("scroll-event", lambda widget, event: True)
        self.widget_map['scroll_method'] = method_combo
        reuse_cb = Gtk.CheckButton(label="复用隐形光标")
        reuse_cb.set_tooltip_markup("若使用隐形光标且选择复用，则程序退出后不会删除创建的光标，下次启动时会尝试复用")
        self.widget_map['reuse_invisible_cursor'] = reuse_cb
        if IS_WAYLAND:
            label.set_sensitive(False)
            method_combo.set_sensitive(False)
            reuse_cb.set_sensitive(False)
        grid3.attach(label, 0, 0, 1, 1)
        grid3.attach(method_combo, 1, 0, 1, 1)
        grid3.attach(reuse_cb, 2, 0, 1, 1)
        frame4 = Gtk.Frame(label="整格模式")
        vbox.pack_start(frame4, False, False, 0)
        self.advanced_containers.append(frame4)
        grid4 = Gtk.Grid()
        grid4.get_style_context().add_class("config-section")
        grid4.set_row_spacing(15)
        grid4.set_column_spacing(15)
        frame4.add(grid4)
        grid_interval_label = Gtk.Label(label="滚动间隔 (ms):", xalign=0)
        grid_interval_tooltip = "整格模式下，每次滚动完后的间隔时间"
        grid_interval_label.set_tooltip_markup(grid_interval_tooltip)
        grid_interval_spin = Gtk.SpinButton()
        grid_interval_spin.set_range(50, 1000)
        grid_interval_spin.set_increments(50, 100)
        grid_interval_spin.set_tooltip_markup(grid_interval_tooltip)
        grid_interval_spin.connect("scroll-event", lambda widget, event: True)
        grid_interval_spin.set_halign(Gtk.Align.START)
        grid4.attach(grid_interval_label, 0, 0, 1, 1)
        grid4.attach(grid_interval_spin, 1, 0, 1, 1)
        self.widget_map['grid_scroll_interval_ms'] = grid_interval_spin
        action_options = [
            ("scroll", "仅滚动"),
            ("scroll_capture", "滚动后截图"),
            ("capture_scroll", "截图后滚动"),
            ("scroll_delete", "滚动并删除"),
        ]
        actions_config = [
            ('forward_action', '前进执行动作', "定义在<b>整格模式</b>下，点击“前进”按钮或使用其快捷键时执行的复合动作"),
            ('backward_action', '后退执行动作', "定义在<b>整格模式</b>下，点击“后退”按钮或使用其快捷键时执行的复合动作")
        ]
        for i, (key, desc, tooltip) in enumerate(actions_config):
            lbl = Gtk.Label(label=f"{desc}:", xalign=0)
            lbl.set_tooltip_markup(tooltip)
            combo = Gtk.ComboBoxText()
            combo.set_tooltip_markup(tooltip)
            combo.connect("scroll-event", lambda widget, event: True)
            for val, desc in action_options:
                combo.append(val, desc)
            combo.set_halign(Gtk.Align.START)
            grid4.attach(lbl, 0, i + 1, 1, 1)
            grid4.attach(combo, 1, i + 1, 1, 1)
            self.widget_map[key] = combo
        f_label = Gtk.Label(label="滚动格数公式:", xalign=0)
        f_label.set_tooltip_markup("在整格模式下且启用误差修正时，计算滚动格数的公式\n变量 <b>{ticks}</b> 代表（高度 / 滚动单位）向下取整，支持函数: <b>min, max, int</b>")
        f_entry = Gtk.Entry()
        f_entry.set_tooltip_markup(f_label.get_tooltip_markup())
        f_entry.set_hexpand(True)
        grid4.attach(f_label, 0, 3, 1, 1)
        grid4.attach(f_entry, 1, 3, 1, 1)
        self.widget_map['grid_scroll_ticks_formula'] = f_entry
        self.stack.add_titled(scrolled, "behavior", "行为设置")

    def _create_theme_appearance_page(self):
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        vbox.get_style_context().add_class("config-container")
        scrolled.add(vbox)
        theme_appearance_settings = [
            ('Interface.Theme', 'border_color'), ('Interface.Theme', 'static_bar_color'),
            ('Interface.Theme', 'info_panel_css'), ('Interface.Theme', 'button_css'), ('Interface.Theme', 'instruction_panel_css'),
            ('Interface.Theme', 'simulated_window_css'), ('Interface.Theme', 'preview_panel_css'), ('Interface.Theme', 'config_panel_css'),
            ('Interface.Theme', 'notification_css'),
            ('Interface.Theme', 'mask_css'), ('Interface.Theme', 'dialog_css'), ('Interface.Theme', 'feedback_widget_css'),
        ]
        self._add_restore_button(vbox, theme_appearance_settings)
        color_frame = Gtk.Frame(label="颜色外观")
        vbox.pack_start(color_frame, False, False, 0)
        color_grid = Gtk.Grid()
        color_grid.get_style_context().add_class("config-section")
        color_grid.set_row_spacing(10)
        color_grid.set_column_spacing(15)
        color_frame.add(color_grid)
        color_configs = [
            ('border_color', "边框颜色", "截图区域边框主要的颜色"),
            ('static_bar_color', "静态栏指示色", "在边框上标记检测到的顶部栏、底部栏和侧边栏的颜色")
        ]
        for i, (key, desc, tooltip) in enumerate(color_configs):
            label = Gtk.Label(label=f"{desc}:", xalign=0)
            btn = CustomColorButton()
            btn.connect("clicked", self._show_embedded_color_chooser)
            label.set_tooltip_markup(tooltip)
            btn.set_tooltip_markup(tooltip)
            self.widget_map[key] = btn
            color_grid.attach(label, 0, i, 1, 1)
            color_grid.attach(btn, 1, i, 1, 1)
        # 自定义样式（CSS）
        css_expander = Gtk.Expander(label="自定义样式（CSS）")
        css_expander.set_expanded(True)
        vbox.pack_start(css_expander, True, True, 0)
        css_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        css_vbox.get_style_context().add_class("config-section")
        css_expander.add(css_vbox)
        css_configs = [
            ("info_panel_css", "信息面板样式"),
            ("button_css", "按钮样式"),
            ("instruction_panel_css", "提示面板样式"),
            ("notification_css", "通知样式"),
            ("simulated_window_css", "模拟窗口通用样式"),
            ("preview_panel_css", "预览面板样式"),
            ('config_panel_css', '配置面板样式'),
            ("mask_css", "遮罩层样式"),
            ("dialog_css", "对话框样式"),
            ("feedback_widget_css", "反馈面板样式"),
        ]
        for key, desc in css_configs:
            label = Gtk.Label(label=f"{desc}:", xalign=0)
            label.set_tooltip_markup("在此处输入自定义 CSS 代码以调整外观")
            scrolled_css = Gtk.ScrolledWindow()
            scrolled_css.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scrolled_css.set_size_request(-1, 200)
            scrolled_css.get_style_context().add_class("config-section")
            textview = Gtk.TextView()
            textview.set_wrap_mode(Gtk.WrapMode.WORD)
            scrolled_css.add(textview)
            frame = Gtk.Frame()
            frame.set_shadow_type(Gtk.ShadowType.IN)
            frame.add(scrolled_css)
            css_vbox.pack_start(label, False, False, 0)
            css_vbox.pack_start(frame, True, True, 0)
            self.widget_map[key] = textview
        self.theme_appearance_page = scrolled
        self.stack.add_titled(scrolled, "theme", "主题外观")

    def _on_sound_theme_changed(self, combo):
        selected_theme = combo.get_active_id()
        if not selected_theme or selected_theme not in self.sound_themes:
            return
        sounds_in_theme = self.sound_themes[selected_theme]
        sound_combos = [self.widget_map.get(k) for k in self.SOUND_KEYS if self.widget_map.get(k)]
        for sound_combo in sound_combos:
            current_value = sound_combo.get_active_id()
            sound_combo.remove_all()
            for sound in sounds_in_theme:
                sound_combo.append(sound, sound)
            if current_value in sounds_in_theme:
                sound_combo.set_active_id(current_value)

    def _on_play_sound_clicked(self, button, sound_combo):
        theme_combo = self.widget_map.get('sound_theme')
        theme_name = theme_combo.get_active_id()
        sound_name = sound_combo.get_active_id()
        if theme_name and sound_name:
            logging.debug(f"试听音效: 主题='{theme_name}', 声音='{sound_name}'")
            SystemInteraction.play_sound(sound_name, theme_name=theme_name)
        elif not theme_name:
            logging.warning("无法试听：请先选择一个声音主题")
        else:
            logging.warning("无法试听：请先选择一个音效")

    def _create_system_performance_page(self):
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        vbox.get_style_context().add_class("config-container")
        scrolled.add(vbox)
        system_perf_settings = [
            ('System', 'max_viewer_dimension'), ('System', 'large_image_opener'),
            ('Performance', 'max_scroll_per_tick'), ('Performance', 'min_scroll_per_tick'), ('Preview', 'preview_cache_size'),
            ('System', 'sound_theme'), ('System', 'capture_sound'), ('System', 'undo_sound'), ('System', 'finalize_sound'), ('System', 'warning_sound'),
            ('System', 'log_file'),
        ]
        self._add_restore_button(vbox, system_perf_settings)
        frame1 = Gtk.Frame(label="打开图片")
        vbox.pack_start(frame1, False, False, 0)
        grid1 = Gtk.Grid()
        grid1.get_style_context().add_class("config-section")
        grid1.set_row_spacing(15)
        grid1.set_column_spacing(15)
        frame1.add(grid1)
        # 图片尺寸阈值
        dim_label = Gtk.Label(label="图片尺寸阈值:", xalign=0)
        dim_tooltip = "当最终图片长或宽超过此值时，会使用下面的命令打开图片\n设为 <b>-1</b> 总是用系统默认方式打开图片\n设为 <b>0</b> 总是用自定义命令打开图片"
        dim_label.set_tooltip_markup(dim_tooltip)
        dim_spin = Gtk.SpinButton()
        dim_spin.set_tooltip_markup(dim_tooltip)
        dim_spin.connect("scroll-event", lambda widget, event: True)
        dim_spin.set_range(-1, 131071)
        dim_spin.set_increments(1, 100)
        dim_spin.set_halign(Gtk.Align.START)
        self.widget_map['max_viewer_dimension'] = dim_spin
        grid1.attach(dim_label, 0, 0, 1, 1)
        grid1.attach(dim_spin, 1, 0, 1, 1)
        # 大尺寸图片打开命令
        label = Gtk.Label(label="大图打开命令:", xalign=0)
        label.set_tooltip_markup("当最终图片长或宽超过上方阈值时，使用此终端命令打开图片\n<b>{filepath}</b> 会被替换为图片文件路径，示例：flatpak run org.libvips.vipsdisp \"{filepath}\"\n直接设为 <b>default_browser</b> 可用浏览器打开")
        self.large_opener_entry = Gtk.Entry()
        self.large_opener_entry.set_tooltip_markup(label.get_tooltip_markup())
        self.large_opener_entry.set_hexpand(True)
        self.widget_map['large_image_opener'] = self.large_opener_entry
        grid1.attach(label, 0, 1, 1, 1)
        grid1.attach(self.large_opener_entry, 1, 1, 1, 1)
        # 性能调优
        frame2 = Gtk.Frame(label="性能调优")
        vbox.pack_start(frame2, False, False, 0)
        grid2 = Gtk.Grid()
        grid2.get_style_context().add_class("config-section")
        grid2.set_row_spacing(15)
        grid2.set_column_spacing(15)
        frame2.add(grid2)
        performance_configs = [
            ("max_scroll_per_tick", "每格最大滚动像素", (120, 500), (10, 50), "用于匹配和校准的最大滚动阈值（单位：缓冲区px），需要不小于实际滚动单位"),
            ("min_scroll_per_tick", "每格最小滚动像素", (1, 60), (1, 10), "用于匹配和校准的最小滚动阈值（单位：缓冲区px），可以大于实际滚动单位"),
            ("preview_cache_size", "预览缓存大小 (张)", (10, 50), (1, 5), "预览在内存中保留的图片数量，不会少于视口内的图片数量，增加可减少加载时间但会占用更多内存")
        ]
        num_items = len(performance_configs)
        mid_point = (num_items + 1) // 2
        for i, (key, desc, (min_val, max_val), (step, page), tooltip) in enumerate(performance_configs):
            row = i % mid_point
            col_base = (i // mid_point) * 2
            label = Gtk.Label(label=f"{desc}:", xalign=0)
            label.set_tooltip_markup(tooltip)
            spin = Gtk.SpinButton()
            spin.set_tooltip_markup(tooltip)
            spin.connect("scroll-event", lambda widget, event: True)
            spin.set_range(min_val, max_val)
            spin.set_increments(step, page)
            spin.set_halign(Gtk.Align.START)
            grid2.attach(label, col_base, row, 1, 1)
            grid2.attach(spin, col_base + 1, row, 1, 1)
            self.widget_map[key] = spin
        # 声音主题
        frame3 = Gtk.Frame(label="声音主题")
        vbox.pack_start(frame3, False, False, 0)
        grid3 = Gtk.Grid()
        grid3.get_style_context().add_class("config-section")
        grid3.set_row_spacing(10)
        grid3.set_column_spacing(15)
        frame3.add(grid3)
        label = Gtk.Label(label="声音主题:", xalign=0)
        theme_combo = Gtk.ComboBoxText()
        theme_combo.connect("scroll-event", lambda widget, event: True)
        for theme_name in sorted(self.sound_themes.keys()):
            theme_combo.append(theme_name, theme_name)
        theme_combo.connect("changed", self._on_sound_theme_changed)
        self.widget_map['sound_theme'] = theme_combo
        grid3.attach(label, 0, 0, 1, 1)
        grid3.attach(theme_combo, 1, 0, 1, 1)
        sound_configs = [
            ("capture_sound", "截图音效"),
            ("undo_sound", "撤销音效"),
            ("finalize_sound", "完成音效"),
            ("warning_sound", "警告音效")
        ]
        for i, (key, desc) in enumerate(sound_configs):
            label = Gtk.Label(label=f"{desc}:", xalign=0)
            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            sound_combo = Gtk.ComboBoxText()
            sound_combo.connect("scroll-event", lambda widget, event: True)
            play_button = Gtk.Button()
            play_button.set_label("试听")
            play_button.connect("clicked", self._on_play_sound_clicked, sound_combo)
            hbox.pack_start(sound_combo, False, False, 0)
            hbox.pack_start(play_button, False, False, 0)
            self.widget_map[key] = sound_combo
            grid3.attach(label, 0, i + 1, 1, 1)
            grid3.attach(hbox, 1, i + 1, 1, 1)
        # 路径
        frame4 = Gtk.Frame(label="路径")
        vbox.pack_start(frame4, False, False, 0)
        grid4 = Gtk.Grid()
        grid4.get_style_context().add_class("config-section")
        grid4.set_row_spacing(15)
        grid4.set_column_spacing(15)
        frame4.add(grid4)
        log_label = Gtk.Label(label="日志文件路径:", xalign=0)
        log_label.set_tooltip_markup("指定日志文件的保存路径，支持使用 <b>~</b> 代表主目录，变量 <b>{pid}</b> 代表当前进程 ID")
        log_entry = Gtk.Entry()
        log_entry.set_tooltip_markup(log_label.get_tooltip_markup())
        log_entry.set_hexpand(True)
        grid4.attach(log_label, 0, 0, 1, 1)
        grid4.attach(log_entry, 1, 0, 1, 1)
        self.widget_map['log_file'] = log_entry
        temp_label = Gtk.Label(label="临时目录:", xalign=0)
        temp_label.set_tooltip_markup("存放当前实例截图的目录，修改请编辑配置文件")
        temp_entry = Gtk.Entry()
        temp_entry.set_text(str(self.config.TEMP_DIRECTORY))
        temp_entry.set_tooltip_markup(temp_label.get_tooltip_markup())
        temp_entry.set_editable(False)
        temp_entry.set_can_focus(True)
        temp_entry.set_hexpand(True)
        grid4.attach(temp_label, 0, 1, 1, 1)
        grid4.attach(temp_entry, 1, 1, 1, 1)
        cfg_label = Gtk.Label(label="配置文件路径:", xalign=0)
        cfg_label.set_tooltip_markup("本次程序运行加载的配置文件路径")
        cfg_entry = Gtk.Entry()
        cfg_entry.set_text(str(self.config.config_path))
        cfg_entry.set_tooltip_markup(cfg_label.get_tooltip_markup())
        cfg_entry.set_editable(False)
        cfg_entry.set_can_focus(True)
        cfg_entry.set_hexpand(True)
        grid4.attach(cfg_label, 0, 2, 1, 1)
        grid4.attach(cfg_entry, 1, 2, 1, 1)
        self.system_performance_page = scrolled
        self.stack.add_titled(scrolled, "system", "系统与性能")

    def _create_grid_calibration_page(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        vbox.get_style_context().add_class("config-container")
        info_label = Gtk.Label(label="用于手动添加或调整应用的滚动单位 (缓冲区px)")
        info_label.set_xalign(0)
        vbox.pack_start(info_label, False, False, 0)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_shadow_type(Gtk.ShadowType.IN)
        vbox.pack_start(scrolled, True, True, 0)
        self.grid_listbox = Gtk.ListBox()
        self.grid_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        empty_lbl = Gtk.Label(label="暂无已保存的配置")
        empty_lbl.get_style_context().add_class("dim-label")
        empty_lbl.show()
        self.grid_listbox.set_placeholder(empty_lbl)
        scrolled.add(self.grid_listbox)
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        button_box.set_halign(Gtk.Align.END)
        vbox.pack_start(button_box, False, False, 0)
        add_button = Gtk.Button(label="添加")
        add_button.connect("clicked", lambda w: self._add_grid_row())
        remove_button = Gtk.Button(label="删除选中项")
        remove_button.connect("clicked", self._on_grid_remove)
        button_box.pack_start(add_button, False, False, 0)
        button_box.pack_start(remove_button, False, False, 0)
        self.grid_calibration_page = vbox
        self.stack.add_titled(vbox, "grid", "整格模式校准")

    def _add_grid_row(self, app_class="", unit=0, matching_enabled=False):
        row = Gtk.ListBoxRow()
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        hbox.set_margin_start(10)
        hbox.set_margin_end(10)
        hbox.set_margin_top(5)
        hbox.set_margin_bottom(5)
        row.add(hbox)
        entry = Gtk.Entry()
        entry.set_placeholder_text("应用程序类名")
        entry.set_text(app_class)
        entry.last_key = app_class
        entry.set_hexpand(True)
        spin = Gtk.SpinButton()
        spin.set_range(0, 300)
        spin.set_increments(1, 10)
        spin.set_value(unit)
        check = Gtk.CheckButton(label="修正误差")
        check.set_tooltip_markup("启用模板匹配修正滚动误差\n启用后，请确保滚动距离小于截图区高度，否则修正无效")
        check.set_active(matching_enabled)
        def on_grid_change(*args):
            raw_text = entry.get_text()
            key = raw_text.strip().lower()
            if not key: return False
            if raw_text != key:
                entry.set_text(key)
            val = f"{spin.get_value_as_int()}, {str(check.get_active()).lower()}"
            old_key = getattr(entry, 'last_key', '')
            if old_key and old_key != key:
                self.config.remove_value('ApplicationScrollUnits', old_key)
            self.config.set_value('ApplicationScrollUnits', key, val)
            entry.last_key = key
            return False
        entry.connect("focus-out-event", on_grid_change)
        spin.connect("value-changed", on_grid_change)
        check.connect("toggled", on_grid_change)
        for w in [entry, spin, check]:
            w.connect("button-press-event", self._on_grid_row_child_clicked)
        spin.connect("scroll-event", lambda w,e: True)
        hbox.pack_start(entry, True, True, 0)
        hbox.pack_start(spin, False, False, 0)
        hbox.pack_start(check, False, False, 0)
        self.grid_listbox.add(row)
        row.show_all()

    def _on_grid_remove(self, widget):
        selected_row = self.grid_listbox.get_selected_row()
        if selected_row:
            hbox = selected_row.get_child()
            entry = hbox.get_children()[0]
            key = entry.get_text().strip().lower()
            if key:
                self.config.remove_value('ApplicationScrollUnits', key)
            self.grid_listbox.remove(selected_row)

    def _on_grid_row_child_clicked(self, widget, event):
        parent = widget.get_parent()
        while parent and not isinstance(parent, Gtk.ListBoxRow):
            parent = parent.get_parent()
        if isinstance(parent, Gtk.ListBoxRow):
            self.grid_listbox.select_row(parent)
        return False

    def _on_advanced_toggle(self, switch, gparam):
        self.show_advanced = switch.get_active()
        self._update_advanced_visibility()

    def _update_advanced_visibility(self):
        advanced_pages = [
            self.theme_appearance_page,
            self.system_performance_page,
            self.grid_calibration_page
        ]
        for page_widget in advanced_pages:
            page_widget.set_visible(self.show_advanced)
        for container in self.advanced_containers:
            container.set_visible(self.show_advanced)

    def _on_format_changed(self, combo):
        is_jpeg = combo.get_active_text() == "JPEG"
        self.jpeg_label.set_sensitive(is_jpeg)
        self.jpeg_quality_spin.set_sensitive(is_jpeg)
        self._update_filename_preview()

    def _on_restore_defaults_clicked(self, button, settings_to_restore):
        self._cancel_hotkey_capture()
        self._is_batch_restoring = True
        restored_count = 0
        for section, key in settings_to_restore:
            default_str = self.config.get_default_string(section, key)
            current_str = self.config.get_raw_string(section, key)
            if current_str != default_str:
                restored_count += 1
                self.config.set_value(section, key, default_str)
            widget = self.widget_map.get(key)
            if widget:
                self._update_widget_value(widget, section, key, default_str)
        self._is_batch_restoring = False
        if restored_count > 0:
            self.status_label.set_text(f"已恢复 {restored_count} 项配置到默认值")
        else:
            self.status_label.set_text("当前页配置已是默认值")
        page_keys = [item[1] for item in settings_to_restore]
        if 'filename_template' in page_keys:
            self._update_filename_preview()

    def _get_widget_value(self, widget, key):
        if isinstance(widget, Gtk.CheckButton) or isinstance(widget, Gtk.Switch):
            return str(widget.get_active()).lower()
        elif isinstance(widget, CustomColorButton):
            rgba = widget.get_rgba()
            return f"{rgba.red:.2f}, {rgba.green:.2f}, {rgba.blue:.2f}, {rgba.alpha:.2f}"
        elif isinstance(widget, Gtk.Button):
            if widget == self.capturing_hotkey_button:
                attr_name = f"HOTKEY_{key.upper()}"
                hotkey_def = getattr(self.config, attr_name, None)
                return hotkey_def.to_string() if hotkey_def else ""
            return widget.get_label()
        elif isinstance(widget, Gtk.Entry):
            return widget.get_text()
        elif isinstance(widget, Gtk.ComboBoxText):
            return widget.get_active_id()
        elif isinstance(widget, Gtk.SpinButton):
            if widget.get_digits() == 0:
                return str(widget.get_value_as_int())
            else:
                return str(widget.get_value())
        elif isinstance(widget, Gtk.TextView):
            buffer = widget.get_buffer()
            start, end = buffer.get_bounds()
            return buffer.get_text(start, end, False)
        return None

    def _update_widget_value(self, widget, section, key, raw_str):
        attr_name = f"HOTKEY_{key.upper()}" if section == 'Hotkeys' else key.upper()
        typed_val = getattr(self.config, attr_name)
        if isinstance(widget, (Gtk.Switch, Gtk.CheckButton)):
            widget.set_active(typed_val)
        elif isinstance(widget, CustomColorButton):
            widget.set_rgba(Gdk.RGBA(*typed_val))
        elif isinstance(widget, Gtk.Button):
            if section == 'Hotkeys':
                widget.set_label(typed_val.to_string())
            else:
                widget.set_label(raw_str)
        elif isinstance(widget, Gtk.Entry):
            widget.set_text(raw_str)
        elif isinstance(widget, Gtk.ComboBoxText):
            widget.set_active_id(raw_str)
        elif isinstance(widget, Gtk.SpinButton):
            widget.set_value(typed_val)
        elif isinstance(widget, Gtk.TextView):
            widget.get_buffer().set_text(raw_str.lstrip())

    def _load_config_values(self):
        keys_to_skip = ['sound_theme'] + self.SOUND_KEYS
        for section, key in self.managed_settings:
            if key in keys_to_skip:
                continue
            widget = self.widget_map.get(key)
            if widget:
                raw_str = self.config.get_raw_string(section, key)
                self._update_widget_value(widget, section, key, raw_str)
        theme_widget = self.widget_map.get('sound_theme')
        theme_widget.set_active_id(self.config.SOUND_THEME)
        self._on_sound_theme_changed(theme_widget)
        for key in self.SOUND_KEYS:
            widget = self.widget_map.get(key)
            value = getattr(self.config, key.upper(), None)
            if value:
                widget.set_active_id(value)
        self.grid_listbox.foreach(lambda child: self.grid_listbox.remove(child))
        scroll_units = self.config.get_section_items('ApplicationScrollUnits')
        for app, value_str in scroll_units:
            unit, enabled = self.config.parse_string_to_value('ApplicationScrollUnits', app, value_str)
            self._add_grid_row(app, unit, enabled)
        self._update_filename_preview()
        self._on_format_changed(self.format_combo)

    def _bind_all_widgets(self):
        for section, key in self.managed_settings:
            if section == 'Hotkeys': continue
            widget = self.widget_map.get(key)
            if not widget: continue
            def on_change(w, *args, _s=section, _k=key):
                self._on_setting_changed(w, _s, _k)
                if isinstance(w, Gtk.Entry): return False
            if isinstance(widget, (Gtk.Switch, Gtk.CheckButton)):
                if isinstance(widget, Gtk.Switch): widget.connect("notify::active", on_change)
                else: widget.connect("toggled", on_change)
            elif isinstance(widget, Gtk.SpinButton):
                widget.connect("value-changed", on_change)
            elif isinstance(widget, Gtk.ComboBoxText):
                widget.connect("changed", on_change)
            elif isinstance(widget, CustomColorButton):
                widget.connect("color-changed", on_change)
            elif isinstance(widget, Gtk.Entry):
                widget.connect("focus-out-event", on_change)
                widget.connect("activate", on_change)
                if key in ['filename_template', 'filename_timestamp_format']:
                    widget.connect("changed", self._update_filename_preview)
            elif isinstance(widget, Gtk.TextView):
                widget.connect("focus-out-event", on_change)

    def _on_setting_changed(self, widget, section, key):
        new_value = self._get_widget_value(widget, key)
        if new_value is not None:
            current = self.config.get_raw_string(section, key)
            if current != new_value:
                self.config.set_value(section, key, new_value)

    def _on_config_changed_signal(self, config_obj, section, key, value):
        if self._is_batch_restoring:
            return
        if self.config.is_restart_required(key):
            msg = f"已保存 '{key}' (需重启生效)"
        else:
            msg = f"已保存 '{key}' (已生效)"
        self.status_label.set_text(msg)
        if section == 'ApplicationScrollUnits':
            target_app = key.lower()
            if value is None:
                return
            unit, enabled = self.config.parse_string_to_value('ApplicationScrollUnits', key, value)
            for row in self.grid_listbox.get_children():
                hbox = row.get_child()
                entry, spin, check = hbox.get_children()
                if entry.get_text().strip().lower() == target_app:
                    if spin.get_value_as_int() != unit:
                        spin.set_value(unit)
                    if check.get_active() != enabled:
                        check.set_active(enabled)
                    break
            else:
                self._add_grid_row(target_app, unit, enabled)

    def update_status_focus(self, has_focus):
        global hotkey_manager
        disable_msg = "快捷键已自动禁用"
        if has_focus:
            if hotkey_manager and hotkey_manager.are_hotkeys_enabled:
                self.status_label.set_text(disable_msg)
        else:
            if self.status_label.get_text() == disable_msg:
                self.status_label.set_text("")

class PreviewPanel(SimulatedWindow):
    """显示长图预览的面板"""
    def __init__(self, model: StitchModel, config_obj: Config, parent_overlay: 'CaptureOverlay'):
        super().__init__(parent_overlay, title="长图预览", css_class="simulated-window", resizable=True)
        self.model = model
        self.config = config_obj
        self.is_fit_width_mode = True
        self.effective_scale_factor = 1.0
        # 逻辑px
        self.drawing_area_width = 1
        self.drawing_area_height = 1
        self.stick_to_bottom = True
        self._pending_center_ratios = None
        self._is_autoscroll_pending = False
        self.last_viewport_size = (-1, -1)
        self._resize_timer_id = None
        self.drag_start_pos = None # 窗口坐标
        self.drag_start_scroll = None
        self.is_selection_mode = False
        self.selection_action = None
        self.current_pointer = (0, 0) # 窗口坐标
        # 缓冲区px
        self.selection_anchor_y = None
        self.selection_moving_y = None
        self.selection_autoscroll_timer = None
        self.selection_autoscroll_velocity = 0.0
        # 逻辑px
        self.initial_y_offset = 0
        self.last_scroll_y = 0
        self.scroll_dy = 0
        self.last_roi_set = set()
        top_button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        top_button_box.set_margin_top(0)
        top_button_box.set_margin_bottom(0)
        top_button_box.set_margin_start(4)
        top_button_box.set_margin_end(4)
        self.add_content(top_button_box, expand=False, fill=False)
        top_button_box.set_halign(Gtk.Align.CENTER)
        self.btn_start_selection = Gtk.Button(label="选择")
        self.btn_start_selection.set_tooltip_text("选择区域")
        self.btn_start_selection.connect("clicked", self._on_start_selection_mode)
        self.btn_cancel_selection = Gtk.Button(label="取消")
        self.btn_cancel_selection.set_tooltip_text("退出选择")
        self.btn_cancel_selection.connect("clicked", lambda btn: self.cancel_selection_mode())
        self.btn_cancel_selection.set_sensitive(False)
        self.btn_delete_selection = Gtk.Button(label="删除")
        self.btn_delete_selection.set_tooltip_text("删除选定区域（修复内容重复）")
        self.btn_delete_selection.connect("clicked", self._on_delete_clicked)
        self.btn_delete_selection.set_sensitive(False)
        self.btn_restore_selection = Gtk.Button(label="恢复")
        self.btn_restore_selection.set_tooltip_text("恢复选定区域内的接缝（修复内容缺失）")
        self.btn_restore_selection.connect("clicked", self._on_restore_clicked)
        self.btn_undo_mod = Gtk.Button.new_from_icon_name("edit-undo-symbolic", Gtk.IconSize.BUTTON)
        self.btn_undo_mod.set_tooltip_text("撤销上一步编辑 (删除/恢复)")
        self.btn_undo_mod.connect("clicked", lambda w: self.model.undo())
        self.btn_redo_mod = Gtk.Button.new_from_icon_name("edit-redo-symbolic", Gtk.IconSize.BUTTON)
        self.btn_redo_mod.set_tooltip_text("重做上一步编辑 (删除/恢复)")
        self.btn_redo_mod.connect("clicked", lambda w: self.model.redo())
        for btn in [self.btn_start_selection, self.btn_cancel_selection,
                    self.btn_delete_selection, self.btn_restore_selection,
                    self.btn_undo_mod, self.btn_redo_mod]:
            btn.get_style_context().add_class("no-padding")
            btn.get_style_context().add_class(Gtk.STYLE_CLASS_FLAT)
        top_button_box.pack_start(self.btn_start_selection, False, False, 0)
        top_button_box.pack_start(self.btn_cancel_selection, False, False, 0)
        top_button_box.pack_start(self.btn_delete_selection, False, False, 0)
        top_button_box.pack_start(self.btn_restore_selection, False, False, 0)
        top_button_box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, margin=4), False, False, 0)
        top_button_box.pack_start(self.btn_undo_mod, False, False, 0)
        top_button_box.pack_start(self.btn_redo_mod, False, False, 0)
        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.add_content(self.scrolled_window, expand=True, fill=True)
        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.add_events(
            Gdk.EventMask.EXPOSURE_MASK |
            Gdk.EventMask.BUTTON_PRESS_MASK |
            Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK
        )
        self.scrolled_window.add(self.drawing_area)
        button_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        button_hbox.set_halign(Gtk.Align.CENTER)
        button_hbox.set_margin_top(5)
        button_hbox.set_margin_bottom(5)
        self.add_content(button_hbox, expand=False, fill=False)
        self.btn_scroll_top = Gtk.Button.new_from_icon_name("go-top-symbolic", Gtk.IconSize.BUTTON)
        self.btn_scroll_top.set_tooltip_text("滚动到顶部")
        self.btn_scroll_top.connect("clicked", lambda w: self._scroll_vertical('top'))
        button_hbox.pack_start(self.btn_scroll_top, False, False, 0)
        self.btn_scroll_bottom = Gtk.Button.new_from_icon_name("go-bottom-symbolic", Gtk.IconSize.BUTTON)
        self.btn_scroll_bottom.set_tooltip_text("滚动到底部")
        self.btn_scroll_bottom.connect("clicked", lambda w: self._scroll_vertical('bottom'))
        button_hbox.pack_start(self.btn_scroll_bottom, False, False, 0)
        separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        button_hbox.pack_start(separator, False, False, 5)
        self.btn_zoom_out = Gtk.Button.new_from_icon_name("zoom-out-symbolic", Gtk.IconSize.BUTTON)
        self.btn_zoom_out.set_tooltip_text("缩小")
        self.btn_zoom_out.connect("clicked", lambda w: self.adjust_zoom('out'))
        button_hbox.pack_start(self.btn_zoom_out, False, False, 0)
        self.btn_zoom_reset = Gtk.Button.new_from_icon_name("zoom-original-symbolic", Gtk.IconSize.BUTTON)
        self.btn_zoom_reset.set_tooltip_text("重置缩放 (100%)")
        self.btn_zoom_reset.connect("clicked", lambda w: self.adjust_zoom('reset'))
        button_hbox.pack_start(self.btn_zoom_reset, False, False, 0)
        self.btn_zoom_fit = Gtk.Button.new_from_icon_name("zoom-fit-best-symbolic", Gtk.IconSize.BUTTON)
        self.btn_zoom_fit.set_tooltip_text("自适应宽度")
        self.btn_zoom_fit.connect("clicked", self._set_fit_width_mode)
        button_hbox.pack_start(self.btn_zoom_fit, False, False, 0)
        self.btn_zoom_in = Gtk.Button.new_from_icon_name("zoom-in-symbolic", Gtk.IconSize.BUTTON)
        self.btn_zoom_in.set_tooltip_text("放大")
        self.btn_zoom_in.connect("clicked", lambda w: self.adjust_zoom('in'))
        button_hbox.pack_start(self.btn_zoom_in, False, False, 0)
        self.zoom_label = Gtk.Label(label="100%")
        self.zoom_label.set_margin_start(5)
        button_hbox.pack_start(self.zoom_label, False, False, 0)
        self.model.connect("model-updated", self.on_model_updated)
        self.model.connect("image-ready", lambda m, f, p: self.drawing_area.queue_draw())
        self.model.connect('modification-stack-changed', lambda m: self._update_button_sensitivity())
        v_adj = self.scrolled_window.get_vadjustment()
        if v_adj:
            v_adj.connect("value-changed", self.on_scroll_changed)
            v_adj.connect("changed", self._update_button_sensitivity)
        self.drawing_area.connect("draw", self.on_draw)
        self.drawing_area.connect("button-press-event", self._on_drawing_area_button_press)
        self.drawing_area.connect("motion-notify-event", self._on_drawing_area_motion_notify)
        self.drawing_area.connect("button-release-event", self._on_drawing_area_button_release)
        self.drawing_area.connect("size-allocate", self._on_drawing_area_size_allocate)
        self.scrolled_window.connect("size-allocate", self.on_viewport_resized)
        self.on_model_updated(self.model)
        self._update_button_sensitivity()
        self.show_all()
        min_req_size, _ = self.get_preferred_size()
        initial_w = min_req_size.width + 60
        initial_h = self.config.PREVIEW_PANEL_HEIGHT
        self.set_size_request(initial_w, initial_h)

    def hide(self):
        self.drag_start_pos = None
        self.drag_start_scroll = None
        self.selection_action = None
        self._stop_autoscroll()
        if self.drawing_area.get_window():
            self.drawing_area.get_window().set_cursor(self.cursors['default'])
        super().hide()

    # 逻辑px {
    def _get_center_ratios(self, viewport_w=None, viewport_h=None):
        hadj = self.scrolled_window.get_hadjustment()
        vadj = self.scrolled_window.get_vadjustment()
        if viewport_w is None:
            viewport_w = self.scrolled_window.get_allocated_width()
        if viewport_h is None:
            viewport_h = self.scrolled_window.get_allocated_height()
        content_w = self.drawing_area_width
        content_h = self.drawing_area_height
        if content_w <= viewport_w:
            center_x_ratio = 0.5
        else:
            center_x_ratio = (hadj.get_value() + viewport_w / 2) / content_w
        if content_h <= viewport_h:
            center_y_ratio = 0.5
        else:
            center_y_ratio = (vadj.get_value() + viewport_h / 2) / content_h
        return center_x_ratio, center_y_ratio

    def _set_scroll_from_ratios(self, center_x_ratio, center_y_ratio):
        hadj = self.scrolled_window.get_hadjustment()
        vadj = self.scrolled_window.get_vadjustment()
        viewport_w = self.scrolled_window.get_allocated_width()
        viewport_h = self.scrolled_window.get_allocated_height()
        new_content_w = self.drawing_area_width
        new_content_h = self.drawing_area_height
        if new_content_w > viewport_w:
            new_h_value = (center_x_ratio * new_content_w) - (viewport_w / 2)
            hadj.set_value(new_h_value)
        if new_content_h > viewport_h:
            new_v_value = (center_y_ratio * new_content_h) - (viewport_h / 2)
            vadj.set_value(new_v_value)

    def _update_zoom_label(self):
        self.zoom_label.set_text(f"{self.effective_scale_factor * 100:.0f}%")
        return False

    def adjust_zoom(self, action):
        if action == 'in':
            new_zoom = min(self.effective_scale_factor * self.config.PREVIEW_ZOOM_FACTOR, self.config.PREVIEW_MAX_ZOOM)
        elif action == 'out':
            new_zoom = max(self.effective_scale_factor / self.config.PREVIEW_ZOOM_FACTOR, self.config.PREVIEW_MIN_ZOOM)
        elif action == 'reset':
            new_zoom = 1.0
        else:
            return
        if abs(new_zoom - self.effective_scale_factor) < 1e-5 and not self.is_fit_width_mode:
            return
        self._pending_center_ratios = self._get_center_ratios()
        self.is_fit_width_mode = False
        self.effective_scale_factor = new_zoom
        self._update_drawing_area_size()
        self._update_button_sensitivity()
        self._update_zoom_label()

    def _set_fit_width_mode(self, button=None):
        if self.is_fit_width_mode:
            return
        self._pending_center_ratios = self._get_center_ratios()
        self.is_fit_width_mode = True
        self._update_drawing_area_size()
        self._update_button_sensitivity()
        self._update_zoom_label()

    def on_viewport_resized(self, widget, allocation):
        if (allocation.width, allocation.height) != self.last_viewport_size:
            old_w = self.last_viewport_size[0] if self.last_viewport_size[0] > 0 else allocation.width
            old_h = self.last_viewport_size[1] if self.last_viewport_size[1] > 0 else allocation.height
            if self._pending_center_ratios is None:
                self._pending_center_ratios = self._get_center_ratios(viewport_w=old_w, viewport_h=old_h)
            self.last_viewport_size = (allocation.width, allocation.height)
            if self._resize_timer_id is not None: return
            def _do_update():
                self._resize_timer_id = None
                self._update_drawing_area_size()
                return False
            self._resize_timer_id = GLib.timeout_add(16, _do_update)

    def _on_drawing_area_size_allocate(self, widget, allocation):
        widget.queue_draw()
        if self._pending_center_ratios or self.stick_to_bottom:
            self._apply_pending_scroll()

    def on_model_updated(self, model_instance):
        if self.model.capture_count == 0 and self.is_selection_mode:
            logging.debug("模型已空，预览面板自动退出选择模式")
            self.cancel_selection_mode()
        v_adj = self.scrolled_window.get_vadjustment()
        if v_adj.get_upper() <= 0:
            self.stick_to_bottom = True
        self._update_drawing_area_size()
    # 逻辑px }

    def _scroll_vertical(self, target):
        v_adj = self.scrolled_window.get_vadjustment()
        if not v_adj: return
        if target == 'top':
            v_adj.set_value(v_adj.get_lower())
        elif target == 'bottom':
            v_adj.set_value(max(v_adj.get_lower(), v_adj.get_upper() - v_adj.get_page_size()))

    def _get_selection_absolute_bounds(self):
        if self.selection_anchor_y is None or self.selection_moving_y is None:
            return None, None, False
        y_start_abs = min(self.selection_anchor_y, self.selection_moving_y)
        y_end_abs = max(self.selection_anchor_y, self.selection_moving_y)
        is_valid = (y_end_abs - y_start_abs) > 0
        return y_start_abs, y_end_abs, is_valid

    def _update_button_sensitivity(self, adjustment=None):
        _, _, has_valid_selection = self._get_selection_absolute_bounds()
        has_captures = self.model.capture_count > 0
        self.btn_start_selection.set_sensitive(has_captures and (not self.is_selection_mode))
        self.btn_cancel_selection.set_sensitive(has_captures and self.is_selection_mode)
        self.btn_delete_selection.set_sensitive(has_captures and self.is_selection_mode and has_valid_selection)
        self.btn_restore_selection.set_sensitive(has_captures and self.is_selection_mode and has_valid_selection)
        self.btn_undo_mod.set_sensitive(has_captures and len(self.model.modifications) > 0)
        self.btn_redo_mod.set_sensitive(has_captures and len(self.model.redo_stack) > 0)
        v_adj = self.scrolled_window.get_vadjustment()
        can_scroll = v_adj and (v_adj.get_upper() > v_adj.get_page_size() + 1)
        self.btn_scroll_top.set_sensitive(can_scroll)
        self.btn_scroll_bottom.set_sensitive(can_scroll)
        self.btn_zoom_in.set_sensitive(self.effective_scale_factor < self.config.PREVIEW_MAX_ZOOM)
        self.btn_zoom_out.set_sensitive(self.effective_scale_factor > self.config.PREVIEW_MIN_ZOOM)
        self.btn_zoom_reset.set_sensitive(abs(self.effective_scale_factor - 1.0) > 1e-5 or self.is_fit_width_mode)
        self.btn_zoom_fit.set_sensitive(not self.is_fit_width_mode)

    def _update_drawing_area_size(self):
        """根据模型数据、缩放级别和视口大小计算绘制区域尺寸和缩放因子"""
        monitor_scale = self.parent_overlay.session.scale
        # 缓冲区px
        image_width = self.model.image_width
        virtual_height = self.model.total_virtual_height
        viewport_width = self.scrolled_window.get_allocated_width()
        viewport_height = self.scrolled_window.get_allocated_height()
        if viewport_width <= 0:
            viewport_width, viewport_height = self.get_default_size()
        if image_width <= 0 or virtual_height <= 0:
            # 逻辑px
            self.drawing_area_width = max(1, viewport_width)
            self.drawing_area_height = max(1, viewport_height)
            if self.is_fit_width_mode:
                self.effective_scale_factor = 1.0
        else:
            # 缓冲区px -> 逻辑px
            logical_img_w = image_width / monitor_scale
            logical_img_h = virtual_height / monitor_scale
            if self.is_fit_width_mode:
                self.effective_scale_factor = (viewport_width / logical_img_w) if (0 < viewport_width < logical_img_w) else 1.0
            self.drawing_area_width = math.ceil(logical_img_w * self.effective_scale_factor)
            self.drawing_area_height = math.ceil(logical_img_h * self.effective_scale_factor)
        self.initial_y_offset = (viewport_height - self.drawing_area_height) // 2 if self.drawing_area_height < viewport_height else 0
        GLib.idle_add(self._update_zoom_label)
        old_w, old_h = self.drawing_area.get_size_request()
        size_changed = (old_w != self.drawing_area_width) or (old_h != self.drawing_area_height)
        self.drawing_area.set_size_request(self.drawing_area_width, self.drawing_area_height)
        self.drawing_area.queue_draw()
        if self.stick_to_bottom or self._pending_center_ratios:
            self._is_autoscroll_pending = True
            if not size_changed:
                GLib.idle_add(self._apply_pending_scroll)
        GLib.idle_add(self._update_button_sensitivity)

    def _apply_pending_scroll(self):
        # 逻辑px
        v_adj = self.scrolled_window.get_vadjustment()
        if not v_adj: return False
        if self._pending_center_ratios:
            self._set_scroll_from_ratios(*self._pending_center_ratios)
        elif self.stick_to_bottom:
            new_upper = v_adj.get_upper()
            page_size = v_adj.get_page_size()
            if new_upper > page_size:
                v_adj.set_value(new_upper - page_size)
        self._pending_center_ratios = None
        self._is_autoscroll_pending = False
        return False

    def on_scroll_changed(self, adjustment):
        if self._is_autoscroll_pending:
            return
        if self.selection_action:
            self._update_selection_from_pointer()
        current_value = adjustment.get_value()
        self.scroll_dy = current_value - self.last_scroll_y
        self.last_scroll_y = current_value
        self.stick_to_bottom = current_value + adjustment.get_page_size() >= adjustment.get_upper() - 5

    def _drawing_y_to_render_y(self, drawing_y):
        monitor_scale = self.parent_overlay.session.scale
        return ((drawing_y - self.initial_y_offset) / self.effective_scale_factor) * monitor_scale # 屏幕逻辑px -> 原逻辑px -> 缓冲区px

    def _absolute_y_to_render_y(self, absolute_y):
        # 缓冲区px
        if not self.model.render_plan:
            return absolute_y
        for piece in self.model.render_plan:
            if piece['absolute_y_start'] <= absolute_y < piece['absolute_y_end']:
                return piece['render_y_start'] + (absolute_y - piece['absolute_y_start'])
            elif piece['absolute_y_start'] > absolute_y:
                return piece['render_y_start']
        last = self.model.render_plan[-1]
        return last['render_y_start'] + last['height']

    def _render_y_to_absolute_y(self, render_y):
        # 缓冲区px
        if not self.model.render_plan:
            return render_y
        piece_render_y_starts = [p['render_y_start'] for p in self.model.render_plan]
        index = max(0, bisect.bisect_right(piece_render_y_starts, render_y) - 1)
        piece = self.model.render_plan[index]
        if render_y >= piece['render_y_start'] + piece['height']:
            if index + 1 < len(self.model.render_plan):
                next_piece = self.model.render_plan[index + 1]
                return next_piece['absolute_y_start']
            else:
                return piece['absolute_y_end']
        return piece['absolute_y_start'] + render_y - piece['render_y_start']

    def _update_selection_from_pointer(self):
        if not self.selection_action:
            return
        vadj = self.scrolled_window.get_vadjustment()
        res = self.scrolled_window.translate_coordinates(self.parent_overlay, 0, 0)
        if not res or not vadj: return
        # 逻辑px
        viewport_top = res[1]
        draw_y = vadj.get_value() + self.current_pointer[1] - viewport_top
        min_y = self.initial_y_offset
        max_y = self.initial_y_offset + self.drawing_area_height
        clamped_drawing_y = max(min_y, min(draw_y, max_y))
        # 缓冲区px
        render_plan_y = self._drawing_y_to_render_y(clamped_drawing_y)
        self.selection_moving_y = round(self._render_y_to_absolute_y(render_plan_y))
        GLib.idle_add(self.drawing_area.queue_draw)

    def _get_hovered_resize_handle(self, y):
        # 缓冲区px
        if self.selection_action:
            return None
        y_top_abs, y_bottom_abs, is_valid = self._get_selection_absolute_bounds()
        if not is_valid:
            return None
        y_top_render = self._absolute_y_to_render_y(y_top_abs)
        y_bottom_render = self._absolute_y_to_render_y(y_bottom_abs)
        monitor_scale = self.parent_overlay.session.scale
        handle_size_render = (self.config.PREVIEW_RESIZE_HANDLE_SIZE / self.effective_scale_factor) * monitor_scale # 屏幕逻辑px -> 原逻辑px -> 缓冲区px
        render_y_at_mouse = self._drawing_y_to_render_y(y)
        dist_top = abs(render_y_at_mouse - y_top_render)
        dist_bottom = abs(render_y_at_mouse - y_bottom_render)
        if dist_top < handle_size_render or dist_bottom < handle_size_render:
            return 'top' if dist_top < dist_bottom else 'bottom'
        return None

    def _on_start_selection_mode(self, button):
        if self.is_selection_mode:
            return
        self.is_selection_mode = True
        if self.drag_start_pos is not None:
            self.drag_start_pos = None
            self.drag_start_scroll = None
        self.drawing_area.get_window().set_cursor(self.cursors['crosshair'])
        self._update_button_sensitivity()
        self.drawing_area.queue_draw()

    def cancel_selection_mode(self):
        if not self.is_selection_mode:
            return
        self.is_selection_mode = False
        self.selection_anchor_y = None
        self.selection_moving_y = None
        self.selection_action = None
        self._stop_autoscroll()
        self.drawing_area.get_window().set_cursor(self.cursors['default'])
        self._update_button_sensitivity()
        self.drawing_area.queue_draw()

    def _on_delete_clicked(self, button):
        # 缓冲区px
        y_start_abs, y_end_abs, is_valid = self._get_selection_absolute_bounds()
        if not is_valid:
            logging.warning("删除操作已取消：当前选区无效")
            return
        mod = {'type': 'delete', 'y_start_abs': y_start_abs, 'y_end_abs': y_end_abs}
        if self.model.modifications and self.model.modifications[-1] == mod:
            logging.debug("'delete' 修改重复，跳过添加")
            return
        self.model.add_modification(mod)
        self.drawing_area.queue_draw()

    def _on_restore_clicked(self, button):
        # 缓冲区px
        sel_start_abs, sel_end_abs, is_valid = self._get_selection_absolute_bounds()
        if not is_valid:
            logging.warning("恢复操作已取消：当前选区无效")
            return
        mods_added = 0
        restored_seams_indices = {mod['seam_index'] for mod in self.model.modifications if mod['type'] == 'restore'}
        for i, entry in enumerate(self.model.entries[:-1]):
            next_entry = self.model.entries[i+1]
            seam_abs_y = next_entry['absolute_y_start']
            if sel_start_abs <= seam_abs_y < sel_end_abs:
                if i in restored_seams_indices:
                    logging.debug(f"接缝 {i} 已被恢复，跳过")
                    continue
                if any(del_start <= seam_abs_y < del_end for del_start, del_end in self.model.merged_del_regions):
                    logging.debug(f"接缝 {i} 位于已删除区域内，跳过恢复操作")
                    continue
                logging.debug(f"选区跨越接缝 {i} (abs_y: {seam_abs_y})，添加恢复修改")
                mod = {'type': 'restore', 'seam_index': i}
                self.model.add_modification(mod)
                mods_added += 1
        if mods_added > 0:
            logging.debug(f"已为 {mods_added} 个接缝添加了恢复操作")
        else:
            logging.debug("选区内未发现可恢复的接缝")

    def _get_color(self, name):
        context = self.drawing_area.get_style_context()
        success, color = context.lookup_color(name)
        if success:
            return (color.red, color.green, color.blue, color.alpha)
        default_str = self.config.get_default_css_color('preview_panel', name, "#000000")
        parsed = Gdk.RGBA()
        if parsed.parse(default_str):
            return (parsed.red, parsed.green, parsed.blue, parsed.alpha)
        return (0.0, 0.0, 0.0, 1.0)

    def on_draw(self, widget, cr):
        # 逻辑px
        widget_width = widget.get_allocated_width()
        widget_height = widget.get_allocated_height()
        bg_color = self._get_color('preview_bg')
        cr.set_source_rgba(*bg_color)
        cr.paint()
        if not self.model.render_plan:
            text_color = self._get_color('preview_text')
            cr.set_source_rgba(*text_color)
            layout = PangoCairo.create_layout(cr)
            layout.set_font_description(Pango.FontDescription("Sans 24"))
            layout.set_text("暂无截图", -1)
            text_width, text_height = layout.get_pixel_size()
            x = (widget_width - text_width) // 2
            y = (widget_height - text_height) // 2
            cr.move_to(x, y)
            PangoCairo.show_layout(cr, layout)
            return
        monitor_scale = self.parent_overlay.session.scale
        draw_area_w = self.drawing_area_width
        draw_area_h = self.drawing_area_height
        draw_x_offset = (widget_width - draw_area_w) // 2 if widget_width > draw_area_w else 0
        cr.translate(draw_x_offset, self.initial_y_offset)
        final_scale = self.effective_scale_factor / monitor_scale
        cr.scale(final_scale, final_scale) # 缓冲区px -> 原逻辑px -> 屏幕逻辑px
        # 缓冲区px
        clip_x1, visible_y1_widget, clip_x2, visible_y2_widget = cr.clip_extents()
        visible_y1_model, visible_y2_model = cr.clip_extents()[1::2]
        model_y_positions = [p['render_y_start'] for p in self.model.render_plan]
        first_index = max(0, bisect.bisect_right(model_y_positions, visible_y1_model) - 1)
        current_roi_set = set()
        preload_count = max(2, self.config.PREVIEW_CACHE_SIZE // 5)
        current_loop_index = first_index
        while current_loop_index < len(self.model.render_plan):
            piece = self.model.render_plan[current_loop_index]
            dest_y = piece['render_y_start']
            if dest_y >= visible_y2_model:
                break
            current_roi_set.add(piece['filepath'])
            current_loop_index += 1
        preload_indices = []
        if self.scroll_dy > 0:
            start_preload = current_loop_index
            preload_indices.extend(range(start_preload, min(len(self.model.render_plan), start_preload + preload_count)))
        elif self.scroll_dy < 0:
            end_preload = first_index
            preload_indices.extend(range(max(0, end_preload - preload_count), end_preload))
        else:
            half = max(1, preload_count // 2)
            preload_indices.extend(range(max(0, first_index - half), first_index))
            preload_indices.extend(range(current_loop_index, min(len(self.model.render_plan), current_loop_index + half)))
        for idx in preload_indices:
            current_roi_set.add(self.model.render_plan[idx]['filepath'])
        if current_roi_set != self.last_roi_set:
            self.model.update_roi(current_roi_set)
            self.last_roi_set = current_roi_set
        for i in range(first_index, len(self.model.render_plan)):
            piece = self.model.render_plan[i]
            filepath = piece.get('filepath')
            entry_index = piece.get('entry_index')
            src_y = piece.get('src_y', 0)
            src_height = piece.get('height', 0)
            dest_y = model_y_positions[i]
            dest_h = src_height
            if dest_y >= visible_y2_model:
                break
            if dest_y + dest_h <= visible_y1_model:
                continue
            bundle = self.model.request_image(filepath)
            if not bundle:
                entry = self.model.entries[entry_index] if 0 <= entry_index < len(self.model.entries) else None
                thumb_bundle = entry.get('thumb') if entry else None
                if thumb_bundle:
                    thumb_surface, _ = thumb_bundle
                    cr.save()
                    cr.set_antialias(cairo.ANTIALIAS_NONE)
                    cr.translate(0, dest_y)
                    orig_h = entry['height']
                    thumb_h = thumb_surface.get_height()
                    thumb_w = thumb_surface.get_width()
                    scale_y = orig_h / thumb_h if thumb_h > 0 else 1
                    scale_x = self.model.image_width / thumb_w if thumb_w > 0 else 1
                    cr.scale(scale_x, scale_y)
                    t_src_y = src_y / scale_y
                    cr.set_source_surface(thumb_surface, 0, -t_src_y)
                    cr.get_source().set_extend(cairo.EXTEND_PAD)
                    cr.rectangle(0, 0, thumb_w, src_height / scale_y)
                    cr.fill()
                    cr.restore()
                continue
            surface, _ = bundle
            original_width = surface.get_width()
            cr.save()
            try:
                cr.translate(0, dest_y)
                cr.set_source_surface(surface, 0, -src_y)
                cr.get_source().set_extend(cairo.EXTEND_PAD)
                cr.rectangle(0, 0, original_width, src_height + 1.0 / final_scale)
                cr.fill()
            except Exception as e:
                logging.error(f"绘制 surface {Path(filepath).name} 时出错: {e}")
            finally:
                cr.restore()
        # 如果在选择模式下，绘制蒙版和选框
        current_scale = final_scale
        if self.is_selection_mode:
            mask_color = self._get_color('preview_mask')
            cr.set_source_rgba(*mask_color)
            total_draw_y_start = 0
            total_draw_height = self.model.total_virtual_height
            total_draw_x_start = 0
            total_draw_width = self.model.image_width
            sel_start_abs, sel_end_abs, is_valid = self._get_selection_absolute_bounds()
            if not is_valid:
                cr.rectangle(total_draw_x_start, total_draw_y_start, total_draw_width, total_draw_height)
                cr.fill()
            else:
                sel_start_render = self._absolute_y_to_render_y(sel_start_abs)
                sel_end_render = self._absolute_y_to_render_y(sel_end_abs)
                height_above = max(0, sel_start_render - total_draw_y_start)
                if height_above >= 1:
                    cr.rectangle(total_draw_x_start, total_draw_y_start, total_draw_width, height_above)
                    cr.fill()
                height_below = max(0, (total_draw_y_start + total_draw_height) - sel_end_render)
                if height_below >= 1:
                    cr.rectangle(total_draw_x_start, sel_end_render, total_draw_width, height_below)
                    cr.fill()
                sel_h_render = abs(sel_end_render - sel_start_render)
                if sel_h_render >= 1:
                    cr.set_line_width(2.0 / current_scale)
                    if self.selection_action == 'draw':
                        drawing_border_color = self._get_color('preview_drawing_border')
                        cr.set_source_rgba(*drawing_border_color)
                        cr.set_dash([6.0 / current_scale, 4.0 / current_scale])
                    else:
                        static_border_color = self._get_color('preview_static_border')
                        cr.set_source_rgba(*static_border_color)
                        cr.set_dash([])
                    cr.rectangle(total_draw_x_start, sel_start_render, total_draw_width, sel_h_render)
                    cr.stroke()
                    cr.set_dash([])
                elif sel_h_render < 1 and not self.selection_action:
                    cr.set_line_width(3.0 / current_scale)
                    del_line_color = self._get_color('preview_delete_line')
                    cr.set_source_rgba(*del_line_color)
                    cr.set_dash([8.0 / current_scale, 6.0 / current_scale])
                    seam_y_render = sel_start_render
                    cr.move_to(total_draw_x_start, seam_y_render)
                    cr.line_to(total_draw_x_start + total_draw_width, seam_y_render)
                    cr.stroke()
                    cr.set_dash([])
                restored_seam_indices = {mod['seam_index'] for mod in self.model.modifications if mod['type'] == 'restore'}
                cr.set_line_width(3.0 / current_scale)
                cr.set_dash([8.0 / current_scale, 6.0 / current_scale])
                unmatched_color = self._get_color('preview_unmatched_seam')
                matched_color = self._get_color('preview_matched_seam')
                for i, entry in enumerate(self.model.entries[:-1]):
                    next_entry = self.model.entries[i+1]
                    seam_start_abs = next_entry['absolute_y_start']
                    if sel_start_abs <= seam_start_abs < sel_end_abs:
                        if any(del_start <= seam_start_abs < del_end for del_start, del_end in self.model.merged_del_regions):
                            continue
                        has_cropping = (entry['crop_bottom'] < entry['height']) or (next_entry['crop_top'] > 0)
                        if (not has_cropping) or (i in restored_seam_indices):
                            cr.set_source_rgba(*unmatched_color)
                        else:
                            cr.set_source_rgba(*matched_color)
                        seam_y_render = self._absolute_y_to_render_y(seam_start_abs)
                        cr.move_to(total_draw_x_start, seam_y_render)
                        cr.line_to(total_draw_x_start + total_draw_width, seam_y_render)
                        cr.stroke()
                cr.set_dash([])

    def _on_drawing_area_button_press(self, widget, event):
        if event.button == 1:
            if self.is_selection_mode:
                # 缓冲区px
                render_plan_y = self._drawing_y_to_render_y(event.y)
                absolute_model_y = round(self._render_y_to_absolute_y(render_plan_y))
                handle = self._get_hovered_resize_handle(event.y)
                if handle:
                    self.selection_action = 'resize'
                    y_top_abs, y_bottom_abs, _ = self._get_selection_absolute_bounds()
                    self.selection_moving_y = absolute_model_y
                    self.selection_anchor_y = y_bottom_abs if handle == 'top' else y_top_abs
                    self.drawing_area.get_window().set_cursor(self.cursors['ns-resize'])
                else:
                    self.selection_action = 'draw'
                    self.selection_anchor_y = absolute_model_y
                    self.selection_moving_y = absolute_model_y
                    self.drawing_area.get_window().set_cursor(self.cursors['grabbing'])
                    self.drawing_area.queue_draw()
                return True
            # 逻辑px
            hadj = self.scrolled_window.get_hadjustment()
            vadj = self.scrolled_window.get_vadjustment()
            can_scroll_h = hadj and hadj.get_upper() > hadj.get_page_size()
            can_scroll_v = vadj and vadj.get_upper() > vadj.get_page_size()
            if can_scroll_h or can_scroll_v:
                self.drag_start_pos = self.parent_overlay.controller.scroll_manager.get_pointer_position(target=CoordSys.WINDOW)
                self.drag_start_scroll = (hadj.get_value() if hadj else 0, vadj.get_value() if vadj else 0)
                self.drawing_area.get_window().set_cursor(self.cursors['grab'])
                return True
        return False

    def _stop_autoscroll(self):
        if self.selection_autoscroll_timer is not None:
            GLib.source_remove(self.selection_autoscroll_timer)
            self.selection_autoscroll_timer = None
        self.selection_autoscroll_velocity = 0.0

    def _check_and_trigger_autoscroll(self):
        # 逻辑px
        if not self.selection_action:
            self._stop_autoscroll()
            return
        vadj = self.scrolled_window.get_vadjustment()
        if not vadj: return
        res = self.scrolled_window.translate_coordinates(self.parent_overlay, 0, 0)
        viewport_top = res[1] if res else 0
        viewport_bottom = viewport_top + self.scrolled_window.get_allocated_height()
        velocity = 0.0
        if self.current_pointer[1] < viewport_top:
            diff = viewport_top - self.current_pointer[1]
            velocity = -(diff * self.config.PREVIEW_AUTOSCROLL_SENSITIVITY)
        elif self.current_pointer[1] > viewport_bottom:
            diff = self.current_pointer[1] - viewport_bottom
            velocity = diff * self.config.PREVIEW_AUTOSCROLL_SENSITIVITY
        current_val = vadj.get_value()
        max_val = vadj.get_upper() - vadj.get_page_size()
        min_val = vadj.get_lower()
        if (velocity > 0 and current_val >= max_val - 1.0) or (velocity < 0 and current_val <= min_val + 1.0):
            velocity = 0.0
        self.selection_autoscroll_velocity = velocity
        should_run = abs(self.selection_autoscroll_velocity) >= 1.0
        if should_run and self.selection_autoscroll_timer is None:
            logging.debug(f"启动自动滚动定时器")
            self.selection_autoscroll_timer = GLib.timeout_add(16, self._auto_scroll_selection)
        elif not should_run:
            if self.selection_autoscroll_timer is not None:
                logging.debug("速度低于阈值，停止自动滚动定时器")
            self._stop_autoscroll()

    def _auto_scroll_selection(self):
        if not self.selection_action or abs(self.selection_autoscroll_velocity) < 1.0:
            self._stop_autoscroll()
            return False
        # 逻辑px
        vadj = self.scrolled_window.get_vadjustment()
        current_value = vadj.get_value()
        new_value = current_value + self.selection_autoscroll_velocity
        lower = vadj.get_lower()
        upper = vadj.get_upper() - vadj.get_page_size()
        new_value_clamped = max(lower, min(new_value, upper))
        if abs(new_value_clamped - current_value) > 1e-3:
            vadj.set_value(new_value_clamped)
        if abs(new_value_clamped - new_value) > 1e-3:
            logging.debug("自动滚动到达边缘，定时器停止")
            self._stop_autoscroll()
            return False
        return True

    def _on_drawing_area_motion_notify(self, widget, event):
        self.current_pointer = self.parent_overlay.controller.scroll_manager.get_pointer_position(target=CoordSys.WINDOW)
        if self.selection_action:
            self._update_selection_from_pointer()
            self._check_and_trigger_autoscroll()
            return True
        if self.is_selection_mode:
            handle = self._get_hovered_resize_handle(event.y)
            if handle:
                self.drawing_area.get_window().set_cursor(self.cursors['ns-resize'])
            else:
                self.drawing_area.get_window().set_cursor(self.cursors['crosshair'])
            return True
        if self.drag_start_pos is not None:
            # 逻辑px
            hadj = self.scrolled_window.get_hadjustment()
            vadj = self.scrolled_window.get_vadjustment()
            delta_x = self.current_pointer[0] - self.drag_start_pos[0]
            delta_y = self.current_pointer[1] - self.drag_start_pos[1]
            if hadj:
                new_h_value = self.drag_start_scroll[0] - (delta_x * self.config.PREVIEW_DRAG_SENSITIVITY)
                new_h_value_clamped = max(hadj.get_lower(), min(new_h_value, hadj.get_upper() - hadj.get_page_size()))
                hadj.set_value(new_h_value_clamped)
            if vadj:
                new_v_value = self.drag_start_scroll[1] - (delta_y * self.config.PREVIEW_DRAG_SENSITIVITY)
                new_v_value_clamped = max(vadj.get_lower(), min(new_v_value, vadj.get_upper() - vadj.get_page_size()))
                vadj.set_value(new_v_value_clamped)
            self.drawing_area.get_window().set_cursor(self.cursors['grabbing'])
            return True
        return False

    def _on_drawing_area_button_release(self, widget, event):
        if event.button == 1:
            if self.selection_autoscroll_timer is not None:
                logging.debug("鼠标释放，停止自动滚动")
            self._stop_autoscroll()
            if self.selection_action:
                _, _, is_valid = self._get_selection_absolute_bounds()
                if not is_valid:
                    self.selection_anchor_y = None
                    self.selection_moving_y = None
                self.selection_action = None
                handle = self._get_hovered_resize_handle(event.y)
                if handle:
                    self.drawing_area.get_window().set_cursor(self.cursors['ns-resize'])
                else:
                    self.drawing_area.get_window().set_cursor(self.cursors['crosshair'])
                self._update_button_sensitivity()
                GLib.idle_add(self.drawing_area.queue_draw)
                return True
            if self.drag_start_pos is not None:
                self.drag_start_pos = None
                self.drag_start_scroll = None
                self.drawing_area.get_window().set_cursor(self.cursors['default'])
                return True
        return False

class CoordinatePatternWidget(Gtk.DrawingArea):
    """用于窗口坐标校准的控件，绘制特定的点阵图案"""
    # 逻辑px
    def __init__(self):
        super().__init__()
        self.pixel_scale = 2
        self.padding = 12
        self.bg_color_gray = 0.15
        self.bitmap = self._generate_bitmap()
        rows, cols = self.bitmap.shape
        self.content_w = cols * self.pixel_scale
        self.content_h = rows * self.pixel_scale
        self.set_size_request(self.content_w + self.padding * 2, self.content_h + self.padding * 2)

    def _generate_bitmap(self):
        # 校准用的 HZK16 点阵数据 (拼, 长, 图)
        CALIBRATION_BYTES_PIN = b'\x12\x08\x11\x18\x10\xa0\x13\xfc\xfd\x10\x11\x10\x15\x10\x19\x147\xfe\xd1\x10\x11\x10\x11\x10\x11\x10\x11\x10R\x10$\x10'
        CALIBRATION_BYTES_CHANG = b'\x08\x00\x08\x10\x080\x08@\x08\x80\t\x00\x08\x04\xff\xfe\t\x00\t\x00\x08\x80\x08@\x08 \t\x1c\x0e\x08\x08\x00'
        CALIBRATION_BYTES_TU = b'\x00\x04\x7f\xfeD\x04G\xe4LDR\x84A\x04B\x84FDI<p\x94F\x04A\x04@\x84\x7f\xfc@\x04'
        data_list = [CALIBRATION_BYTES_PIN, CALIBRATION_BYTES_CHANG, CALIBRATION_BYTES_TU]
        char_h = 16
        char_w = 16
        total_w = len(data_list) * char_w
        bytes_per_row = char_w // 8
        bitmap = np.zeros((char_h, total_w), dtype=bool)
        for char_idx, char_bytes in enumerate(data_list):
            x_offset = char_idx * char_w
            for row in range(char_h):
                for byte_idx in range(bytes_per_row):
                    current_byte = char_bytes[row * bytes_per_row + byte_idx]
                    for bit in range(8):
                        if current_byte & (0x80 >> bit):
                            bitmap[row, x_offset + byte_idx * 8 + bit] = True
        return bitmap

    def do_draw(self, cr):
        w = self.get_allocated_width()
        h = self.get_allocated_height()
        cr.set_source_rgb(self.bg_color_gray, self.bg_color_gray, self.bg_color_gray)
        radius = 8
        degrees = math.pi / 180.0
        cr.new_sub_path()
        cr.arc(w - radius, radius, radius, -90 * degrees, 0 * degrees)
        cr.arc(w - radius, h - radius, radius, 0 * degrees, 90 * degrees)
        cr.arc(radius, h - radius, radius, 90 * degrees, 180 * degrees)
        cr.arc(radius, radius, radius, 180 * degrees, 270 * degrees)
        cr.close_path()
        cr.fill()
        cr.translate(self.padding, self.padding)
        cr.set_source_rgb(1, 1, 1)
        # 关闭抗锯齿
        cr.set_antialias(cairo.ANTIALIAS_NONE)
        rows, cols = self.bitmap.shape
        ps = self.pixel_scale
        for y in range(rows):
            for x in range(cols):
                if self.bitmap[y, x]:
                    cr.rectangle(x * ps, y * ps, ps, ps)
        cr.fill()
        return False

class CoordinateManager:
    def __init__(self, session: CaptureSession, overlay: 'CaptureOverlay', frame_grabber: FrameGrabber):
        self.session = session
        self.overlay = overlay
        self.frame_grabber = frame_grabber
        self.is_running = False
        self.is_calibration_done = False

    def get_screen_geometry(self) -> Gdk.Rectangle:
        """获取覆盖层应在的显示器的几何信息"""
        # 逻辑px全局坐标
        window = self.overlay
        display = Gdk.Display.get_default()
        if window.get_window() and window.get_window().is_visible():
            monitor = display.get_monitor_at_window(window.get_window())
            if monitor:
                return monitor.get_geometry()
        monitor = display.get_primary_monitor()
        if monitor:
            return monitor.get_geometry()
        logging.warning("无法确定显示器，将使用 1920x1080 作为回退")
        rect = Gdk.Rectangle()
        rect.x = 0
        rect.y = 0
        rect.width = 1920
        rect.height = 1080
        return rect

    def get_all_monitors_geometry(self):
        """获取所有显示器的合并全局几何范围"""
        display = Gdk.Display.get_default()
        n_monitors = display.get_n_monitors()
        min_x, min_y = 0, 0
        max_x, max_y = 0, 0
        for i in range(n_monitors):
            monitor = display.get_monitor(i)
            if monitor:
                geo = monitor.get_geometry()
                if i == 0:
                    min_x, min_y = geo.x, geo.y
                    max_x, max_y = geo.x + geo.width, geo.y + geo.height
                else:
                    min_x = min(min_x, geo.x)
                    min_y = min(min_y, geo.y)
                    max_x = max(max_x, geo.x + geo.width)
                    max_y = max(max_y, geo.y + geo.height)
        return min_x, min_y, max_x, max_y

    def initialize_screen_config(self):
        logging.debug("正在初始化屏幕几何信息...")
        # 逻辑px全局坐标
        screen_rect = self.get_screen_geometry()
        rect_w = screen_rect.width
        rect_h = screen_rect.height
        logging.debug(f"当前屏幕宽度 {rect_w} 逻辑px，高度 {rect_h} 逻辑px")
        scale = self.overlay.get_scale_factor()
        if IS_WAYLAND:
            logging.info("尝试通过视频流校准真实缩放比例...")
            try:
                detected_buf_w, detected_buf_h = self.frame_grabber.wait_for_valid_frame(timeout=2.0) # 缓冲区px
                logging.debug(f"当前屏幕宽度 {detected_buf_w} 缓冲区px，高度 {detected_buf_h} 缓冲区px")
                if rect_w > 0:
                    scale = detected_buf_w / rect_w
                    logging.info(f"校准成功: 真实 scale {scale:.3f}")
                else:
                    logging.warning("校准失败: 宽度无效，保持默认 scale")
            except Exception as e:
                logging.warning(f"校准失败: {e}")
                GLib.idle_add(send_notification, "Wayland 录制初始化异常", f"无法获取屏幕画面，截图功能不可用\n{e}", "critical", "dialog-error")
                self.is_calibration_done = True
        self.session.set_screen_config(rect=screen_rect, scale=scale)
        return screen_rect, scale

    def map_point(self, x, y, source: CoordSys, target: CoordSys):
        # x, y: 逻辑px
        if source == target:
            return x, y
        rect = self.session.screen_rect
        mon_origin_x = rect.x if rect else 0
        mon_origin_y = rect.y if rect else 0
        win_origin_x = mon_origin_x + self.session.monitor_offset_x
        win_origin_y = mon_origin_y + self.session.monitor_offset_y
        gx, gy = x, y
        if source == CoordSys.WINDOW:
            gx, gy = x + win_origin_x, y + win_origin_y
        elif source == CoordSys.MONITOR:
            gx, gy = x + mon_origin_x, y + mon_origin_y
        if target == CoordSys.GLOBAL:
            return gx, gy
        elif target == CoordSys.MONITOR:
            return gx - mon_origin_x, gy - mon_origin_y
        elif target == CoordSys.WINDOW:
            return gx - win_origin_x, gy - win_origin_y

    def get_valid_screen_size(self):
        # 逻辑px
        rect = self.session.screen_rect
        if rect:
            screen_w, screen_h = rect.width, rect.height
        else:
            screen_w, screen_h = self.overlay.get_allocated_width(), self.overlay.get_allocated_height()
        return self.map_point(screen_w, screen_h, source=CoordSys.MONITOR, target=CoordSys.WINDOW)

    def calibrate_offsets(self):
        if self.is_running: return
        self.is_running = True
        logging.info("启动窗口坐标校准...")
        cal_widget = CoordinatePatternWidget()
        target_log_x = 30
        target_log_y = 30
        self.overlay.overlay_manager.show(cal_widget, anchor=(target_log_x, target_log_y), layer=OverlayManager.LAYER_TOP, mask=False)
        while Gtk.events_pending():
            Gtk.main_iteration()
        threading.Thread(target=self._run_calibration_thread, args=(cal_widget, target_log_x, target_log_y), daemon=True).start()

    def _run_calibration_thread(self, widget, log_x, log_y):
        # 显示器坐标
        time.sleep(0.6)
        full_img = None
        current_scale = self.session.scale
        temp_path = config.TEMP_DIRECTORY / "cal_temp.png"
        rect = self.session.screen_rect
        event = threading.Event()
        success = [False]
        def main_thread_capture():
            try:
                cap_x, cap_y = self.map_point(0, 0, source=CoordSys.MONITOR, target=self.frame_grabber.target_coords)
                success[0] = self.frame_grabber.capture(cap_x, cap_y, rect.width, rect.height, temp_path, scale=current_scale, include_cursor=False)
            except Exception as e:
                logging.error(f"坐标校准截图调用失败: {e}")
            finally:
                event.set()
            return False
        GLib.idle_add(main_thread_capture)
        event.wait()
        if success[0] and temp_path.exists():
            full_img = cv2.imread(str(temp_path))
            try: os.remove(temp_path)
            except: pass
        if full_img is None:
            logging.warning("坐标校准失败: 无法获取屏幕帧")
            self._finish(widget)
            return
        bitmap = widget.bitmap
        rows, cols = bitmap.shape
        logic_w = cols * widget.pixel_scale
        logic_h = rows * widget.pixel_scale
        bg_gray = int(widget.bg_color_gray * 255)
        template = np.full((logic_h, logic_w), bg_gray, dtype=np.uint8)
        for r in range(rows):
            for c in range(cols):
                if bitmap[r, c]:
                    template[r*widget.pixel_scale : (r+1)*widget.pixel_scale, c*widget.pixel_scale : (c+1)*widget.pixel_scale] = 255
        if current_scale != 1.0:
            template = cv2.resize(template, None, fx=current_scale, fy=current_scale, interpolation=cv2.INTER_NEAREST) # 逻辑px -> 缓冲区px
        screen_gray = cv2.cvtColor(full_img, cv2.COLOR_BGR2GRAY)
        # 缓冲区px
        screen_h, screen_w = screen_gray.shape
        template_h, template_w = template.shape
        # 逻辑px -> 缓冲区px
        expected_x_buf = math.ceil((log_x + widget.padding) * current_scale)
        expected_y_buf = math.ceil((log_y + widget.padding) * current_scale)
        margin = 80
        pred_x1 = max(0, int(expected_x_buf - margin))
        pred_y1 = max(0, int(expected_y_buf - margin))
        pred_x2 = min(screen_w, int(expected_x_buf + template_w + margin))
        pred_y2 = min(screen_h, int(expected_y_buf + template_h + margin))
        search_regions = [
            ("范围搜索", (pred_y1, pred_y2, pred_x1, pred_x2)),
            ("左上搜索", (0, screen_h // 2, 0, screen_w // 2)),
            ("全屏搜索", (0, screen_h, 0, screen_w))
        ]
        final_max_val = -1.0
        final_match_loc = None
        for name, (y1, y2, x1, x2) in search_regions:
            if (x2 - x1) < template_w or (y2 - y1) < template_h:
                continue
            roi = screen_gray[y1:y2, x1:x2]
            res = cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
            if max_val > final_max_val:
                final_max_val = max_val
            if max_val > 0.90:
                logging.debug(f"在 [{name}] 阶段找到匹配，相似度 {max_val:.3f}")
                final_match_loc = (x1 + max_loc[0], y1 + max_loc[1])
                break
        offset_result = None
        if final_max_val > 0.90 and final_match_loc is not None:
            # 缓冲区px
            found_x, found_y = final_match_loc
            # 缓冲区px -> 逻辑px
            offset_x = (found_x - expected_x_buf) / current_scale
            offset_y = (found_y - expected_y_buf) / current_scale
            logging.info(f"窗口坐标校准完成: offset=({offset_x:.1f}, {offset_y:.1f}) 逻辑px")
            offset_result = (offset_x, offset_y)
        else:
            logging.warning(f"窗口坐标校准失败: 未找到匹配图案 (最高相似度={final_max_val:.3f})")
        GLib.idle_add(self._apply_calibration_result, offset_result)
        self._finish(widget)

    def _apply_calibration_result(self, offset_result):
        self.is_calibration_done = True
        if offset_result is not None:
            self.session.set_screen_config(offset_x=offset_result[0], offset_y=offset_result[1])
        self.overlay.update_layout()
        self.overlay.canvas.queue_draw()

    def _finish(self, widget):
        self.is_running = False
        GLib.idle_add(self.overlay.overlay_manager.dismiss, widget)

class OverlayManager:
    LAYER_BASE = 0
    LAYER_LOW = 10
    LAYER_MEDIUM = 20
    LAYER_MEDIUM_UP = 25
    LAYER_HIGH = 30
    LAYER_TOP = 40

    def __init__(self, overlay_window):
        self.overlay = overlay_window
        self.overlay_container = overlay_window.overlay_container
        self.overlay_container.connect("get-child-position", self._on_get_child_position)
        self.widget_positions = {}
        self.active_widgets = []
        self.widget_timers = {}
        self.widget_gestures = {}
        self.blocking_widget = None
        self.is_mask_visual = False
        self.mask_layer = self._create_mask()
        self._xlib_disp = None

    def _on_get_child_position(self, overlay, widget, allocation):
        pos = self.widget_positions.get(widget)
        if pos is None: return False
        allocation.x = int(pos[0])
        allocation.y = int(pos[1])
        req, _ = widget.get_preferred_size()
        allocation.width = req.width
        allocation.height = req.height
        return True

    def _create_mask(self):
        mask = Gtk.EventBox()
        mask.set_visible_window(True)
        screen = mask.get_screen()
        visual = screen.get_rgba_visual()
        if visual and screen.is_composited():
            mask.set_visual(visual)
        mask.set_app_paintable(True)
        mask.connect("draw", self._on_draw_mask)
        mask.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK)
        mask.connect("button-press-event", self._on_mask_press)
        mask.connect("notify::visible", lambda w, p: self.overlay.update_input_shape())
        mask.connect("realize", lambda w: w.get_window().set_cursor(self.overlay.cursors['default']))
        self.overlay_container.add_overlay(mask)
        self.widget_positions[mask] = (0, 0)
        return mask

    def _on_mask_press(self, widget, event):
        if self.blocking_widget and self.blocking_widget.get_visible():
            SystemInteraction.play_sound(config.WARNING_SOUND)
            self._trigger_shake(self.blocking_widget)
        return True

    def _trigger_shake(self, widget):
        if getattr(widget, 'is_animating', False): return
        widget.is_animating = True
        start_x, start_y = self.get_widget_position(widget)
        start_time = time.monotonic()
        duration = 0.4
        amplitude = 10
        def frame():
            elapsed = time.monotonic() - start_time
            if elapsed >= duration:
                self.move_widget(widget, start_x, start_y)
                widget.is_animating = False
                return False
            progress = elapsed / duration
            offset_x = amplitude * math.sin(progress * 6 * math.pi) * (1 - progress)
            self.move_widget(widget, start_x + offset_x, start_y)
            return True
        GLib.timeout_add(16, frame)

    def _on_draw_mask(self, widget, cr):
        if not self.is_mask_visual:
            return False
        context = widget.get_style_context()
        context.add_class("mask-layer")
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        Gtk.render_background(context, cr, 0, 0, width, height)
        context.remove_class("mask-layer")
        return True

    def move_widget(self, widget, x, y):
        self.widget_positions[widget] = (int(x), int(y))
        widget.queue_resize()

    def get_widget_position(self, widget):
        return self.widget_positions.get(widget, (0, 0))

    def add_managed_widget(self, widget, layer=LAYER_BASE):
        if widget in [item['widget'] for item in self.active_widgets]:
            return
        if widget.get_parent() != self.overlay_container:
            self.overlay_container.add_overlay(widget)
            self.widget_positions[widget] = (0, 0)
        self.active_widgets.append({'widget': widget, 'layer': layer, 'mask': False})
        gesture = Gtk.GestureMultiPress.new(widget)
        gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        gesture.connect("pressed", lambda g, n, x, y, w=widget: self.bring_to_front(w))
        self.widget_gestures[widget] = gesture
        widget.connect("notify::visible", lambda w, p: (self.overlay.update_input_shape(), self._update_z_order()))
        widget.connect("size-allocate", lambda w, a: self.overlay.update_input_shape())
        self._update_z_order()

    def bring_to_front(self, widget):
        for item in self.active_widgets:
            if item['widget'] == widget:
                layer_items = [i for i in self.active_widgets if i['layer'] == item['layer']]
                if layer_items and layer_items[-1] == item:
                    return
                self.active_widgets.remove(item)
                self.active_widgets.append(item)
                self._update_z_order()
                break

    def show(self, widget, anchor='center', layer=LAYER_HIGH, mask=False, auto_dismiss=0):
        for item in self.active_widgets:
            if item['widget'] == widget:
                item['layer'] = layer
                item['mask'] = mask
                break
        else:
            self.overlay_container.add_overlay(widget)
            self.widget_positions[widget] = (0, 0)
            self.active_widgets.append({'widget': widget, 'layer': layer, 'mask': mask})
        widget.show_all()
        if anchor is not None:
            self._apply_anchor(widget, anchor)
        self.bring_to_front(widget)
        self._update_z_order()
        if auto_dismiss > 0:
            timer_id = GLib.timeout_add_seconds(auto_dismiss, self.dismiss, widget)
            self.widget_timers[widget] = timer_id
        return None

    def set_blocking(self, widget, blocking):
        if blocking:
            self.blocking_widget = widget
        else:
            if self.blocking_widget == widget:
                self.blocking_widget = None
        self._update_z_order()

    def run_modal(self, widget, anchor='center', layer=LAYER_HIGH, mask=True):
        self.show(widget, anchor=anchor, layer=layer, mask=mask)
        self.set_blocking(widget, True)
        widget.show_timestamp = time.time()
        is_dialog = isinstance(widget, EmbeddedDialog)
        if is_dialog:
            self.overlay.session.push_context(Context.DIALOG)
        loop = GLib.MainLoop()
        result_container = {'response': Gtk.ResponseType.NONE, 'data': None}
        def on_response(dialog, response_id):
            result_container['response'] = response_id
            if hasattr(dialog, 'get_result'):
                result_container['data'] = dialog.get_result()
            loop.quit()
        handler_id = widget.connect('response', on_response)
        try:
            loop.run()
        except Exception as e:
            logging.error(f"阻塞循环异常: {e}")
        finally:
            self.blocking_widget = None
            if is_dialog:
                self.overlay.session.pop_context(Context.DIALOG)
        if widget in [item['widget'] for item in self.active_widgets]:
            if widget.handler_is_connected(handler_id):
                widget.disconnect(handler_id)
            self.dismiss(widget)
        return result_container['response'], result_container['data']

    def dismiss(self, widget):
        if widget in self.widget_timers:
            GLib.source_remove(self.widget_timers[widget])
            del self.widget_timers[widget]
        if widget in self.widget_gestures:
            del self.widget_gestures[widget]
        for item in self.active_widgets:
            if item['widget'] == widget:
                self.overlay_container.remove(widget)
                self.widget_positions.pop(widget, None)
                self.active_widgets.remove(item)
                widget.destroy()
                break
        self._update_z_order()
        return False

    def dismiss_by_type(self, widget_type):
        dismissed = False
        for item in self.active_widgets[:]:
            if isinstance(item['widget'], widget_type):
                self.dismiss(item['widget'])
                dismissed = True
        return dismissed

    def _update_z_order(self):
        target_mask_layer = -1
        self.is_mask_visual = False
        for item in self.active_widgets:
            if item['widget'].get_visible() and item['mask'] and item['layer'] > target_mask_layer:
                target_mask_layer = item['layer']
                self.is_mask_visual = True
        if self.blocking_widget and self.blocking_widget.get_visible():
            for item in self.active_widgets:
                if item['widget'] == self.blocking_widget:
                    if item['layer'] > target_mask_layer:
                        target_mask_layer = item['layer']
                    break
        target_order = []
        for idx, item in enumerate(self.active_widgets):
            if item['widget'].get_visible():
                sort_layer = float(item['layer'])
                if item['layer'] == target_mask_layer:
                    if item['widget'] == self.blocking_widget or item['mask']:
                        sort_layer += 0.1
                    else:
                        sort_layer -= 0.1
                target_order.append((sort_layer, idx, item['widget']))
        if target_mask_layer > -1:
            rect = self.overlay.session.screen_rect
            w = rect.width if rect else self.overlay.get_allocated_width()
            h = rect.height if rect else self.overlay.get_allocated_height()
            self.mask_layer.set_size_request(w, h)
            self.mask_layer.show()
            self.mask_layer.queue_draw()
            target_order.append((float(target_mask_layer), -1, self.mask_layer))
        else:
            self.mask_layer.hide()
        target_order.sort(key=lambda x: (x[0], x[1]))
        for i, (_, _, widget) in enumerate(target_order):
            if widget.get_parent() == self.overlay_container:
                self.overlay_container.reorder_overlay(widget, i)
        for _, _, widget in target_order:
            if widget.get_has_window() and widget.get_window():
                widget.get_window().raise_()

    def _apply_anchor(self, widget, anchor):
        valid_w, valid_h = self.overlay.coord_manager.get_valid_screen_size()
        _, req = widget.get_preferred_size()
        ww, wh = req.width, req.height
        x, y = 0, 0 # 逻辑px窗口坐标
        if isinstance(anchor, str):
            if anchor == 'center':
                x = (valid_w - ww) // 2
                y = (valid_h - wh) // 2
            elif anchor == 'top-center':
                x = (valid_w - ww) // 2
                y = 40
            elif anchor == 'top-left':
                x = 20
                y = 20
            elif anchor == 'mouse':
                mouse_win_x, mouse_win_y = self.overlay.controller.scroll_manager.get_pointer_position(target=CoordSys.WINDOW)
                target_x = mouse_win_x + 10
                target_y = mouse_win_y + 10
                if target_x + ww > valid_w:
                    target_x = mouse_win_x - ww - 10
                if target_y + wh > valid_h:
                    target_y = mouse_win_y - wh - 10
                x = max(0, target_x)
                y = max(0, target_y)
        elif isinstance(anchor, (tuple, list)) and len(anchor) == 2:
            x, y = anchor
        self.move_widget(widget, max(0, int(x)), max(0, int(y)))

    def recalculate_input_shapes(self, base_region, base_rects):
        final_region = cairo.Region()
        if base_region:
            final_region.union(base_region)
        if self.mask_layer.get_visible():
            rect = self.overlay.session.screen_rect
            w = rect.width if rect else self.overlay.get_allocated_width()
            h = rect.height if rect else self.overlay.get_allocated_height()
            base_region.union(cairo.RectangleInt(0, 0, w, h))
            g_x, g_y = self.overlay.coord_manager.map_point(0, 0, source=CoordSys.WINDOW, target=CoordSys.GLOBAL)
            base_rects.append((g_x, g_y, w, h))
        else:
            for item in self.active_widgets:
                widget = item['widget']
                if widget.get_visible() and widget.get_window():
                    alloc = widget.get_allocation()
                    x, y = self.get_widget_position(widget)
                    base_region.union(cairo.RectangleInt(x, y, alloc.width, alloc.height))
                    g_x, g_y = self.overlay.coord_manager.map_point(x, y, source=CoordSys.WINDOW, target=CoordSys.GLOBAL)
                    base_rects.append((g_x, g_y, alloc.width, alloc.height))
        return base_region, base_rects

    def sync_wm_shape(self, cairo_region):
        if IS_WAYLAND or not self.overlay.get_window():
            return
        try:
            if self._xlib_disp is None:
                self._xlib_disp = display.Display()
            if not self._xlib_disp.has_extension('SHAPE'):
                return
            win_id = self.overlay.get_window().get_xid()
            win_obj = self._xlib_disp.create_resource_object('window', win_id)
            root = self._xlib_disp.screen().root
            current = win_obj
            wm_parents = []
            while True:
                tree = current.query_tree()
                if tree.parent == root or tree.parent == X.NONE:
                    break
                wm_parents.append(tree.parent)
                current = tree.parent
            for parent in wm_parents:
                geom = parent.translate_coords(win_obj, 0, 0)
                dx, dy = geom.x, geom.y
                offset_rects = []
                for i in range(cairo_region.num_rectangles()):
                    rect = cairo_region.get_rectangle(i)
                    offset_rects.append((rect.x + dx, rect.y + dy, rect.width, rect.height))
                parent.shape_rectangles(shape.SO.Set, shape.SK.Input, X.Unsorted, 0, 0, offset_rects,)
            self._xlib_disp.sync()
        except Exception as e:
            logging.warning(f"同步 WM 穿透形状失败: {e}")

    def dispatch_dialog_key(self, response_id):
        widget = self.blocking_widget
        if not widget: return False
        if time.time() - getattr(widget, 'show_timestamp', 0) < 0.2:
            return False
        widget.emit('response', response_id)
        return False

    def cleanup(self):
        if self._xlib_disp:
            try: self._xlib_disp.close()
            except: pass
            self._xlib_disp = None

class CaptureOverlay(Gtk.Window):
    def __init__(self, config_obj: Config, frame_grabber: FrameGrabber, log_queue: queue.Queue):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        global GLOBAL_OVERLAY
        GLOBAL_OVERLAY = self
        self.overlay_container = Gtk.Overlay()
        self.add(self.overlay_container)
        self.canvas = Gtk.DrawingArea()
        self.canvas.connect("draw", self.on_draw)
        self.overlay_container.add(self.canvas)
        self.overlay_container.show_all()
        self.cached_blocking_rects = [] # 全局坐标
        self.rects_lock = threading.Lock()
        self.session = CaptureSession()
        self.session.connect('state-changed', self._on_session_state_changed)
        self.session.connect('mode-changed', self._on_session_mode_changed)
        self.session.connect('geometry-changed', self._on_session_geometry_changed)
        self.session.connect('static-bars-changed', lambda s: self.canvas.queue_draw())
        self.session.connect('grid-config-changed', lambda s: self._update_info_display())
        self.session.connect('screen-config-changed', lambda s: (self.update_layout(), self.canvas.queue_draw()))
        self.session.push_context(Context.BASE)
        self.frame_grabber = frame_grabber
        self.controller = ActionController(self.session, self, config_obj, frame_grabber)
        self.log_queue = log_queue
        # 窗口坐标
        self.start_pos = None
        self.current_pos = None
        self.resize_edge = None
        self.drag_start_pos = None
        self.drag_start_geometry = None
        self.coord_manager = CoordinateManager(self.session, self, frame_grabber)
        self.stitch_model = self.controller.stitch_model
        self.stitch_model.connect('model-updated', self.on_model_updated_ui)
        self.show_side_panel = True
        self.show_button_panel = True
        self.side_panel_on_left = True
        self.preview_panel = None
        self.config_panel = None
        self.instruction_panel = None
        self.user_wants_instruction_panel = config_obj.SHOW_INSTRUCTION_PANEL_ON_START
        self._pending_mod_action = None
        self.last_hotkey_trigger_time = 0
        self._setup_window()
        self.css_providers = {}
        self.apply_global_styles()
        self.overlay_manager = OverlayManager(self)
        self._initialize_cursors()
        self._connect_events()
        self.create_panels()
        self._setup_config_handlers()
        config_obj.connect('setting-changed', self._on_config_changed)
        logging.info(f"覆盖层已创建")

    def _setup_window(self):
        self.set_app_paintable(True)
        visual = self.get_screen().get_rgba_visual()
        if visual and self.get_screen().is_composited():
            self.set_visual(visual)
        else:
            logging.warning("无法设置 RGBA visual，透明度可能无法工作")
        if IS_WAYLAND:
            logging.info("正在检查 GtkLayerShell 支持...")
            layer_shell_supported = True
            if not GTK_LAYER_SHELL_AVAILABLE:
                logging.warning("未找到 'gtk-layer-shell' 库")
                layer_shell_supported = False
            elif not GtkLayerShell.is_supported():
                logging.warning("当前的 Wayland 合成器不支持 'wlr-layer-shell' 协议")
                layer_shell_supported = False
            if not layer_shell_supported:
                self.set_decorated(False)
                empty_titlebar = Gtk.Fixed()
                self.set_titlebar(empty_titlebar)
                send_notification("窗口未置顶", "需手动置顶", "warning")
            else:
                logging.debug("Wayland: 正在应用 GtkLayerShell 属性...")
                GtkLayerShell.init_for_window(self)
                GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
                GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
                GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.BOTTOM, True)
                GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, True)
                GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
                GtkLayerShell.set_namespace(self, "scroll_stitch_overlay")
                protocol_version = GtkLayerShell.get_protocol_version()
                logging.debug(f"GtkLayerShell 协议版本: {protocol_version}")
                if protocol_version >= 4:
                    GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.ON_DEMAND)
                else:
                    logging.warning(f"Wayland 协议版本 {protocol_version} < 4，回退到独占键盘模式")
                    GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        else:
            self.set_decorated(False)
            self.set_keep_above(True)
            self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
            self.set_skip_taskbar_hint(True)
            self.set_skip_pager_hint(True)

    def _on_session_geometry_changed(self, session, new_geometry):
        self.update_layout()
        self.canvas.queue_draw()

    def _on_session_state_changed(self, session, key, value):
        if key == 'is_selection_done' and value is True:
            self.update_layout()
            self._update_info_display()
            self.session.pop_context(Context.SELECTING)
            GLib.timeout_add(100, self.update_layout)
            if IS_WAYLAND:
                self._trigger_wayland_layout_refresh(remaining_retries=2)
        elif key == 'is_exiting' and value is True:
            self.session.clear_context()
        elif key == 'is_finished' and value is True:
            self.session.pop_context(Context.BASE)
            self.enter_notification_mode()

    def _on_session_mode_changed(self, session, mode_str):
        self._update_info_display()
        mode = CaptureMode(mode_str)
        is_auto = (mode == CaptureMode.AUTO)
        if self.show_button_panel and self.button_panel:
            self.button_panel.update_button_state(mode)
            if not is_auto:
                can_undo = self.stitch_model.capture_count > 0
                self.button_panel.set_undo_sensitive(can_undo)
        if self.side_panel:
            self.side_panel.function_panel.update_button_state(mode)
        self.update_layout()

    def _setup_config_handlers(self):
        self.config_handler_map = {
            'border_color': self.canvas.queue_draw,
            'static_bar_color': self.canvas.queue_draw,
            'border_width': lambda: (self.update_layout(), self.canvas.queue_draw()),
            'enable_side_panel': lambda: (self.update_layout(), self._update_info_display()),
            'enable_buttons': self.update_layout,
            'side_panel_width': self._refresh_panels_and_layout,
            'button_panel_width': self._refresh_panels_and_layout,
            'enable_grid_action_buttons': lambda: (self.button_panel.update_button_state(self.session.current_mode), self.update_layout()),
            'enable_auto_scroll_buttons': lambda: (self.button_panel.update_button_state(self.session.current_mode), self.update_layout()),
            'show_capture_count': lambda: (self._update_info_display(), self.update_layout()),
            'show_total_dimensions': lambda: (self._update_info_display(), self.update_layout()),
            'show_current_mode': lambda: (self._update_info_display(), self.update_layout()),
            'capture_count_format': lambda: (self._update_info_display(), self.update_layout())
        }

    def _refresh_panels_and_layout(self):
        if self.side_panel:
            self.side_panel.info_panel.set_size_request(config.SIDE_PANEL_WIDTH, -1)
            self.side_panel.function_panel.set_size_request(config.SIDE_PANEL_WIDTH, -1)
        if self.button_panel:
            self.button_panel.set_size_request(config.BUTTON_PANEL_WIDTH, -1)
        self.update_layout()

    def reload_hotkeys(self):
        global hotkey_manager
        if hotkey_manager:
            hotkey_manager.rebuild_listener()
        if self.instruction_panel:
            self.instruction_panel.reload_keys()
            self.update_layout()

    def _on_config_changed(self, config_obj, section, key, value):
        if key.endswith('_css'):
            logging.debug(f"检测到 CSS 配置 {key} 变更，正在重新应用样式...")
            self.apply_global_styles()
            self.queue_draw()
            if self.preview_panel and self.preview_panel.get_visible():
                self.preview_panel.drawing_area.queue_draw()
            if key == 'config_panel_css' and self.config_panel:
                self.config_panel.create_log_tags()
                self.config_panel.redisplay_logs()
            return
        if section == 'Hotkeys':
            logging.debug(f"检测到热键 {key} 变更，正在重载监听器...")
            self.reload_hotkeys()
            return
        if section == 'ApplicationScrollUnits':
            if self.session.current_mode == CaptureMode.GRID and self.session.grid_app_class == key:
                if value is not None:
                    unit, enabled = config.parse_string_to_value('ApplicationScrollUnits', key, value)
                    self.session.set_grid_config(key, unit, enabled)
                    if not enabled:
                        self.controller.grid_mode_controller.snap_current_height()
                else:
                    self.session.set_grid_config(None, 0, False)
                    self.session.set_mode(CaptureMode.FREE)
                    GLib.idle_add(send_notification, "整格模式已关闭", f"'{key}' 滚动配置被移除，已恢复自由模式", "normal")
            return
        handler = self.config_handler_map.get(key)
        if handler:
            GLib.idle_add(handler)

    def on_global_focus_changed(self, window, widget):
        if widget:
            managed_widgets = [item['widget'] for item in self.overlay_manager.active_widgets]
            current = widget
            while current:
                if current in managed_widgets:
                    self.overlay_manager.bring_to_front(current)
                    break
                current = current.get_parent()
        global hotkey_manager
        if not hotkey_manager or not hotkey_manager.are_hotkeys_enabled:
            return
        if self.config_panel and getattr(self.config_panel, 'capturing_hotkey_button', None):
            hotkey_manager.set_paused(True)
            return
        input_types = (Gtk.Editable, Gtk.TextView)
        is_input_widget = isinstance(widget, input_types) if widget else False
        if is_input_widget:
            hotkey_manager.set_paused(True)
            logging.debug(f"输入控件 {type(widget).__name__} 获得焦点，暂停热键")
            if self.config_panel and self.config_panel.get_visible():
                self.config_panel.update_status_focus(True)
        else:
            hotkey_manager.set_paused(False)
            if self.config_panel and self.config_panel.get_visible():
                self.config_panel.update_status_focus(False)

    def _initialize_cursors(self):
        display = self.get_display()
        cursor_names = [
            'default', 'n-resize', 's-resize', 'w-resize', 'e-resize',
            'nw-resize', 'se-resize', 'ne-resize', 'sw-resize', 'ns-resize',
            'grab', 'grabbing', "crosshair"
        ]
        self.cursors = {}
        try:
            default_cursor = Gdk.Cursor.new_from_name(display, 'default')
        except TypeError:
            default_cursor = None
        for name in cursor_names:
            try:
                cursor = Gdk.Cursor.new_from_name(display, name)
            except TypeError:
                logging.warning(f"无法加载光标 '{name}'，使用默认光标替代")
                cursor = default_cursor
            self.cursors[name] = cursor

    def enter_notification_mode(self):
        """仅显示通知：隐藏所有面板，停止绘制选框"""
        logging.info("进入通知驻留模式")
        self.side_panel.hide()
        self.button_panel.hide()
        self.instruction_panel.hide()
        self.overlay_manager.mask_layer.hide()
        if self.preview_panel: self.preview_panel.hide()
        if self.config_panel: self.config_panel.hide()
        self.canvas.queue_draw()
        GLib.idle_add(self.update_input_shape)

    def _connect_events(self):
        self.connect("map-event", self.on_map_event)
        self.connect("set-focus", self.on_global_focus_changed)
        self.connect("size-allocate", lambda w, a: self.update_input_shape())
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK |
                        Gdk.EventMask.BUTTON_RELEASE_MASK |
                        Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect("button-press-event", self.on_button_press)
        self.connect("button-release-event", self.on_button_release)
        self.connect("motion-notify-event", self.on_motion_notify)
        self.connect("key-press-event", self.on_key_press_event)
        self.connect("key-release-event", self.on_key_release_event)
        self.connect("destroy", Gtk.main_quit)

    def apply_global_styles(self):
        screen = Gdk.Screen.get_default()
        priority = Gtk.STYLE_PROVIDER_PRIORITY_USER
        def load_css(css_data, name):
            if not css_data:
                return
            try:
                provider = Gtk.CssProvider()
                provider.load_from_data(css_data.encode('utf-8'))
                if name in self.css_providers:
                    Gtk.StyleContext.remove_provider_for_screen(screen, self.css_providers[name])
                Gtk.StyleContext.add_provider_for_screen(screen, provider, priority)
                self.css_providers[name] = provider
            except Exception as e:
                logging.warning(f"{name} CSS 语法错误，保持原样式: {e}")
        load_css(config.INFO_PANEL_CSS, "Info Panel")
        load_css(config.BUTTON_CSS, "Button Style")
        load_css(config.INSTRUCTION_PANEL_CSS, "Instruction Panel")
        load_css(config.SIMULATED_WINDOW_CSS, "Simulated Window")
        load_css(config.PREVIEW_PANEL_CSS, "Preview Panel")
        load_css(config.CONFIG_PANEL_CSS, "Config Panel")
        load_css(config.NOTIFICATION_CSS, "Notification")
        load_css(config.MASK_CSS, "Mask")
        load_css(config.DIALOG_CSS, "Dialog")
        load_css(config.FEEDBACK_WIDGET_CSS, "Feedback Widget")
        no_padding_css = """
        .no-padding { padding: 0px; }
        """.strip()
        load_css(no_padding_css, "No Padding Style")
        logging.debug("已应用所有全局 CSS 样式")

    def on_key_press_event(self, widget, event):
        if self.config_panel and self.config_panel.get_visible():
            if self.config_panel.on_key_press(widget, event):
                return True
        if isinstance(self.get_focus(), (Gtk.Editable, Gtk.TextView)):
            self._pending_mod_action = None
            return False
        if self._pending_mod_action:
            waiting_keyval, _ = self._pending_mod_action
            if event.keyval != waiting_keyval:
                self._pending_mod_action = None
        incoming_hotkey = HotkeyDefinition.from_gdk_event(event)
        global hotkey_manager
        if not hotkey_manager:
            return False
        if hotkey_manager.is_listening:
            return False
        action = hotkey_manager.get_active_action(incoming_hotkey)
        if action:
            if incoming_hotkey.is_modifier_only():
                self._pending_mod_action = (event.keyval, action)
            else:
                current_time = time.time()
                if current_time - self.last_hotkey_trigger_time >= config.HOTKEY_DEBOUNCE_TIME:
                    self.last_hotkey_trigger_time = current_time
                    action()
                self._pending_mod_action = None
            return True
        return False

    def on_key_release_event(self, widget, event):
        if self.config_panel and self.config_panel.get_visible():
            if self.config_panel.handle_key_release(widget, event):
                return True
        if self._pending_mod_action:
            waiting_keyval, action = self._pending_mod_action
            if event.keyval == waiting_keyval:
                self.last_hotkey_trigger_time = time.time()
                action()
                return True
            self._pending_mod_action = None
        return False

    def on_map_event(self, widget, event):
        if self.session.screen_rect is None:
            screen_rect, scale = self.coord_manager.initialize_screen_config()
            rect_w, rect_h = screen_rect.width, screen_rect.height
            self.controller.scroll_manager.init_devices(screen_rect, scale)
            self.session.push_context(Context.SELECTING)
            if not self.session.is_selection_done:
                self.resize(rect_w, rect_h)
                self.canvas.set_size_request(rect_w, rect_h)
                if not IS_WAYLAND:
                    self.move(screen_rect.x, screen_rect.y) # 全局坐标
        if not self.session.is_selection_done:
            self.update_layout()
            def _set_cursor_delayed():
                if self.get_window():
                    self.get_window().set_cursor(self.cursors["crosshair"])
                return False
            GLib.timeout_add(250, _set_cursor_delayed)
            if not self.coord_manager.is_calibration_done:
                self.coord_manager.calibrate_offsets()

    def on_model_updated_ui(self, model_instance):
        if self.session.is_selection_done:
            self._update_info_display()
            can_undo = model_instance.capture_count > 0 and not self.controller.is_auto_scrolling
            if self.show_button_panel:
                self.button_panel.set_undo_sensitive(can_undo)
            self.canvas.queue_draw()

    def _update_info_display(self):
        if self.show_side_panel and self.side_panel:
            mode_str = self.session.current_mode.value
            if self.session.current_mode == CaptureMode.GRID and self.session.grid_app_class:
                app_name = GLib.markup_escape_text(self.session.grid_app_class)
                mode_str += f"\n<span size='smaller'>({app_name})</span>"
            self.side_panel.info_panel.update_info(
                count=self.stitch_model.capture_count,
                width=self.stitch_model.image_width,
                height=self.stitch_model.total_virtual_height,
                mode_str=mode_str
            )

    # 窗口坐标 {
    def _calculate_preview_position(self):
        # 逻辑px
        if not self.preview_panel: return (0, 0)
        valid_screen_w, valid_screen_h = self.coord_manager.get_valid_screen_size()
        preview_w, preview_h = self.preview_panel.get_size_request()
        sel_geo = self.session.geometry
        sel_x, sel_y = sel_geo['x'], sel_geo['y']
        sel_w, sel_h = sel_geo['w'], sel_geo['h']
        cluster_left_x = sel_x
        cluster_right_x = sel_x + sel_w
        spacing = 20
        border = config.BORDER_WIDTH
        if self.show_side_panel and self.side_panel_on_left:
            cluster_left_x -= (config.SIDE_PANEL_WIDTH + border)
        right_panel_w = 0
        if self.show_button_panel:
            right_panel_w = config.BUTTON_PANEL_WIDTH
        if self.show_side_panel and not self.side_panel_on_left:
            right_panel_w = config.SIDE_PANEL_WIDTH
        if right_panel_w > 0:
            cluster_right_x += (right_panel_w + border)
        space_left = cluster_left_x - spacing
        space_right = valid_screen_w - cluster_right_x - spacing
        can_place_right = space_right >= preview_w
        can_place_left = space_left >= preview_w
        place_left = space_left > space_right
        if place_left:
            target_x = cluster_left_x - spacing - preview_w
        else:
            target_x = cluster_right_x + spacing
        target_y = sel_y - border
        if target_y + preview_h > valid_screen_h:
            overflow = (target_y + preview_h) - valid_screen_h
            target_y -= overflow
        if target_x < 0: target_x = 0
        if target_x + preview_w > valid_screen_w: target_x = valid_screen_w - preview_w
        return (int(target_x), int(target_y))

    def toggle_instruction_panel(self):
        self.user_wants_instruction_panel = not self.user_wants_instruction_panel
        state = "显示" if self.user_wants_instruction_panel else "隐藏"
        logging.info(f"用户切换提示面板: {state}")
        self.update_layout()

    def create_panels(self):
        self.side_panel = SidePanel()
        self.overlay_manager.add_managed_widget(self.side_panel, OverlayManager.LAYER_BASE)
        self.side_panel.connect("toggle-grid-mode-clicked", lambda w: self.controller.grid_mode_controller.toggle())
        self.side_panel.connect("toggle-preview-clicked", lambda w: self.toggle_preview_panel())
        self.side_panel.connect("open-config-clicked", lambda w: self.toggle_config_panel())
        self.side_panel.connect("toggle-hotkeys-clicked", lambda w: hotkey_manager.toggle_hotkeys() if hotkey_manager else None)
        self.side_panel.hide()
        self.button_panel = ButtonPanel()
        self.overlay_manager.add_managed_widget(self.button_panel, OverlayManager.LAYER_BASE)
        self.button_panel.connect("grid-backward-clicked", lambda w: self.controller.handle_movement_action('up', source='button'))
        self.button_panel.connect("grid-forward-clicked", lambda w: self.controller.handle_movement_action('down', source='button'))
        self.button_panel.connect("auto-scroll-start-clicked", lambda w: self.controller.start_auto_scroll(widget=w, source='button'))
        self.button_panel.connect("auto-scroll-stop-clicked", lambda w: self.controller.stop_auto_scroll())
        self.button_panel.connect("capture-clicked", self.controller.take_capture)
        self.button_panel.connect("undo-clicked", self.controller.delete_last_capture)
        self.button_panel.connect("finalize-clicked", self.controller.finalize_and_quit)
        self.button_panel.connect("cancel-clicked", self.controller.quit_and_cleanup)
        self.button_panel.hide()
        self.instruction_panel = InstructionPanel()
        self.overlay_manager.add_managed_widget(self.instruction_panel, OverlayManager.LAYER_BASE)
        self.instruction_panel.hide()
        self.preview_panel = PreviewPanel(self.controller.stitch_model, config, self)
        self.overlay_manager.add_managed_widget(self.preview_panel, OverlayManager.LAYER_MEDIUM)
        self.preview_panel.hide()
        self.config_panel = ConfigPanel(config, self, self.log_queue)
        self.overlay_manager.add_managed_widget(self.config_panel, OverlayManager.LAYER_MEDIUM)
        self.overlay_manager.add_managed_widget(self.config_panel.file_chooser_panel, OverlayManager.LAYER_MEDIUM_UP)
        self.overlay_manager.add_managed_widget(self.config_panel.color_chooser_panel, OverlayManager.LAYER_MEDIUM_UP)
        self.config_panel.hide()

    def toggle_config_panel(self):
        if not self.config_panel: return
        if self.config_panel.get_visible():
            logging.debug("隐藏配置面板")
            self.config_panel.hide()
        else:
            logging.debug("显示配置面板")
            anchor = 'center' if not self.config_panel.user_has_moved else None
            self.overlay_manager.show(self.config_panel, anchor=anchor, layer=OverlayManager.LAYER_MEDIUM)

    def toggle_preview_panel(self):
        if not self.preview_panel: return
        if self.preview_panel.get_visible():
            logging.debug("隐藏预览面板")
            self.preview_panel.hide()
        else:
            logging.debug("显示预览面板")
            anchor = self._calculate_preview_position() if not self.preview_panel.user_has_moved else None
            self.overlay_manager.show(self.preview_panel, anchor=anchor, layer=OverlayManager.LAYER_MEDIUM)

    def on_draw(self, widget, cr):
        if self.session.is_finished:
            cr.set_source_rgba(0, 0, 0, 0)
            cr.set_operator(cairo.OPERATOR_SOURCE)
            cr.paint()
            return False
        if not self.session.is_selection_done:
            cr.set_source_rgba(0.0, 0.0, 0.0, 0.4)
            rect = self.session.screen_rect
            if rect:
                cr.rectangle(0, 0, rect.width, rect.height)
                cr.fill()
            else:
                cr.paint()
            if self.start_pos is not None and self.current_pos is not None:
                x1, y1 = self.start_pos
                x2, y2 = self.current_pos
                x = min(x1, x2)
                y = min(y1, y2)
                w = abs(x1 - x2)
                h = abs(y1 - y2)
                cr.set_operator(cairo.OPERATOR_CLEAR)
                cr.rectangle(x, y, w, h)
                cr.fill()
                cr.set_operator(cairo.OPERATOR_OVER)
                r, g, b, a = config.BORDER_COLOR
                cr.set_source_rgba(r, g, b, a)
                cr.set_line_width(config.BORDER_WIDTH)
                cr.rectangle(x, y, w, h)
                cr.stroke()
            return
        cr.set_source_rgba(0, 0, 0, 0)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        border_width = config.BORDER_WIDTH
        cr.set_line_width(border_width)
        border_x_rel = self.session.geometry['x'] - config.BORDER_WIDTH
        border_y_rel = self.session.geometry['y'] - config.BORDER_WIDTH
        rect_x = border_x_rel + border_width / 2
        rect_y = border_y_rel + border_width / 2
        rect_w = self.session.geometry['w'] + border_width
        rect_h = self.session.geometry['h'] + border_width
        cr.set_source_rgba(*config.BORDER_COLOR)
        cr.rectangle(rect_x, rect_y, rect_w, rect_h)
        cr.stroke()
        header_height, footer_height, left_width, right_width = self.session.static_bars
        has_bars = any([header_height, footer_height, left_width, right_width])
        if has_bars:
            cr.set_source_rgba(*config.STATIC_BAR_COLOR)
            if header_height > 0:
                y_end = min(rect_y + rect_h, rect_y + header_height)
                cr.move_to(rect_x, rect_y)
                cr.line_to(rect_x, y_end)
                cr.move_to(rect_x + rect_w, rect_y)
                cr.line_to(rect_x + rect_w, y_end)
                cr.stroke()
            if footer_height > 0:
                y_start = max(rect_y, rect_y + rect_h - footer_height)
                cr.move_to(rect_x, rect_y + rect_h)
                cr.line_to(rect_x, y_start)
                cr.move_to(rect_x + rect_w, rect_y + rect_h)
                cr.line_to(rect_x + rect_w, y_start)
                cr.stroke()
            if left_width > 0:
                x_end = min(rect_x + rect_w, rect_x + left_width)
                cr.move_to(rect_x, rect_y)
                cr.line_to(x_end, rect_y)
                cr.move_to(rect_x, rect_y + rect_h)
                cr.line_to(x_end, rect_y + rect_h)
                cr.stroke()
            if right_width > 0:
                x_start = max(rect_x, rect_x + rect_w - right_width)
                cr.move_to(rect_x + rect_w, rect_y)
                cr.line_to(x_start, rect_y)
                cr.move_to(rect_x + rect_w, rect_y + rect_h)
                cr.line_to(x_start, rect_y + rect_h)
                cr.stroke()

    # 逻辑px {
    def is_point_in_ui(self, g_x, g_y):
        # gx, gy: 全局坐标
        with self.rects_lock:
            for rx, ry, rw, rh in self.cached_blocking_rects:
                if rx <= g_x < rx + rw and ry <= g_y < ry + rh:
                    return True
        return False

    def update_input_shape(self):
        if not self.get_window(): return
        rect = self.session.screen_rect
        if rect:
            win_w, win_h = rect.width, rect.height
        else:
            win_w, win_h = self.get_size()
        if not self.session.is_selection_done:
            full_region = cairo.Region(cairo.RectangleInt(0, 0, win_w, win_h))
            self.get_window().input_shape_combine_region(full_region, 0, 0)
            return
        final_input_region = cairo.Region()
        new_blocking_rects = [] # 全局坐标
        if not self.session.is_finished:
            selection_x_rel = round(self.session.geometry.get('x', 0))
            selection_y_rel = round(self.session.geometry.get('y', 0))
            selection_w = round(self.session.geometry.get('w', 0))
            selection_h = round(self.session.geometry.get('h', 0))
            border_area_x_rel = selection_x_rel - config.BORDER_WIDTH
            border_area_y_rel = selection_y_rel - config.BORDER_WIDTH
            border_area_width = selection_w + 2 * config.BORDER_WIDTH
            border_area_height = selection_h + 2 * config.BORDER_WIDTH
            border_full_region = cairo.Region(cairo.RectangleInt(border_area_x_rel, border_area_y_rel, border_area_width, border_area_height))
            inner_transparent_region = cairo.Region(cairo.RectangleInt(selection_x_rel, selection_y_rel, selection_w, selection_h))
            border_full_region.subtract(inner_transparent_region)
            final_input_region.union(border_full_region)
        final_region, final_rects = self.overlay_manager.recalculate_input_shapes(final_input_region, new_blocking_rects)
        self.get_window().input_shape_combine_region(final_region, 0, 0)
        self.overlay_manager.sync_wm_shape(final_region)
        with self.rects_lock:
            self.cached_blocking_rects = final_rects
        if IS_WAYLAND:
            self._trigger_wayland_layout_refresh(remaining_retries=1)

    def get_cursor_edge(self, x, y):
        rect = self.session.screen_rect
        win_w = rect.width if rect else self.get_allocated_width()
        win_h = rect.height if rect else self.get_allocated_height()
        border_width = config.BORDER_WIDTH
        if not self.session.is_selection_done: return None
        selection_x_rel = self.session.geometry.get('x', 0)
        selection_y_rel = self.session.geometry.get('y', 0)
        border_area_x_rel = selection_x_rel - border_width
        border_area_y_rel = selection_y_rel - border_width
        border_area_right_rel = selection_x_rel + self.session.geometry.get('w', 0) + border_width
        border_area_bottom_rel = selection_y_rel + self.session.geometry.get('h', 0) + border_width
        on_top = (border_area_y_rel <= y < selection_y_rel) and (border_area_x_rel <= x < border_area_right_rel)
        on_bottom = (border_area_bottom_rel - border_width < y <= border_area_bottom_rel) and (border_area_x_rel <= x < border_area_right_rel)
        edges = []
        if on_top: edges.append('top')
        elif on_bottom: edges.append('bottom')
        if not self.session.is_horizontally_locked:
            on_left = (border_area_x_rel <= x < selection_x_rel) and (border_area_y_rel <= y < border_area_bottom_rel)
            on_right = (border_area_right_rel - border_width < x <= border_area_right_rel) and (border_area_y_rel <= y < border_area_bottom_rel)
            if on_left: edges.append('left')
            elif on_right: edges.append('right')
        result = '-'.join(edges)
        return result if result else None

    def on_button_press(self, widget, event):
        if self.overlay_manager.dismiss_by_type(NotificationWidget):
            logging.debug("检测到点击通知外部，已关闭相关通知")
        if event.button == 1:
            if not self.session.is_selection_done:
                self.start_pos = (event.x, event.y)
                self.current_pos = (event.x, event.y)
                self.canvas.queue_draw()
                return True
            else:
                self.resize_edge = self.get_cursor_edge(event.x, event.y)
                if self.resize_edge:
                    self.drag_start_pos = (event.x, event.y)
                    self.drag_start_geometry = self.session.geometry.copy()
                    return True
                return False
        return False
    # 逻辑px }

    def _trigger_wayland_layout_refresh(self, remaining_retries=0):
        rect = self.session.screen_rect
        w, h = rect.width, rect.height
        self.resize(w + 1, h + 1)
        def _restore_size():
            self.resize(w, h)
            return False
        GLib.idle_add(_restore_size)
        if remaining_retries > 0:
            GLib.timeout_add(150, self._trigger_wayland_layout_refresh, remaining_retries - 1)

    def on_button_release(self, widget, event):
        if not self.session.is_selection_done:
            if event.button == 1 and self.start_pos is not None and self.current_pos is not None:
                # 逻辑px
                x1, y1 = self.start_pos
                x2, y2 = self.current_pos
                self.start_pos = None
                self.current_pos = None
                final_x = min(x1, x2)
                final_y = min(y1, y2)
                raw_w = abs(x1 - x2)
                raw_h = abs(y1 - y2)
                min_size = config.MIN_SELECTION_SIZE
                final_w = max(raw_w, min_size)
                final_h = max(raw_h, min_size)
                if raw_w < min_size or raw_h < min_size:
                    logging.info("选区太小，保持在选择阶段")
                    self.canvas.queue_draw()
                    self.update_layout()
                    return True
                geometry = {'x': final_x, 'y': final_y, 'w': final_w, 'h': final_h}
                scale = self.session.scale
                buf_w, buf_h = int(geometry['w'] * scale), int(geometry['h'] * scale) # 缓冲区px
                logging.info(f"选区完成，逻辑px: { {k: round(v, 1) for k, v in geometry.items()} } (scale={scale:.3f}) -> 缓冲区px: w={buf_w}, h={buf_h}")
                self.session.set_geometry(geometry)
                self.session.set_selection_done(True)
                if config.SHOW_PREVIEW_ON_START:
                    self.toggle_preview_panel()
                return True
            return False
        else:
            was_dragging = self.resize_edge is not None
            self.resize_edge = None
            self.drag_start_pos = None
            self.drag_start_geometry = None
            return was_dragging

    def update_layout(self):
        # 逻辑px
        rect = self.session.screen_rect
        if rect:
            screen_w = rect.width
            screen_h = rect.height
        else:
            screen_w, screen_h = self.get_size()
        if not self.session.is_selection_done:
            blocking_rects = []
            if self.start_pos is not None and self.current_pos is not None:
                x1, y1 = self.start_pos
                x2, y2 = self.current_pos
                rect = (min(x1, x2), min(y1, y2), abs(x1 - x2), abs(y1 - y2))
                blocking_rects.append(rect)
            self._update_instruction_panel_layout(screen_w, screen_h, blocking_rects)
            return
        selection_h = self.session.geometry.get('h', 0)
        border_area_x_rel = self.session.geometry.get('x', 0) - config.BORDER_WIDTH
        border_area_y_rel = self.session.geometry.get('y', 0) - config.BORDER_WIDTH
        border_area_width = self.session.geometry.get('w', 0) + 2 * config.BORDER_WIDTH
        border_area_height = selection_h + 2 * config.BORDER_WIDTH
        border_area_right_rel = border_area_x_rel + border_area_width
        side_panel_needed_w = config.SIDE_PANEL_WIDTH
        button_panel_needed_w = config.BUTTON_PANEL_WIDTH
        has_space_right_for_button_panel = (screen_w - border_area_right_rel) >= config.BUTTON_PANEL_WIDTH
        has_space_right_for_side_panel = (screen_w - border_area_right_rel) >= config.SIDE_PANEL_WIDTH
        has_space_left_for_side_panel = border_area_x_rel >= config.SIDE_PANEL_WIDTH
        self.show_side_panel = False
        self.show_button_panel = False
        self.side_panel_on_left = True
        if config.ENABLE_SIDE_PANEL:
            if has_space_left_for_side_panel:
                self.show_side_panel = True
            elif has_space_right_for_side_panel:
                self.show_side_panel = True
                self.side_panel_on_left = False
        side_panel_occupies_right = self.show_side_panel and not self.side_panel_on_left
        if config.ENABLE_BUTTONS and has_space_right_for_button_panel and not side_panel_occupies_right:
                self.show_button_panel = True
        blocking_rects = [(border_area_x_rel, border_area_y_rel, border_area_width, border_area_height)]
        # 更新子组件的可见性和位置
        if self.show_side_panel and self.side_panel.update_visibility_by_height(selection_h):
            self.side_panel.show()
            panel_x_rel = border_area_x_rel - config.SIDE_PANEL_WIDTH if self.side_panel_on_left else border_area_right_rel
            self.overlay_manager.move_widget(self.side_panel, panel_x_rel, border_area_y_rel)
            _, nat_h = self.side_panel.get_preferred_height()
            blocking_rects.append((panel_x_rel, border_area_y_rel, config.SIDE_PANEL_WIDTH, nat_h))
        else:
            self.side_panel.hide()
        if self.show_button_panel and self.button_panel.update_visibility_by_height(selection_h, self.session.current_mode):
            self.button_panel.show()
            self.overlay_manager.move_widget(self.button_panel, border_area_right_rel, border_area_y_rel)
            _, nat_h = self.button_panel.get_preferred_height()
            blocking_rects.append((border_area_right_rel, border_area_y_rel, config.BUTTON_PANEL_WIDTH, nat_h))
        else:
            self.button_panel.hide()
        self._update_instruction_panel_layout(screen_w, screen_h, blocking_rects)
        self.update_input_shape()

    def _update_instruction_panel_layout(self, screen_w, screen_h, blocking_rects):
        if not self.instruction_panel:
            return
        if not self.user_wants_instruction_panel or not self.coord_manager.is_calibration_done:
            self.instruction_panel.hide()
            return
        margin = 20
        self.instruction_panel.show()
        _, nat_size = self.instruction_panel.get_preferred_size()
        panel_w, panel_h = nat_size.width, nat_size.height
        valid_screen_w, valid_screen_h = self.coord_manager.get_valid_screen_size()
        if valid_screen_h < panel_h + margin * 2 or valid_screen_w < panel_w + margin * 2:
            self.instruction_panel.hide()
            return
        target_x = margin
        target_y = valid_screen_h - panel_h - margin
        panel_rect = (target_x, target_y, panel_w, panel_h)
        def rects_intersect(r1, r2):
            return not (r1[0] >= r2[0] + r2[2] or
                        r1[0] + r1[2] <= r2[0] or
                        r1[1] >= r2[1] + r2[3] or
                        r1[1] + r1[3] <= r2[1])
        is_obstructed = any(rects_intersect(panel_rect, r) for r in blocking_rects)
        should_be_visible = not is_obstructed
        current_visible = self.instruction_panel.get_visible()
        if current_visible != should_be_visible:
            if should_be_visible:
                self.overlay_manager.show(self.instruction_panel, anchor=(target_x, target_y), layer=OverlayManager.LAYER_BASE)
            else:
                self.instruction_panel.hide()
                self.canvas.queue_draw_area(target_x, target_y, panel_w, panel_h)
        if should_be_visible:
            cur_x, cur_y = self.overlay_manager.get_widget_position(self.instruction_panel)
            if cur_x != target_x or cur_y != target_y:
                self.overlay_manager.show(self.instruction_panel, anchor=(target_x, target_y), layer=OverlayManager.LAYER_BASE)

    def on_motion_notify(self, widget, event):
        if event.window != widget.get_window():
            if self.get_window():
                self.get_window().set_cursor(self.cursors.get('default'))
            return False
        if not self.session.is_selection_done:
            if self.start_pos is not None:
                self.current_pos = (event.x, event.y)
                self.update_layout()
                self.canvas.queue_draw()
                return True
            return False
        else:
            if self.resize_edge is not None:
                if not self.drag_start_pos or not self.drag_start_geometry:
                    return False
                # 逻辑px
                delta_x = event.x - self.drag_start_pos[0]
                delta_y = event.y - self.drag_start_pos[1]
                new_geo = self.drag_start_geometry.copy()
                scale = self.session.scale
                start_h = self.drag_start_geometry['h']
                _, start_cap_y = self.coord_manager.map_point(
                    self.drag_start_geometry['x'], self.drag_start_geometry['y'],
                    source=CoordSys.WINDOW, target=self.frame_grabber.target_coords
                )
                # 逻辑px -> 缓冲区px
                start_y_buf = math.ceil(start_cap_y * scale)
                start_h_buf = int((start_cap_y + start_h) * scale) - start_y_buf
                is_grid_mode = (self.session.current_mode == CaptureMode.GRID)
                grid_unit_buf = self.session.grid_unit if is_grid_mode else 0 # 缓冲区px
                is_grid_snap = grid_unit_buf > 0 and not self.session.grid_matching_enabled
                is_dragging_top = 'top' in self.resize_edge
                is_dragging_bottom = 'bottom' in self.resize_edge
                is_dragging_left = 'left' in self.resize_edge
                is_dragging_right = 'right' in self.resize_edge
                min_h = grid_unit_buf / scale if is_grid_snap else config.MIN_SELECTION_SIZE # 缓冲区px -> 逻辑px
                min_w = config.MIN_SELECTION_SIZE
                if is_dragging_top:
                    new_geo['y'] += delta_y
                elif is_dragging_bottom:
                    new_geo['h'] += delta_y
                if not self.session.is_horizontally_locked:
                    if is_dragging_left:
                        new_geo['x'] += delta_x
                    elif is_dragging_right:
                        new_geo['w'] += delta_x
                if new_geo['h'] < min_h:
                    if is_dragging_top: new_geo['y'] -= (min_h - new_geo['h'])
                    new_geo['h'] = min_h
                if new_geo['w'] < min_w:
                    if is_dragging_left: new_geo['x'] -= (min_w - new_geo['w'])
                    new_geo['w'] = min_w
                x_min, y_min = config.BORDER_WIDTH, config.BORDER_WIDTH
                valid_w, valid_h = self.coord_manager.get_valid_screen_size()
                x_max = valid_w - config.BORDER_WIDTH
                y_max = valid_h - config.BORDER_WIDTH
                if not self.session.is_horizontally_locked:
                    if new_geo['x'] < x_min:
                        if is_dragging_left: new_geo['w'] -= (x_min - new_geo['x'])
                        new_geo['x'] = x_min
                    if new_geo['x'] + new_geo['w'] > x_max:
                        new_geo['w'] = x_max - new_geo['x']
                    if new_geo['w'] < min_w:
                        if is_dragging_left: new_geo['x'] -= (min_w - new_geo['w'])
                        new_geo['w'] = min_w
                if new_geo['y'] < y_min:
                    if is_dragging_top: new_geo['h'] -= (y_min - new_geo['y'])
                    new_geo['y'] = y_min
                if new_geo['y'] + new_geo['h'] > y_max:
                    new_geo['h'] = y_max - new_geo['y']
                if new_geo['h'] < min_h:
                    if is_dragging_top: new_geo['y'] -= (min_h - new_geo['h'])
                    new_geo['h'] = min_h
                if is_grid_snap:
                    delta_h = new_geo['h'] - start_h
                    delta_h_buf = round(delta_h * scale) # 逻辑px -> 缓冲区px
                    units_changed = int(delta_h_buf / grid_unit_buf)
                    target_h_buf = max(grid_unit_buf, start_h_buf + (units_changed * grid_unit_buf))
                    if is_dragging_top:
                        if (new_geo['h'] < start_h) and (abs(new_geo['y'] + new_geo['h'] - y_max) < 1e-5):
                            _, cap_bottom = self.coord_manager.map_point(
                                new_geo['x'], new_geo['y'] + new_geo['h'],
                                source=CoordSys.WINDOW, target=self.frame_grabber.target_coords
                            )
                            y_buf = int(cap_bottom * scale) - target_h_buf # 逻辑px -> 缓冲区px
                            snapped_h_logical = cap_bottom - (y_buf - 0.01) / scale # 缓冲区px -> 逻辑px
                            new_geo['y'] = new_geo['y'] + new_geo['h'] - snapped_h_logical
                            new_geo['h'] = snapped_h_logical
                        else:
                            _, new_cap_y = self.coord_manager.map_point(
                                new_geo['x'], new_geo['y'],
                                source=CoordSys.WINDOW, target=self.frame_grabber.target_coords
                            )
                            new_y_buf = math.ceil(new_cap_y * scale)
                            snapped_h_logical = (new_y_buf + target_h_buf + 0.01) / scale - new_cap_y
                            new_geo['h'] = snapped_h_logical
                    elif is_dragging_bottom:
                        snapped_h_logical = (start_y_buf + target_h_buf + 0.01) / scale - start_cap_y
                        new_geo['h'] = snapped_h_logical
                self.session.set_geometry(new_geo)
                return True
            else:
                edge = self.get_cursor_edge(event.x, event.y)
                cursor_map = {
                    'top': 'n-resize', 'bottom': 's-resize',
                    'left': 'w-resize', 'right': 'e-resize',
                    'top-left': 'nw-resize', 'bottom-right': 'se-resize',
                    'top-right': 'ne-resize', 'bottom-left': 'sw-resize',
                }
                cursor_name = cursor_map.get(edge, 'default')
                cursor = self.cursors.get(cursor_name)
                if self.get_window():
                    self.get_window().set_cursor(cursor)
                return False
    # 窗口坐标 }

class XlibScrollListener(threading.Thread):
    """基于 XRecord 的滚动监听器"""
    def __init__(self, overlay):
        super().__init__(daemon=True)
        self.overlay = overlay
        self.local_dpy = display.Display()
        self.record_dpy = display.Display()
        self.ctx = None
        self.scroll_accumulator = 0
        self.lock = threading.Lock()
        self.running = False

    def get_scroll_delta(self, reset=True):
        with self.lock:
            val = self.scroll_accumulator
            if reset:
                self.scroll_accumulator = 0
            return val

    def _handler(self, reply):
        if not self.running or reply.category != record.FromServer:
            return
        if reply.client_swapped or not len(reply.data) or reply.data[0] < 2:
            return
        data = reply.data
        while len(data):
            event, data = rq.EventField(None).parse_binary_value(data, self.record_dpy.display, None, None)
            if event.type == X.ButtonPress:
                should_record = True
                if self.overlay:
                    # 缓冲区px全局坐标
                    root_x = getattr(event, 'root_x', None)
                    root_y = getattr(event, 'root_y', None)
                    scale = self.overlay.session.scale
                    # 缓冲区px -> 逻辑px
                    logic_x = root_x / scale
                    logic_y = root_y / scale
                    if self.overlay.is_point_in_ui(logic_x, logic_y):
                        should_record = False
                if should_record:
                    with self.lock:
                        if event.detail == 4:
                            self.scroll_accumulator -= 1
                        elif event.detail == 5:
                            self.scroll_accumulator += 1

    def run(self):
        self.running = True
        try:
            self.ctx = self.record_dpy.record_create_context(
                0, [record.AllClients],
                [{
                    'core_requests': (0, 0), 'core_replies': (0, 0),
                    'ext_requests': (0, 0, 0, 0), 'ext_replies': (0, 0, 0, 0),
                    'delivered_events': (0, 0),
                    'device_events': (X.ButtonPressMask, X.ButtonReleaseMask),
                    'errors': (0, 0), 'client_started': False, 'client_died': False,
                }]
            )
            self.record_dpy.record_enable_context(self.ctx, self._handler)
        except Exception as e:
            logging.error(f"XRecord 监听器崩溃: {e}")
        finally:
            self._cleanup()

    def stop(self):
        self.running = False
        try:
            if self.ctx is not None and self.local_dpy is not None:
                self.local_dpy.record_disable_context(self.ctx)
                self.local_dpy.flush()
        except Exception as e:
            logging.debug(f"停止 XlibScrollListener 时异常: {e}")

    def _cleanup(self):
        try:
            self.record_dpy.record_free_context(self.ctx)
            self.local_dpy.close()
            self.record_dpy.close()
        except:
            pass

class XlibHotkeyInterceptor(threading.Thread):
    """使用 Xlib (XGrabKey) 在后台线程中拦截全局热键，并支持动态启用/禁用"""
    def __init__(self, hotkey_defs):
        super().__init__(daemon=True)
        self.hotkey_defs = hotkey_defs
        self.running = False
        self.disp = None
        self.root = None
        self.lock = threading.Lock()
        self.mouse_grabbed = False
        self.mouse_click_callback = None
        self.last_trigger_time = 0
        self.currently_grabbed = set()
        self.key_registry = {}
        self.active_keys = set()
        self.mod_keycodes = set()
        self.pending_mod_action = None

    def _schedule_update(self):
        if not self.running: return
        target_keys = []
        for key_id, infos in self.key_registry.items():
            for name, callback in infos:
                if name in self.active_keys:
                    target_keys.append(key_id)
                    break
        threading.Thread(target=self._apply_grab_state, args=(target_keys,), daemon=True).start()

    def _apply_grab_state(self, target_key_tuples):
        with self.lock:
            if not self.disp or not self.root:
                return
            if self.mouse_click_callback and not self.mouse_grabbed:
                try:
                    self.root.grab_button(1, X.AnyModifier, 0, X.ButtonPressMask, X.GrabModeAsync, X.GrabModeAsync, X.NONE, X.NONE)
                    self.mouse_grabbed = True
                    logging.debug("Xlib: 已抓取鼠标左键")
                except Exception as e:
                    logging.warning(f"Xlib GrabButton 失败: {e}")
            elif not self.mouse_click_callback and self.mouse_grabbed:
                try:
                    self.root.ungrab_button(1, X.AnyModifier)
                    self.mouse_grabbed = False
                    logging.debug("Xlib: 已释放鼠标左键抓取")
                except Exception as e:
                    logging.warning(f"Xlib UngrabButton 失败: {e}")
            target_set = set(target_key_tuples)
            to_grab = target_set - self.currently_grabbed
            to_ungrab = self.currently_grabbed - target_set
            if not to_grab and not to_ungrab:
                return
            for (keycode, mask) in to_ungrab:
                masks_to_process = [
                    mask,
                    mask | X.Mod2Mask,
                    mask | X.LockMask,
                    mask | X.Mod2Mask | X.LockMask
                ]
                for m in masks_to_process:
                    try:
                        self.root.ungrab_key(keycode, m, self.root)
                    except Exception as e:
                        logging.warning(f"UngrabKey 失败: {e}")
                self.currently_grabbed.remove((keycode, mask))
            for (keycode, mask) in to_grab:
                masks_to_process = [
                    mask,
                    mask | X.Mod2Mask,
                    mask | X.LockMask,
                    mask | X.Mod2Mask | X.LockMask
                ]
                for m in masks_to_process:
                    try:
                        self.root.grab_key(keycode, m, False, X.GrabModeAsync, X.GrabModeAsync)
                    except Exception as e:
                        logging.warning(f"GrabKey 失败 (kc={keycode}): {e}")
                self.currently_grabbed.add((keycode, mask))
            try:
                self.disp.flush()
            except Exception as e:
                logging.warning(f"disp.flush() 失败: {e}")

    def enable_mouse_click_monitor(self, enabled: bool, callback=None):
        with self.lock:
            self.mouse_click_callback = callback if enabled else None
        self._schedule_update()

    def set_active_keys(self, active_keys):
        with self.lock:
            self.active_keys = set(active_keys)
        self._schedule_update()

    def _rebuild_mappings(self):
        if not self.disp: return
        new_registry = {}
        new_mod_keycodes = set()
        for hotkey_def, name, callback in self.hotkey_defs:
            x11_mask_strs, x11_keysym_strs = hotkey_def.to_x11()
            base_mask = 0
            for m_str in x11_mask_strs:
                base_mask |= getattr(X, m_str, 0)
            is_mod_only = hotkey_def.is_modifier_only()
            processed_key_ids = set()
            for keysym_str in x11_keysym_strs:
                keysym = XK.string_to_keysym(keysym_str)
                if not keysym:
                    logging.warning(f"无法识别 keysym 字符串 '{keysym_str}'（动作: {name}）")
                    continue
                keycode = self.disp.keysym_to_keycode(keysym)
                if not keycode:
                    logging.warning(f"无法为 keysym '{keysym_str}' 获取物理 keycode（动作: {name}）")
                    continue
                key_id = (keycode, base_mask)
                if key_id in processed_key_ids: continue
                processed_key_ids.add(key_id)
                if key_id not in new_registry:
                    new_registry[key_id] = []
                new_registry[key_id].append((name, callback))
                if is_mod_only:
                    new_mod_keycodes.add(keycode)
        self.key_registry = new_registry
        self.mod_keycodes = new_mod_keycodes

    def update_config(self, hotkey_defs):
        with self.lock:
            self.hotkey_defs = hotkey_defs
            self._rebuild_mappings()
            self._schedule_update()

    def run(self):
        """线程主循环，监听 X events"""
        try:
            self.disp = display.Display()
            self.root = self.disp.screen().root
            with self.lock:
                self._rebuild_mappings()
            self.running = True
        except Exception as e:
            logging.error(f"XlibHotkeyInterceptor 线程初始化 Display 失败: {e}")
            GLib.idle_add(send_notification, "热键服务启动失败", f"无法连接到 X Display: {e}\n全局快捷键将不可用", "warning", config.WARNING_SOUND)
            self.running = False
            return
        self._schedule_update()
        logging.debug("Xlib 热键拦截线程已启动")
        while self.running:
            try:
                event = self.disp.next_event()
                if event.type == X.ButtonPress:
                    if self.mouse_click_callback and event.detail == 1:
                        logging.debug("Xlib: 检测到鼠标左键点击，执行回调")
                        self.mouse_click_callback()
                if event.type == X.KeyPress:
                    keycode = event.detail
                    if self.pending_mod_action:
                        waiting_keycode, _ = self.pending_mod_action
                        if keycode != waiting_keycode:
                            logging.debug("检测到组合键，取消待定的修饰键单键动作")
                            self.pending_mod_action = None
                    clean_state = event.state & (X.ShiftMask | X.ControlMask | X.Mod1Mask | X.Mod4Mask)
                    key_id = (keycode, clean_state)
                    infos = self.key_registry.get(key_id)
                    if not infos: continue
                    for key_name, callback in infos:
                        if key_name in self.active_keys:
                            log_key_str = f"key='{key_name}'（id={key_id}）"
                            if keycode in self.mod_keycodes:
                                self.pending_mod_action = (keycode, callback)
                                logging.debug(f"修饰键热键 {log_key_str} 按下，等待松开...")
                            else:
                                current_time = time.time()
                                if current_time - self.last_trigger_time >= config.HOTKEY_DEBOUNCE_TIME:
                                    logging.debug(f"Xlib 拦截到热键 {log_key_str} 并执行回调")
                                    self.last_trigger_time = current_time
                                    GLib.idle_add(lambda cb=callback: (cb(), False)[1])
                            break
                elif event.type == X.KeyRelease:
                    if self.pending_mod_action:
                        waiting_keycode, waiting_callback = self.pending_mod_action
                        if event.detail == waiting_keycode:
                            logging.debug("修饰键松开且未被组合使用，执行动作")
                            self.last_trigger_time = time.time()
                            GLib.idle_add(lambda: (waiting_callback(), False)[1])
                            self.pending_mod_action = None
            except Exception as e:
                if self.running:
                    logging.error(f"Xlib 事件循环错误: {e}")
                    time.sleep(0.1)
        logging.debug("Xlib 热键拦截线程正在停止...")
        with self.lock:
            self.active_keys = set()
        self._schedule_update()
        if self.disp:
            try:
                self.disp.close()
            except Exception as e:
                logging.error(f"关闭 X Display 连接时出错: {e}")
        logging.debug("Xlib 热键拦截线程已停止")

    def stop(self):
        logging.debug("收到停止 Xlib 拦截线程的请求...")
        self.running = False
        if self.disp and self.root:
            try:
                client_event = protocol.event.ClientMessage(window=self.root, client_type=self.disp.intern_atom("_STOP_THREAD"), data=(8, [0] * 20))
                self.disp.send_event(self.root, client_event, event_mask=X.NoEventMask)
                self.disp.flush()
                logging.debug("已发送 ClientMessage 事件以唤醒 Xlib 事件循环")
            except Exception as e:
                logging.warning(f"发送唤醒事件失败: {e}")
        else:
            logging.warning("无法发送唤醒事件，Display 尚未初始化或已关闭")

class EvdevListener(threading.Thread):
    """使用 evdev 直接读取输入设备实现全局热键和滚动监听"""
    def __init__(self, overlay, hotkey_defs):
        super().__init__(daemon=True)
        self.overlay = overlay
        self.hotkey_defs = hotkey_defs
        self.running = False
        self.devices = []
        self._needs_refresh = False
        self.key_registry = {}
        self.active_keys = set()
        self.active_mods = HotkeyModifiers.NONE
        self.scroll_accumulator = 0
        self.scroll_lock = threading.Lock()
        self.mouse_click_callback = None
        self.last_trigger_time = 0
        self.code_to_mod_flag = {}
        for evdev_code_str, mod_flag in HotkeyDefinition.EVDEV_CODE_TO_MODIFIER.items():
            if hasattr(e, evdev_code_str):
                self.code_to_mod_flag[getattr(e, evdev_code_str)] = mod_flag
        self.mod_keycodes = set()
        self.pending_mod_action = None
        self._rebuild_mappings()

    def _sync_initial_modifiers(self):
        self.active_mods = HotkeyModifiers.NONE
        for dev in self.devices:
            try:
                active_codes = dev.active_keys()
                for code in active_codes:
                    mod_flag = self.code_to_mod_flag.get(code)
                    if mod_flag is not None:
                        self.active_mods |= mod_flag
            except OSError:
                pass
        logging.debug(f"Evdev: 初始修饰键状态 {self.active_mods} 已同步")

    def _rebuild_mappings(self):
        new_registry = {}
        new_mod_keycodes = set()
        for hotkey_def, name, callback in self.hotkey_defs:
            base_mask = hotkey_def.modifiers
            evdev_code_strs = hotkey_def.to_evdev()
            is_mod_only = hotkey_def.is_modifier_only()
            processed_key_ids = set()
            for code_str in evdev_code_strs:
                if not hasattr(e, code_str):
                    logging.warning(f"Evdev: 无法识别按键 {code_str}（动作: {name}）")
                    continue
                keycode = getattr(e, code_str)
                key_id = (base_mask, keycode)
                if key_id in processed_key_ids: continue
                processed_key_ids.add(key_id)
                if key_id not in new_registry:
                    new_registry[key_id] = []
                new_registry[key_id].append((name, callback))
                if is_mod_only:
                    new_mod_keycodes.add(keycode)
        self.key_registry = new_registry
        self.mod_keycodes = new_mod_keycodes

    def update_config(self, hotkey_defs):
        self.hotkey_defs = hotkey_defs
        self.pending_mod_action = None
        self._rebuild_mappings()

    def enable_mouse_click_monitor(self, enabled: bool, callback=None):
        self.mouse_click_callback = callback if enabled else None
        logging.debug(f"Evdev: 鼠标点击监听已 {'启用' if enabled else '禁用'}")

    def set_active_keys(self, active_keys):
        self.active_keys = set(active_keys)

    def get_scroll_delta(self, reset=True):
        with self.scroll_lock:
            val = self.scroll_accumulator
            if reset:
                self.scroll_accumulator = 0
            return val

    def _find_input_devices(self):
        """查找所有具有键盘特性或鼠标左键的设备"""
        valid_devices = []
        for path in evdev.list_devices():
            dev = evdev.InputDevice(path)
            caps = dev.capabilities()
            is_valid = False
            if e.EV_KEY in caps:
                keys = caps[e.EV_KEY]
                has_rel = e.EV_REL in caps and e.REL_WHEEL in caps[e.EV_REL]
                if e.KEY_ENTER in keys or e.BTN_LEFT in keys or has_rel:
                    is_valid = True
            if is_valid:
                valid_devices.append(dev)
            else:
                dev.close()
        return valid_devices

    def refresh_devices(self):
        self._needs_refresh = True

    def run(self):
        asyncio.set_event_loop(asyncio.new_event_loop())
        if not EVDEV_AVAILABLE or not INPUT_AVAILABLE:
            return
        try:
            self.devices = self._find_input_devices()
            if not self.devices:
                logging.warning("未检测到输入设备，Evdev 监听器无法工作")
                GLib.idle_add(send_notification, "热键服务启动失败", "未检测到可用的输入设备，全局快捷键可能无法工作", "warning", config.WARNING_SOUND)
                return
            fds = {dev.fd: dev for dev in self.devices}
            self._sync_initial_modifiers()
            self.running = True
            logging.debug(f"Evdev 监听器启动，监控 {len(self.devices)} 个设备")
            while self.running:
                if self._needs_refresh:
                    self._needs_refresh = False
                    new_devs = self._find_input_devices()
                    current_paths = {d.path for d in self.devices}
                    for dev in new_devs:
                        if dev.path not in current_paths:
                            self.devices.append(dev)
                            fds[dev.fd] = dev
                            logging.debug(f"Evdev: 动态加入新设备 {dev.name} 监听 ({dev.path})")
                r, w, x = select.select(fds, [], [], 0.5)
                for fd in r:
                    dev = fds[fd]
                    try:
                        for event in dev.read():
                            if event.type == e.EV_KEY:
                                self._process_key_event(event)
                            elif event.type == e.EV_REL and event.code == e.REL_WHEEL:
                                with self.scroll_lock:
                                    should_record = True
                                    try:
                                        pos = self.overlay.controller.scroll_manager.get_pointer_position(target=CoordSys.GLOBAL)
                                        if self.overlay.is_point_in_ui(pos[0], pos[1]):
                                            should_record = False
                                    except Exception:
                                        pass
                                    if should_record:
                                        self.scroll_accumulator -= event.value
                    except OSError:
                        del fds[fd]
                        if dev in self.devices:
                            self.devices.remove(dev)
        except Exception as err:
            logging.error(f"Evdev 监听循环发生错误: {err}")
        finally:
            for dev in self.devices:
                try: dev.close()
                except: pass

    def _process_key_event(self, event):
        if event.value == 1 or event.value == 2:
            if event.code == e.BTN_LEFT and self.mouse_click_callback:
                logging.debug("Evdev: 检测到鼠标左键点击，执行回调")
                self.mouse_click_callback()
                return
            mod_flag = self.code_to_mod_flag.get(event.code)
            is_modifier_key = mod_flag is not None
            if self.pending_mod_action:
                waiting_code, _ = self.pending_mod_action
                if event.code != waiting_code:
                    self.pending_mod_action = None
            search_mods = self.active_mods
            key_id = (search_mods, event.code)
            infos = self.key_registry.get(key_id)
            if infos:
                for key_name, callback in infos:
                    if key_name in self.active_keys:
                        if event.code in self.mod_keycodes:
                            if event.value == 1:
                                self.pending_mod_action = (event.code, callback)
                                logging.debug(f"Evdev: 修饰键 {key_name} 按下，等待松开")
                        else:
                            current_time = time.time()
                            if current_time - self.last_trigger_time >= config.HOTKEY_DEBOUNCE_TIME:
                                logging.debug(f"Evdev 触发热键: {key_name} (value={event.value})")
                                self.last_trigger_time = current_time
                                GLib.idle_add(lambda cb=callback: (cb(), False)[1])
                        break
            if is_modifier_key and event.value == 1:
                self.active_mods |= mod_flag
        elif event.value == 0:
            if self.pending_mod_action:
                waiting_code, waiting_cb = self.pending_mod_action
                if event.code == waiting_code:
                    logging.debug("Evdev: 修饰键松开且未被组合使用，执行动作")
                    self.last_trigger_time = time.time()
                    GLib.idle_add(lambda: (waiting_cb(), False)[1])
                self.pending_mod_action = None
            mod_flag = self.code_to_mod_flag.get(event.code)
            if mod_flag is not None:
                self.active_mods &= ~mod_flag

    def stop(self):
        self.running = False

class HotkeyManager:
    CONTEXT_ACTION_WHITELIST = {
        Context.BASE: {
            'capture', 'finalize', 'undo', 'cancel',
            'auto_scroll_start', 'auto_scroll_stop', 'grid_forward', 'grid_backward',
            'configure_scroll_unit', 'toggle_grid_mode',
            'toggle_config_panel', 'toggle_preview', 'toggle_instruction_panel',
            'preview_zoom_in', 'preview_zoom_out'
        },
        Context.SELECTING: {
            'cancel', 'toggle_instruction_panel'
        },
        Context.DIALOG: {
            'dialog_confirm', 'dialog_cancel'
        }
    }

    def __init__(self, config_obj):
        self.config = config_obj
        self.listener = None
        self.is_paused = False
        self.are_hotkeys_enabled = True
        self.overlay = None
        self.active_keys = set()
        self.registered_hotkeys = []
        self._action_callbacks = []

    @property
    def is_listening(self):
        return self.listener is not None and getattr(self.listener, 'running', False)

    @property
    def backend(self):
        if self.listener:
            if isinstance(self.listener, EvdevListener):
                return 'evdev'
            elif isinstance(self.listener, XlibHotkeyInterceptor):
                return 'xlib'
        return None

    def setup(self, overlay):
        self.overlay = overlay
        self.overlay.session.connect('context-changed', lambda s: self.update_active_keys())
        def dialog_confirm():
            logging.debug("全局热键触发: 确认对话框")
            GLib.idle_add(self.overlay.overlay_manager.dispatch_dialog_key, Gtk.ResponseType.OK)
        def dialog_cancel():
            logging.debug("全局热键触发: 取消对话框")
            GLib.idle_add(self.overlay.overlay_manager.dispatch_dialog_key, Gtk.ResponseType.CANCEL)
        self._action_callbacks = [
            ('capture', self.overlay.controller.take_capture),
            ('finalize', self.overlay.controller.finalize_and_quit),
            ('undo', self.overlay.controller.delete_last_capture),
            ('cancel', self.overlay.controller.quit_and_cleanup),
            ('auto_scroll_start', lambda: self.overlay.controller.start_auto_scroll(source='hotkey')),
            ('auto_scroll_stop', lambda: self.overlay.controller.stop_auto_scroll()),
            ('grid_forward', lambda: self.overlay.controller.handle_movement_action('down', source='hotkey')),
            ('grid_backward', lambda: self.overlay.controller.handle_movement_action('up', source='hotkey')),
            ('configure_scroll_unit', self.overlay.controller.grid_mode_controller.start_calibration),
            ('toggle_grid_mode', self.overlay.controller.grid_mode_controller.toggle),
            ('toggle_config_panel', self.overlay.toggle_config_panel),
            ('toggle_preview', self.overlay.toggle_preview_panel),
            ('toggle_hotkeys_enabled', self.toggle_hotkeys),
            ('toggle_instruction_panel', self.overlay.toggle_instruction_panel),
            ('preview_zoom_in', lambda: self.overlay.preview_panel.adjust_zoom('in') if self.overlay.preview_panel and self.overlay.preview_panel.get_visible() else None),
            ('preview_zoom_out', lambda: self.overlay.preview_panel.adjust_zoom('out') if self.overlay.preview_panel and self.overlay.preview_panel.get_visible() else None),
            ('dialog_confirm', dialog_confirm),
            ('dialog_cancel', dialog_cancel),
        ]
        self.rebuild_listener()

    def rebuild_listener(self):
        hotkey_defs = []
        reported_conflicts = set()
        for key_name, callback in self._action_callbacks:
            hotkey_def = getattr(config, f"HOTKEY_{key_name.upper()}", None)
            if not hotkey_def or not hotkey_def.is_valid():
                logging.warning(f"跳过无效或未找到的热键定义: {key_name}")
                continue
            conflicts = self.get_hotkey_conflicts(key_name, hotkey_def)
            for conflicting_key in conflicts:
                conflict_pair = frozenset([key_name, conflicting_key])
                if conflict_pair not in reported_conflicts:
                    logging.warning(f"热键冲突: {key_name} 与 {conflicting_key} 使用相同的组合 {hotkey_def.to_string()} 且位于同一上下文")
                    reported_conflicts.add(conflict_pair)
            hotkey_defs.append((hotkey_def, key_name, callback))
        self.registered_hotkeys = hotkey_defs
        if self.listener and self.listener.is_alive():
            self.listener.update_config(hotkey_defs)
            logging.debug(f"{self.backend} 监听器已动态更新配置")
            return
        can_evdev_monitor_input = EVDEV_AVAILABLE and INPUT_AVAILABLE
        if not can_evdev_monitor_input:
            missing = []
            if not EVDEV_AVAILABLE: missing.append("未安装 evdev 库")
            if not INPUT_AVAILABLE: missing.append("缺少 /dev/input 读取权限")
            evdev_mon_err_msg = "、".join(missing)
        if IS_WAYLAND:
            logging.info("尝试使用 Evdev 监听全局热键...")
            if not can_evdev_monitor_input:
                logging.error(f"{evdev_mon_err_msg}，Wayland 下无法使用全局快捷键")
                return
            try:
                self.listener = EvdevListener(self.overlay, hotkey_defs)
                self.listener.start()
            except Exception as e:
                logging.error(f"启动 Evdev 监听器失败: {e}")
                GLib.idle_add(send_notification, "热键服务启动失败", f"无法启动 Evdev 监听: {e}\n全局快捷键将不可用", "warning", config.WARNING_SOUND)
        else:
            logging.info("尝试使用 Xlib 拦截全局热键...")
            try:
                self.listener = XlibHotkeyInterceptor(hotkey_defs)
                self.listener.start()
            except Exception as e:
                logging.warning(f"Xlib 启动失败: {e}，尝试回退到 Evdev...")
                fallback_success = False
                if can_evdev_monitor_input:
                    try:
                        self.listener = EvdevListener(self.overlay, hotkey_defs)
                        self.listener.start()
                        logging.info("已成功回退到 Evdev 全局热键监听")
                        fallback_success = True
                    except Exception as e2:
                        logging.error(f"Evdev 回退启动失败: {e2}")
                else:
                    logging.warning(f"{evdev_mon_err_msg}，无法进行回退")
                if not fallback_success:
                    logging.error("所有热键监听方式均启动失败")
                    GLib.idle_add(send_notification, "热键服务启动失败", f"Xlib 错误，且无法回退到 Evdev\n全局快捷键将不可用", "warning", config.WARNING_SOUND)

    def set_paused(self, paused: bool):
        self.is_paused = paused
        self.update_active_keys()

    def toggle_hotkeys(self):
        self.are_hotkeys_enabled = not self.are_hotkeys_enabled
        self.update_active_keys()
        state_str = "启用" if self.are_hotkeys_enabled else "禁用"
        GLib.idle_add(send_notification, "快捷键状态", f"截图会话的快捷键当前已{state_str}")
        logging.debug(f"快捷键状态已切换为: {state_str}")

    def update_active_keys(self):
        if self.is_paused:
            self.active_keys = set()
            if self.listener: self.listener.set_active_keys(set())
        else:
            active_keys = {'toggle_hotkeys_enabled'}
            if self.are_hotkeys_enabled and self.overlay:
                ctx = self.overlay.session.get_current_context()
                if ctx in self.CONTEXT_ACTION_WHITELIST:
                    active_keys.update(self.CONTEXT_ACTION_WHITELIST[ctx])
            self.active_keys = active_keys
            if self.listener:
                self.listener.set_active_keys(active_keys)
        if self.overlay and self.overlay.instruction_panel:
            GLib.idle_add(self.overlay.instruction_panel.reload_keys)

    def get_active_action(self, incoming_hotkey: HotkeyDefinition):
        if not incoming_hotkey or not incoming_hotkey.is_valid():
            return None
        for hotkey_def, key_name, callback in self.registered_hotkeys:
            if incoming_hotkey == hotkey_def and key_name in self.active_keys:
                return callback
        return None

    def get_hotkey_conflicts(self, target_key: str, hotkey_def: HotkeyDefinition) -> list:
        if not hotkey_def or not hotkey_def.is_valid():
            return []
        def get_ctxs(action):
            if action == 'toggle_hotkeys_enabled': return set(Context)
            return {c for c, acts in self.CONTEXT_ACTION_WHITELIST.items() if action in acts}
        conflicts = []
        for key in self.config.CONFIG_SCHEMA.get('Hotkeys', {}).keys():
            if key == target_key: continue
            existing_def = getattr(self.config, f"HOTKEY_{key.upper()}", None)
            if existing_def and existing_def == hotkey_def:
                if get_ctxs(target_key) & get_ctxs(key):
                    conflicts.append(key)
        return conflicts

    def enable_mouse_click_monitor(self, enabled: bool, callback=None):
        if self.listener and hasattr(self.listener, 'enable_mouse_click_monitor'):
            self.listener.enable_mouse_click_monitor(enabled, callback)

    def stop(self):
        if self.listener and self.listener.is_alive():
            self.listener.stop()

def main():
    parser = argparse.ArgumentParser(description="一个自动/辅助式长截图工具")
    parser.add_argument('-c', '--config', type=Path, help="指定一个自定义配置文件的路径")
    args = parser.parse_args()
    global config
    config = Config(custom_path=args.config)
    log_queue = SystemInteraction.setup_logging(config)
    SystemInteraction.cleanup_temp_dirs(config)
    frame_grabber = None
    if IS_WAYLAND:
        logging.info("检测到 Wayland 会话，加载 Wayland 后端")
        frame_grabber = WaylandFrameGrabber()
        if not frame_grabber.prepare():
            logging.info("用户取消了屏幕录制授权，程序静默退出")
            sys.exit(0)
    else:
        logging.info("检测到 X11 会话，加载 X11 后端")
        try:
            libx11 = SystemInteraction.load_library(['libX11.so.6', 'libX11.so'], 'X11')
            if libx11:
                libx11.XInitThreads()
                logging.debug("已调用 XInitThreads() 以确保多线程安全")
        except Exception as e:
            logging.warning(f"无法调用 XInitThreads(): {e}。应用可能不稳定")
        frame_grabber = X11FrameGrabber()
    display = Gdk.Display.get_default()
    if display is None:
        logging.error("无法获取 GDK Display，程序无法运行")
        sys.exit(1)
    logging.info("启动全屏覆盖窗口，等待用户选择区域...")
    SystemInteraction.ensure_temp_directory(config.TEMP_DIRECTORY)
    overlay = CaptureOverlay(config, frame_grabber, log_queue)
    overlay.show()
    global hotkey_manager
    hotkey_manager = HotkeyManager(config)
    hotkey_manager.setup(overlay)
    SystemInteraction.check_dependencies()
    Gtk.main()

if __name__ == "__main__":
    main()
