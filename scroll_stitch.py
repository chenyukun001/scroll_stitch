#!/usr/bin/env python3
import sys
import ctypes
from ctypes import POINTER, c_int, c_short, c_ulong, c_char_p, c_void_p, Structure
import webbrowser
import os
import shlex
import shutil
import re
import subprocess
import logging
import queue
import collections
import threading
import itertools
import abc
from pathlib import Path
from datetime import datetime
import time
import math
import bisect
import select
from PIL import Image
import cv2
import numpy as np
import configparser
import argparse
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Gdk, GLib, GObject, Pango, PangoCairo, GdkPixbuf
import cairo
IS_WAYLAND = False
try:
    gdk_display = Gdk.Display.get_default()
    if gdk_display:
        IS_WAYLAND = "wayland" in gdk_display.get_name().lower()
    else:
        IS_WAYLAND = "wayland" in os.environ.get('XDG_SESSION_TYPE', '').lower()
except Exception as e:
    IS_WAYLAND = "wayland" in os.environ.get('XDG_SESSION_TYPE', '').lower()
if not IS_WAYLAND:
    from Xlib import display, X, protocol, XK
    from Xlib.ext import xtest
    Gst, GstVideo, Gio = None, None, None
else:
    display, X, protocol, XK, xtest = None, None, None, None, None
    try:
        gi.require_version('Gst', '1.0')
        gi.require_version('GstVideo', '1.0')
        gi.require_version('Gio', '2.0')
        from gi.repository import Gst, GstVideo, Gio
    except ValueError:
        logging.error("Wayland 环境下缺少 GStreamer 依赖")
        sys.exit(1)
try:
    import evdev
    from evdev import UInput, ecodes as e, AbsInfo
    EVDEV_AVAILABLE = True
except ImportError:
    EVDEV_AVAILABLE = False
GTK_LAYER_SHELL_AVAILABLE = False
try:
    gi.require_version('GtkLayerShell', '0.1')
    from gi.repository import GtkLayerShell
    GTK_LAYER_SHELL_AVAILABLE = True
except (ImportError, ValueError):
    GTK_LAYER_SHELL_AVAILABLE = False
# 全局实例
WINDOW_MANAGER = None
FRAME_GRABBER = None
GLOBAL_OVERLAY = None
hotkey_listener = None
are_hotkeys_enabled = True
log_queue = None

class Config:
    def __init__(self, custom_path= None):
        self.parser = configparser.ConfigParser(interpolation=None)
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
            self.parser.read(self.config_path, encoding='utf-8')
        else:
            self.config_path = default_config_path
            self._create_default_config()
            self.parser.read(self.config_path, encoding='utf-8')
        self._gtk_modifier_map = {
            'ctrl': Gdk.ModifierType.CONTROL_MASK, 'control': Gdk.ModifierType.CONTROL_MASK,
            'shift': Gdk.ModifierType.SHIFT_MASK,
            'alt': Gdk.ModifierType.MOD1_MASK,
            'super': Gdk.ModifierType.SUPER_MASK, 'win': Gdk.ModifierType.SUPER_MASK,
        }
        self.GTK_MODIFIER_MASK = (
            Gdk.ModifierType.CONTROL_MASK | 
            Gdk.ModifierType.SHIFT_MASK | 
            Gdk.ModifierType.MOD1_MASK |
            Gdk.ModifierType.SUPER_MASK
        )
        self._key_map_gtk_special = {
            'space': Gdk.KEY_space, 'enter': Gdk.KEY_Return,
            'backspace': Gdk.KEY_BackSpace, 'esc': Gdk.KEY_Escape,
            'up': Gdk.KEY_Up, 'down': Gdk.KEY_Down,
            'left': Gdk.KEY_Left, 'right': Gdk.KEY_Right,
            'minus': Gdk.KEY_minus, 'equal': Gdk.KEY_equal,
            'f1': Gdk.KEY_F1, 'f2': Gdk.KEY_F2, 'f3': Gdk.KEY_F3, 'f4': Gdk.KEY_F4,
            'f5': Gdk.KEY_F5, 'f6': Gdk.KEY_F6, 'f7': Gdk.KEY_F7, 'f8': Gdk.KEY_F8,
            'f9': Gdk.KEY_F9, 'f10': Gdk.KEY_F10, 'f11': Gdk.KEY_F11, 'f12': Gdk.KEY_F12,
        }
        self._gtk_modifier_keyval_map = {
            'shift': (Gdk.KEY_Shift_L, Gdk.KEY_Shift_R),
            'ctrl': (Gdk.KEY_Control_L, Gdk.KEY_Control_R), 'control': (Gdk.KEY_Control_L, Gdk.KEY_Control_R),
            'alt': (Gdk.KEY_Alt_L, Gdk.KEY_Alt_R),
            'super': (Gdk.KEY_Super_L, Gdk.KEY_Super_R), 'win': (Gdk.KEY_Super_L, Gdk.KEY_Super_R),
        }
        self._load_settings()

    def _parse_hotkey_string(self, hotkey_str: str):
        if not hotkey_str:
            return {'gtk_keys': tuple(), 'gtk_mask': 0, 'main_key_str': None}
        original_str = hotkey_str
        parts = [p.strip() for p in hotkey_str.lower().split('+') if p.strip()]
        clean_parts = [p.replace('<', '').replace('>', '') for p in parts]
        gtk_mask = 0
        gtk_keys_tuple = tuple()
        main_key_str = None
        if len(clean_parts) == 1 and clean_parts[0] in self._gtk_modifier_keyval_map:
            main_key_str = clean_parts[0]
            gtk_keys_tuple = self._gtk_modifier_keyval_map[main_key_str]
        else:
            for part in clean_parts:
                if part in self._gtk_modifier_map:
                    gtk_mask |= self._gtk_modifier_map[part]
                else:
                    main_key_str = part
            if main_key_str:
                key_to_lookup = main_key_str
                if (gtk_mask & Gdk.ModifierType.SHIFT_MASK) and len(main_key_str) == 1 and main_key_str.isalpha():
                    key_to_lookup = main_key_str.upper()
                gtk_key_val = None
                if key_to_lookup in self._key_map_gtk_special:
                    gtk_key_val = self._key_map_gtk_special[key_to_lookup]
                elif len(key_to_lookup) >= 1:
                    gtk_key_val = Gdk.keyval_from_name(key_to_lookup)
                else:
                    logging.warning(f"无法解析GTK主按键: '{main_key_str}' in '{original_str}'")
                if gtk_key_val:
                    gtk_keys_tuple = (gtk_key_val,)
            elif not gtk_mask:
                 logging.error(f"快捷键 '{original_str}' 无效")
        return {
            'gtk_keys': gtk_keys_tuple,
            'gtk_mask': gtk_mask,
            'main_key_str': main_key_str
        }

    def _load_settings(self):
        # Behavior
        self.ENABLE_FREE_SCROLL_MATCHING = self.parser.getboolean('Behavior', 'enable_free_scroll_matching', fallback=True)
        self.SCROLL_METHOD = self.parser.get('Behavior', 'scroll_method', fallback='move_user_cursor')
        self.CAPTURE_WITH_CURSOR = self.parser.getboolean('Behavior', 'capture_with_cursor', fallback=False)
        self.REUSE_INVISIBLE_CURSOR = self.parser.getboolean('Behavior', 'reuse_invisible_cursor', fallback=False)
        self.FORWARD_ACTION = self.parser.get('Behavior', 'forward_action', fallback='capture_scroll')
        self.BACKWARD_ACTION = self.parser.get('Behavior', 'backward_action', fallback='scroll_delete')
        # Interface.Components
        self.ENABLE_BUTTONS = self.parser.getboolean('Interface.Components', 'enable_buttons', fallback=True)
        self.ENABLE_GRID_ACTION_BUTTONS = self.parser.getboolean('Interface.Components', 'enable_grid_action_buttons', fallback=True)
        self.ENABLE_AUTO_SCROLL_BUTTONS = self.parser.getboolean('Interface.Components', 'enable_auto_scroll_buttons', fallback=True)
        self.ENABLE_SIDE_PANEL = self.parser.getboolean('Interface.Components', 'enable_side_panel', fallback=True)
        self.SHOW_PREVIEW_ON_START = self.parser.getboolean('Interface.Components', 'show_preview_on_start', fallback=True)
        self.SHOW_CAPTURE_COUNT = self.parser.getboolean('Interface.Components', 'show_capture_count', fallback=True)
        self.SHOW_TOTAL_DIMENSIONS = self.parser.getboolean('Interface.Components', 'show_total_dimensions', fallback=True)
        self.SHOW_CURRENT_MODE = self.parser.getboolean('Interface.Components', 'show_current_mode', fallback=True)
        self.SHOW_INSTRUCTION_PANEL_ON_START = self.parser.getboolean('Interface.Components', 'show_instruction_panel_on_start', fallback=True)
        # Interface.Layout 
        # 逻辑px {
        self.BORDER_WIDTH = self.parser.getint('Interface.Layout', 'border_width', fallback=4)
        self.HANDLE_HEIGHT = self.parser.getint('Interface.Layout', 'handle_height', fallback=10) 
        self.BUTTON_PANEL_WIDTH = self.parser.getint('Interface.Layout', 'button_panel_width', fallback=100)
        self.SIDE_PANEL_WIDTH = self.parser.getint('Interface.Layout', 'side_panel_width', fallback=150)
        self.BUTTON_SPACING = self.parser.getint('Interface.Layout', 'button_spacing', fallback=5)
        self.PROCESSING_DIALOG_WIDTH = self.parser.getint('Interface.Layout', 'processing_dialog_width', fallback=200)
        self.PROCESSING_DIALOG_HEIGHT = self.parser.getint('Interface.Layout', 'processing_dialog_height', fallback=90)
        self.PROCESSING_DIALOG_SPACING = self.parser.getint('Interface.Layout', 'processing_dialog_spacing', fallback=15)
        self.PROCESSING_DIALOG_BORDER_WIDTH = self.parser.getint('Interface.Layout', 'processing_dialog_border_width', fallback=20)
        # Interface.Theme
        color_str = self.parser.get('Interface.Theme', 'border_color', fallback='0.73, 0.25, 0.25, 1.00')
        self.BORDER_COLOR = tuple(float(c.strip()) for c in color_str.split(','))
        indicator_color_str = self.parser.get('Interface.Theme', 'matching_indicator_color', fallback='0.60, 0.76, 0.95, 1.00')
        self.MATCHING_INDICATOR_COLOR = tuple(float(c.strip()) for c in indicator_color_str.split(','))
        self.PROCESSING_DIALOG_CSS = self.parser.get('Interface.Theme', 'processing_dialog_css', fallback="""
.processing-dialog-bg { background-color: rgba(20, 20, 30, 0.85); border-radius: 8px; color: white; font-size: 26px; }
        """.strip()).lstrip()
        self.INFO_PANEL_CSS = self.parser.get('Interface.Theme', 'info_panel_css', fallback="""
.info-panel, .info-panel:backdrop { background-color: rgba(43, 42, 51, 0.8); border: 1px solid #505070; border-radius: 8px; padding: 5px; color: #e0e0e0; }
.info-panel label, .info-panel label:backdrop { font-weight: bold; color: #e0e0e0; }
.info-panel #label_dimensions, .info-panel #label_dimensions:backdrop { font-size: 26px; color: #948bc1; }
.info-panel #label_count, .info-panel #label_count:backdrop { font-size: 24px; opacity: 0.9; color: #e0e0e0; }
.info-panel #label_mode, .info-panel #label_mode:backdrop { font-size: 23px; opacity: 0.9; color: #e0e0e0; }
        """.strip()).lstrip()
        self.INSTRUCTION_PANEL_CSS = self.parser.get('Interface.Theme', 'instruction_panel_css', fallback="""
.instruction-panel { background-color: rgba(30, 30, 30, 0.85); border: 1px solid #555; border-radius: 6px; padding: 10px; color: #f0f0f0; font-size: 18px; }
.instruction-panel label { margin-bottom: 2px; }
.key-label { font-weight: bold; color: #8be9fd; margin-right: 10px; }
.desc-label { color: #f8f8f2; }
        """.strip()).lstrip()
        self.NOTIFICATION_CSS = self.parser.get('Interface.Theme', 'notification_css', fallback="""
.notification-panel { background-color: rgba(40, 40, 45, 0.98); border: 1px solid rgba(255,255,255,0.2); border-radius: 12px; padding: 24px; color: white; }
.notification-critical .notif-title { color: #ff5555; border-bottom-color: rgba(255, 85, 85, 0.5); }
.notification-warning .notif-title { color: #ec9028; border-bottom-color: rgba(236, 144, 40, 0.5); }
.notification-success .notif-title { color: #78c93f; border-bottom-color: rgba(120, 201, 63, 0.5); }
.notif-title { font-weight: bold; font-size: 26px; color: #f5f5f5; border-bottom: 1px solid rgba(255,255,255,0.5); padding-bottom: 8px; }
.notif-msg { font-size: 20px; color: #f8f8f2; }
.notif-btn { padding: 6px 20px; font-size: 22px; margin-left: 4px; margin-right: 4px; border-radius: 6px; font-weight: bold; min-width: 80px; }
        """.strip()).lstrip()
        self.DIALOG_CSS = self.parser.get('Interface.Theme', 'dialog_css', fallback="""
.embedded-dialog { background-color: rgba(35, 35, 40, 0.95); border: 1px solid rgba(255, 255, 255, 0.15); border-radius: 16px; color: #ffffff; padding: 30px; }
.dialog-title { font-size: 28px; font-weight: bold; color: #f5f5f5; }
.dialog-message { font-size: 24px; margin-bottom: 10px; color: #e0e0e0; }
.dialog-btn { border-radius: 8px; border-width: 1px; border-style: solid; padding: 10px 30px; margin: 0 10px; }
        """.strip()).lstrip()
        self.MASK_CSS = self.parser.get('Interface.Theme', 'mask_css', fallback="""
.mask-layer { background-color: rgba(0, 0, 0, 0.6); }
        """.strip()).lstrip()
        self.SIMULATED_WINDOW_CSS = self.parser.get('Interface.Theme', 'simulated_window_css', fallback="""
.simulated-window, .simulated-window:backdrop { background-color: #fdfdfd; color: #2e3436; border: 1px solid #b0b0b0; border-radius: 8px; box-shadow: 0 3px 10px rgba(0,0,0,0.2); }
.window-header, .window-header:backdrop { background-color: #f2f2f2; border-bottom: 1px solid #dcdcdc; border-radius: 8px 8px 0 0; padding: 6px 10px; }
.window-title, .window-title:backdrop { font-size: 28px; font-weight: bold; color: #333333; text-shadow: none; }
        """.strip()).lstrip()
        # 逻辑px }
        # Interface.Strings
        self.DIALOG_QUIT_TITLE = self.parser.get('Interface.Strings', 'dialog_quit_title', fallback='确认放弃截图？')
        self.DIALOG_QUIT_MESSAGE = self.parser.get('Interface.Strings', 'dialog_quit_message', fallback='您已经截取了 {count} 张图片。确定要放弃它们吗？')
        self.DIALOG_QUIT_BTN_YES = self.parser.get('Interface.Strings', 'dialog_quit_button_yes', fallback='是 ({key})')
        self.DIALOG_QUIT_BTN_NO = self.parser.get('Interface.Strings', 'dialog_quit_button_no', fallback='否 ({key})')
        self.STR_CAPTURE_COUNT_FORMAT = self.parser.get('Interface.Strings', 'capture_count_format', fallback='截图: {count}')
        self.STR_PROCESSING_TEXT = self.parser.get('Interface.Strings', 'processing_dialog_text', fallback='正在处理…')
        # Output
        self.SAVE_DIRECTORY = Path(self.parser.get('Output', 'save_directory', fallback='~/Pictures/截图')).expanduser()
        self.SAVE_FORMAT = self.parser.get('Output', 'save_format', fallback='PNG').upper()
        self.JPEG_QUALITY = self.parser.getint('Output', 'jpeg_quality', fallback=95)
        self.FILENAME_TEMPLATE = self.parser.get('Output', 'filename_template', fallback='长截图 {timestamp}')
        self.FILENAME_TIMESTAMP_FORMAT = self.parser.get('Output', 'filename_timestamp_format', raw=True, fallback='%Y-%m-%d %H-%M-%S')
        # System
        self.COPY_TO_CLIPBOARD = self.parser.getboolean('System', 'copy_to_clipboard_on_finish', fallback=True)
        self.NOTIFICATION_CLICK_ACTION = self.parser.get('System', 'notification_click_action', fallback='open_file').lower().strip()
        self.LARGE_IMAGE_OPENER = self.parser.get('System', 'large_image_opener', fallback='default_browser').strip()
        self.SOUND_THEME = self.parser.get('System', 'sound_theme', fallback='freedesktop')
        self.CAPTURE_SOUND = self.parser.get('System', 'capture_sound', fallback='screen-capture')
        self.UNDO_SOUND = self.parser.get('System', 'undo_sound', fallback='bell')
        self.FINALIZE_SOUND = self.parser.get('System', 'finalize_sound', fallback='complete')
        log_file_path_str = self.parser.get('System', 'log_file', fallback='~/.scroll_stitch.log')
        self.LOG_FILE = Path(log_file_path_str).expanduser()
        temp_dir_str = self.parser.get('System', 'temp_directory_base', fallback='/tmp/scroll_stitch_{pid}')
        self.TMP_DIR = Path(temp_dir_str.format(pid=os.getpid()))
        # Performance
        # 缓冲区px {
        self.GRID_MATCHING_MAX_OVERLAP = self.parser.getint('Performance', 'grid_matching_max_overlap', fallback=20)
        self.FREE_SCROLL_MATCHING_MAX_OVERLAP = self.parser.getint('Performance', 'free_scroll_matching_max_overlap', fallback=200)
        self.MIN_SCROLL_PER_TICK = self.parser.getint('Performance', 'min_scroll_per_tick', fallback=30)
        self.MAX_SCROLL_PER_TICK = self.parser.getint('Performance', 'max_scroll_per_tick', fallback=230)
        self.MAX_VIEWER_DIMENSION = self.parser.getint('Performance', 'max_viewer_dimension', fallback=32767)
        # 缓冲区px }
        self.AUTO_SCROLL_TICKS_PER_STEP = self.parser.getint('Performance', 'auto_scroll_ticks_per_step', fallback=2)
        self.PREVIEW_DRAG_SENSITIVITY = self.parser.getfloat('Performance', 'preview_drag_sensitivity', fallback=2.0)
        # Hotkeys
        self.str_capture = self.parser.get('Hotkeys', 'capture', fallback='space')
        self.str_finalize = self.parser.get('Hotkeys', 'finalize', fallback='enter')
        self.str_undo = self.parser.get('Hotkeys', 'undo', fallback='backspace')
        self.str_cancel = self.parser.get('Hotkeys', 'cancel', fallback='esc')
        self.str_dialog_confirm = self.parser.get('Hotkeys', 'dialog_confirm', fallback='space')
        self.str_dialog_cancel = self.parser.get('Hotkeys', 'dialog_cancel', fallback='esc')
        self.str_grid_backward = self.parser.get('Hotkeys', 'grid_backward', fallback='b')
        self.str_grid_forward = self.parser.get('Hotkeys', 'grid_forward', fallback='f')
        self.str_auto_scroll_start = self.parser.get('Hotkeys', 'auto_scroll_start', fallback='s')
        self.str_auto_scroll_stop = self.parser.get('Hotkeys', 'auto_scroll_stop', fallback='e')
        self.str_configure_scroll_unit = self.parser.get('Hotkeys', 'configure_scroll_unit', fallback='c')
        self.str_toggle_grid_mode = self.parser.get('Hotkeys', 'toggle_grid_mode', fallback='<shift>')
        self.str_open_config_editor = self.parser.get('Hotkeys', 'open_config_editor', fallback='g')
        self.str_toggle_preview = self.parser.get('Hotkeys', 'toggle_preview', fallback='w')
        self.str_preview_zoom_in = self.parser.get('Hotkeys', 'preview_zoom_in', fallback='<ctrl>+equal')
        self.str_preview_zoom_out = self.parser.get('Hotkeys', 'preview_zoom_out', fallback='<ctrl>+minus')
        self.str_toggle_hotkeys_enabled = self.parser.get('Hotkeys', 'toggle_hotkeys_enabled', fallback='f4')
        self.str_toggle_instruction_panel = self.parser.get('Hotkeys', 'toggle_instruction_panel', fallback='f1')
        self.HOTKEY_CAPTURE = self._parse_hotkey_string(self.str_capture)
        self.HOTKEY_FINALIZE = self._parse_hotkey_string(self.str_finalize)
        self.HOTKEY_UNDO = self._parse_hotkey_string(self.str_undo)
        self.HOTKEY_CANCEL = self._parse_hotkey_string(self.str_cancel)
        self.HOTKEY_GRID_BACKWARD = self._parse_hotkey_string(self.str_grid_backward)
        self.HOTKEY_GRID_FORWARD = self._parse_hotkey_string(self.str_grid_forward)
        self.HOTKEY_AUTO_SCROLL_START = self._parse_hotkey_string(self.str_auto_scroll_start)
        self.HOTKEY_AUTO_SCROLL_STOP = self._parse_hotkey_string(self.str_auto_scroll_stop)
        self.HOTKEY_CONFIGURE_SCROLL_UNIT = self._parse_hotkey_string(self.str_configure_scroll_unit)
        self.HOTKEY_TOGGLE_GRID_MODE = self._parse_hotkey_string(self.str_toggle_grid_mode)
        self.HOTKEY_TOGGLE_PREVIEW = self._parse_hotkey_string(self.str_toggle_preview)
        self.HOTKEY_OPEN_CONFIG_EDITOR = self._parse_hotkey_string(self.str_open_config_editor)
        self.HOTKEY_TOGGLE_INSTRUCTION_PANEL = self._parse_hotkey_string(self.str_toggle_instruction_panel)
        self.HOTKEY_TOGGLE_HOTKEYS_ENABLED = self._parse_hotkey_string(self.str_toggle_hotkeys_enabled)
        self.HOTKEY_PREVIEW_ZOOM_IN = self._parse_hotkey_string(self.str_preview_zoom_in)
        self.HOTKEY_PREVIEW_ZOOM_OUT = self._parse_hotkey_string(self.str_preview_zoom_out)
        self.HOTKEY_DIALOG_CONFIRM = self._parse_hotkey_string(self.str_dialog_confirm)
        self.HOTKEY_DIALOG_CANCEL = self._parse_hotkey_string(self.str_dialog_cancel)

    @staticmethod
    def get_default_config_string():
        """返回包含所有默认设置的配置字符串"""
        return """
[Behavior]
enable_free_scroll_matching = true
scroll_method = move_user_cursor
capture_with_cursor = false
reuse_invisible_cursor = false
forward_action = capture_scroll
backward_action = scroll_delete

[Interface.Components]
enable_buttons = true
enable_grid_action_buttons = true
enable_auto_scroll_buttons = true
enable_side_panel = true
show_preview_on_start = true
show_capture_count = true
show_total_dimensions = true
show_current_mode = true
show_instruction_panel_on_start = true

[Interface.Layout]
border_width = 4
handle_height = 10
button_panel_width = 100
side_panel_width = 150
button_spacing = 5
processing_dialog_width = 200
processing_dialog_height = 90
processing_dialog_spacing = 15
processing_dialog_border_width = 20

[Interface.Theme]
border_color = 0.73, 0.25, 0.25, 1.00
matching_indicator_color = 0.60, 0.76, 0.95, 1.00
processing_dialog_css = 
    .processing-dialog-bg { background-color: rgba(20, 20, 30, 0.85); border-radius: 8px; color: white; font-size: 26px; }
info_panel_css = 
    .info-panel, .info-panel:backdrop { background-color: rgba(43, 42, 51, 0.8); border: 1px solid #505070; border-radius: 8px; padding: 5px; color: #e0e0e0; }
    .info-panel label, .info-panel label:backdrop { font-weight: bold; color: #e0e0e0; }
    .info-panel #label_dimensions, .info-panel #label_dimensions:backdrop { font-size: 26px; color: #948bc1; }
    .info-panel #label_count, .info-panel #label_count:backdrop { font-size: 24px; opacity: 0.9; color: #e0e0e0; }
    .info-panel #label_mode, .info-panel #label_mode:backdrop { font-size: 23px; opacity: 0.9; color: #e0e0e0; }
instruction_panel_css = 
    .instruction-panel { background-color: rgba(30, 30, 30, 0.85); border: 1px solid #555; border-radius: 6px; padding: 10px; color: #f0f0f0; font-size: 18px; }
	.instruction-panel label { margin-bottom: 2px; }
	.key-label { font-weight: bold; color: #8be9fd; margin-right: 10px; }
	.desc-label { color: #f8f8f2; }
notification_css = 
    .notification-panel { background-color: rgba(40, 40, 45, 0.98); border: 1px solid rgba(255,255,255,0.2); border-radius: 12px; padding: 24px; color: white; }
	.notification-critical .notif-title { color: #ff5555; border-bottom-color: rgba(255, 85, 85, 0.5); }
	.notification-warning .notif-title { color: #ec9028; border-bottom-color: rgba(236, 144, 40, 0.5); }
    .notification-success .notif-title { color: #78c93f; border-bottom-color: rgba(120, 201, 63, 0.5); }
	.notif-title { font-weight: bold; font-size: 26px; color: #f5f5f5; border-bottom: 1px solid rgba(255,255,255,0.5); padding-bottom: 8px; }
	.notif-msg { font-size: 20px; color: #f8f8f2; }
	.notif-btn { padding: 6px 20px; font-size: 22px; margin-left: 4px; margin-right: 4px; border-radius: 6px; font-weight: bold; min-width: 80px; }
dialog_css = 
    .embedded-dialog { background-color: rgba(35, 35, 40, 0.95); border: 1px solid rgba(255, 255, 255, 0.15); border-radius: 16px; color: #ffffff; padding: 30px; }
	.dialog-title { font-size: 28px; font-weight: bold; color: #f5f5f5; }
	.dialog-message { font-size: 24px; margin-bottom: 10px; color: #e0e0e0; }
	.dialog-btn { border-radius: 8px; border-width: 1px; border-style: solid; padding: 10px 30px; margin: 0 10px; }
mask_css = 
    .mask-layer { background-color: rgba(0, 0, 0, 0.6); }
simulated_window_css = 
    .simulated-window, .simulated-window:backdrop { background-color: #fdfdfd; color: #2e3436; border: 1px solid #b0b0b0; border-radius: 8px; box-shadow: 0 3px 10px rgba(0,0,0,0.2); }
	.window-header, .window-header:backdrop { background-color: #f2f2f2; border-bottom: 1px solid #dcdcdc; border-radius: 8px 8px 0 0; padding: 6px 10px; }
	.window-title, .window-title:backdrop { font-size: 28px; font-weight: bold; color: #333333; text-shadow: none; }

[Interface.Strings]
dialog_quit_title = 确认放弃截图？
dialog_quit_message = 您已经截取了 {count} 张图片。确定要放弃它们吗？
dialog_quit_button_yes = 是 ({key})
dialog_quit_button_no = 否 ({key})
processing_dialog_text = 正在处理…
capture_count_format = 截图: {count}

[Output]
save_directory = ~/Pictures/截图
save_format = PNG
jpeg_quality = 95
filename_template = 长截图 {timestamp}
filename_timestamp_format = %Y-%m-%d %H-%M-%S

[System]
copy_to_clipboard_on_finish = true
notification_click_action = open_file
large_image_opener = default_browser
sound_theme = freedesktop
capture_sound = screen-capture
undo_sound = bell
finalize_sound = complete
log_file = ~/.scroll_stitch.log
temp_directory_base = /tmp/scroll_stitch_{pid}

[Performance]
grid_matching_max_overlap = 20
free_scroll_matching_max_overlap = 200
auto_scroll_ticks_per_step = 2
max_scroll_per_tick = 230
min_scroll_per_tick = 30
max_viewer_dimension = 32767
preview_drag_sensitivity = 2.0

[Hotkeys]
capture = space
finalize = enter
undo = backspace
cancel = esc
grid_backward = b
grid_forward = f
auto_scroll_start = s
auto_scroll_stop = e
configure_scroll_unit = c
toggle_grid_mode = <shift>
open_config_editor = g
toggle_preview = w
toggle_instruction_panel = f1
toggle_hotkeys_enabled = f4
preview_zoom_in = <ctrl>+equal
preview_zoom_out = <ctrl>+minus
dialog_confirm = space
dialog_cancel = esc

[ApplicationScrollUnits]
        """.strip()

    def _create_default_config(self):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, 'w', encoding='utf-8') as f:
            f.write(Config.get_default_config_string())
        logging.info(f"已在 {self.config_path} 目录下创建默认配置文件")

    def get_scroll_unit(self, app_class: str):
        """从配置中获取指定应用程序的滚动单位和模板匹配设置"""
        if self.parser.has_section('ApplicationScrollUnits'):
            value_str = self.parser.get('ApplicationScrollUnits', app_class, fallback='0,false')
            parts = [p.strip() for p in value_str.split(',')]
            try:
                unit = int(parts[0])
                enabled = parts[1].lower() == 'true' if len(parts) > 1 else False
                return unit, enabled
            except (ValueError, IndexError):
                return 0, False
        return 0, False

    def save_scroll_unit(self, app_class: str, unit_value: int, matching_enabled: bool):
        """将计算出的滚动单位和匹配设置保存到配置文件中"""
        # unit_value: 缓冲区px
        try:
            if not self.parser.has_section('ApplicationScrollUnits'):
                self.parser.add_section('ApplicationScrollUnits')
            value_to_save = f"{unit_value},{str(matching_enabled).lower()}"
            self.parser.set('ApplicationScrollUnits', app_class, value_to_save)
            with open(self.config_path, 'w', encoding='utf-8') as configfile:
                self.parser.write(configfile)
            logging.info(f"成功将配置 '{app_class} = {value_to_save}' 写入 {self.config_path}")
            return True
        except Exception as e:
            logging.error(f"写入配置文件失败: {e}")
            return False

    def save_setting(self, section: str, key: str, value: str):
        try:
            if not self.parser.has_section(section):
                self.parser.add_section(section)
            self.parser.set(section, key, str(value))
            with open(self.config_path, 'w', encoding='utf-8') as configfile:
                self.parser.write(configfile)
            logging.info(f"成功将配置 '{key} = {value}' 写入 [{section}]")
            return True
        except Exception as e:
            logging.error(f"写入配置文件失败: {e}")
            return False

class InvisibleCursorScroller:
    def __init__(self, min_x, min_y, max_x, max_y, park_x, park_y, config: Config):
        self.config = config
        # 缓冲区px全局坐标
        self.min_x = min_x
        self.min_y = min_y
        self.max_x = max_x
        self.max_y = max_y
        self.park_x = park_x
        self.park_y = park_y
        self.master_id = None
        self.ui_mouse = None
        self.unique_name = "scroll-stitch-cursor"
        self.is_ready = False

    def _device_exists(self, device_name):
        """检查具有给定名称的 xinput 设备是否存在"""
        try:
            output = subprocess.check_output(['xinput', 'list', '--name-only']).decode()
            return device_name in output.splitlines()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def _get_all_master_ids(self, master_name):
        """获取所有具有指定名称的主指针设备的ID列表"""
        ids = []
        try:
            output = subprocess.check_output(['xinput', 'list']).decode()
            pattern = fr'{re.escape(master_name)} pointer\s+id=(\d+)'
            matches = re.findall(pattern, output)
            ids = [int(match) for match in matches]
            logging.info(f"找到 {len(ids)} 个名为 '{master_name}' 的主指针设备: {ids}")
        except (subprocess.CalledProcessError, FileNotFoundError, ValueError) as e:
            logging.error(f"查找主设备 ID 时出错: {e}")
        return ids

    def _wait_for_device(self, device_name, timeout=3):
        """轮询 'xinput list' 直到找到指定的设备或超时"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                output = subprocess.check_output(['xinput', 'list']).decode()
                if device_name in output:
                    logging.info(f"设备 '{device_name}' 已被 X Server 识别")
                    return True
            except subprocess.CalledProcessError:
                pass
            time.sleep(0.1)
        logging.error(f"等待设备 '{device_name}' 超时（{timeout}秒）")
        return False

    def setup(self):
        try:
            master_pointer_name = f"{self.unique_name} pointer"
            mouse_dev_name = f"VirtualMouse-{self.unique_name}"
            existing_master_ids = self._get_all_master_ids(self.unique_name)
            master_id_to_use = None
            if not self.config.REUSE_INVISIBLE_CURSOR:
                if existing_master_ids:
                    logging.info("配置为不复用，正在尝试清理所有检测到的旧主设备...")
                    for old_id in existing_master_ids:
                        try:
                            result = subprocess.run(
                                ['xinput', 'remove-master', str(old_id)],
                                check=False, capture_output=True, text=True, timeout=1
                            )
                            if result.returncode == 0:
                                logging.info(f"成功移除旧主设备 ID: {old_id}")
                            else:
                                logging.warning(f"尝试移除旧主设备 ID {old_id} 未成功 (可能已被移除或权限问题). stderr: {result.stderr.strip()}")
                        except subprocess.TimeoutExpired:
                            logging.warning(f"移除旧主设备 ID {old_id} 超时.")
                        except Exception as e_remove:
                            logging.warning(f"尝试移除旧主设备 ID {old_id} 时发生异常: {e_remove}")
                    existing_master_ids = []
                else:
                    logging.info("配置为不复用，且未检测到旧主设备。")
            else:
                if len(existing_master_ids) == 0:
                    logging.info("配置为复用，但未找到现有设备，将创建新设备。")
                elif len(existing_master_ids) == 1:
                    master_id_to_use = existing_master_ids[0]
                    logging.info(f"配置为复用，找到唯一现有设备 ID: {master_id_to_use}，将复用。")
                else:
                    logging.warning(f"配置为复用，但检测到多个 ({len(existing_master_ids)}) 同名主设备: {existing_master_ids}。将尝试复用第一个 ID: {existing_master_ids[0]}")
                    master_id_to_use = existing_master_ids[0]
            if master_id_to_use is None:
                logging.info(f"创建新的主指针设备 '{self.unique_name}'")
                ids_before = self._get_all_master_ids(self.unique_name)
                subprocess.check_call(['xinput', 'create-master', self.unique_name])
                time.sleep(0.2)
                new_master_id = None
                output_after = ""
                for _ in range(10):
                    ids_after = self._get_all_master_ids(self.unique_name)
                    diff_ids = list(set(ids_after) - set(ids_before))
                    if len(diff_ids) == 1:
                        new_master_id = diff_ids[0]
                        logging.info(f"成功识别新创建的主设备 ID: {new_master_id}")
                        break
                    elif len(diff_ids) > 1:
                         logging.warning(f"检测到多个新设备 ID: {diff_ids}，将使用第一个: {diff_ids[0]}")
                         new_master_id = diff_ids[0]
                         break
                    time.sleep(0.1)
                else:
                    ids_now = self._get_all_master_ids(self.unique_name)
                    if len(ids_now) == len(ids_before) + 1:
                        new_master_id = max(ids_now) if ids_now else None
                    else:
                        raise RuntimeError(f"创建主设备后无法可靠地识别其 ID。创建前: {ids_before}, 当前: {ids_now}")
                self.master_id = new_master_id
                self._create_virtual_devices()
                if not self._wait_for_device(mouse_dev_name):
                    logging.error("虚拟设备未能及时被 X Server 识别。尝试清理...")
                    try:
                        subprocess.run(['xinput', 'remove-master', str(self.master_id)], check=False)
                    except Exception as e_cleanup:
                        logging.warning(f"清理失败的主设备 {self.master_id} 时出错: {e_cleanup}")
                logging.info(f"将新虚拟设备附加到主设备 ID {self.master_id}")
                subprocess.check_call(['xinput', 'reattach', mouse_dev_name, str(self.master_id)])
            else:
                self.master_id = master_id_to_use
                try:
                    self._create_virtual_devices()
                    logging.info(f"尝试重新打开 UInput 句柄以复用设备 (Master ID: {self.master_id})。")
                    subprocess.check_call(['xinput', 'reattach', mouse_dev_name, str(self.master_id)])
                    logging.info(f"已重新附加虚拟设备到 Master ID: {self.master_id}")
                except Exception as e_reopen:
                    logging.warning(f"复用设备 (Master ID: {self.master_id}) 时重新打开 UInput 或重新附加失败: {e_reopen}。滚动功能可能无效。")
            self.park()
            logging.info(f"隐形光标设置完成 (Master ID: {self.master_id})")
            self.is_ready = True
            return self
        except Exception as e:
            logging.error(f"创建/设置隐形光标失败: {e}")
            self.cleanup()
            self.is_ready = False
            return self

    def park(self):
        self.move(self.park_x, self.park_y)
        logging.info(f"隐形光标已停放至全局坐标 ({self.park_x}, {self.park_y})处")

    def _create_virtual_devices(self):
        # 虚拟鼠标 (用于移动光标)
        mouse_caps = {
            e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT],
            e.EV_REL: [e.REL_WHEEL],
            e.EV_ABS: [
                (e.ABS_X, AbsInfo(value=self.min_x, min=self.min_x, max=self.max_x, fuzz=0, flat=0, resolution=0)),
                (e.ABS_Y, AbsInfo(value=self.min_y, min=self.min_y, max=self.max_y, fuzz=0, flat=0, resolution=0)),
            ],
        }
        self.ui_mouse = UInput(mouse_caps, name=f'VirtualMouse-{self.unique_name}')

    def move(self, x, y):
        # x, y: 缓冲区px全局坐标
        self.ui_mouse.write(e.EV_ABS, e.ABS_X, x)
        self.ui_mouse.write(e.EV_ABS, e.ABS_Y, y)
        self.ui_mouse.syn()

    def discrete_scroll(self, num_clicks):
        """模拟鼠标滚轮进行离散滚动"""
        if num_clicks == 0:
            return
        value = -1 if num_clicks < 0 else 1
        for _ in range(abs(num_clicks)):
            self.ui_mouse.write(e.EV_REL, e.REL_WHEEL, value)
            self.ui_mouse.syn()
            time.sleep(0.01)

    def cleanup(self):
        if not self.config.REUSE_INVISIBLE_CURSOR:
            logging.info("清理隐形光标资源")
            if self.ui_mouse:
                self.ui_mouse.close()
                self.ui_mouse = None
            if self.master_id is not None:
                try:
                     command = ['xinput', 'remove-master', str(self.master_id)]
                     subprocess.check_call(command)
                     logging.info(f"已移除隐形光标 (Master ID: {self.master_id})")
                except Exception as e:
                     logging.warning(f"清理隐形光标主设备时出错: {e}")
            self.master_id = None
        else:
            logging.info("跳过隐形光标资源清理（启用复用）")
            if self.is_ready:
                self.park()

class EvdevWheelScroller:
    """一个虚拟鼠标，用于触发滚轮事件"""
    def __init__(self):
        self.REL_WHEEL_HI_RES = getattr(e, 'REL_WHEEL_HI_RES', 0x08)
        capabilities = {
            e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT],
            e.EV_REL: [e.REL_WHEEL, self.REL_WHEEL_HI_RES],
        }
        # UInput 的初始化
        self.ui_device = UInput(capabilities, name='scroll_stitch-wheel-mouse', version=0x1)
        logging.info("EvdevWheelScroller 初始化成功，虚拟滚轮鼠标已创建")

    def scroll_discrete(self, num_clicks):
        """模拟鼠标滚轮进行离散滚动"""
        if num_clicks == 0:
            return
        value = -1 if num_clicks < 0 else 1
        hi_res_value = value * 120
        for _ in range(abs(num_clicks)):
            self.ui_device.write(e.EV_REL, e.REL_WHEEL, value)
            self.ui_device.write(e.EV_REL, self.REL_WHEEL_HI_RES, hi_res_value)
            self.ui_device.syn()
            time.sleep(0.01)

    def close(self):
        if self.ui_device:
            self.ui_device.close()
            logging.info("虚拟滚轮鼠标已关闭")

class EvdevAbsoluteMouse:
    """在 Wayland 下使用绝对定位设备来移动鼠标"""
    def __init__(self, min_x, min_y, max_x, max_y):
        # 缓冲区px全局坐标
        self.min_x = min_x
        self.min_y = min_y
        self.max_x = max_x
        self.max_y = max_y
        caps = {
            e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE],
            e.EV_ABS: [
                (e.ABS_X, AbsInfo(value=self.min_x, min=self.min_x, max=self.max_x, fuzz=0, flat=0, resolution=0)),
                (e.ABS_Y, AbsInfo(value=self.min_y, min=self.min_y, max=self.max_y, fuzz=0, flat=0, resolution=0)),
            ]
        }
        self.device = UInput(caps, name='scroll-stitch-mover', version=0x1)
        logging.info(f"EvdevAbsoluteMouse 已创建: 全局坐标范围 ({min_x},{min_y}) -> ({max_x},{max_y})")

    def move(self, x, y):
        # x,y: 缓冲区px全局坐标
        self.device.write(e.EV_ABS, e.ABS_X, int(x))
        self.device.write(e.EV_ABS, e.ABS_Y, int(y))
        self.device.syn()

    def close(self):
        if self.device:
            self.device.close()

def play_sound(sound_name: str, theme_name: str = None):
    if not sound_name:
        return
    effective_theme = theme_name if theme_name is not None else config.SOUND_THEME
    if not effective_theme:
        logging.warning("播放声音失败：未指定有效的主题")
        return
    base_path = Path(f"/usr/share/sounds/{effective_theme}/stereo/")
    if not base_path.is_dir():
        logging.warning(f"声音主题目录不存在: {base_path}")
        return
    sound_file_path = None
    for ext in ['.oga', '.wav', '.ogg']:
        path_to_check = base_path / f"{sound_name}{ext}"
        if path_to_check.is_file():
            sound_file_path = str(path_to_check)
            break
    if not sound_file_path:
        logging.warning(f"在主题 '{effective_theme}' 中未找到声音文件: {sound_name}")
        return
    try:
        subprocess.Popen(["paplay", sound_file_path])
        logging.info(f"正在播放声音: {sound_file_path}")
    except FileNotFoundError:
        logging.warning(f"播放命令 'paplay' 未找到，请确保已安装")

class EmbeddedNotificationPanel(Gtk.EventBox):
    def __init__(self, overlay, title, message, level="normal", action_path=None, width=0, height=0):
        super().__init__()
        self.overlay = overlay
        self.action_path = Path(action_path) if action_path else None
        self.width = width
        self.height = height
        # 逻辑px {
        self.set_size_request(520, -1)
        self.set_visible_window(True)
        self.get_style_context().add_class("notification-panel")
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
        lbl_msg.set_max_width_chars(50)
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
        self.connect("enter-notify-event", self._on_enter)
        self.connect("leave-notify-event", self._on_leave)
        self.connect("button-press-event", self._on_button_press)
        self.connect("button-release-event", self._on_button_release)

    def _on_enter(self, widget, event):
        if self.overlay and self.overlay.get_window():
            self.overlay.get_window().set_cursor(self.overlay.cursors['default'])
        return False

    def _on_leave(self, widget, event):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        if self.overlay and self.overlay.get_window():
            target = self.overlay.cursors['crosshair'] if not self.overlay.is_selection_done else None
            self.overlay.get_window().set_cursor(target)
        return False

    def _on_button_press(self, widget, event):
        return True

    def _on_button_release(self, widget, event):
        return True

    def _on_open_file(self, widget):
        if not self.action_path: return
        try:
            is_large = False
            if config.MAX_VIEWER_DIMENSION >= 0 and self.width > 0 and self.height > 0:
                if max(self.width, self.height) > config.MAX_VIEWER_DIMENSION:
                    is_large = True
            opener = config.LARGE_IMAGE_OPENER
            if is_large and opener == 'default_browser':
                webbrowser.open(self.action_path.as_uri())
            elif is_large and opener.strip():
                command_str = opener.replace('{filepath}', str(self.action_path))
                subprocess.Popen(shlex.split(command_str))
            else:
                subprocess.Popen(['xdg-open', str(self.action_path)])
            self.close()
        except Exception as e:
            logging.error(f"打开文件失败: {e}")

    def _on_open_dir(self, widget):
        if not self.action_path: return
        try:
            target = self.action_path if self.action_path.is_dir() else self.action_path.parent
            subprocess.Popen(['xdg-open', str(target)])
            self.close()
        except Exception as e:
            logging.error(f"打开目录失败: {e}")

    def close(self):
        self.overlay.dismiss_notification(self)

def send_desktop_notification(title, message, sound_name=None, level="normal", action_path=None, controller=None, width=0, height=0):
    try:
        if sound_name:
            play_sound(sound_name)
        overlay = None
        if controller and hasattr(controller, 'view'):
            overlay = controller.view
        elif GLOBAL_OVERLAY:
            overlay = GLOBAL_OVERLAY
        if overlay:
            overlay.show_embedded_notification(title, message, level, action_path, controller, width, height)
        else:
            logging.warning("无法找到主窗口覆盖层，通知仅记录日志: " + message)
    except Exception as e:
        logging.error(f"发送内嵌通知失败: {e}")
        if controller:
            GLib.idle_add(controller._perform_cleanup)

class XFixesCursorImage(Structure):
    # 缓冲区px {
    _fields_ = [
        ('x', c_short), # 全局坐标
        ('y', c_short), # 全局坐标
        ('width', c_short),
        ('height', c_short),
        ('xhot', c_short),
        ('yhot', c_short),
        ('cursor_serial', c_ulong),
        ('pixels', POINTER(c_ulong)),
        ('atom', c_ulong),
        ('name', c_char_p),
    ]

class FrameGrabberBase(abc.ABC):
    def __init__(self):
        self.global_offset_x = 0
        self.global_offset_y = 0

    def set_global_offset(self, x, y):
        self.global_offset_x = x
        self.global_offset_y = y

    @abc.abstractmethod
    def capture(self, x: int, y: int, w: int, h: int, filepath: Path, scale: float = 1.0, include_cursor: bool = False) -> bool:
        pass

    def prepare(self):
        pass

    def cleanup(self):
        pass

class X11FrameGrabber(FrameGrabberBase):
    def _get_cursor_image(self):
        """使用 ctypes 调用 X11 和 XFixes 获取当前光标图像"""
        dpy = None
        try:
            try: libx11 = ctypes.CDLL('libX11.so.6')
            except OSError:
                libx11 = ctypes.CDLL('libX11.so')
            try: libxfixes = ctypes.CDLL('libXfixes.so.3')
            except OSError:
                try: libxfixes = ctypes.CDLL('libXfixes.so')
                except OSError:
                    logging.error('无法加载 libXfixes.so 库')
                    return None
            XOpenDisplay = libx11.XOpenDisplay
            XOpenDisplay.restype = c_void_p
            XOpenDisplay.argtypes = [c_char_p]
            XCloseDisplay = libx11.XCloseDisplay
            XCloseDisplay.argtypes = [c_void_p]
            XQueryExtension = libx11.XQueryExtension
            XQueryExtension.restype = c_int
            XQueryExtension.argtypes = [c_void_p, c_char_p, 
                                        POINTER(c_int), POINTER(c_int), POINTER(c_int)]
            display_name = os.environ.get('DISPLAY', ':0').encode('utf-8')
            dpy = XOpenDisplay(display_name)
            if not dpy:
                logging.error(f'无法打开 X Display: {display_name.decode()}')
                return None
            opcode = c_int()
            event_base = c_int()
            error_base = c_int()
            has_xfixes = XQueryExtension(
                dpy, 
                b'XFIXES',
                ctypes.byref(opcode),
                ctypes.byref(event_base),
                ctypes.byref(error_base)
            )
            if not has_xfixes:
                logging.warning('XFIXES 扩展不可用')
                XCloseDisplay(dpy)
                return None
            XFixesGetCursorImage = libxfixes.XFixesGetCursorImage
            XFixesGetCursorImage.restype = POINTER(XFixesCursorImage)
            XFixesGetCursorImage.argtypes = [c_void_p]
            cursor_image_ptr = XFixesGetCursorImage(dpy)
            if not cursor_image_ptr:
                logging.warning('XFixesGetCursorImage 返回 NULL')
                XCloseDisplay(dpy)
                return None
            cursor_image = cursor_image_ptr.contents
            x = cursor_image.x
            y = cursor_image.y
            width = cursor_image.width
            height = cursor_image.height
            xhot = cursor_image.xhot
            yhot = cursor_image.yhot
            if width <= 0 or height <= 0:
                logging.warning(f'光标尺寸无效: {width}x{height}')
                libx11.XFree(cursor_image_ptr)
                XCloseDisplay(dpy)
                return None
            pixel_count = width * height
            pixels = cursor_image.pixels
            if sys.maxsize > 2**32:
                cursor_data = np.array([pixels[i] & 0xFFFFFFFF for i in range(pixel_count)], 
                                       dtype=np.uint32).reshape(height, width)
            else:
                cursor_data = np.array([pixels[i] for i in range(pixel_count)], 
                                       dtype=np.uint32).reshape(height, width)
            bgra = np.zeros((height, width, 4), dtype=np.uint8)
            bgra[..., 2] = (cursor_data >> 16) & 0xFF
            bgra[..., 1] = (cursor_data >> 8) & 0xFF
            bgra[..., 0] = cursor_data & 0xFF
            bgra[..., 3] = (cursor_data >> 24) & 0xFF
            libx11.XFree.argtypes = [c_void_p]
            libx11.XFree(cursor_image_ptr)
            XCloseDisplay(dpy)
            logging.info(f'成功获取光标图像: {width}x{height} at ({x},{y}), hotspot=({xhot},{yhot})')
            return {
                'image': bgra,
                'x': x,
                'y': y,
                'xhot': xhot,
                'yhot': yhot,
                'width': width,
                'height': height
            }
        except Exception as e:
            logging.error(f'获取光标图像失败: {e}', exc_info=True)
            if dpy:
                try: XCloseDisplay(dpy)
                except: pass
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

    def capture(self, x: int, y: int, w: int, h: int, filepath: Path, scale: float = 1.0, include_cursor: bool = False) -> bool:
        """使用 GDK 从根窗口截取指定区域并保存到文件"""
        # x, y: 显示器坐标; x, y, w, h: 逻辑px
        try:
            cursor_info = None
            if include_cursor:
                cursor_info = self._get_cursor_image()
            root_window = Gdk.get_default_root_window()
            if not root_window:
                logging.error("无法获取 GDK 根窗口")
                return False
            g_x, g_y = x + self.global_offset_x, y + self.global_offset_y # 显示器坐标 -> 全局坐标
            pixbuf = Gdk.pixbuf_get_from_window(root_window, g_x, g_y, w, h)
            if not pixbuf:
                logging.error(f"从区域 {w}x{h}+{x}+{y} 抓取 pixbuf 失败")
                return False
            if cursor_info:
                width = pixbuf.get_width()
                height = pixbuf.get_height()
                channels = pixbuf.get_n_channels()
                pixels = pixbuf.get_pixels()
                if channels == 3:
                    img = Image.frombuffer('RGB', (width, height), pixels, 
                                           'raw', 'RGB', pixbuf.get_rowstride(), 1)
                else:
                    img = Image.frombuffer('RGBA', (width, height), pixels, 
                                           'raw', 'RGBA', pixbuf.get_rowstride(), 1)
                screenshot_array = np.array(img)
                # 逻辑px -> 缓冲区px
                g_x_buf = round(g_x * scale)
                g_y_buf = round(g_y * scale)
                screenshot_array = self._blend_cursor(screenshot_array, cursor_info, g_x_buf, g_y_buf)
                img = Image.fromarray(screenshot_array)
                img.save(str(filepath), 'PNG')
            else:
                pixbuf.savev(str(filepath), 'png', [], [])
                logging.info(f"成功使用 GDK 截图到: {filepath}")
            return True
        except Exception as e:
            logging.error(f"使用 GDK 截图失败: {e}")
            return False

class WaylandFrameGrabber(FrameGrabberBase):
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
            logging.error(f"Wayland Grabber: DBus 连接失败: {e}")

    def prepare_sync(self):
        if self.state != "IDLE": return True
        logging.info("Wayland Grabber: 正在请求屏幕录制权限...")
        self.init_loop = GLib.MainLoop()
        self._start_portal_request()
        try:
            self.init_loop.run()
        except KeyboardInterrupt:
            return False
        self.init_loop = None
        if self.state == "STREAMING":
            logging.info("Wayland Grabber: 授权成功，后台流已启动")
            return True
        else:
            logging.error(f"Wayland Grabber: 授权失败或被取消 (State: {self.state})")
            return False

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
            self.portal.call_sync('CreateSession', GLib.Variant('(a{sv})', (options,)),
                                  Gio.DBusCallFlags.NONE, -1, None)
        except Exception as e:
            logging.error(f"CreateSession 失败: {e}")
            self.state = "ERROR"
            if self.init_loop: self.init_loop.quit()

    def _on_portal_response(self, connection, sender, path, iface, signal, params, user_data):
        response_code, results = params.unpack()
        if response_code != 0:
            logging.error(f"Portal 请求被拒绝 (code={response_code})")
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
                self.portal.call_sync('SelectSources', 
                                      GLib.Variant('(oa{sv})', (self.session_handle, opts)),
                                      Gio.DBusCallFlags.NONE, -1, None)
                self.state = "SELECTING"
        elif self.state == "SELECTING":
            self.state = "STARTING"
            opts = {'handle_token': GLib.Variant('s', f"start_{os.getpid()}")}
            self.portal.call_sync('Start', 
                                  GLib.Variant('(osa{sv})', (self.session_handle, '', opts)),
                                  Gio.DBusCallFlags.NONE, -1, None)
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
            self.pipeline.set_state(Gst.State.PLAYING)
        except Exception as e:
            logging.error(f"Pipeline 启动失败: {e}")

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
            arr = np.frombuffer(map_info.data, dtype=np.uint8)
            if arr.size >= h * stride:
                frame_bgr = arr[:h*stride].reshape((h, stride))[:, :w*4].reshape((h, w, 4))[:, :, :3]
                with self.frame_lock:
                    self.latest_frame = frame_bgr.copy()
        finally:
            buffer.unmap(map_info)
        return Gst.FlowReturn.OK

    def capture(self, x: int, y: int, w: int, h: int, filepath: Path, scale: float = 1.0, include_cursor: bool = False) -> bool:
        # x, y: 显示器坐标; x, y, w, h: 逻辑px
        with self.frame_lock:
            if self.latest_frame is None: return False
            # 逻辑px -> 缓冲区px
            x_buf = math.ceil(x * scale)
            y_buf = math.ceil(y * scale)
            w_buf = int(w * scale)
            h_buf = int(h * scale)
            img_h, img_w, _ = self.latest_frame.shape # 缓冲区px
            x1 = max(0, x_buf)
            y1 = max(0, y_buf)
            x2 = min(img_w, x_buf + w_buf)
            y2 = min(img_h, y_buf + h_buf)
            crop = self.latest_frame[y1:y2, x1:x2]
        try:
            cv2.imwrite(str(filepath), crop)
            return True
        except Exception as e:
            logging.error(f"Wayland 保存失败: {e}")
            return False
            
    def cleanup(self):
        if self.pipeline: self.pipeline.set_state(Gst.State.NULL)
        if self.session_handle:
            try: self.portal.call_sync('Close', None, Gio.DBusCallFlags.NONE, -1, None)
            except: pass

class WindowManagerBase(abc.ABC):
    @abc.abstractmethod
    def setup_overlay_window(self, window: Gtk.Window):
        """配置 Gtk.Window 以作为覆盖层运行"""
        pass

    def get_screen_geometry(self, window: Gtk.Window) -> Gdk.Rectangle:
        """获取覆盖层应在的显示器的几何信息"""
        # 逻辑px全局坐标
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

class X11WindowManager(WindowManagerBase):
    """X11 窗口管理器实现"""
    def setup_overlay_window(self, window: Gtk.Window):
        window.set_decorated(False)
        window.set_keep_above(True)
        window.set_app_paintable(True)
        window.set_type_hint(Gdk.WindowTypeHint.NORMAL)
        window.set_skip_taskbar_hint(True)
        window.set_skip_pager_hint(True)
        visual = window.get_screen().get_rgba_visual()
        if visual and window.get_screen().is_composited():
            window.set_visual(visual)

class WaylandWindowManager(WindowManagerBase):
    """Wayland 窗口管理器实现"""
    def setup_overlay_window(self, window: Gtk.Window):
        window.set_app_paintable(True)
        visual = window.get_screen().get_rgba_visual()
        if visual and window.get_screen().is_composited():
            window.set_visual(visual)
        else:
            logging.warning("无法设置 RGBA visual，透明度可能无法工作。")
        if not GTK_LAYER_SHELL_AVAILABLE or not GtkLayerShell.is_supported():
            window.set_decorated(False)
            empty_titlebar = Gtk.Fixed()
            window.set_titlebar(empty_titlebar)
            send_desktop_notification("窗口未置顶", "需手动置顶", level="warning")
        else:
            logging.info("WaylandWindowManager: 正在应用 GtkLayerShell 属性...")
            GtkLayerShell.init_for_window(window)
            GtkLayerShell.set_layer(window, GtkLayerShell.Layer.OVERLAY)
            GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.TOP, True)
            GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.BOTTOM, True)
            GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.LEFT, True)
            GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.RIGHT, True)
            GtkLayerShell.set_namespace(window, "scroll_stitch_overlay")
            protocol_version = GtkLayerShell.get_protocol_version()
            logging.info(f"GtkLayerShell 协议版本: {protocol_version}")
            if protocol_version >= 4:
                GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.ON_DEMAND)
            else:
                logging.warning(f"Wayland 协议版本 {protocol_version} < 4，回退到独占键盘模式")
            GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.EXCLUSIVE)

# 缓冲区px {
def _find_overlap_brute_force(img_top, img_bottom, min_h, max_h):
    h1, _, _ = img_top.shape
    best_match_score = -1.0
    found_overlap = 0
    for h in range(max_h, min_h - 1, -1):
        region_top = img_top[h1 - h:, :]
        template_bottom = img_bottom[0:h, :]
        result = cv2.matchTemplate(region_top, template_bottom, cv2.TM_CCOEFF_NORMED)
        score = result[0][0]
        if score > best_match_score:
            best_match_score = score
            found_overlap = h
        if score > 0.98:
            break
    return found_overlap, best_match_score

def _run_pyramid_step(img_top, img_bottom, max_overlap_search, scale_factor):
    """执行单次金字塔匹配步骤"""
    search_radius = max(3, int(0.8 / scale_factor))
    small_top = cv2.resize(img_top, (0, 0), fx=scale_factor, fy=scale_factor, interpolation=cv2.INTER_AREA)
    small_bottom = cv2.resize(img_bottom, (0, 0), fx=scale_factor, fy=scale_factor, interpolation=cv2.INTER_AREA)
    max_overlap_scaled = int(max_overlap_search * scale_factor)
    coarse_overlap_scaled, _ = _find_overlap_brute_force(small_top, small_bottom, 1, max_overlap_scaled)
    estimated_overlap = int(coarse_overlap_scaled / scale_factor)
    h1, _, _ = img_top.shape
    h2, _, _ = img_bottom.shape
    min_fine_search = max(1, estimated_overlap - search_radius)
    max_fine_search = min(max_overlap_search, estimated_overlap + search_radius, h1 - 1, h2 - 1)
    if max_fine_search <= min_fine_search:
        return estimated_overlap, 0.0
    return _find_overlap_brute_force(img_top, img_bottom, min_fine_search, max_fine_search)

def _find_overlap_pyramid(img_top, img_bottom, max_overlap_search):
    PYRAMID_CUTOFF_THRESHOLD = 50
    if max_overlap_search < PYRAMID_CUTOFF_THRESHOLD:
        return _find_overlap_brute_force(img_top, img_bottom, 1, max_overlap_search)
    scale_factor_1 = (2.0 / max_overlap_search)**0.5
    scale_factor_1 = max(0.08, min(scale_factor_1, 0.5))
    overlap_1, score_1 = _run_pyramid_step(img_top, img_bottom, max_overlap_search, scale_factor_1)
    if score_1 > 0.90:
        return overlap_1, score_1
    if scale_factor_1 >= 0.3:
        return overlap_1, score_1
    logging.info(f"快速匹配分数低 ({score_1:.3f})，尝试更高精度重试...")
    scale_factor_2 = 1.5 * scale_factor_1
    overlap_2, score_2 = _run_pyramid_step(img_top, img_bottom, max_overlap_search, scale_factor_2)
    if score_2 > score_1:
        logging.info(f"高精度重试成功: score {score_1:.3f} -> {score_2:.3f}")
        return overlap_2, score_2
    return overlap_1, score_1

def stitch_images_in_memory_from_model(render_plan: list, image_width: int, total_height: int, progress_callback=None):
    if not render_plan:
        return None
    num_pieces = len(render_plan)
    final_width_int = int(round(image_width))
    final_height_int = int(round(total_height))
    logging.info(f"开始从 {num_pieces} 个渲染片段拼接图像，最终尺寸: {final_width_int}x{final_height_int} (原始浮点高度: {total_height})")
    pil_cache = {}
    try:
        stitched_image = Image.new('RGBA', (final_width_int, final_height_int))
        y_offset = 0
        for i, piece in enumerate(render_plan):
            filepath = piece['filepath']
            src_y = piece['src_y']
            src_height = piece['height']
            dest_y = piece['render_y_start']
            try:
                if filepath not in pil_cache:
                    pil_cache[filepath] = Image.open(filepath)
                img_pil = pil_cache[filepath]
                box_upper = int(round(src_y))
                box_lower = int(round(src_y + src_height))
                box = (0, box_upper, img_pil.width, box_lower)
                cropped_img = img_pil.crop(box)
                if cropped_img.width != final_width_int:
                    logging.warning(f"图片片段 {filepath} 宽度 {cropped_img.width} 与预期 {final_width_int} 不符")
                stitched_image.paste(cropped_img, (0, int(round(dest_y))))
            except Exception as e_load:
                logging.error(f"加载/裁剪/粘贴图片失败 {filepath} (src_y={src_y}): {e_load}")
            if progress_callback:
                GLib.idle_add(progress_callback, (i + 1) / num_pieces)
        logging.info("图像拼接完成")
        return stitched_image
    except Exception as e:
        logging.exception(f"拼接图像时发生严重错误: {e}")
        return None
    finally:
        for img in pil_cache.values():
            img.close()
        logging.info(f"PIL 缓存中的 {len(pil_cache)} 张图片已关闭")
# 缓冲区px }

def copy_to_clipboard(image_path: Path) -> bool:
    """使用 Gtk.Clipboard 将图片复制到剪贴板"""
    copy_start_time = time.perf_counter()
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(image_path))
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_image(pixbuf)
        copy_duration = time.perf_counter() - copy_start_time
        logging.info(f"图片 {image_path} 已通过 GTK 复制到剪贴板，耗时: {copy_duration:.3f} 秒")
        return True
    except GLib.Error as e:
        copy_duration = time.perf_counter() - copy_start_time
        logging.error(f"使用 GTK 复制到剪贴板失败: {e}，耗时: {copy_duration:.3f} 秒")
        return False

def create_feedback_panel(text, show_progress_bar=False):
    # 逻辑px
    container = Gtk.EventBox()
    container.set_visible_window(False)
    panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=config.PROCESSING_DIALOG_SPACING // 2)
    container.add(panel)
    panel.get_style_context().add_class("processing-dialog-bg")
    top_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=config.PROCESSING_DIALOG_SPACING)
    spinner = Gtk.Spinner()
    spinner.start()
    label = Gtk.Label()
    label.set_markup(f"<span color='white'>{GLib.markup_escape_text(text)}</span>")
    top_hbox.pack_start(spinner, True, True, 0)
    top_hbox.pack_start(label, True, True, 0)
    panel.set_border_width(config.PROCESSING_DIALOG_BORDER_WIDTH)
    panel.pack_start(top_hbox, True, True, 0)
    progress_bar = None
    if show_progress_bar:
        progress_bar = Gtk.ProgressBar()
        progress_bar.set_fraction(0.0)
        panel.pack_start(progress_bar, True, True, 5)
    container.set_size_request(config.PROCESSING_DIALOG_WIDTH, config.PROCESSING_DIALOG_HEIGHT)
    container.show_all()
    return container, progress_bar

class CalibrationWidget(Gtk.DrawingArea):
    """用于坐标校准的控件，绘制特定的点阵图案"""
    # 逻辑px
    def __init__(self):
        super().__init__()
        self.pixel_scale = 2
        self.padding = 12
        self.content_w = 48 * self.pixel_scale
        self.content_h = 16 * self.pixel_scale
        self.set_size_request(self.content_w + self.padding * 2, self.content_h + self.padding * 2)
        self.bitmap = self.get_calibration_bitmap()

    def get_calibration_bitmap(self):
        bitmap = np.zeros((16, 48), dtype=bool)
        # 校准用的 HZK16 点阵数据 (拼, 长, 图)
        CALIBRATION_BYTES_PIN = b'\x12\x08\x11\x18\x10\xa0\x13\xfc\xfd\x10\x11\x10\x15\x10\x19\x147\xfe\xd1\x10\x11\x10\x11\x10\x11\x10\x11\x10R\x10$\x10'
        CALIBRATION_BYTES_CHANG = b'\x08\x00\x08\x10\x080\x08@\x08\x80\t\x00\x08\x04\xff\xfe\t\x00\t\x00\x08\x80\x08@\x08 \t\x1c\x0e\x08\x08\x00'
        CALIBRATION_BYTES_TU = b'\x00\x04\x7f\xfeD\x04G\xe4LDR\x84A\x04B\x84FDI<p\x94F\x04A\x04@\x84\x7f\xfc@\x04'
        data_list = [CALIBRATION_BYTES_PIN, CALIBRATION_BYTES_CHANG, CALIBRATION_BYTES_TU]
        for char_idx, char_bytes in enumerate(data_list):
            x_offset = char_idx * 16
            for row in range(16):
                b1 = char_bytes[row * 2]
                b2 = char_bytes[row * 2 + 1]
                for bit in range(8):
                    if b1 & (0x80 >> bit): bitmap[row, x_offset + bit] = True
                    if b2 & (0x80 >> bit): bitmap[row, x_offset + 8 + bit] = True
        return bitmap

    def do_draw(self, cr):
        w = self.get_allocated_width()
        h = self.get_allocated_height()
        cr.set_source_rgb(0.15, 0.15, 0.15)
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

class StitchModel(GObject.Object):
    """管理拼接数据的模型，支持异步更新和信号通知"""
    # 缓冲区px
    __gsignals__ = {
        'model-updated': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'modification-stack-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
    def __init__(self):
        super().__init__()
        self.entries = []
        self.image_width = 0
        self.total_virtual_height = 0
        self.modifications = []
        self.redo_stack = []
        self.absolute_plan = []    # 基础层
        self.collapsed_plan = []   # 中间层
        self.render_plan = []      # 渲染层
        self.pixbuf_cache = collections.OrderedDict()
        self.CACHE_SIZE = config.parser.getint('Performance', 'preview_cache_size', fallback=10)

    @property
    def capture_count(self) -> int:
        """返回当前截图数量"""
        return len(self.entries)

    def _get_cached_pixbuf(self, filepath):
        if filepath in self.pixbuf_cache:
            self.pixbuf_cache.move_to_end(filepath)
            return self.pixbuf_cache[filepath]
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(filepath)
            self.pixbuf_cache[filepath] = pixbuf
            if len(self.pixbuf_cache) > self.CACHE_SIZE:
                oldest_key, _ = self.pixbuf_cache.popitem(last=False)
            return pixbuf
        except GLib.Error as e:
            logging.error(f"无法加载图片文件用于缓存 {filepath}: {e}")
            if filepath in self.pixbuf_cache:
                 del self.pixbuf_cache[filepath]
            return None
        except Exception as e:
            logging.error(f"加载 Pixbuf 时发生意外错误 {filepath}: {e}")
            if filepath in self.pixbuf_cache:
                 del self.pixbuf_cache[filepath]
            return None

    def _regenerate_plans(self):
        """依次生成三层：Absolute, Collapsed, Render"""
        if not self.entries:
            self.total_virtual_height = 0
            self.absolute_plan = []
            self.collapsed_plan = []
            self.render_plan = []
            GLib.idle_add(self.emit, 'model-updated')
            return
        self._generate_absolute_plan()
        self._generate_collapsed_plan()
        self._generate_render_plan()
        GLib.idle_add(self.emit, 'model-updated')

    def _generate_absolute_plan(self):
        self.absolute_plan = []
        current_a_y = 0
        if not self.entries:
            self.total_absolute_height = 0
            return
        for i, entry in enumerate(self.entries):
            self.absolute_plan.append({
                'entry_index': i,
                'filepath': entry['filepath'],
                'absolute_y_start': current_a_y,
                'height': entry['height'],
                'overlap_with_next': entry['overlap'] if i < len(self.entries) - 1 else 0
            })
            current_a_y += entry['height']
        self.total_absolute_height = current_a_y

    def _generate_collapsed_plan(self):
        self.collapsed_plan = []
        current_c_y = 0
        if not self.absolute_plan:
            self.total_collapsed_height = 0
            return
        restored_seams = {mod['seam_index'] for mod in self.modifications if mod['type'] == 'restore'}
        for i, abs_piece in enumerate(self.absolute_plan):
            is_last = (i == len(self.absolute_plan) - 1)
            if is_last:
                effective_overlap = 0
            elif i in restored_seams:
                effective_overlap = 0
                logging.info(f"接缝 {i} 已恢复，设置 effective_overlap = 0")
            else:
                effective_overlap = abs_piece['overlap_with_next']
            visible_height = abs_piece['height'] - effective_overlap
            self.collapsed_plan.append({
                'entry_index': abs_piece['entry_index'],
                'filepath': abs_piece['filepath'],
                'absolute_y_start': abs_piece['absolute_y_start'],
                'absolute_y_end': abs_piece['absolute_y_start'] + abs_piece['height'],
                'collapsed_y_start': current_c_y,
                'height': visible_height,
                'src_y': 0,
                'original_height': abs_piece['height']
            })
            current_c_y += visible_height
        self.total_collapsed_height = current_c_y

    def _generate_render_plan(self):
        self.render_plan = []
        current_r_y = 0
        if not self.collapsed_plan:
            self.total_virtual_height = 0
            return
        delete_regions = [(mod['y_start_abs'], mod['y_end_abs']) 
                          for mod in self.modifications if mod['type'] == 'delete']
        for collapsed_piece in self.collapsed_plan:
            piece_abs_start = collapsed_piece['absolute_y_start']
            piece_abs_end = collapsed_piece['absolute_y_start'] + collapsed_piece['original_height']
            visible_intervals = [(piece_abs_start, piece_abs_end)]
            for del_start, del_end in delete_regions:
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
            final_render_intervals = []
            c_visible_abs_start = collapsed_piece['absolute_y_start']
            c_visible_abs_end = collapsed_piece['absolute_y_start'] + collapsed_piece['height']
            for vis_start, vis_end in visible_intervals:
                final_abs_start = max(vis_start, c_visible_abs_start)
                final_abs_end = min(vis_end, c_visible_abs_end)
                if final_abs_end > final_abs_start:
                    final_render_intervals.append((final_abs_start, final_abs_end))
            for abs_start, abs_end in final_render_intervals:
                height = abs_end - abs_start
                if height <= 1e-5:
                    continue
                src_y = abs_start - collapsed_piece['absolute_y_start']
                self.render_plan.append({
                    'entry_index': collapsed_piece['entry_index'],
                    'filepath': collapsed_piece['filepath'],
                    'absolute_y_start': abs_start,
                    'absolute_y_end': abs_end,
                    'render_y_start': current_r_y,
                    'height': height,
                    'src_y': src_y,
                })
                current_r_y += height
        self.total_virtual_height = current_r_y

    def undo(self):
        """撤销上一个修改"""
        if not self.modifications:
            logging.info("StitchModel: 撤销栈为空，无操作")
            return
        mod = self.modifications.pop()
        self.redo_stack.append(mod)
        logging.info(f"StitchModel: 撤销操作 {mod.get('type')}")
        GLib.idle_add(self._regenerate_plans)
        GLib.idle_add(self.emit, 'modification-stack-changed')

    def redo(self):
        """重做上一个撤销的修改"""
        if not self.redo_stack:
            logging.info("StitchModel: 重做栈为空，无操作")
            return
        mod = self.redo_stack.pop()
        self.modifications.append(mod)
        logging.info(f"StitchModel: 重做操作 {mod.get('type')}")
        GLib.idle_add(self._regenerate_plans)
        GLib.idle_add(self.emit, 'modification-stack-changed')

    def add_modification(self, mod: dict):
        logging.info(f"StitchModel: 添加新修改: {mod}")
        self.modifications.append(mod)
        if self.redo_stack:
            logging.info("StitchModel: 新修改导致重做栈被清空")
            self.redo_stack.clear()
        GLib.idle_add(self.emit, 'modification-stack-changed')
        GLib.idle_add(self._regenerate_plans)

    def add_entry(self, filepath: str, width: int, height: int, overlap_with_previous: int):
        logging.info(f"主线程: 收到添加请求: {filepath}, h={height}, overlap={overlap_with_previous}")
        if not self.entries:
            self.image_width = width
            self.entries.append({'filepath': filepath, 'height': height, 'overlap': 0})
        else:
            self.entries[-1]['overlap'] = overlap_with_previous
            self.entries.append({'filepath': filepath, 'height': height, 'overlap': 0})
        logging.info(f"添加第 {len(self.entries)} 张截图, 滚动距离: {height - overlap_with_previous}px")
        GLib.idle_add(self._regenerate_plans)

    def pop_entry(self):
        if not self.entries:
            return
        logging.info("主线程: 收到移除最后一个条目的请求")
        last_entry_index = len(self.entries) - 1
        last_abs_piece = None
        if self.absolute_plan and len(self.absolute_plan) > last_entry_index:
            last_abs_piece = self.absolute_plan[last_entry_index]
        elif self.absolute_plan:
            logging.warning(f"pop_entry: absolute_plan (len {len(self.absolute_plan)}) 与 entries (len {len(self.entries)}) 不同步")
        if last_abs_piece:
            entry_abs_start = last_abs_piece['absolute_y_start']
            entry_abs_end = last_abs_piece['absolute_y_start'] + last_abs_piece['height']
            seam_index_to_remove = last_entry_index - 1
            logging.info(f"正在清理与截图 {last_entry_index} (AbsY: [{entry_abs_start}, {entry_abs_end}], SeamIdx: {seam_index_to_remove}) 相关的修改")
            new_modifications = []
            removed_count = 0
            for mod in self.modifications:
                mod_applies = False
                if mod['type'] == 'delete':
                    mod_start = mod['y_start_abs']
                    mod_end = mod['y_end_abs']
                    if max(entry_abs_start, mod_start) < min(entry_abs_end, mod_end):
                        mod_applies = True
                        logging.info(f"删除操作 {mod} 与被删除截图重叠，将被移除")
                elif mod['type'] == 'restore':
                    if mod['seam_index'] == seam_index_to_remove:
                         mod_applies = True
                         logging.info(f"恢复操作 {mod} 与被删除截图相关，将被移除")
                if mod_applies:
                    removed_count += 1
                else:
                    new_modifications.append(mod)
            if removed_count > 0:
                self.modifications = new_modifications
                logging.info(f"已移除 {removed_count} 个与被删除截图相关的修改")
                if self.redo_stack:
                    self.redo_stack.clear()
                    logging.info("由于删除了截图，重做栈已清空")
                GLib.idle_add(self.emit, 'modification-stack-changed')
        popped_entry = self.entries.pop()
        if popped_entry['filepath'] in self.pixbuf_cache:
            del self.pixbuf_cache[popped_entry['filepath']]
            logging.info(f"从缓存中移除 {popped_entry['filepath']}")
        try:
            filepath_to_remove = Path(popped_entry['filepath'])
            if filepath_to_remove.exists():
                os.remove(filepath_to_remove)
                logging.info(f"已删除文件: {filepath_to_remove}")
        except OSError as e:
            logging.error(f"删除文件失败 {popped_entry['filepath']}: {e}")
        if self.entries:
            self.entries[-1]['overlap'] = 0
            last_entry = self.entries[-1]
        else:
            self.image_width = 0
            logging.info("所有截图已移除")
        GLib.idle_add(self._regenerate_plans)

class CaptureSession:
    """管理一次滚动截图会话的数据和状态"""
    def __init__(self):
        self.is_horizontally_locked: bool = False
        self.geometry: dict = {} # 逻辑px窗口坐标
        self.detected_app_class: str = None
        self.is_matching_enabled: bool = False
        self.known_scroll_distances = [] # 缓冲区px

    def update_geometry(self, new_geometry):
        """更新捕获区域的几何信息，并确保所有值为整数"""
        # new_geometry: 逻辑px窗口坐标
        self.geometry = {key: int(value) for key, value in new_geometry.items()}

    def cleanup(self):
        """清理临时文件和目录"""
        if config.TMP_DIR.exists():
            try:
                shutil.rmtree(config.TMP_DIR)
            except OSError as e:
                logging.error(f"清理临时目录失败: {e}")

# 逻辑px {
class AppClassInputDialog(Gtk.Dialog):
    def __init__(self, parent):
        super().__init__(title="输入应用标识符", transient_for=parent, flags=0)
        self.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK
        )
        self.set_default_size(300, 120)
        self.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        self.set_border_width(10)
        box = self.get_content_area()
        box.set_spacing(10)
        label = Gtk.Label(label="无法自动检测底层应用\n请输入一个名称来保存/加载此应用的滚动配置：")
        label.set_line_wrap(True)
        label.set_xalign(0)
        box.pack_start(label, False, False, 0)
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("例如: chrome, vscode, novel-reader...")
        self.entry.connect("activate", lambda w: self.response(Gtk.ResponseType.OK))
        box.pack_start(self.entry, False, False, 0)
        self.show_all()

    def get_text(self):
        return self.entry.get_text().strip()

class AppClassSelectionDialog(Gtk.Dialog):
    def __init__(self, parent, config_obj):
        super().__init__(title="选择应用配置", transient_for=parent, flags=0)
        self.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK
        )
        self.set_default_size(300, 200)
        self.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        self.set_border_width(10)
        box = self.get_content_area()
        box.set_spacing(5)
        label = Gtk.Label(label="请选择一个已校准的配置：")
        label.set_xalign(0)
        box.pack_start(label, False, False, 5)
        self.combo = Gtk.ComboBoxText()
        has_items = False
        if config_obj.parser.has_section('ApplicationScrollUnits'):
            for key in config_obj.parser['ApplicationScrollUnits']:
                self.combo.append(key, key)
                has_items = True
        if not has_items:
            self.combo.append("none", "无已保存配置 (请先校准)")
            self.combo.set_active_id("none")
            self.combo.set_sensitive(False)
        else:
            self.combo.set_active(0)
        box.pack_start(self.combo, False, False, 0)
        self.show_all()

    def get_selected(self):
        active_id = self.combo.get_active_id()
        if active_id == "none": return None
        return active_id
# 逻辑px }

class GridModeController:
    def __init__(self, config: Config, session: CaptureSession, view: 'CaptureOverlay'):
        self.config = config
        self.session = session
        self.view = view
        self.is_active = False
        self.grid_unit = 0 # 缓冲区px
        self._is_toggling = False
        self.calibration_state = None
        if not IS_WAYLAND:
            try:
                self.x_display = display.Display()
            except Exception as e:
                self.x_display = None
                logging.error(f"无法连接到 X Display，整格模式功能将不可用: {e}")
        else:
            logging.info("检测到 Wayland，Xlib 窗口检测将不可用。")
            self.x_display = None

    def _get_window_at_coords(self, d, x: int, y: int):
        """X11在不移动鼠标的情况下，获取指定全局坐标下的窗口ID和WM_CLASS"""
        # x, y: 逻辑px全局坐标
        if IS_WAYLAND:
            logging.warning("_get_window_at_coords 在 Wayland 上不可用。")
            return None, None
        if not d:
            return None, None
        scale = self.view.scale
        # 逻辑px -> 缓冲区px
        buf_x = int(x * scale)
        buf_y = int(y * scale)
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
                        logging.info(
                            f"定位成功! ID={win_obj.id}, Class={app_class}, Scale={scale}, "
                            f"AbsGeom=({abs_x},{abs_y},{win_w},{win_h}), PhysPoint=({buf_x},{buf_y})"
                        )
                        return win_obj.id, app_class
                except Exception as e:
                    logging.error(f"错误： {e}")
        except Exception as e:
            logging.error(f"使用 python-xlib 查找窗口时发生严重错误: {e}")
        return None, None

    def _get_app_class_at_center(self):
        # 逻辑px窗口坐标
        if IS_WAYLAND:
            logging.info("Wayland 环境下无法通过 Xlib 检测应用类别。")
            return None
        shot_x = self.session.geometry['x']
        shot_y = self.session.geometry['y']
        center_x = int(shot_x + self.session.geometry['w'] / 2)
        center_y = int(shot_y + self.session.geometry['h'] / 2)
        g_center_x, g_center_y = self.view.window_to_global(center_x, center_y) # 窗口坐标 -> 全局坐标
        _, app_class = self._get_window_at_coords(self.x_display, g_center_x, g_center_y)
        if app_class:
            logging.info(f"检测到底层应用: {app_class}")
        return app_class

    def _prompt_for_app_class_input(self):
        """弹窗让用户手动输入应用标识符 (用于校准)"""
        global hotkey_listener
        if hotkey_listener:
            hotkey_listener.set_normal_keys_grabbed(False)
        dialog = AppClassInputDialog(self.view if not IS_WAYLAND else None)
        response = dialog.run()
        result = None
        if response == Gtk.ResponseType.OK:
            result = dialog.get_text()
        dialog.destroy()
        if hotkey_listener and are_hotkeys_enabled:
            hotkey_listener.set_normal_keys_grabbed(True)
        return result

    def _prompt_for_app_class_selection(self):
        """弹窗让用户从已有列表中选择 (用于切换模式)"""
        global hotkey_listener
        if hotkey_listener:
            hotkey_listener.set_normal_keys_grabbed(False)
        dialog = AppClassSelectionDialog(self.view if not IS_WAYLAND else None, self.config)
        response = dialog.run()
        result = None
        if response == Gtk.ResponseType.OK:
            result = dialog.get_selected()
        dialog.destroy()
        if hotkey_listener and are_hotkeys_enabled:
            hotkey_listener.set_normal_keys_grabbed(True)
        return result

    def toggle(self):
        """切换整格模式的开关"""
        # 缓冲区px
        if self._is_toggling:
            logging.warning("GridModeController: toggle 正在进行中，忽略重复请求")
            return
        self._is_toggling = True
        try:
            if self.view.controller.is_auto_scrolling:
                logging.warning("自动滚动模式下忽略切换整格模式请求")
                return
            if self.is_active:
                self.is_active = False
                self.grid_unit = 0
                self.session.detected_app_class = None
                self.session.is_matching_enabled = False
                self.view.button_panel.set_grid_action_buttons_visible(False)
                self.view.controller.set_current_mode("自由模式")
                self.view.queue_draw()
                logging.info("整格模式已关闭")
                send_desktop_notification("整格模式已关闭", "边框拖动已恢复自由模式", level="normal")
                return
            # 尝试开启整格模式
            app_class = self._get_app_class_at_center()
            if not app_class:
                app_class = self._prompt_for_app_class_selection()
            if not app_class:
                send_desktop_notification("模式切换失败", "无法检测到底层应用程序", level="warning")
                return
            grid_unit_from_config, matching_enabled = config.get_scroll_unit(app_class)
            if grid_unit_from_config > 0:
                self.is_active = True
                self.grid_unit = grid_unit_from_config
                self.session.detected_app_class = app_class
                self.session.is_matching_enabled = matching_enabled
                self.view.button_panel.set_grid_action_buttons_visible(True)
                match_status = "启用" if matching_enabled else "禁用"
                self.view.controller.set_current_mode("整格模式")
                scale = self.view.scale
                logging.info(f"为应用 '{app_class}' 启用整格模式，图片单位: {self.grid_unit}px, 模板匹配: {match_status}")
                send_desktop_notification("整格模式已启用", f"应用: {app_class}, 滚动单位: {self.grid_unit}px, 误差修正: {match_status}", level="normal")
                self._snap_current_height()
            else:
                logging.warning(f"应用 '{app_class}' 未在配置中找到滚动单位，无法启用整格模式")
                send_desktop_notification("模式切换失败", f"'{app_class}' 的滚动单位未配置", level="warning")
        finally:
            self._is_toggling = False

    def _snap_current_height(self):
        """将当前选区的高度对齐到最近的整格单位"""
        if not self.is_active or self.grid_unit == 0:
            return
        scale = self.view.scale
        geo = self.session.geometry.copy() 
        current_h = geo['h'] # 逻辑px
        # 计算最接近的整数倍
        current_h_phys = current_h * scale # 逻辑px -> 缓冲区px
        ticks = round(current_h_phys / self.grid_unit)
        if ticks < 1: ticks = 1
        target_h_phys = ticks * self.grid_unit # 缓冲区px
        snapped_h = int(math.ceil(target_h_phys / scale)) # 缓冲区px -> 逻辑px
        if geo['h'] != snapped_h:
            geo['h'] = snapped_h
            self.session.update_geometry(geo)
            self.view.update_layout()
            self.view.queue_draw()
            logging.info(f"高度对齐: {target_h_phys} 缓冲区px, {snapped_h} 逻辑px (Scale={scale})")

    def start_calibration(self):
        """启动自动滚动单位校准流程"""
        if self.view.controller.is_auto_scrolling:
            logging.warning("自动滚动模式下忽略配置滚动单位请求")
            return
        if self.is_active:
            send_desktop_notification("操作无效", "请先按 Shift 键退出整格模式再进行配置", level="normal")
            return
        app_class = self._get_app_class_at_center()
        if not app_class:
            app_class = self._prompt_for_app_class_input()
        if not app_class:
            send_desktop_notification("配置失败", "无法检测到底层应用程序", level="warning")
            return
        logging.info(f"为应用 '{app_class}' 启动自动校准...")
        # 逻辑px窗口坐标
        panel_x = 20
        panel_y = 20
        dialog_text = f"正在为 {app_class} 自动校准...\n请勿操作"
        panel, _ = create_feedback_panel(text=dialog_text, show_progress_bar=False)
        self.view.fixed_container.put(panel, panel_x, panel_y)
        panel.show_all()
        _, nat_size = panel.get_preferred_size()
        panel_w, panel_h = nat_size.width, nat_size.height
        panel_rect = {'x': panel_x, 'y': panel_y, 'w': panel_w, 'h': panel_h}
        should_hide = False
        def _rects_overlap(r1, r2):
            return not (r1['x'] >= r2['x'] + r2['w'] or 
                        r1['x'] + r1['w'] <= r2['x'] or 
                        r1['y'] >= r2['y'] + r2['h'] or 
                        r1['y'] + r1['h'] <= r2['y'])
        if _rects_overlap(panel_rect, self.session.geometry):
            should_hide = True
            logging.info("校准面板遮挡了截图选区，将自动隐藏")
        if not should_hide and self.view.show_side_panel and self.view.side_panel.get_visible():
            alloc = self.view.side_panel.get_allocation()
            side_rect = {'x': self.view.side_panel.translate_coordinates(self.view, 0, 0)[0], 
                         'y': self.view.side_panel.translate_coordinates(self.view, 0, 0)[1], 
                         'w': alloc.width, 'h': alloc.height}
            if _rects_overlap(panel_rect, side_rect):
                should_hide = True
        if not should_hide and self.view.show_button_panel and self.view.button_panel.get_visible():
            alloc = self.view.button_panel.get_allocation()
            btn_rect = {'x': self.view.button_panel.translate_coordinates(self.view, 0, 0)[0],
                        'y': self.view.button_panel.translate_coordinates(self.view, 0, 0)[1],
                        'w': alloc.width, 'h': alloc.height}
            if _rects_overlap(panel_rect, btn_rect):
                should_hide = True
        if should_hide:
            panel.hide()
        scale = self.view.scale
        self.calibration_state = {
            "app_class": app_class,
            "num_samples": 4,
            "measured_units": [],
            "panel": panel,
            "ticks_to_scroll": max(1, int(int(self.session.geometry['h'] * scale) / self.config.MAX_SCROLL_PER_TICK)) # 逻辑px -> 缓冲区px
        }
        self.view.queue_draw()
        self.view._update_input_shape()
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
            scale = self.view.scale
            buf_h = int(h * scale) # 逻辑px -> 缓冲区px
            ticks_to_scroll = state['ticks_to_scroll']
            num_samples = state['num_samples']
            mon_x, mon_y = self.view.window_to_monitor(shot_x, shot_y) # 窗口坐标 -> 显示器坐标
            def safe_capture(path):
                result = [False]
                event = threading.Event()
                def task():
                    try:
                        result[0] = FRAME_GRABBER.capture(mon_x, mon_y, w, h, path, scale, include_cursor=False)
                    except Exception as e:
                        logging.error(f"校准截图异常: {e}")
                    finally:
                        event.set()
                GLib.idle_add(task)
                event.wait()
                return result[0]
            logging.info(f"校准参数: 截图区高度={h} 逻辑px, 约 {buf_h} 缓冲区px, 每次滚动格数={ticks_to_scroll}, 采样次数={num_samples}")
            state["filepath_before"] = config.TMP_DIR / "cal_before.png"
            if not safe_capture(state["filepath_before"]):
                 logging.error("校准初始截图失败")
                 GLib.idle_add(self._finalize_calibration, False)
                 return
            self.view.controller.scroll_manager.scroll_discrete(-ticks_to_scroll)
            time.sleep(0.4)
            for step in range(1, num_samples + 1):
                filepath_after = config.TMP_DIR / "cal_after.png"
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
                    max_search_overlap = h_buf - (ticks_to_scroll * min_scroll_buf)
                    found_overlap, score = _find_overlap_pyramid(img_top, img_bottom, max_search_overlap)
                    if score > 0.95:
                        scroll_dist_px = h_buf - found_overlap
                        unit = scroll_dist_px / state['ticks_to_scroll']
                        if unit < min_scroll_buf:
                            logging.warning(f"检测到滚动距离过小({unit:.2f} 缓冲区px/格)，已到达页面末端。提前中止采样")
                            GLib.idle_add(self._finalize_calibration, True)
                            return
                        state["measured_units"].append(unit)
                        logging.info(f"采样 {step}: 成功，滚动单位 ≈ {unit:.2f} 缓冲区px/格，相似度 {score:.3f}")
                    else:
                        logging.warning(f"采样 {step}: 匹配失败（相似度 {score:.3f}）")
                        bottom_check_height = ticks_to_scroll * min_scroll_buf
                        if ActionController._check_if_bottom_reached(img_top, img_bottom, bottom_check_height):
                            logging.warning(f"校准：检测到底部（匹配失败后底部仍一致），提前中止采样")
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
                    self.view.controller.scroll_manager.scroll_discrete(-state['ticks_to_scroll'])
                    time.sleep(0.4)
            GLib.idle_add(self._finalize_calibration, True)
        except Exception as e:
            logging.exception(f"校准线程发生严重错误: {e}")
            GLib.idle_add(self._finalize_calibration, False)
    
    def _finalize_calibration(self, success):
        """分析数据、通过聚类剔除离群值、保存结果并清理"""
        state = self.calibration_state
        state["panel"].destroy()
        self.view._update_input_shape()
        if os.path.exists(state.get("filepath_before", "")):
            os.remove(state["filepath_before"])
        MIN_VALID_SAMPLES = 2
        if not success or not state["measured_units"] or len(state["measured_units"]) < MIN_VALID_SAMPLES:
            msg = f"为 '{state['app_class']}' 校准失败\n有效采样数据不足，请在内容更丰富的区域操作或确保界面有足够的滚动空间"
            send_desktop_notification("配置失败", msg, level="warning")
            logging.error(msg.replace('\n', ' '))
            return
        units = sorted(state["measured_units"])
        logging.info(f"开始聚类分析，原始数据: {units}")
        if not units:
            self._finalize_calibration(success=False)
            return
        TOLERANCE = 5
        clusters = []
        for unit in units:
            placed = False
            for cluster in clusters:
                if abs(unit - np.mean(cluster)) < TOLERANCE:
                    cluster.append(unit)
                    placed = True
                    break
            if not placed:
                clusters.append([unit])
        if not clusters:
            self._finalize_calibration(success=False)
            return
        largest_cluster = max(clusters, key=len)
        logging.info(f"聚类结果: {clusters}。选择的最大集群: {largest_cluster}")
        if len(largest_cluster) < MIN_VALID_SAMPLES:
            msg = f"为 '{state['app_class']}' 校准失败。\n采样数据一致性过差，无法找到共识值"
            send_desktop_notification("配置失败", msg, level="warning")
            logging.error(msg.replace('\n', ' '))
            return
        final_avg_unit = round(np.mean(largest_cluster))
        final_std_dev = np.std(largest_cluster)
        matching_enabled = final_std_dev >= 0.05
        logging.info(f"最终分析: 平均滚动单位={final_avg_unit} 缓冲区px, 标准差={final_std_dev:.3f}, 决策:开启误差修正={matching_enabled}")
        if config.save_scroll_unit(state["app_class"], final_avg_unit, matching_enabled):
            status_str = "启用" if matching_enabled else "禁用"
            msg = f"已为 '{state['app_class']}' 保存滚动单位: {final_avg_unit}px (缓冲区px)\n误差修正已{status_str}"
            send_desktop_notification("配置成功", msg, level="success")
        else:
            send_desktop_notification("配置失败", "写入配置文件时发生错误", level="warning")

class ScrollManager:
    def __init__(self, config: Config, session: CaptureSession, view: 'CaptureOverlay'):
        self.config = config
        self.session = session
        self.view = view
        self.is_fine_scrolling = False
        self.gdk_display = Gdk.Display.get_default()
        self.gdk_seat = self.gdk_display.get_default_seat()
        self.gdk_pointer = self.gdk_seat.get_pointer()
        self.gdk_screen = self.gdk_display.get_default_screen()
        self.evdev_abs_mouse = None

    def _get_pointer_position(self):
        """使用 GDK 获取当前鼠标指针位置"""
        # 逻辑px全局坐标
        def _get_pos_impl():
            try:
                if IS_WAYLAND:
                    win = self.view.get_window()
                    if win:
                        _, wx, wy, _ = win.get_device_position(self.gdk_pointer)
                        return self.view.window_to_global(wx, wy) # 窗口坐标 -> 全局坐标
                    return (0, 0)
                else:
                    _, x, y = self.gdk_pointer.get_position()
                    return (x, y)
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

    def _set_pointer_position(self, x, y):
        """设置鼠标指针位置"""
        # x, y: 逻辑px全局坐标
        def do_warp():
            try:
                if IS_WAYLAND:
                    if self.evdev_abs_mouse:
                        scale = self.view.scale
                        # 逻辑px -> 缓冲区px
                        buf_x = round(x * scale)
                        buf_y = round(y * scale)
                        self.evdev_abs_mouse.move(buf_x, buf_y)
                    else:
                        logging.warning("Wayland 下缺少 EvdevAbsoluteMouse，无法移动鼠标")
                else:
                    self.gdk_pointer.warp(self.gdk_screen, x, y)
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
        center_x = int(shot_x + self.session.geometry['w'] / 2)
        center_y = int(shot_y + self.session.geometry['h'] / 2)
        g_center_x, g_center_y = self.view.window_to_global(center_x, center_y) # 窗口坐标 -> 全局坐标
        if self.config.SCROLL_METHOD == 'invisible_cursor' and self.view.invisible_scroller:
            logging.info(f"使用隐形光标执行离散滚动: {ticks} 格")
            scroller = self.view.invisible_scroller
            try:
                scroller.move(g_center_x, g_center_y)
                time.sleep(0.05)
                scroller.discrete_scroll(ticks)
            finally:
                time.sleep(0.05)
                scroller.park()
        else:
            logging.info(f"使用用户光标执行离散滚动: {ticks} 格")
            original_pos = self._get_pointer_position() # 逻辑px全局坐标
            self._set_pointer_position(g_center_x + 1, g_center_y + 1) 
            self._set_pointer_position(g_center_x, g_center_y)
            time.sleep(0.05)
            try:
                scrolled_via_xtest = False
                if not IS_WAYLAND:
                    try:
                        logging.info(f"使用 XTest 执行离散滚动: {ticks} 格")
                        disp = display.Display()
                        button_code = 4 if ticks > 0 else 5
                        num_clicks = abs(ticks)
                        for i in range(num_clicks):
                            xtest.fake_input(disp, X.ButtonPress, button_code)
                            disp.sync()
                            time.sleep(0.01)
                            xtest.fake_input(disp, X.ButtonRelease, button_code)
                            disp.sync()
                            if i < num_clicks - 1:
                                time.sleep(0.03)
                        disp.close()
                        scrolled_via_xtest = True
                    except Exception as e:
                        logging.warning(f"使用 XTest 模拟滚动失败，尝试回退到 Evdev: {e}")
                        try: disp.close()
                        except: pass
                if not scrolled_via_xtest:
                    if self.view.evdev_wheel_scroller:
                        logging.info(f"使用 Evdev 执行离散滚动: {ticks} 格")
                        try:
                            self.view.evdev_wheel_scroller.scroll_discrete(ticks)
                        except Exception as e:
                            logging.error(f"使用 Evdev 模拟滚动失败: {e}")
                    else:
                        logging.warning("滚动失败 Evdev 未配置")
            except Exception as e:
                logging.error(f"模拟滚动失败: {e}")
            if return_cursor:
               time.sleep(0.05)
               self._set_pointer_position(*original_pos) # 逻辑px全局坐标
            else:
                time.sleep(0.05)

class ActionController:
    """处理所有用户操作和业务逻辑"""
    def __init__(self, session: CaptureSession, view: 'CaptureOverlay', config: Config):
        self.session = session
        self.view = view
        self.config = config
        self.scroll_manager = ScrollManager(self.config, self.session, self.view)
        self.grid_mode_controller = GridModeController(self.config, self.session, self.view)
        self._hotkey_actions = [
            (config.HOTKEY_CAPTURE, self.take_capture),
            (config.HOTKEY_FINALIZE, self.finalize_and_quit),
            (config.HOTKEY_UNDO, self.delete_last_capture),
            (config.HOTKEY_CANCEL, self.quit_and_cleanup),
            (config.HOTKEY_GRID_BACKWARD, lambda: self.handle_movement_action('up', source='hotkey')),
            (config.HOTKEY_GRID_FORWARD, lambda: self.handle_movement_action('down', source='hotkey')),
            (config.HOTKEY_AUTO_SCROLL_START, self.start_auto_scroll),
            (config.HOTKEY_AUTO_SCROLL_STOP, self.stop_auto_scroll),
            (config.HOTKEY_CONFIGURE_SCROLL_UNIT, self.grid_mode_controller.start_calibration),
            (config.HOTKEY_TOGGLE_GRID_MODE, self.grid_mode_controller.toggle),
            (config.HOTKEY_TOGGLE_PREVIEW, self.view.toggle_preview_panel),
            (config.HOTKEY_OPEN_CONFIG_EDITOR, self.view.toggle_config_panel),
            (config.HOTKEY_TOGGLE_INSTRUCTION_PANEL, self.view.toggle_instruction_panel),
            (config.HOTKEY_PREVIEW_ZOOM_IN, lambda: self.view.preview_panel._zoom_in() if self.view.preview_panel and self.view.preview_panel.get_visible() else None),
            (config.HOTKEY_PREVIEW_ZOOM_OUT, lambda: self.view.preview_panel._zoom_out() if self.view.preview_panel and self.view.preview_panel.get_visible() else None),
        ]
        self.current_mode_str = "自由模式"
        self.final_notification = None
        self.is_exiting = False
        self.is_dragging = False
        self.is_processing_movement = False
        self.is_auto_scrolling = False
        self.auto_scroll_timer_id = None
        self.is_first_auto_capture = False
        self.auto_scroll_original_cursor_pos = None # 逻辑px全局坐标
        self.auto_scroll_needs_capture = False
        self.auto_scroll_return_cursor = False
        self.auto_mode_context = None
        self.SCROLL_TIME_MS = 200
        self.CAPTURE_DELAY_MS = 150
        self.AUTO_SCROLL_INTERVAL_MS = 300
        self.AUTO_CAPTURE_DELAY_MS = 50
        self.resize_edge = None
        # 逻辑px窗口坐标
        self.drag_start_geometry = {}
        self.drag_start_x_rel = 0
        self.drag_start_y_rel = 0
        self._capture_filename_counter = 0
        self.stitch_model = StitchModel()
        self.task_queue = queue.Queue()
        self.result_queue = queue.Queue()
        self.stitch_worker = threading.Thread(
            target=self._stitch_worker_loop,
            args=(self.task_queue, self.result_queue, session.known_scroll_distances),
            daemon=True
        )
        self.stitch_worker_running = True
        self.stitch_worker.start()
        self.result_check_timer_id = GLib.timeout_add(100, self._check_result_queue)
        logging.info("StitchWorker 后台线程及结果检查器已启动")
        self.stitch_model.connect('model-updated', self._on_model_updated)

    def _on_model_updated(self, model_instance):
        self.update_info_panel()
        can_undo = self.stitch_model.capture_count > 0 and not self.is_auto_scrolling
        if self.view.show_side_panel:
            self.view.button_panel.set_undo_sensitive(can_undo)
        self._check_horizontal_lock_state()

    def set_current_mode(self, mode_str: str):
        self.current_mode_str = mode_str
        self.update_info_panel()

    # 缓冲区px {
    def update_info_panel(self):
        if self.view.show_side_panel and self.view.side_panel:
            self.view.side_panel.info_panel.update_info(
                count=self.stitch_model.capture_count,
                width=self.stitch_model.image_width,
                height=self.stitch_model.total_virtual_height,
                mode_str=self.current_mode_str
            )

    def _check_horizontal_lock_state(self):
        should_be_locked = self.stitch_model.capture_count > 0
        if should_be_locked and not self.session.is_horizontally_locked:
            self.session.is_horizontally_locked = True
            logging.info("第一张截图已添加到模型，窗口水平位置和宽度已被锁定")
        elif not should_be_locked and self.session.is_horizontally_locked:
            self.session.is_horizontally_locked = False
            logging.info("所有截图均已移除，已解锁窗口水平调整功能")

    def _check_result_queue(self):
        while not self.result_queue.empty():
            try:
                result = self.result_queue.get_nowait()
                result_type = result[0]
                payload = result[1]
                if result_type == 'ADD_RESULT':
                    filepath, width, height, overlap = payload
                    logging.info(f"主线程: 处理结果 {Path(filepath).name}, overlap={overlap}")
                    self.stitch_model.add_entry(filepath, width, height, overlap)
                elif result_type == 'LEARNED_SCROLL':
                    s_new = payload
                    if s_new not in self.session.known_scroll_distances:
                        self.session.known_scroll_distances.append(s_new)
                        logging.info(f"主线程: 学习到新滚动距离: {s_new} 缓冲区px")
                elif result_type == 'POP_REQUEST_RECEIVED':
                    logging.info("主线程: 收到 Worker 的 POP 确认，执行模型删除")
                    self.stitch_model.pop_entry()
                elif result_type == 'BOTTOM_REACHED':
                    logging.info("主线程: 收到 Worker 的 BOTTOM_REACHED 信号，停止自动滚动")
                    if self.is_auto_scrolling:
                        GLib.idle_add(self.stop_auto_scroll, "检测到页面已到达底部")
                    else:
                        logging.warning("收到 BOTTOM_REACHED 但当前并非自动滚动状态")
            except queue.Empty:
                break
            except Exception as e:
                logging.exception(f"处理 Worker 结果时出错: {e}")
        return True

    @staticmethod
    def _check_if_bottom_reached(img_top, img_bottom, search_height, threshold=0.95):
        """检查 img_bottom 的底部是否与 img_top 的底部高度匹配"""
        h_top, _, _ = img_top.shape
        h_bottom, w_bottom, _ = img_bottom.shape
        effective_search_height = min(search_height, h_top, h_bottom)
        if effective_search_height <= 0:
            return False
        template_bottom = img_bottom[h_bottom - effective_search_height:, :]
        region_top_bottom = img_top[h_top - effective_search_height:, :]
        result = cv2.matchTemplate(region_top_bottom, template_bottom, cv2.TM_CCOEFF_NORMED)
        score = result[0][0]
        return score >= threshold

    @staticmethod
    def _stitch_worker_loop(task_queue: queue.Queue, result_queue: queue.Queue, known_scroll_distances: list):
        """后台工作线程的主循环"""
        logging.info("StitchWorker 线程开始运行...")
        while True:
            try:
                task = task_queue.get(timeout=1)
            except queue.Empty:
                continue
            if task is None or task.get('type') == 'EXIT':
                logging.info("StitchWorker 收到退出信号")
                break
            if task.get('type') == 'ADD':
                filepath_str = task.get('filepath')
                prev_filepath_str = task.get('prev_filepath')
                should_match = task.get('should_perform_matching', False)
                auto_mode_context = task.get('auto_mode_context')
                is_grid_mode = task.get('is_grid_mode', False)
                grid_matching_enabled = task.get('grid_matching_enabled', False)
                filepath = Path(filepath_str)
                logging.info(f"StitchWorker: 处理 ADD 任务: {filepath.name}")
                if not filepath.is_file():
                    logging.error(f"StitchWorker: 文件不存在 {filepath}")
                    task_queue.task_done()
                    continue
                try:
                    img_new_np = cv2.imread(filepath_str)
                    if img_new_np is None: raise ValueError("cv2.imread 返回 None")
                    h_new, w_new, _ = img_new_np.shape
                    should_perform_matching = False
                    max_overlap_to_use = 0
                    if prev_filepath_str:
                        ticks_scrolled = task.get('ticks_scrolled', 2)
                        if auto_mode_context is not None:
                            should_perform_matching = True
                            max_overlap_to_use = h_new - ticks_scrolled * config.MIN_SCROLL_PER_TICK
                        elif is_grid_mode:
                            should_perform_matching = grid_matching_enabled
                            max_overlap_to_use = config.GRID_MATCHING_MAX_OVERLAP
                        else:
                            should_perform_matching = config.ENABLE_FREE_SCROLL_MATCHING
                            max_overlap_to_use = config.FREE_SCROLL_MATCHING_MAX_OVERLAP
                    overlap = 0
                    if prev_filepath_str:
                        logging.info(f"StitchWorker: 计算 {filepath.name} 与 {Path(prev_filepath_str).name} 的重叠")
                        img_top_np = cv2.imread(prev_filepath_str)
                        score = 0.0
                        if img_top_np is None: raise ValueError(f"无法加载上一张图片 {prev_filepath_str}")
                        h_top, _, _ = img_top_np.shape
                        predicted_overlap = -1
                        if known_scroll_distances:
                            valid_candidates = []
                            for s_known in known_scroll_distances:
                                potential_overlap = h_new - s_known
                                if 1 <= potential_overlap < min(h_top, h_new):
                                    _, score = _find_overlap_brute_force(img_top_np, img_new_np, potential_overlap, potential_overlap)
                                    PREDICTION_THRESHOLD = 0.95
                                    if score > PREDICTION_THRESHOLD:
                                        valid_candidates.append((potential_overlap, score))
                            if valid_candidates:
                                valid_candidates.sort(key=lambda x: x[0], reverse=True)
                                best_candidate = valid_candidates[0]
                                predicted_overlap = best_candidate[0]
                                logging.info(f"StitchWorker: 预测选中最佳 overlap={predicted_overlap} (score={best_candidate[1]:.3f})，候选数: {len(valid_candidates)}")
                        if predicted_overlap != -1:
                            overlap = predicted_overlap
                        else:
                            if should_perform_matching:
                                search_range = min(max_overlap_to_use, h_top - 1, h_new - 1)
                                if search_range > 0:
                                    logging.info(f"StitchWorker: 预测失败，执行全范围搜索 (max={search_range}px)...")
                                    found_overlap, score = _find_overlap_pyramid(img_top_np, img_new_np, search_range)
                                    s_new = h_new - found_overlap
                                    QUALITY_THRESHOLD = 0.95
                                    bottom_check_height = config.MIN_SCROLL_PER_TICK
                                    if score >= QUALITY_THRESHOLD and s_new >= ticks_scrolled*config.MIN_SCROLL_PER_TICK:
                                        overlap = found_overlap
                                        logging.info(f"StitchWorker: 计算重叠成功: 滚动距离{s_new}px, score={score:.3f}")
                                        is_stuck = False
                                        KNOWN_SCROLL_DEVIATION_LOW = 0.5
                                        KNOWN_SCROLL_DEVIATION_HIGH = 1.5
                                        if auto_mode_context is not None and known_scroll_distances:
                                            stable_distance = np.median(known_scroll_distances[-5:])
                                            if stable_distance > 0 and (s_new < (stable_distance * KNOWN_SCROLL_DEVIATION_LOW) or s_new > (stable_distance * KNOWN_SCROLL_DEVIATION_HIGH)):
                                                logging.warning(f"StitchWorker: 检测到滚动距离异常。当前: {s_new}px, 稳定值: {stable_distance:.1f}px。")
                                                is_stuck = True
                                        if is_stuck:
                                            if ActionController._check_if_bottom_reached(img_top_np, img_new_np, bottom_check_height):
                                                logging.info("StitchWorker: 滚动距离异常且检测到底部，发送 BOTTOM_REACHED 信号")
                                                result_queue.put(('BOTTOM_REACHED', None))
                                                continue
                                            else:
                                                logging.warning("StitchWorker: 滚动距离异常，但底部检测未通过。将接受此帧。")
                                        if s_new > 0 and s_new not in known_scroll_distances:
                                            result_queue.put(('LEARNED_SCROLL', s_new))
                                    else:
                                        logging.warning(f"StitchWorker: 计算重叠失败 (score={score:.3f}, s_new={s_new}px). 阈值未满足 (score>={QUALITY_THRESHOLD} and s_new>={ticks_scrolled*config.MIN_SCROLL_PER_TICK})")
                                        if auto_mode_context is not None:
                                            if ActionController._check_if_bottom_reached(img_top_np, img_new_np, bottom_check_height):
                                                logging.info("StitchWorker: 检测到底部，发送 BOTTOM_REACHED 信号")
                                                result_queue.put(('BOTTOM_REACHED', None))
                                                continue
                                            else:
                                                logging.info("StitchWorker: 重叠匹配失败，底部检测也未通过")
                                else:
                                    logging.warning("StitchWorker: 有效搜索范围为0，跳过重叠计算")
                            else:
                                logging.info("StitchWorker: 无需进行匹配，设置 overlap=0")
                                overlap = 0
                    result_queue.put(('ADD_RESULT', (filepath_str, w_new, h_new, overlap)))
                except Exception as e:
                    logging.exception(f"StitchWorker: 处理 ADD 任务时出错 ({filepath.name}): {e}")
                    try:
                        if 'w_new' not in locals() or 'h_new' not in locals():
                            with Image.open(filepath) as img: w_new, h_new = img.size
                        result_queue.put(('ADD_RESULT', (filepath_str, w_new, h_new, 0)))
                    except Exception as fallback_e:
                        logging.error(f"StitchWorker: 获取图片尺寸失败: {fallback_e}")
                finally:
                    task_queue.task_done()
            elif task.get('type') == 'POP':
                logging.info("StitchWorker: 收到 POP 任务，发送确认回主线程")
                result_queue.put(('POP_REQUEST_RECEIVED', None))
                task_queue.task_done()
            else:
                logging.warning(f"StitchWorker: 收到未知任务类型: {task.get('type')}")
                task_queue.task_done()
        logging.info("StitchWorker 线程已结束。")
    # 缓冲区px }

    def handle_movement_action(self, direction: str, source: str = 'hotkey'):
        """根据配置文件处理前进/后退动作 (滚动, 截图, 删除). """
        if self.is_exiting:
            return
        if self.is_processing_movement:
            logging.warning("正在处理上一个移动动作，忽略新的请求")
            return
        if not self.grid_mode_controller.is_active:
            logging.warning("非整格模式下，前进/后退动作无效")
            send_desktop_notification("操作无效", "前进/后退操作仅在整格模式下可用", level="normal")
            return
        self.is_processing_movement = True
        grid_unit_buf = self.grid_mode_controller.grid_unit # 缓冲区px
        scale = self.view.scale
        if grid_unit_buf <= 0:
            logging.error("整格模式滚动单位无效，无法执行操作")
            self.is_processing_movement = False
            return
        action_str = config.FORWARD_ACTION if direction == 'down' else config.BACKWARD_ACTION
        actions = action_str.lower().replace(" ", "").split('_')
        def do_scroll_action(callback):
            region_height = self.session.geometry['h'] # 逻辑px
            num_ticks = int(int(region_height * scale) / grid_unit_buf) # 逻辑px -> 缓冲区px
            direction_sign = 1 if direction == 'up' else -1
            total_ticks = num_ticks * direction_sign
            should_return = (source == 'button')
            self.scroll_manager.scroll_discrete(total_ticks, return_cursor=should_return)
            GLib.timeout_add(self.SCROLL_TIME_MS, callback)
            return False
        def do_capture_action(callback):
            logging.info("执行截图...")
            self.take_capture()
            GLib.timeout_add(self.CAPTURE_DELAY_MS, callback)
            return False
        def do_delete_action(callback):
            logging.info("执行删除...")
            self.delete_last_capture()
            GLib.timeout_add(self.CAPTURE_DELAY_MS, callback)
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

    def _release_movement_lock(self):
         if self.is_processing_movement:
             self.is_processing_movement = False
         return False

    def take_capture(self, widget=None, auto_mode=False):
        """执行截图的核心逻辑"""
        if self.is_exiting:
            return
        grabbed_seat = None
        filepath = None
        self.auto_mode_context = None
        if not auto_mode and self.is_auto_scrolling:
            logging.warning("自动滚动模式下忽略手动截图请求")
            return
        try:
            # 逻辑px窗口坐标
            shot_x = self.session.geometry['x']
            shot_y = self.session.geometry['y']
            shot_w = self.session.geometry['w']
            shot_h = self.session.geometry['h']
            is_grid = self.grid_mode_controller.is_active
            is_auto = self.is_auto_scrolling or auto_mode
            should_include_cursor = self.config.CAPTURE_WITH_CURSOR and not is_grid and not is_auto
            if auto_mode:
                if self.is_first_auto_capture:
                    logging.info("自动模式：截取首次完整高度")
                    cap_x, cap_y, cap_w, cap_h = shot_x, shot_y, shot_w, shot_h
                    self.auto_mode_context = {'initial_full': True}
                    self.is_first_auto_capture = False
                else:
                    ticks_to_scroll = self.config.AUTO_SCROLL_TICKS_PER_STEP
                    capture_height_per_tick_buf = self.config.MAX_SCROLL_PER_TICK # 缓冲区px
                    total_capture_height = capture_height_per_tick_buf * ticks_to_scroll
                    scale = self.view.scale
                    cap_h_logical = min(int(total_capture_height / scale), shot_h) # 缓冲区px -> 逻辑px
                    cap_h = cap_h_logical
                    cap_y = shot_y + shot_h - cap_h
                    cap_x, cap_w = shot_x, shot_w
                    self.auto_mode_context = {'initial_full': False}
            else:
                cap_x, cap_y, cap_w, cap_h = shot_x, shot_y, shot_w, shot_h
                self.auto_mode_context = None
            mon_x, mon_y = self.view.window_to_monitor(cap_x, cap_y) # 窗口坐标 -> 显示器坐标
            self._move_cursor_out_if_needed(mon_x, mon_y, cap_w, cap_h, should_include_cursor)
            if IS_WAYLAND and not should_include_cursor:
                time.sleep(0.1)
            if cap_w <= 0.5 or cap_h <= 0.5:
                logging.warning(f"捕获区域过小，跳过截图。尺寸: {cap_w}x{cap_h}")
                send_desktop_notification("截图跳过", "选区太小，无法截图", "dialog-warning", level="warning")
                return
            filepath = config.TMP_DIR / f"{self._capture_filename_counter:04d}_capture.png"
            self._capture_filename_counter += 1
            if FRAME_GRABBER.capture(mon_x, mon_y, cap_w, cap_h, filepath, self.view.scale, include_cursor=should_include_cursor):
                logging.info(f"已捕获截图: {filepath}")
                if not auto_mode:
                    play_sound(config.CAPTURE_SOUND)
                prev_filepath = self.stitch_model.entries[-1]['filepath'] if self.stitch_model.entries else None
                task = {
                    'type': 'ADD',
                    'filepath': str(filepath),
                    'prev_filepath': prev_filepath,
                    'is_grid_mode': self.grid_mode_controller.is_active,
                    'grid_matching_enabled': self.session.is_matching_enabled,
                    'auto_mode_context': self.auto_mode_context,
                    'ticks_scrolled': self.config.AUTO_SCROLL_TICKS_PER_STEP if auto_mode and not self.auto_mode_context.get('initial_full', False) else 2
                }
                self.task_queue.put(task)
            else:
                 logging.error(f"截图失败: {filepath}")
                 filepath = None
        except Exception as e:
            logging.error(f"执行截图失败: {e}")
            send_desktop_notification("截图失败", f"无法执行截图命令: {e}", "dialog-warning", level="warning")
            filepath = None

    def _move_cursor_out_if_needed(self, mon_x, mon_y, w, h, should_include_cursor):
        """Wayland 在自动模式和整格模式下如果配置为截取鼠标指针，则将其移动到截图区域之外"""
        # mon_x, mon_y, w, h: 逻辑px; mon_x, mon_y: 显示器坐标
        if not IS_WAYLAND:
            return
        if not config.CAPTURE_WITH_CURSOR:
            return
        if should_include_cursor:
            return
        monitor_offset_x = self.view.screen_rect.x if self.view.screen_rect else 0
        monitor_offset_y = self.view.screen_rect.y if self.view.screen_rect else 0
        # 显示器坐标 -> 全局坐标
        g_x_logic = mon_x + monitor_offset_x
        g_y_logic = mon_y + monitor_offset_y
        target_x_logic = g_x_logic + w + 40
        target_y_logic = g_y_logic + (h // 2)
        scale = self.view.scale
        # 逻辑px -> 缓冲区px
        target_x_buf = round(target_x_logic * scale)
        target_y_buf = round(target_y_logic * scale)
        abs_mouse = self.scroll_manager.evdev_abs_mouse
        if abs_mouse:
            target_x_buf = min(target_x_buf, abs_mouse.max_x)
            target_y_buf = min(target_y_buf, abs_mouse.max_y)
            logging.info(f"移动鼠标至区域外 ({target_x_buf}, {target_y_buf}) 以避开截图")
            abs_mouse.move(target_x_buf, target_y_buf)

    def delete_last_capture(self, widget=None):
        if self.is_exiting:
            return
        logging.info("请求删除最后一张截图...")
        if self.is_auto_scrolling:
            logging.warning("自动滚动模式下忽略撤销请求")
            return
        play_sound(config.UNDO_SOUND)
        task = {'type': 'POP'}
        self.task_queue.put(task)

    def start_auto_scroll(self, widget=None, source='hotkey'):
        if self.is_exiting:
            return
        if self.is_auto_scrolling:
            logging.warning("自动滚动已在运行中")
            return
        self.is_auto_scrolling = True
        global hotkey_listener
        if hotkey_listener:
            hotkey_listener.enable_mouse_click_stop(True)
        self.set_current_mode("自动模式")
        self.auto_scroll_return_cursor = (source == 'button')
        if self.auto_scroll_return_cursor and self.config.SCROLL_METHOD == 'move_user_cursor':
            self.auto_scroll_original_cursor_pos = self.scroll_manager._get_pointer_position()
            logging.info(f"自动模式：记录原始光标位置 {self.auto_scroll_original_cursor_pos}")
        self.auto_scroll_needs_capture = False
        if self.stitch_model.capture_count == 0:
            logging.info("自动模式：首次启动，先进行一次完整截图")
            self.is_first_auto_capture = True
            self.take_capture(auto_mode=True)
            self.auto_scroll_timer_id = GLib.timeout_add(self.AUTO_CAPTURE_DELAY_MS, self._auto_scroll_step)
        else:
            logging.info("自动模式：继续添加截图，直接开始滚动")
            self.is_first_auto_capture = False
            self._auto_scroll_step()
        send_desktop_notification("自动模式已启动", f"按 {config.str_auto_scroll_stop.upper()} 或点击鼠标左键停止", level="normal")
        btn_panel = self.view.button_panel
        btn_panel.btn_capture.set_sensitive(False)
        btn_panel.btn_undo.set_sensitive(False)
        btn_panel.btn_auto_start.set_sensitive(False)

    def stop_auto_scroll(self, reason_message=None):
        if not self.is_auto_scrolling:
            return
        logging.info("正在停止自动滚动...")
        self.is_auto_scrolling = False
        global hotkey_listener
        if hotkey_listener:
            hotkey_listener.enable_mouse_click_stop(False)
        should_restore = self.auto_scroll_return_cursor and self.auto_scroll_original_cursor_pos
        if self.auto_scroll_timer_id:
            GLib.source_remove(self.auto_scroll_timer_id)
            self.auto_scroll_timer_id = None
            logging.info("自动滚动定时器已移除")
            if self.auto_scroll_needs_capture:
                delay_ms = 300
                GLib.timeout_add(delay_ms, self._perform_delayed_final_capture, should_restore)
                should_restore = False
        self._release_movement_lock()
        if should_restore:
            logging.info(f"自动模式结束：恢复原始光标位置到 {self.auto_scroll_original_cursor_pos}")
            self.scroll_manager._set_pointer_position(*self.auto_scroll_original_cursor_pos)
            self.auto_scroll_original_cursor_pos = None
        if reason_message:
            send_desktop_notification("自动滚动已停止", reason_message, level="normal")
        else:
            send_desktop_notification("自动模式已停止", "用户按快捷键停止", level="normal")
        if self.grid_mode_controller.is_active:
            self.set_current_mode("整格模式")
        else:
            self.set_current_mode("自由模式")
        btn_panel = self.view.button_panel
        btn_panel.btn_capture.set_sensitive(True)
        btn_panel.set_undo_sensitive(self.stitch_model.capture_count > 0)
        btn_panel.btn_grid_forward.set_sensitive(True)
        btn_panel.btn_grid_backward.set_sensitive(True)
        btn_panel.btn_auto_start.set_sensitive(True)

    def _perform_delayed_final_capture(self, should_restore_cursor=False):
        if self.is_exiting:
            return False
        logging.info("自动模式：执行延迟后的最终截图")
        self.take_capture(auto_mode=True)
        self.auto_scroll_needs_capture = False
        if should_restore_cursor and self.auto_scroll_original_cursor_pos:
            logging.info(f"延迟恢复原始光标位置到 {self.auto_scroll_original_cursor_pos}")
            self.scroll_manager._set_pointer_position(*self.auto_scroll_original_cursor_pos)
            self.auto_scroll_original_cursor_pos = None
        return False

    def _auto_scroll_step(self):
        if not self.is_auto_scrolling:
            self._release_movement_lock()
            return False
        if self.is_processing_movement:
            logging.warning("自动滚动：正在处理上一动作，等待100ms")
            self.auto_scroll_timer_id = GLib.timeout_add(100, self._auto_scroll_step)
            return False
        ticks_to_scroll = self.config.AUTO_SCROLL_TICKS_PER_STEP
        base_interval_ms = self.AUTO_SCROLL_INTERVAL_MS
        dynamic_interval_ms = int(base_interval_ms * (1 + 0.6 * (ticks_to_scroll - 1)))
        dynamic_interval_ms = min(dynamic_interval_ms, 2000)
        logging.info(f"自动滚动: 滚动 {ticks_to_scroll} 格, 等待 {dynamic_interval_ms}ms 后截图")
        self.scroll_manager.scroll_discrete(-ticks_to_scroll, return_cursor=False)
        self.auto_scroll_needs_capture = True
        self.is_processing_movement = True
        self.auto_scroll_timer_id = GLib.timeout_add(
            dynamic_interval_ms,
            self._auto_capture_step
        )
        return False

    def _auto_capture_step(self):
        if not self.is_auto_scrolling:
            self._release_movement_lock()
            return False
        self.take_capture(auto_mode=True)
        self.is_processing_movement = False
        self.auto_scroll_needs_capture = False
        self.auto_scroll_timer_id = GLib.timeout_add(
            self.AUTO_CAPTURE_DELAY_MS, 
            self._auto_scroll_step
        )
        return False

    def finalize_and_quit(self, widget=None):
        """执行完成拼接并退出的逻辑"""
        # 缓冲区px
        if self.is_exiting:
            logging.warning("正在退出中，忽略完成请求")
            return
        if self.is_auto_scrolling:
            self.stop_auto_scroll()
        if self.stitch_model.capture_count == 0:
            logging.warning("未进行任何截图。正在退出")
            self.quit_and_cleanup()
            return
        if hotkey_listener and hotkey_listener.is_alive():
            hotkey_listener.stop()
        self.is_exiting = True
        logging.info("请求停止 StitchWorker 并等待...")
        self.task_queue.put({'type': 'EXIT'})
        self.stitch_worker.join(timeout=2.0)
        self.stitch_worker_running = False
        self._check_result_queue()
        logging.info("StitchWorker 已停止且结果队列已清空.")
        processing_panel, progress_bar = self.view._show_processing_panel()
        if self.view.preview_panel and self.view.preview_panel.get_visible():
             logging.info("检测到预览面板已打开，正在隐藏它...")
             GLib.idle_add(self.view.preview_panel.hide)
        if self.view.config_panel and self.view.config_panel.get_visible():
             logging.info("检测到配置面板已打开，正在隐藏它...")
             GLib.idle_add(self.view.config_panel.hide)
        render_plan_snapshot = list(self.stitch_model.render_plan)
        image_width_snapshot = self.stitch_model.image_width
        if render_plan_snapshot:
            last_piece = render_plan_snapshot[-1]
            total_height_snapshot = last_piece['render_y_start'] + last_piece['height']
        else:
            total_height_snapshot = 0
        thread = threading.Thread(
            target=self._perform_final_stitch_and_save,
            args=(processing_panel, progress_bar, render_plan_snapshot, image_width_snapshot, total_height_snapshot),
            daemon=True
        )
        thread.start()

    def _release_heavy_resources(self):
        """在保持窗口显示通知的同时，释放线程等重资源"""
        logging.info("正在释放重资源...")
        if FRAME_GRABBER:
            FRAME_GRABBER.cleanup()
        if self.view.invisible_scroller:
            self.view.invisible_scroller.cleanup()
            self.view.invisible_scroller = None

    def _ensure_cleanup(self):
        if self.final_notification is not None:
            logging.warning("通知关闭回调超时，强制执行清理")
            self.final_notification = None
            self._perform_cleanup()
        return GLib.SOURCE_REMOVE

    def _perform_final_stitch_and_save(self, processing_panel, progress_bar, render_plan, image_width, total_height):
        """在后台线程中执行拼接和保存"""
        # 缓冲区px
        finalize_start_time = time.perf_counter()
        label_widget = None
        try:
            vbox = processing_panel.get_children()[0]
            top_hbox = vbox.get_children()[0]
            label_widget = top_hbox.get_children()[1]
        except Exception as e:
            logging.warning(f"无法找到处理窗口中的Label控件: {e}")
        def update_progress(fraction):
            GLib.idle_add(progress_bar.set_fraction, fraction)
            return GLib.SOURCE_REMOVE
        def update_label_text(text):
            if label_widget:
                markup = f"<span color='white' font_weight='bold'>{GLib.markup_escape_text(text)}</span>"
                GLib.idle_add(label_widget.set_markup, markup)
            return GLib.SOURCE_REMOVE
        def _schedule_clipboard_task(path_to_copy):
            copy_to_clipboard(path_to_copy)
            return GLib.SOURCE_REMOVE
        try:
            if not render_plan:
                logging.warning("传入的截图列表为空，退出处理")
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
            if stitched_image:
                update_label_text("正在保存...")
                GLib.idle_add(progress_bar.set_fraction, 1.0)
                save_start_time = time.perf_counter()
                if config.SAVE_FORMAT == 'JPEG':
                    logging.info(f"以 JPEG 格式保存，质量为 {config.JPEG_QUALITY}")
                    if stitched_image.mode == 'RGBA':
                        stitched_image = stitched_image.convert('RGB')
                    stitched_image.save(str(output_file), 'JPEG', quality=config.JPEG_QUALITY)
                else:
                    logging.info("以 PNG 格式保存")
                    stitched_image.save(str(output_file), 'PNG')
                save_duration = time.perf_counter() - save_start_time
                logging.info(f"图片成功使用 Pillow 拼接并保存到: {output_file}，保存耗时: {save_duration:.3f} 秒")
                message = f"已保存到: {output_file}"
                if config.COPY_TO_CLIPBOARD:
                    update_label_text("复制到剪贴板...")
                    logging.info("开始复制到剪贴板")
                    GLib.idle_add(_schedule_clipboard_task, output_file)
                    message += "\n并已复制到剪贴板"
                total_finalize_duration = time.perf_counter() - finalize_start_time
                logging.info(f"完成最终处理总耗时: {total_finalize_duration:.3f} 秒")
                GLib.idle_add(
                    lambda: send_desktop_notification(
                        title="长截图制作成功",
                        message=message,
                        sound_name=config.FINALIZE_SOUND,
                        level="success",
                        action_path=output_file,
                        controller=self,
                        width=image_width,
                        height=total_height,
                    )
                )
                self._release_heavy_resources()
                GLib.idle_add(self.view.enter_notification_mode)
        except Exception as e:
            logging.error(f"最终处理时发生错误: {e}")
            GLib.idle_add(
                lambda: send_desktop_notification(
                    title="长截图制作失败",
                    message=f"发生错误: {e}",
                    sound_name="dialog-error",
                    level="warning",
                )
            )
            GLib.idle_add(self._perform_cleanup)
        finally:
            GLib.idle_add(processing_panel.destroy)

    def quit_and_cleanup(self, widget=None):
        """处理带确认的退出逻辑"""
        if self.is_exiting:
            logging.warning("正在退出中，忽略重复退出请求")
            return
        if self.is_auto_scrolling:
            self.stop_auto_scroll()
        if self.stitch_model.capture_count == 0:
            logging.info("没有截图，直接退出")
            self._perform_cleanup()
            return
        response = self.view.show_quit_confirmation_dialog()
        if self.is_exiting: return
        if response == Gtk.ResponseType.YES:
            logging.info("用户确认放弃截图")
            self._perform_cleanup()
        else:
            logging.info("用户取消了放弃操作")

    def _perform_cleanup(self):
        """执行最终的清理工作"""
        self.is_exiting = True
        logging.info("正在执行清理和退出操作")
        global hotkey_listener
        if hotkey_listener and hotkey_listener.is_alive():
            hotkey_listener.stop()
        if self.result_check_timer_id:
             GLib.source_remove(self.result_check_timer_id)
             self.result_check_timer_id = None
             logging.info("结果检查定时器已移除")
        if self.stitch_worker_running:
             logging.info("检测到 StitchWorker 仍在运行，尝试最后停止...")
             self.task_queue.put({'type': 'EXIT'})
             self.stitch_worker.join(timeout=0.5)
             self.stitch_worker_running = False
        if self.view.invisible_scroller:
            cleanup_thread = threading.Thread(target=self.view.invisible_scroller.cleanup)
            cleanup_thread.start()
            logging.info("InvisibleCursorScroller.cleanup() 正在后台线程中执行")
        if self.view.evdev_wheel_scroller:
            self.view.evdev_wheel_scroller.close()
            logging.info("EvdevWheelScroller 已关闭")
        if self.scroll_manager.evdev_abs_mouse:
            self.scroll_manager.evdev_abs_mouse.close()
            logging.info("EvdevAbsoluteMouse 已关闭")
        if FRAME_GRABBER:
            FRAME_GRABBER.cleanup()
            logging.info("FrameGrabber 已清理")
        self.session.cleanup()
        Gtk.main_quit()

    def handle_key_press(self, event):
        """处理来自视图的按键事件"""
        global are_hotkeys_enabled
        global hotkey_listener
        if hotkey_listener and hotkey_listener.running and are_hotkeys_enabled:
            return False
        if not are_hotkeys_enabled and not self.view.is_dialog_open:
            logging.info("CaptureOverlay 热键被禁用，忽略按键。")
            return True
        if self.view.is_dialog_open:
            return True
        keyval = event.keyval
        state = event.state & config.GTK_MODIFIER_MASK
        def is_match(hotkey_config):
            return keyval in hotkey_config['gtk_keys'] and state == hotkey_config['gtk_mask']
        for hotkey_config, action in self._hotkey_actions:
            if is_match(hotkey_config):
                action()
                return True
        return False

    def handle_button_press(self, event):
        """处理来自视图的鼠标按下事件"""
        # 逻辑px窗口坐标
        self.resize_edge = self.view.get_cursor_edge(event.x, event.y)
        if self.resize_edge:
            self.is_dragging = True
            self.drag_start_x_rel, self.drag_start_y_rel = event.x, event.y
            self.drag_start_geometry = self.session.geometry.copy()

    def handle_button_release(self, event):
        """处理来自视图的鼠标释放事件"""
        self.is_dragging = False
        self.resize_edge = None

    def handle_motion(self, event):
        """处理来自视图的鼠标移动事件（仅在拖拽时）"""
        if not self.is_dragging or not self.resize_edge:
            return
        # 逻辑px窗口坐标
        x_rel, y_rel = event.x, event.y
        delta_x = x_rel - self.drag_start_x_rel
        delta_y = y_rel - self.drag_start_y_rel
        new_geo = self.drag_start_geometry.copy()
        scale = self.view.scale
        if self.grid_mode_controller.is_active:
            if 'top' in self.resize_edge:
                new_geo['y'] = self.drag_start_geometry['y'] + delta_y
            elif 'bottom' in self.resize_edge:
                # 逻辑px -> 缓冲区px
                delta_y_buf = delta_y * scale
                start_h_buf = self.drag_start_geometry['h'] * scale
                units_dragged = int(delta_y_buf / self.grid_mode_controller.grid_unit)
                target_h_buf = start_h_buf + (units_dragged * self.grid_mode_controller.grid_unit) # 缓冲区px
                snapped_h_logical = math.ceil(target_h_buf / scale) # 缓冲区px -> 逻辑px
                new_geo['h'] = max(math.ceil(self.grid_mode_controller.grid_unit / scale), snapped_h_logical)
            if not self.session.is_horizontally_locked:
                if 'left' in self.resize_edge:
                    new_geo['x'] = self.drag_start_geometry['x'] + delta_x
                elif 'right' in self.resize_edge:
                    new_geo['w'] = self.drag_start_geometry['w'] + delta_x
        else:
            if 'top' in self.resize_edge:
                new_geo['y'] = self.drag_start_geometry['y'] + delta_y
            elif 'bottom' in self.resize_edge:
                new_geo['h'] = self.drag_start_geometry['h'] + delta_y
            if not self.session.is_horizontally_locked:
                if 'left' in self.resize_edge:
                    new_geo['x'] = self.drag_start_geometry['x'] + delta_x
                elif 'right' in self.resize_edge:
                    new_geo['w'] = self.drag_start_geometry['w'] + delta_x
        if self.grid_mode_controller.is_active:
            min_h = math.ceil(self.grid_mode_controller.grid_unit / scale) # 缓冲区px -> 逻辑px
        else:
            min_h = 2 * config.HANDLE_HEIGHT
        min_w = 2 * config.HANDLE_HEIGHT
        if new_geo['h'] < min_h:
            if 'top' in self.resize_edge: new_geo['y'] -= (min_h - new_geo['h'])
            new_geo['h'] = min_h
        if new_geo['w'] < min_w:
            if 'left' in self.resize_edge: new_geo['x'] -= (min_w - new_geo['w'])
            new_geo['w'] = min_w
        rect = self.view.screen_rect
        win_w = rect.width if rect else self.view.get_allocated_width()
        win_h = rect.height if rect else self.view.get_allocated_height()
        x_min, y_min = 0, 0 # 逻辑px窗口坐标
        # 显示器坐标 -> 窗口坐标
        x_max = win_w - self.view.monitor_offset_x
        y_max = win_h - self.view.monitor_offset_y
        grid_unit_buf = self.grid_mode_controller.grid_unit if self.grid_mode_controller.is_active else 0 # 缓冲区px
        is_dragging_left = 'left' in self.resize_edge
        is_dragging_top = 'top' in self.resize_edge
        is_dragging_bottom = 'bottom' in self.resize_edge
        # 逻辑px
        original_h_from_logic = new_geo['h']
        original_y_from_logic = new_geo['y']
        if not self.session.is_horizontally_locked:
            if is_dragging_left:
                if new_geo['x'] < x_min: 
                    new_geo['w'] = new_geo['w'] + (new_geo['x'] - x_min)
                    new_geo['x'] = x_min
                if new_geo['x'] + new_geo['w'] > x_max: 
                    new_geo['w'] = x_max - new_geo['x']
            elif 'right' in self.resize_edge:
                if new_geo['x'] + new_geo['w'] > x_max: 
                    new_geo['w'] = x_max - new_geo['x']
            if new_geo['w'] < min_w:
                if is_dragging_left: new_geo['x'] = new_geo['x'] + new_geo['w'] - min_w
                new_geo['w'] = min_w
        if new_geo['y'] < y_min:
            if is_dragging_top: 
                new_geo['h'] = new_geo['h'] + (new_geo['y'] - y_min)
            new_geo['y'] = y_min
        if new_geo['y'] + new_geo['h'] > y_max:
            if is_dragging_top: 
                new_geo['h'] = y_max - new_geo['y']
            elif is_dragging_bottom: 
                new_geo['h'] = y_max - new_geo['y']
        if new_geo['h'] < min_h:
            if is_dragging_top: new_geo['y'] = new_geo['y'] + new_geo['h'] - min_h
            new_geo['h'] = min_h
        height_was_compressed_by_boundary = (new_geo['h'] != original_h_from_logic)
        if grid_unit_buf > 0 and height_was_compressed_by_boundary:
            start_h = self.drag_start_geometry['h']
            delta_h = new_geo['h'] - start_h
            # 逻辑px -> 缓冲区px
            start_h_buf = start_h * scale
            delta_h_buf = delta_h * scale
            units_changed = int(delta_h_buf / grid_unit_buf)
            # 逻辑px
            snapped_h_buf = start_h_buf + (units_changed * grid_unit_buf)
            snapped_h_buf = max(grid_unit_buf, snapped_h_buf)
            snapped_h_logical = math.ceil(snapped_h_buf / scale) # 缓冲区px -> 逻辑px
            if is_dragging_top:
                # 逻辑px
                y_adjustment = new_geo['h'] - snapped_h_logical
                new_geo['y'] = new_geo['y'] + y_adjustment
                if new_geo['y'] < y_min:
                    new_geo['y'] = y_min
                    new_geo['h'] = max(math.ceil(grid_unit_buf / scale), (new_geo['h'] * scale // grid_unit_buf) * grid_unit_buf)
            new_geo['h'] = snapped_h_logical
        self.session.update_geometry(new_geo)
        self.view.update_layout()
        self.view.queue_draw()

class ButtonPanel(Gtk.Box):
    # 逻辑px
    __gsignals__ = {
        'capture-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'undo-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'finalize-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'cancel-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'grid-backward-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'grid-forward-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'auto-scroll-start-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'auto-scroll-stop-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=config.BUTTON_SPACING)
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
            btn.get_style_context().add_class("force-active-style")
            setattr(self, attr_name, btn)
        if config.ENABLE_GRID_ACTION_BUTTONS:
            self.btn_grid_backward.set_visible(False)
            self.btn_grid_forward.set_visible(False)
        else:
            self.btn_grid_backward.set_no_show_all(True)
            self.btn_grid_forward.set_no_show_all(True)
            self.btn_grid_backward.hide()
            self.btn_grid_forward.hide()
        if config.ENABLE_AUTO_SCROLL_BUTTONS:
            self.btn_auto_start.set_visible(True)
            self.btn_auto_stop.set_visible(True)
        else:
            self.btn_auto_start.set_no_show_all(True)
            self.btn_auto_stop.set_no_show_all(True)
            self.btn_auto_start.hide()
            self.btn_auto_stop.hide()
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
        self.btn_grid_backward.set_visible(False)
        self.btn_grid_forward.set_visible(False)
        self.separator_grid_auto.set_visible(False)
        self.btn_auto_start.set_visible(config.ENABLE_AUTO_SCROLL_BUTTONS)
        self.btn_auto_stop.set_visible(config.ENABLE_AUTO_SCROLL_BUTTONS)
        self.separator_auto_main.set_visible(config.ENABLE_AUTO_SCROLL_BUTTONS)
        _, self._button_natural_h_normal = self.get_preferred_height()
        logging.info(f"缓存的 ButtonPanel 普通模式自然高度: {self._button_natural_h_normal} 逻辑px")
        self.btn_grid_backward.set_visible(config.ENABLE_GRID_ACTION_BUTTONS)
        self.btn_grid_forward.set_visible(config.ENABLE_GRID_ACTION_BUTTONS)
        self.separator_grid_auto.set_visible(config.ENABLE_GRID_ACTION_BUTTONS)
        self.btn_auto_start.set_visible(False)
        self.btn_auto_stop.set_visible(False)
        self.separator_auto_main.set_visible(False)
        _, self._button_natural_h_grid = self.get_preferred_height()
        logging.info(f"缓存的 ButtonPanel 整格模式自然高度: {self._button_natural_h_grid} 逻辑px")
        self.set_grid_action_buttons_visible(False)

    def set_grid_action_buttons_visible(self, visible: bool):
        is_grid_mode = visible
        grid_buttons_show = is_grid_mode and config.ENABLE_GRID_ACTION_BUTTONS
        self.btn_grid_backward.set_visible(grid_buttons_show)
        self.btn_grid_forward.set_visible(grid_buttons_show)
        auto_buttons_show = (not is_grid_mode) and config.ENABLE_AUTO_SCROLL_BUTTONS
        self.btn_auto_start.set_visible(auto_buttons_show)
        self.btn_auto_stop.set_visible(auto_buttons_show)
        self.separator_grid_auto.set_visible(grid_buttons_show)
        self.separator_auto_main.set_visible(auto_buttons_show)

    def set_undo_sensitive(self, sensitive: bool):
        self.btn_undo.set_sensitive(sensitive)

    def update_visibility_by_height(self, available_height: int, is_grid_mode: bool):
        should_show_buttons_base = config.ENABLE_BUTTONS
        if not should_show_buttons_base:
            return False
        required_h = self._button_natural_h_grid if is_grid_mode else self._button_natural_h_normal
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
        self.update_info(0, 0, 0, "自由模式")

    def update_info(self, count: int, width: int, height: int, mode_str: str):
        # width, height: 缓冲区px
        if config.SHOW_CAPTURE_COUNT:
            self.label_count.set_text(config.STR_CAPTURE_COUNT_FORMAT.format(count=count))
            self.label_count.show()
        else:
            self.label_count.hide()
        if config.SHOW_TOTAL_DIMENSIONS:
            pango_attrs = "line_height='0.8'"
            if count > 0:
                width_int = int(round(width))
                height_int = int(round(height))
                dim_markup = f"<span {pango_attrs}>{width_int}\nx\n{height_int}</span>"
            else:
                dim_markup = f"<span {pango_attrs}>宽\nx\n高</span>"
            self.label_dimensions.set_markup(dim_markup)
            self.label_dimensions.show()
        else:
            self.label_dimensions.hide()
        self.label_mode.set_text(mode_str)
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
       super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=config.BUTTON_SPACING) # 逻辑px
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
           btn.get_style_context().add_class("force-active-style")
           self.pack_start(btn, False, False, 0)
           btn.show()
       self.show()

class SidePanel(Gtk.Box):
    # 逻辑px
    __gsignals__ = {
        'toggle-grid-mode-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'toggle-preview-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'open-config-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'toggle-hotkeys-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=config.BUTTON_SPACING)
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
        _, self._info_natural_h = self.info_panel.get_preferred_height()
        logging.info(f"缓存的 InfoPanel 自然高度: {self._info_natural_h} 逻辑px")
        _, self._func_natural_h = self.function_panel.get_preferred_height()
        logging.info(f"缓存的 FunctionPanel 自然高度: {self._func_natural_h} 逻辑px")

    def update_visibility_by_height(self, available_height: int, is_grid_mode: bool):
        should_show_info_base = config.ENABLE_SIDE_PANEL and (config.SHOW_CAPTURE_COUNT or config.SHOW_TOTAL_DIMENSIONS or config.SHOW_CURRENT_MODE)
        should_show_func_base = config.ENABLE_SIDE_PANEL
        if not should_show_info_base:
            self.info_panel.hide()
        required_h_for_info = self._info_natural_h if should_show_info_base else 0
        required_h_for_func = self._func_natural_h if should_show_func_base else 0
        threshold_for_info_only = required_h_for_info
        threshold_for_both = required_h_for_info + required_h_for_func
        can_show_info_panel = available_height >= threshold_for_info_only
        can_show_func_panel = available_height >= threshold_for_both
        if should_show_info_base and can_show_info_panel:
            self.info_panel.show()
        else:
            self.info_panel.hide()
        if should_show_func_base and can_show_func_panel:
            self.function_panel.show()
        else:
            self.function_panel.hide()

class InstructionPanel(Gtk.Box):
    # 逻辑px
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.get_style_context().add_class("instruction-panel")
        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(4)
        self.pack_start(grid, True, True, 0)
        instructions = [
            (config.str_toggle_instruction_panel.upper(), "显隐此面板"),
            (config.str_capture.upper(), "截图"),
            (config.str_finalize.upper(), "完成"),
            (config.str_undo.upper(), "撤销"),
            (config.str_cancel.upper(), "取消"),
            (config.str_auto_scroll_start.upper(), "开始自动模式"),
            (config.str_auto_scroll_stop.upper(), "停止自动模式"),
            (config.str_toggle_grid_mode.upper(), "切换整格模式"),
            (config.str_grid_forward.upper(), "整格前进"),
            (config.str_grid_backward.upper(), "整格后退"),
            (config.str_configure_scroll_unit.upper(), "配置滚动单位"),
            (config.str_toggle_preview.upper(), "显隐预览面板"),
            (config.str_open_config_editor.upper(), "显隐配置面板"),
            (config.str_toggle_hotkeys_enabled.upper(), "开关热键"),
            (config.str_preview_zoom_out.upper(), "放大预览面板"),
            (config.str_preview_zoom_in.upper(), "缩小预览面板"),
        ]
        for i, (key, desc) in enumerate(instructions):
            lbl_key = Gtk.Label(label=key)
            lbl_key.set_halign(Gtk.Align.START)
            lbl_key.get_style_context().add_class("key-label")
            lbl_desc = Gtk.Label(label=desc)
            lbl_desc.set_halign(Gtk.Align.START)
            lbl_desc.get_style_context().add_class("desc-label")
            grid.attach(lbl_key, 0, i, 1, 1)
            grid.attach(lbl_desc, 1, i, 1, 1)
        self.show_all()

class QueueHandler(logging.Handler):
    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        self.log_queue.put(record)

class StreamToLoggerRedirector:
    def __init__(self, logger, level):
        self.logger = logger
        self.level = level
        self.linebuf = ''

    def write(self, buf):
        for line in buf.rstrip().splitlines():
            self.logger.log(self.level, line.rstrip())

    def flush(self):
        pass

class SimulatedWindow(Gtk.EventBox):
    """模拟窗口行为的基础组件，提供标题栏拖动、边缘调整大小、最大化和关闭功能"""
    # 逻辑px
    def __init__(self, parent_overlay, title="Window", css_class="simulated-window", resizable=True):
        super().__init__()
        self.parent_overlay = parent_overlay
        self.is_maximized = False
        self.resizable = resizable
        # 窗口坐标
        self.restore_geometry = None
        self.RESIZE_BORDER = 6
        self._dragging_panel = False
        self._resizing_panel = False
        self._resize_edge = None
        self._drag_anchor_mouse = None
        self._drag_anchor_panel_pos = None
        self._resize_start_rect = None
        self._resize_limit_w = 100
        self._resize_limit_h = 50
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

    def add_content(self, widget, expand=True, fill=True, padding=0):
        self.main_vbox.pack_start(widget, expand, fill, padding)

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
        curr_x = self.parent_overlay.fixed_container.child_get_property(self, "x")
        curr_y = self.parent_overlay.fixed_container.child_get_property(self, "y")
        alloc = self.get_allocation()
        self.restore_geometry = (curr_x, curr_y, alloc.width, alloc.height)
        rect = self.parent_overlay.screen_rect
        screen_w = rect.width if rect else self.parent_overlay.get_allocated_width()
        screen_h = rect.height if rect else self.parent_overlay.get_allocated_height()
        # 显示器坐标 -> 窗口坐标
        valid_screen_w = screen_w - self.parent_overlay.monitor_offset_x
        valid_screen_h = screen_h - self.parent_overlay.monitor_offset_y
        self.set_size_request(valid_screen_w, valid_screen_h)
        self.parent_overlay.fixed_container.move(self, 0, 0)
        self.is_maximized = True
        image = Gtk.Image.new_from_icon_name("window-restore-symbolic", Gtk.IconSize.MENU)
        self.maximize_btn.set_image(image)
        self.maximize_btn.set_tooltip_text("还原")
        self.parent_overlay._update_input_shape()

    def _restore_panel(self):
        if not self.restore_geometry:
            return
        x, y, w, h = self.restore_geometry
        self.set_size_request(w, h)
        self.parent_overlay.fixed_container.move(self, x, y)
        self.is_maximized = False
        image = Gtk.Image.new_from_icon_name("window-maximize-symbolic", Gtk.IconSize.MENU)
        self.maximize_btn.set_image(image)
        self.maximize_btn.set_tooltip_text("最大化")
        self.parent_overlay._update_input_shape()

    def _get_panel_edge(self, x, y):
        if not self.resizable:
            return None
        w = self.get_allocated_width()
        h = self.get_allocated_height()
        border = self.RESIZE_BORDER
        on_top = y < border
        on_bottom = y > h - border
        on_left = x < border
        on_right = x > w - border
        edge = ''
        if on_top: edge = 'top'
        elif on_bottom: edge = 'bottom'
        if on_left: edge += '-left' if edge else 'left'
        elif on_right: edge += '-right' if edge else 'right'
        return edge if edge else None

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
            edge = self._get_panel_edge(event.x, event.y)
            win_x, win_y = widget.translate_coordinates(self.parent_overlay, event.x, event.y)
            self._drag_anchor_mouse = (win_x, win_y)
            curr_x = self.parent_overlay.fixed_container.child_get_property(self, "x")
            curr_y = self.parent_overlay.fixed_container.child_get_property(self, "y")
            if edge:
                self._resizing_panel = True
                self._resize_edge = edge
                alloc = self.get_allocation()
                self._resize_start_rect = (curr_x, curr_y, alloc.width, alloc.height)
                min_req, _ = self.main_vbox.get_preferred_size()
                self._resize_limit_w = min_req.width + 2 * self.RESIZE_BORDER
                self._resize_limit_h = min_req.height + 2 * self.RESIZE_BORDER
                self.user_has_moved = True
                return True
            elif self._is_on_header(event.x, event.y):
                self._dragging_panel = True
                self._drag_anchor_panel_pos = (curr_x, curr_y)
                self.get_window().set_cursor(self.parent_overlay.cursors.get('grabbing'))
                self.user_has_moved = True
                return True
        return False

    def _on_panel_release(self, widget, event):
        if event.button == 1:
            if self._dragging_panel or self._resizing_panel:
                self._dragging_panel = False
                self._resizing_panel = False
                self._resize_edge = None
                self.get_window().set_cursor(None)
                self.parent_overlay._update_input_shape()
                self._on_panel_motion(widget, event)
                return True
        return False

    def _on_panel_motion(self, widget, event):
        if self._dragging_panel:
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
                self.parent_overlay.fixed_container.move(self, new_panel_x, new_panel_y)
                return True
            total_dx = curr_win_x - self._drag_anchor_mouse[0]
            total_dy = curr_win_y - self._drag_anchor_mouse[1]
            new_x = self._drag_anchor_panel_pos[0] + total_dx
            new_y = max(0, self._drag_anchor_panel_pos[1] + total_dy)
            self.parent_overlay.fixed_container.move(self, new_x, new_y)
            return True
        if self._resizing_panel:
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
            self.set_size_request(int(current_new_w), int(current_new_h))
            if current_new_x != start_x or current_new_y != start_y:
                self.parent_overlay.fixed_container.move(self, int(current_new_x), int(current_new_y))
            return True
        edge = self._get_panel_edge(event.x, event.y)
        if edge:
            cursor_name = {
                'top': 'n-resize', 'bottom': 's-resize',
                'left': 'w-resize', 'right': 'e-resize',
                'top-left': 'nw-resize', 'top-right': 'ne-resize',
                'bottom-left': 'sw-resize', 'bottom-right': 'se-resize'
            }.get(edge, 'default')
            self.get_window().set_cursor(self.parent_overlay.cursors.get(cursor_name))
        elif self._is_on_header(event.x, event.y):
            if self._is_over_header_buttons(event.x, event.y):
                self.get_window().set_cursor(None)
            else:
                self.get_window().set_cursor(self.parent_overlay.cursors.get('grab'))
        else:
            self.get_window().set_cursor(None)

class CustomColorButton(Gtk.Button):
    def __init__(self, color_str="0,0,0,1"):
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
        self.add(self.drawing_area)
        self.parse_and_set(color_str)

    def parse_and_set(self, color_str):
        try:
            parts = [float(c.strip()) for c in color_str.split(',')]
            if len(parts) == 4:
                r, g, b, a = parts
                self.rgba = Gdk.RGBA(r, g, b, a)
            self.drawing_area.queue_draw()
        except:
            pass

    def set_rgba(self, rgba):
        self.rgba = rgba
        self.drawing_area.queue_draw()

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

class EmbeddedFileChooser(SimulatedWindow):
    def __init__(self, parent_overlay):
        super().__init__(parent_overlay, title="选择目录", css_class="simulated-window", resizable=True)
        self.target_entry = None
        self.chooser = Gtk.FileChooserWidget(action=Gtk.FileChooserAction.SELECT_FOLDER)
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
        self.set_size_request(nat_size.width, 800)
        self.hide()

    def open_for(self, entry_widget):
        self.target_entry = entry_widget
        current_path = entry_widget.get_text()
        if current_path and os.path.isdir(current_path):
            self.chooser.set_current_folder(current_path)
        self.show_all()
        self.get_window().raise_()

    def _on_select_clicked(self, widget):
        filename = self.chooser.get_filename()
        if filename and self.target_entry:
            self.target_entry.set_text(filename)
        self.hide()

class EmbeddedColorChooser(SimulatedWindow):
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
        self.show_all()
        self.get_window().raise_()

    def _on_select_clicked(self, widget):
        rgba = self.chooser.get_rgba()
        if self.target_button:
            self.target_button.set_rgba(rgba)
        _, req = self.get_preferred_size()
        panel_w, panel_h = req.width, req.height
        self.hide()

class ConfigPanel(SimulatedWindow):
    """配置面板，提供所有设置项的图形化编辑界面"""
    # 逻辑px
    def __init__(self, config_obj, parent_overlay):
        super().__init__(parent_overlay, title="拼长图配置", css_class="simulated-window", resizable=True)
        self.config = config_obj
        self.show_advanced = False
        self.input_has_focus = False
        self.managed_settings = [
            ('Output', 'save_directory'), ('Output', 'save_format'),
            ('Output', 'jpeg_quality'), ('Output', 'filename_template'),
            ('Output', 'filename_timestamp_format'),
            ('Interface.Components', 'enable_buttons'),
            ('Interface.Components', 'enable_grid_action_buttons'), ('Interface.Components', 'enable_auto_scroll_buttons'),
            ('Interface.Components', 'enable_side_panel'),
            ('Interface.Components', 'show_preview_on_start'),
            ('Interface.Components', 'show_capture_count'), ('Interface.Components', 'show_total_dimensions'), ('Interface.Components', 'show_current_mode'),
            ('Interface.Components', 'show_instruction_panel_on_start'),
            ('Behavior', 'enable_free_scroll_matching'), ('Behavior', 'capture_with_cursor'), ('Behavior', 'scroll_method'), ('Behavior', 'reuse_invisible_cursor'),
            ('Behavior', 'forward_action'), ('Behavior', 'backward_action'),
            ('Interface.Theme', 'border_color'), ('Interface.Theme', 'matching_indicator_color'),
            ('Interface.Layout', 'border_width'),
            ('Interface.Layout', 'handle_height'), ('Interface.Layout', 'button_panel_width'),
            ('Interface.Layout', 'side_panel_width'), ('Interface.Layout', 'button_spacing'),
            ('Interface.Layout', 'processing_dialog_width'), ('Interface.Layout', 'processing_dialog_height'),
            ('Interface.Layout', 'processing_dialog_spacing'), ('Interface.Layout', 'processing_dialog_border_width'),
            ('Interface.Theme', 'processing_dialog_css'),
            ('Interface.Theme', 'info_panel_css'),
            ('Interface.Theme', 'simulated_window_css'),
            ('Interface.Theme', 'notification_css'),
            ('Interface.Theme', 'dialog_css'),
            ('Interface.Theme', 'mask_css'),
            ('Interface.Theme', 'instruction_panel_css'),
            ('System', 'copy_to_clipboard_on_finish'), ('System', 'notification_click_action'),
            ('System', 'large_image_opener'), ('System', 'sound_theme'),
            ('System', 'capture_sound'), ('System', 'undo_sound'), ('System', 'finalize_sound'),
            ('Performance', 'grid_matching_max_overlap'), ('Performance', 'free_scroll_matching_max_overlap'),
            ('Performance', 'auto_scroll_ticks_per_step'), ('Performance', 'max_scroll_per_tick'), ('Performance', 'min_scroll_per_tick'),
            ('Performance', 'max_viewer_dimension'), ('Performance', 'preview_drag_sensitivity'),
            ('System', 'log_file'), ('System', 'temp_directory_base'),
        ]
        self.sound_data = self._discover_sound_themes()
        self.capturing_hotkey_button = None
        self.connect("destroy", self._on_destroy)
        self.is_destroyed = False
        self.log_queue = log_queue
        self.log_text_buffer = None
        self.log_timer_id = None
        self.all_log_records = []
        self.log_tags = {}
        self.filter_checkboxes = {}
        self.log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        self._sub_panels_state = {}
        self._setup_ui()
        self._create_log_tags()
        self._load_config_values()
        self.file_chooser_panel = EmbeddedFileChooser(parent_overlay)
        self.color_chooser_panel = EmbeddedColorChooser(parent_overlay)
        self.show_all()
        _, nat_size = self.get_preferred_size()
        self.set_size_request(nat_size.width, 800)
        self.hide()
        self._setup_default_parser()
        self._update_advanced_visibility()
        self.log_timer_id = GLib.timeout_add(150, self._check_log_queue)

    def show(self):
        super().show()
        print(self._sub_panels_state)
        if self._sub_panels_state.get('file_chooser', False):
            if hasattr(self, 'file_chooser_panel') and self.file_chooser_panel:
                self.file_chooser_panel.show()
        if self._sub_panels_state.get('color_chooser', False):
            if hasattr(self, 'color_chooser_panel') and self.color_chooser_panel:
                self.color_chooser_panel.show()

    def hide(self):
        if hasattr(self, 'file_chooser_panel') and self.file_chooser_panel:
            self._sub_panels_state['file_chooser'] = self.file_chooser_panel.get_visible()
            self.file_chooser_panel.hide()
        if hasattr(self, 'color_chooser_panel') and self.color_chooser_panel:
            self._sub_panels_state['color_chooser'] = self.color_chooser_panel.get_visible()
            self.color_chooser_panel.hide()
        super().hide()

    def on_close_clicked(self, btn):
        """窗口关闭时保存所有配置"""
        if self.capturing_hotkey_button:
            key = self.capturing_hotkey_button.get_name()
            prev_text = self.config.parser.get('Hotkeys', key, fallback="")
            self.capturing_hotkey_button.set_label(prev_text)
            self.capturing_hotkey_button = None
        self._save_all_configs()
        global hotkey_listener
        if hotkey_listener and are_hotkeys_enabled:
            hotkey_listener.set_normal_keys_grabbed(True)
            logging.info("配置面板隐藏，全局热键已恢复")
        if self.is_maximized:
            self._restore_panel()
        self.user_has_moved = False
        self.hide()
        self._sub_panels_state = {}

    def _on_destroy(self, widget):
        """面板销毁时的清理操作"""
        if self.is_destroyed:
            return
        self.is_destroyed = True
        if self.log_timer_id:
            GLib.source_remove(self.log_timer_id)
            self.log_timer_id = None
            logging.info("配置面板的日志更新定时器已成功移除")
        self._save_all_configs()

    def ensure_z_order(self):
        if self.get_window():
            self.get_window().raise_()
        if self.file_chooser_panel and self.file_chooser_panel.get_visible():
            if self.file_chooser_panel.get_window():
                self.file_chooser_panel.get_window().raise_()
        if self.color_chooser_panel and self.color_chooser_panel.get_visible():
            if self.color_chooser_panel.get_window():
                self.color_chooser_panel.get_window().raise_()

    def _on_input_focus_in(self, widget, event):
        self.input_has_focus = True
        logging.info(f"输入控件 {type(widget).__name__} 获得焦点，全局热键暂停")
        global hotkey_listener
        if hotkey_listener:
            hotkey_listener.set_normal_keys_grabbed(False)
        return False

    def _on_input_focus_out(self, widget, event):
        self.input_has_focus = False
        logging.info(f"输入控件 {type(widget).__name__} 失去焦点，全局热键恢复")
        global hotkey_listener
        if hotkey_listener and not self.capturing_hotkey_button and are_hotkeys_enabled:
            hotkey_listener.set_normal_keys_grabbed(True)
        return False

    def _connect_focus_handlers(self, widget):
        widget.connect("focus-in-event", self._on_input_focus_in)
        widget.connect("focus-out-event", self._on_input_focus_out)

    def _check_log_queue(self):
        """定时器回调，检查队列中是否有新日志"""
        while not self.log_queue.empty():
            try:
                record = self.log_queue.get_nowait()
                self._process_log_record(record) 
            except queue.Empty:
                break
        return True

    def _process_log_record(self, record):
        self.all_log_records.append(record)
        if self.filter_checkboxes.get(record.levelname) and self.filter_checkboxes[record.levelname].get_active():
            self._insert_record_into_buffer(record)

    def _insert_record_into_buffer(self, record):
        if not self.log_text_buffer: return
        message = self.log_formatter.format(record)
        tag = self.log_tags.get(record.levelname)
        end_iter = self.log_text_buffer.get_end_iter()
        if tag:
            self.log_text_buffer.insert_with_tags(end_iter, message + '\n', tag)
        else:
            self.log_text_buffer.insert(end_iter, message + '\n')
        if self.log_autoscroll_checkbutton.get_active():
            GLib.idle_add(self._scroll_to_end_of_log)

    def _on_filter_changed(self, widget):
        self._redisplay_logs()

    def _redisplay_logs(self):
        if not self.log_text_buffer: return
        self.log_text_buffer.set_text("")
        active_levels = {level for level, cb in self.filter_checkboxes.items() if cb.get_active()}
        for record in self.all_log_records:
            if record.levelname in active_levels:
                self._insert_record_into_buffer(record)

    def _scroll_to_end_of_log(self):
        """将日志视图滚动到末尾"""
        if self.is_destroyed:
            logging.info("_scroll_to_end_of_log: 窗口已销毁，放弃滚动")
            return False
        if self.log_text_buffer:
            end_iter = self.log_text_buffer.get_end_iter()
            self.log_textview.scroll_to_iter(end_iter, 0.0, True, 0.0, 1.0)
        return False

    def _on_clear_log_clicked(self, widget):
        """清空日志按钮的回调"""
        self.all_log_records.clear()
        if self.log_text_buffer:
            self.log_text_buffer.set_text("")

    def _create_log_tags(self):
        """为不同的日志级别创建并配置 TextTag"""
        if not self.log_text_buffer:
            return
        tag_table = self.log_text_buffer.get_tag_table()
        self.log_tags['INFO'] = Gtk.TextTag(name="info")
        self.log_tags['INFO'].set_property("foreground", "#2b2b2b")
        tag_table.add(self.log_tags['INFO'])
        self.log_tags['WARNING'] = Gtk.TextTag(name="warning")
        self.log_tags['WARNING'].set_property("foreground", "#FF8C00")
        tag_table.add(self.log_tags['WARNING'])
        self.log_tags['ERROR'] = Gtk.TextTag(name="error")
        self.log_tags['ERROR'].set_property("foreground", "#DC143C")
        tag_table.add(self.log_tags['ERROR'])
        self.log_tags['DEBUG'] = Gtk.TextTag(name="debug")
        self.log_tags['DEBUG'].set_property("foreground", "#00BFFF")
        tag_table.add(self.log_tags['DEBUG'])

    def _on_copy_log_clicked(self, button):
        """复制日志按钮的回调"""
        if not self.log_text_buffer:
            return
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        start_iter, end_iter = self.log_text_buffer.get_bounds()
        full_log_text = self.log_text_buffer.get_text(start_iter, end_iter, False)
        clipboard.set_text(full_log_text, -1)
        logging.info("日志内容已复制到剪贴板")
        original_label = button.get_label()
        button.set_sensitive(False)
        button.set_label("已复制!")
        def restore_button_state():
            button.set_label(original_label)
            button.set_sensitive(True)
            return False
        GLib.timeout_add(1500, restore_button_state)

    def _setup_default_parser(self):
        self.default_parser = configparser.ConfigParser(interpolation=None)
        default_string = Config.get_default_config_string()
        self.default_parser.read_string(default_string)

    def _key_event_to_string(self, event):
        mods = []
        state = event.state
        keyval = event.keyval
        if state & Gdk.ModifierType.CONTROL_MASK:
            mods.append('<ctrl>')
        if state & Gdk.ModifierType.MOD1_MASK:
            mods.append('<alt>')
        if state & Gdk.ModifierType.SHIFT_MASK:
            mods.append('<shift>')
        if state & Gdk.ModifierType.SUPER_MASK:
            mods.append('<super>')
        key_name_lower = Gdk.keyval_name(keyval).lower()
        is_modifier_only_release = key_name_lower in (
            'shift_l', 'shift_r', 'control_l', 'control_r', 'alt_l', 'alt_r', 'super_l', 'super_r'
        )
        if is_modifier_only_release:
             if 'shift' in key_name_lower and '<ctrl>' not in mods and '<alt>' not in mods and '<super>' not in mods:
                 return '<shift>'
             if 'control' in key_name_lower and '<shift>' not in mods and '<alt>' not in mods and '<super>' not in mods:
                 return '<ctrl>'
             if 'alt' in key_name_lower and '<shift>' not in mods and '<ctrl>' not in mods and '<super>' not in mods:
                 return '<alt>'
        effective_keyval = keyval
        if (state & Gdk.ModifierType.SHIFT_MASK) and not is_modifier_only_release:
            keymap = Gdk.Keymap.get_default()
            success_val, entries = keymap.get_entries_for_keyval(keyval)
            if success_val and entries:
                keycode = entries[0].keycode
                success, key_entries, keyvals = keymap.get_entries_for_keycode(keycode)
                if success and key_entries and keyvals:
                    for i, entry in enumerate(key_entries):
                        if entry.level == 0 and entry.group == 0:
                            effective_keyval = keyvals[i]
                            break
        rev_map = {v: k for k, v in self.config._key_map_gtk_special.items()}
        main_key_str = ""
        if effective_keyval in rev_map:
            main_key_str = rev_map[effective_keyval]
        else:
            codepoint = Gdk.keyval_to_unicode(effective_keyval)
            if codepoint != 0:
                char = chr(codepoint)
                if char.isprintable():
                    main_key_str = char.lower()
            if not main_key_str:
                effective_name = Gdk.keyval_name(effective_keyval)
                if effective_name:
                    main_key_str = effective_name.lower()
                elif not is_modifier_only_release:
                     main_key_str = key_name_lower
        if not main_key_str:
            return "无效组合"
        if not mods:
            return main_key_str
        else:
            return '+'.join(mods) + '+' + main_key_str

    def _on_hotkey_button_clicked(self, button):
        if self.capturing_hotkey_button and self.capturing_hotkey_button != button:
            key_for_prev_button = self.capturing_hotkey_button.get_name()
            prev_text = self.config.parser.get('Hotkeys', key_for_prev_button)
            self.capturing_hotkey_button.set_label(prev_text)
        button.original_label = button.get_label()
        self.capturing_hotkey_button = button
        button.set_label("请按下快捷键…")
        global hotkey_listener
        if hotkey_listener:
            hotkey_listener.set_normal_keys_grabbed(False)
            logging.info("开始捕获快捷键，全局热键暂停")

    def handle_key_press(self, widget, event):
        if self.capturing_hotkey_button:
            return True 
        return False

    def handle_key_release(self, widget, event):
        if not self.capturing_hotkey_button:
            return False
        hotkey_str = self._key_event_to_string(event)
        current_key = self.capturing_hotkey_button.get_name()
        original_label = self.capturing_hotkey_button.original_label
        global hotkey_listener
        if hotkey_str == "无效组合":
            logging.warning(f"捕获到无效的按键释放 {event.keyval} (state={event.state})，取消本次捕获")
            self.capturing_hotkey_button.set_label(original_label)
            self.capturing_hotkey_button = None
            if hotkey_listener and not self.input_has_focus and are_hotkeys_enabled:
                hotkey_listener.set_normal_keys_grabbed(True)
                logging.info("无效捕获，全局热键恢复")
            return True
        dialog_scope = ['dialog_confirm', 'dialog_cancel']
        is_dialog_key = current_key in dialog_scope
        conflict_found = False
        conflicting_key = None
        for key, button in self.hotkey_buttons.items():
            if key == current_key:
                continue
            is_other_key_dialog = key in dialog_scope
            if is_dialog_key != is_other_key_dialog:
                continue
            if button.get_label() == hotkey_str and hotkey_str:
                conflict_found = True
                conflicting_key_desc = next(c[1] for c in self.hotkey_configs if c[0] == key)
                break
        if conflict_found:
            message = f"快捷键 '{hotkey_str}' 已被分配给 '{conflicting_key_desc}'\n请设置一个不同的快捷键"
            send_desktop_notification(
                title="快捷键冲突",
                message=message,
                sound_name="dialog-error",
                level="warning"
            )
            self.capturing_hotkey_button.set_label(original_label)
        else:
            self.capturing_hotkey_button.set_label(hotkey_str)
        self.capturing_hotkey_button = None
        if hotkey_listener and not self.input_has_focus and are_hotkeys_enabled:
            hotkey_listener.set_normal_keys_grabbed(True)
            logging.info("快捷键捕获结束，全局热键恢复")
        return True

    def _setup_ui(self):
        """设置主界面布局"""
        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_content(main_vbox)
        # 创建水平分割的主内容区
        main_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        main_vbox.pack_start(main_hbox, True, True, 0)
        # 左侧边栏
        sidebar_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main_hbox.pack_start(sidebar_container, False, False, 0)
        sidebar_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        sidebar_header.set_margin_start(12)
        sidebar_header.set_margin_end(12)
        sidebar_header.set_margin_top(8)
        sidebar_header.set_margin_bottom(8)
        icon = Gtk.Image.new_from_icon_name("preferences-system", Gtk.IconSize.MENU)
        title_label = Gtk.Label(label="配置选项")
        title_label.set_markup("<b>配置选项</b>")
        sidebar_header.pack_start(icon, False, False, 0)
        sidebar_header.pack_start(title_label, False, False, 0)
        sidebar_container.pack_start(sidebar_header, False, False, 0)
        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sidebar_container.pack_start(separator, False, False, 0)
        self.sidebar = Gtk.StackSidebar()
        self.sidebar.set_size_request(220, -1)
        self.sidebar.set_margin_start(6)
        self.sidebar.set_margin_end(6)
        sidebar_container.pack_start(self.sidebar, True, True, 0)
        # 右侧堆栈容器
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_transition_duration(200)
        self.sidebar.set_stack(self.stack)
        main_hbox.pack_start(self.stack, True, True, 0)
        # 创建各个配置页面
        self._create_output_page()
        self._create_hotkeys_page() 
        self._create_interface_page()
        self._create_theme_layout_page()
        self._create_system_performance_page()
        self._create_grid_calibration_page()
        self._create_interface_strings_page()
        self._create_log_viewer_page()
        # 底部全局操作区
        self._create_bottom_panel(main_vbox)

    def _create_log_viewer_page(self):
        """创建日志查看器页面"""
        page_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        page_vbox.set_margin_start(10)
        page_vbox.set_margin_end(10)
        page_vbox.set_margin_top(10)
        page_vbox.set_margin_bottom(10)
        # 顶部工具栏
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        page_vbox.pack_start(toolbar, False, False, 0)
        clear_button = Gtk.Button(label="清空日志")
        clear_button.connect("clicked", self._on_clear_log_clicked)
        toolbar.pack_start(clear_button, False, False, 0)
        copy_button = Gtk.Button(label="复制日志")
        copy_button.connect("clicked", self._on_copy_log_clicked)
        toolbar.pack_start(copy_button, False, False, 0)
        filter_label = Gtk.Label(label=" | 过滤:")
        toolbar.pack_start(filter_label, False, False, 10)
        log_levels_to_filter = ["INFO", "WARNING", "ERROR"]
        for level in log_levels_to_filter:
            checkbox = Gtk.CheckButton(label=level)
            checkbox.set_active(True)
            checkbox.connect("toggled", self._on_filter_changed)
            toolbar.pack_start(checkbox, False, False, 0)
            self.filter_checkboxes[level] = checkbox
        self.log_autoscroll_checkbutton = Gtk.CheckButton(label="自动滚动到底部")
        self.log_autoscroll_checkbutton.set_active(True)
        toolbar.pack_start(self.log_autoscroll_checkbutton, False, False, 10)
        # 日志显示区域
        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_hexpand(True)
        scrolled_window.set_vexpand(True)
        scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled_window.set_margin_top(5)
        scrolled_window.set_margin_bottom(5)
        page_vbox.pack_start(scrolled_window, True, True, 0)
        self.log_textview = Gtk.TextView()
        self.log_textview.set_editable(False)
        self.log_textview.set_cursor_visible(False)
        self.log_textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.log_text_buffer = self.log_textview.get_buffer()
        scrolled_window.add(self.log_textview)
        self.stack.add_titled(page_vbox, "log_viewer", "日志查看")

    def _create_bottom_panel(self, parent):
        """创建底部的全局操作面板"""
        bottom_frame = Gtk.Frame()
        bottom_frame.set_shadow_type(Gtk.ShadowType.IN)
        parent.pack_start(bottom_frame, False, False, 0)
        bottom_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        bottom_hbox.set_margin_start(10)
        bottom_hbox.set_margin_end(10)
        bottom_hbox.set_margin_top(8)
        bottom_hbox.set_margin_bottom(8)
        bottom_frame.add(bottom_hbox)
        # 高级设置开关
        advanced_label = Gtk.Label(label="显示高级设置")
        self.advanced_switch = Gtk.Switch()
        self.advanced_switch.connect("notify::active", self._on_advanced_toggle)
        bottom_hbox.pack_start(advanced_label, False, False, 0)
        bottom_hbox.pack_start(self.advanced_switch, False, False, 0)
        help_label = Gtk.Label(label="更改会在退出时自动保存，并在下次启动时生效")
        help_label.set_halign(Gtk.Align.END)
        bottom_hbox.pack_end(help_label, False, False, 0)

    def _on_browse_button_clicked(self, widget):
        if self.parent_overlay.screen_rect:
            screen_w, screen_h = self.parent_overlay.screen_rect.width, self.parent_overlay.screen_rect.height
        else:
            screen_w, screen_h = self.parent_overlay.get_allocated_width(), self.parent_overlay.get_allocated_height()
        valid_w = screen_w - self.parent_overlay.monitor_offset_x
        valid_h = screen_h - self.parent_overlay.monitor_offset_y
        req_w, req_h = self.file_chooser_panel.get_size_request()
        x = (valid_w - req_w) // 2
        y = (valid_h - req_h) // 2
        self.parent_overlay.fixed_container.move(self.file_chooser_panel, max(0, x), max(0, y))
        self.file_chooser_panel.open_for(self.save_dir_entry)

    def _show_embedded_color_chooser(self, target_button):
        global_mouse_pos = self.parent_overlay.controller.scroll_manager._get_pointer_position()
        offset_x, offset_y = self.parent_overlay.monitor_offset_x, self.parent_overlay.monitor_offset_y
        if self.parent_overlay.screen_rect:
            screen_w, screen_h = self.parent_overlay.screen_rect.width, self.parent_overlay.screen_rect.height
            offset_x += self.parent_overlay.screen_rect.x
            offset_y += self.parent_overlay.screen_rect.y
        else:
            screen_w, screen_h = self.parent_overlay.get_allocated_width(), self.parent_overlay.get_allocated_height()
        # 显示器坐标 -> 窗口坐标
        valid_w = screen_w - self.parent_overlay.monitor_offset_x
        valid_h = screen_h - self.parent_overlay.monitor_offset_y
        # 全局坐标 -> 窗口坐标
        mouse_win_x = global_mouse_pos[0] - offset_x
        mouse_win_y = global_mouse_pos[1] - offset_y
        self.color_chooser_panel.open_for(target_button)
        self.color_chooser_panel.show_all()
        _, nat_size = self.color_chooser_panel.get_preferred_size()
        panel_w, panel_h = nat_size.width, nat_size.height
        target_x = mouse_win_x + 10
        target_y = mouse_win_y + 10
        if target_x + panel_w > valid_w:
            target_x = mouse_win_x - panel_w - 10
        if target_y + panel_h > valid_h:
            target_y = mouse_win_y - panel_h - 10
        target_x = max(0, target_x)
        target_y = max(0, target_y)
        self.parent_overlay.fixed_container.move(self.color_chooser_panel, int(target_x), int(target_y))

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

    def _create_output_page(self):
        """创建输出设置页面"""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        vbox.set_margin_start(20)
        vbox.set_margin_end(20)
        vbox.set_margin_top(20)
        vbox.set_margin_bottom(20)
        scrolled.add(vbox)
        output_page_settings = [
            ('Output', 'save_directory'), ('Output', 'save_format'),
            ('Output', 'jpeg_quality'), ('Output', 'filename_template'),
            ('Output', 'filename_timestamp_format')
        ]
        restore_button = Gtk.Button(label="恢复本页默认设置")
        restore_button.set_halign(Gtk.Align.END)
        restore_button.set_margin_top(10)
        restore_button.connect("clicked", self._on_restore_defaults_clicked, output_page_settings)
        vbox.pack_end(restore_button, False, False, 0)
        # 保存位置
        frame1 = Gtk.Frame(label="文件输出")
        vbox.pack_start(frame1, False, False, 0)
        grid1 = Gtk.Grid()
        grid1.set_margin_start(15)
        grid1.set_margin_end(15)
        grid1.set_margin_top(10)
        grid1.set_margin_bottom(15)
        grid1.set_row_spacing(10)
        grid1.set_column_spacing(10)
        frame1.add(grid1)
        # 保存目录
        label = Gtk.Label(label="保存到目录:", xalign=0)
        label.set_tooltip_markup("指定拼接后图片的默认保存目录")
        grid1.attach(label, 0, 0, 1, 1)
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.save_dir_entry = Gtk.Entry()
        self._connect_focus_handlers(self.save_dir_entry)
        self.save_dir_entry.set_editable(False)
        self.save_dir_entry.set_hexpand(True)
        hbox.pack_start(self.save_dir_entry, True, True, 0)
        # 创建“浏览”按钮
        browse_button = Gtk.Button(label="浏览…")
        browse_button.connect("clicked", self._on_browse_button_clicked)
        hbox.pack_start(browse_button, False, False, 0)
        grid1.attach(hbox, 1, 0, 1, 1)
        # 文件格式
        label = Gtk.Label(label="文件类型:", xalign=0)
        label.set_tooltip_markup("选择图片的保存格式\n<b>PNG</b>: 无损压缩，文件较大\n<b>JPEG</b>: 有损压缩，文件较小")
        self.format_combo = Gtk.ComboBoxText()
        self.format_combo.set_tooltip_markup(label.get_tooltip_markup())
        self.format_combo.connect("scroll-event", lambda widget, event: True)
        self.format_combo.append("PNG", "PNG")
        self.format_combo.append("JPEG", "JPEG")
        self.format_combo.connect("changed", self._on_format_changed)
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
        self._connect_focus_handlers(self.jpeg_quality_spin)
        self.jpeg_quality_spin.connect("scroll-event", lambda widget, event: True)
        self.jpeg_quality_spin.set_halign(Gtk.Align.START)
        self.jpeg_quality_spin.set_range(1, 100)
        self.jpeg_quality_spin.set_increments(1, 10)
        grid1.attach(self.jpeg_label, 0, 2, 1, 1)
        grid1.attach(self.jpeg_quality_spin, 1, 2, 1, 1)
        # 高级设置框架
        self.output_advanced_frame = Gtk.Frame(label="高级选项")
        vbox.pack_start(self.output_advanced_frame, False, False, 0)
        grid2 = Gtk.Grid()
        grid2.set_margin_start(15)
        grid2.set_margin_end(15)
        grid2.set_margin_top(10)
        grid2.set_margin_bottom(15)
        grid2.set_row_spacing(10)
        grid2.set_column_spacing(10)
        self.output_advanced_frame.add(grid2)
        # 文件名格式
        label = Gtk.Label(label="文件名格式:", xalign=0)
        label.set_tooltip_markup("定义保存文件的名称模板\n变量 <b>{timestamp}</b> 会被替换为下方格式定义的时间戳")
        self.filename_entry = Gtk.Entry()
        self.filename_entry.set_tooltip_markup(label.get_tooltip_markup())
        self._connect_focus_handlers(self.filename_entry)
        help_label = Gtk.Label(label="可用变量: {timestamp}")
        help_label.set_markup("<small>可用变量: {timestamp}</small>")
        grid2.attach(label, 0, 0, 1, 1)
        grid2.attach(self.filename_entry, 1, 0, 1, 1)
        grid2.attach(help_label, 1, 1, 1, 1)
        # 时间戳格式
        label = Gtk.Label(label="时间戳格式:", xalign=0)
        label.set_tooltip_markup("用于生成文件名的 Python strftime 格式字符串\n常用占位符: <b>%Y</b>(年) <b>%m</b>(月) <b>%d</b>(日) <b>%H</b>(时) <b>%M</b>(分) <b>%S</b>(秒)")
        self.timestamp_entry = Gtk.Entry()
        self.timestamp_entry.set_tooltip_markup(label.get_tooltip_markup())
        self._connect_focus_handlers(self.timestamp_entry)
        help_label = Gtk.Label(label="遵循 Python strftime 格式")
        help_label.set_markup("<small>遵循 Python strftime 格式</small>")
        grid2.attach(label, 0, 2, 1, 1)
        grid2.attach(self.timestamp_entry, 1, 2, 1, 1)
        grid2.attach(help_label, 1, 3, 1, 1)
        preview_title_label = Gtk.Label(label="文件名预览:", xalign=0)
        self.filename_preview_label = Gtk.Label(xalign=0)
        self.filename_preview_label.set_selectable(True)
        grid2.attach(preview_title_label, 0, 4, 1, 1)
        grid2.attach(self.filename_preview_label, 1, 4, 1, 1)
        self.filename_entry.connect("changed", self._update_filename_preview)
        self.timestamp_entry.connect("changed", self._update_filename_preview)
        self.stack.add_titled(scrolled, "output", "输出设置")

    def _create_hotkeys_page(self):
        """创建热键设置页面"""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        vbox.set_margin_start(20)
        vbox.set_margin_end(20)
        vbox.set_margin_top(20)
        vbox.set_margin_bottom(20)
        scrolled.add(vbox)
        info_label = Gtk.Label(label="点击下方的按钮，然后按下想设置的快捷键组合")
        info_label.set_line_wrap(True)
        info_label.set_xalign(0)
        vbox.pack_start(info_label, False, False, 0)
        frame = Gtk.Frame(label="快捷键设置")
        vbox.pack_start(frame, False, False, 0)
        grid = Gtk.Grid()
        grid.set_margin_start(15)
        grid.set_margin_end(15)
        grid.set_margin_top(10)
        grid.set_margin_bottom(15)
        grid.set_row_spacing(10)
        grid.set_column_spacing(15)
        frame.add(grid)
        self.hotkey_configs = [
            ("capture", "截图"), ("finalize", "完成"),
            ("undo", "撤销"), ("cancel", "取消"),
            ("grid_backward", "整格后退"), ("grid_forward", "整格前进"),
            ("auto_scroll_start", "开始自动滚动"), ("auto_scroll_stop", "停止自动滚动"),
            ("configure_scroll_unit", "配置滚动单位"), ("toggle_grid_mode", "切换整格模式"),
            ("toggle_preview", "激活/隐藏预览面板"), ("open_config_editor", "激活/隐藏配置面板"),
            ("toggle_instruction_panel", "显示/隐藏提示面板"), ("toggle_hotkeys_enabled", "启用/禁用快捷键"), 
            ("preview_zoom_in", "预览面板放大"), ("preview_zoom_out", "预览面板缩小"),
            ("dialog_confirm", "退出对话框确认"), ("dialog_cancel", "退出对话框取消")
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
        hotkeys_page_settings = [('Hotkeys', key) for key, _ in self.hotkey_configs]
        restore_button = Gtk.Button(label="恢复本页默认设置")
        restore_button.set_halign(Gtk.Align.END)
        restore_button.set_margin_top(10)
        restore_button.connect("clicked", self._on_restore_defaults_clicked, hotkeys_page_settings)
        vbox.pack_end(restore_button, False, False, 0)
        self.stack.add_titled(scrolled, "hotkeys", "热键")

    def _create_interface_page(self):
        """创建界面设置页面"""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        vbox.set_margin_start(20)
        vbox.set_margin_end(20)
        vbox.set_margin_top(20)
        vbox.set_margin_bottom(20)
        scrolled.add(vbox)
        interface_page_settings = [
            ('Interface.Components', 'enable_buttons'),
            ('Interface.Components', 'enable_grid_action_buttons'),
            ('Interface.Components', 'enable_auto_scroll_buttons'),
            ('Interface.Components', 'enable_side_panel'),
            ('Interface.Components', 'show_preview_on_start'),
            ('Interface.Components', 'show_capture_count'),
            ('Interface.Components', 'show_total_dimensions'),
            ('Interface.Components', 'show_current_mode'),
            ('Interface.Components', 'show_instruction_panel_on_start'),
            ('Behavior', 'capture_with_cursor'),
            ('Behavior', 'scroll_method'),
            ('Behavior', 'reuse_invisible_cursor'),
            ('Behavior', 'forward_action'),
            ('Behavior', 'backward_action'),
        ]
        restore_button = Gtk.Button(label="恢复本页默认设置")
        restore_button.set_halign(Gtk.Align.END)
        restore_button.set_margin_top(10)
        restore_button.connect("clicked", self._on_restore_defaults_clicked, interface_page_settings)
        vbox.pack_end(restore_button, False, False, 0)
        # 可见组件
        frame1 = Gtk.Frame(label="可见组件")
        vbox.pack_start(frame1, False, False, 0)
        vbox1 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox1.set_margin_start(15)
        vbox1.set_margin_end(15)
        vbox1.set_margin_top(10)
        vbox1.set_margin_bottom(15)
        frame1.add(vbox1)
        component_configs = [
            ("enable_buttons", "启用主操作按钮", "控制是否显示“截图”、“完成”、“撤销”、“取消”这四个功能按钮"),
            ("enable_grid_action_buttons", "启用前进/后退按钮", "控制在<b>整格模式</b>下是否显示“前进”和“后退”按钮\n禁用后，仍能通过快捷键操作"),
            ("enable_auto_scroll_buttons", "启用开始/停止按钮", "控制在<b>自由模式</b>下是否显示“开始”和“停止”按钮"),
            ("enable_side_panel", "启用侧边栏", "是否在截图区域旁边显示一个用于显示信息面板和功能面板的侧边栏"),
            ("show_preview_on_start", "启动时显示预览面板", "控制是否在截图会话开始时自动打开预览面板"),
            ("show_capture_count", "显示已截图数量", "是否在侧边栏信息面板中显示当前已截取的图片数量"),
            ("show_total_dimensions", "显示最终图片总尺寸", "是否在侧边栏信息面板中显示拼接后图片的总宽度和总高度"),
            ("show_current_mode", "显示当前模式", "是否在侧边栏信息面板中显示当前所处的模式（自由/整格/自动）"),
            ("show_instruction_panel_on_start", "启动时显示提示面板", "每次启动截图会话时，是否在左下角显示一个包含快捷键的提示面板")
        ]
        self.component_checkboxes = {}
        for key, desc, tooltip in component_configs:
            checkbox = Gtk.CheckButton(label=desc)
            checkbox.set_tooltip_markup(tooltip)
            checkbox.connect("toggled", lambda w, k=key: self._on_component_toggled(w, k))
            vbox1.pack_start(checkbox, False, False, 0)
            self.component_checkboxes[key] = checkbox
        # 操作行为
        frame2 = Gtk.Frame(label="操作行为")
        vbox.pack_start(frame2, False, False, 0)
        grid2 = Gtk.Grid()
        grid2.set_margin_start(15)
        grid2.set_margin_end(15)
        grid2.set_margin_top(10)
        grid2.set_margin_bottom(15)
        grid2.set_row_spacing(10)
        grid2.set_column_spacing(10)
        frame2.add(grid2)
        # 包含鼠标指针
        self.cursor_checkbox = Gtk.CheckButton(label="截取鼠标指针")
        self.cursor_checkbox.set_tooltip_markup("截图时是否将鼠标指针也一并截取下来")
        grid2.attach(self.cursor_checkbox, 0, 0, 2, 1)
        self.free_scroll_matching_checkbox = Gtk.CheckButton(label="自由模式启用滚动误差修正")
        tooltip = "在<b>自由模式</b>下，使用模板匹配来修正滚动误差，此功能会增加拼接处理时间\n启用后，请确保每次滚动有重叠部分，否则修正无效"
        self.free_scroll_matching_checkbox.set_tooltip_markup(tooltip)
        grid2.attach(self.free_scroll_matching_checkbox, 3, 0, 2, 1)
        # 高级行为设置
        self.behavior_advanced_frame = Gtk.Frame(label="高级行为设置")
        vbox.pack_start(self.behavior_advanced_frame, False, False, 0)
        grid3 = Gtk.Grid()
        grid3.set_margin_start(15)
        grid3.set_margin_end(15)
        grid3.set_margin_top(10)
        grid3.set_margin_bottom(15)
        grid3.set_row_spacing(10)
        grid3.set_column_spacing(10)
        self.behavior_advanced_frame.add(grid3)
        # 滚动实现方式
        label = Gtk.Label(label="滚动方式:", xalign=0)
        label.set_tooltip_markup("<b>移动用户光标</b>: 临时将用户鼠标移动到截图区域中心来滚动，兼容性好但有干扰\n<b>使用隐形光标</b>: 创建一个独立的虚拟光标来滚动，无干扰但退出时可能导致界面卡顿")
        self.scroll_method_combo = Gtk.ComboBoxText()
        self.scroll_method_combo.set_tooltip_markup(label.get_tooltip_markup())
        self.scroll_method_combo.connect("scroll-event", lambda widget, event: True)
        self.scroll_method_combo.append("move_user_cursor", "移动用户光标")
        self.scroll_method_combo.append("invisible_cursor", "使用隐形光标（实验性功能）")
        self.reuse_cursor_checkbox = Gtk.CheckButton(label="复用隐形光标设备")
        self.reuse_cursor_checkbox.set_tooltip_markup("勾选后，在使用“隐形光标”滚动方式时，程序退出后不会删除创建的虚拟鼠标和触摸板设备，下次启动时会尝试复用它们")
        self.reuse_cursor_checkbox.connect("toggled", lambda w: self._on_behavior_toggled(w, 'reuse_invisible_cursor'))
        if IS_WAYLAND:
            label.set_visible(False)
            self.scroll_method_combo.set_visible(False)
            self.reuse_cursor_checkbox.set_visible(False)
        grid3.attach(self.reuse_cursor_checkbox, 3, 0, 2, 1)
        grid3.attach(label, 0, 0, 1, 1)
        grid3.attach(self.scroll_method_combo, 1, 0, 1, 1)
        # 前进/后退按钮功能
        action_options = [
            ("scroll", "仅滚动"),
            ("scroll_capture", "滚动后截图"),
            ("capture_scroll", "截图后滚动"),
            ("scroll_delete", "滚动并删除"),
        ]
        label = Gtk.Label(label="“前进”动作:", xalign=0)
        label.set_tooltip_markup("定义在<b>整格模式</b>下，点击“前进”按钮或使用其快捷键时执行的复合动作")
        self.forward_combo = Gtk.ComboBoxText()
        self.forward_combo.set_tooltip_markup(label.get_tooltip_markup())
        self.forward_combo.connect("scroll-event", lambda widget, event: True)
        for value, desc in action_options:
            self.forward_combo.append(value, desc)
        grid3.attach(label, 0, 1, 1, 1)
        grid3.attach(self.forward_combo, 1, 1, 1, 1)
        label = Gtk.Label(label="“后退”动作:", xalign=0)
        label.set_tooltip_markup("定义在<b>整格模式</b>下，点击“后退”按钮时执行的复合动作")
        self.backward_combo = Gtk.ComboBoxText()
        self.backward_combo.set_tooltip_markup(label.get_tooltip_markup())
        self.backward_combo.connect("scroll-event", lambda widget, event: True)
        for value, desc in action_options:
            self.backward_combo.append(value, desc)
        grid3.attach(label, 0, 2, 1, 1)
        grid3.attach(self.backward_combo, 1, 2, 1, 1)
        self.stack.add_titled(scrolled, "interface", "截图界面")

    def _create_theme_layout_page(self):
        """创建主题与布局页面"""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        vbox.set_margin_start(20)
        vbox.set_margin_end(20)
        vbox.set_margin_top(20)
        vbox.set_margin_bottom(20)
        scrolled.add(vbox)
        theme_layout_settings = [
            ('Interface.Theme', 'border_color'),
            ('Interface.Theme', 'matching_indicator_color'),
            ('Interface.Layout', 'border_width'),
            ('Interface.Layout', 'handle_height'),
            ('Interface.Layout', 'side_panel_width'),
            ('Interface.Layout', 'button_spacing'),
            ('Interface.Layout', 'processing_dialog_width'),
            ('Interface.Layout', 'processing_dialog_height'),
            ('Interface.Layout', 'processing_dialog_spacing'),
            ('Interface.Layout', 'processing_dialog_border_width'),
            ('Interface.Theme', 'processing_dialog_css'),
            ('Interface.Theme', 'info_panel_css'),
            ('Interface.Theme', 'simulated_window_css'),
            ('Interface.Theme', 'notification_css'),
            ('Interface.Theme', 'dialog_css'),
            ('Interface.Theme', 'mask_css'),
            ('Interface.Theme', 'instruction_panel_css'),
        ]
        restore_button = Gtk.Button(label="恢复本页默认设置")
        restore_button.set_halign(Gtk.Align.END)
        restore_button.set_margin_top(10)
        restore_button.connect("clicked", self._on_restore_defaults_clicked, theme_layout_settings)
        vbox.pack_end(restore_button, False, False, 0)
        # 核心外观
        frame1 = Gtk.Frame(label="核心外观")
        vbox.pack_start(frame1, False, False, 0)
        grid1 = Gtk.Grid()
        grid1.set_margin_start(15)
        grid1.set_margin_end(15)
        grid1.set_margin_top(10)
        grid1.set_margin_bottom(15)
        grid1.set_row_spacing(10)
        grid1.set_column_spacing(10)
        frame1.add(grid1)
        # 边框颜色
        label = Gtk.Label(label="边框颜色:", xalign=0)
        self.border_color_button = CustomColorButton()
        self.border_color_button.connect("clicked", self._show_embedded_color_chooser)
        grid1.attach(label, 0, 0, 1, 1)
        grid1.attach(self.border_color_button, 1, 0, 1, 1)
        # 指示器颜色
        label_ind = Gtk.Label(label="匹配指示器颜色:", xalign=0)
        label_ind.set_tooltip_markup("误差修正功能启用时，在边框上标记区域的颜色")
        self.indicator_color_button = CustomColorButton()
        self.indicator_color_button.connect("clicked", self._show_embedded_color_chooser)
        self.indicator_color_button.set_tooltip_markup(label_ind.get_tooltip_markup())
        grid1.attach(label_ind, 0, 1, 1, 1)
        grid1.attach(self.indicator_color_button, 1, 1, 1, 1)
        # 边框宽度
        label = Gtk.Label(label="边框宽度:", xalign=0)
        self.border_width_spin = Gtk.SpinButton()
        self._connect_focus_handlers(self.border_width_spin)
        self.border_width_spin.connect("scroll-event", lambda widget, event: True)
        self.border_width_spin.set_range(1, 25)
        self.border_width_spin.set_increments(1, 5)
        self.border_width_spin.set_halign(Gtk.Align.START)
        grid1.attach(label, 0, 2, 1, 1)
        grid1.attach(self.border_width_spin, 1, 2, 1, 1)
        # 布局微调
        frame2 = Gtk.Frame(label="布局微调")
        vbox.pack_start(frame2, False, False, 0)
        grid2 = Gtk.Grid()
        grid2.set_margin_start(15)
        grid2.set_margin_end(15)
        grid2.set_margin_top(10)
        grid2.set_margin_bottom(15)
        grid2.set_row_spacing(10)
        grid2.set_column_spacing(10)
        frame2.add(grid2)
        layout_configs = [
            ("handle_height", "拖动手柄高度", 3, 50, "在截图选区上下边缘，可用于拖动调整高度的区域大小，单位：逻辑px"),
            ("button_panel_width", "按钮面板宽度", 80, 200, "右侧按钮面板的宽度，单位：逻辑px"),
            ("side_panel_width", "侧边栏宽度", 80, 200, "功能面板和信息面板的总宽度，单位：逻辑px"),
            ("button_spacing", "按钮间距", 0, 20, "各个按钮之间的垂直间距，单位：逻辑px"),
            ("processing_dialog_width", "处理中对话框宽度", 100, 400, "完成截图后出现的对话框的宽度，单位：逻辑px"),
            ("processing_dialog_height", "处理中对话框高度", 50, 200, "完成截图后出现的对话框的高度，单位：逻辑px"),
            ("processing_dialog_spacing", "处理中对话框间距", 5, 30, "处理中对话框内部元素（图标、文字、进度条）的间距，单位：逻辑px"),
            ("processing_dialog_border_width", "处理中对话框边距", 5, 50, "处理中对话框内容区域距离边缘的距离，单位：逻辑px")
        ]
        self.layout_spins = {}
        for i, (key, desc, min_val, max_val, tooltip) in enumerate(layout_configs):
            label = Gtk.Label(label=f"{desc}:", xalign=0)
            label.set_tooltip_markup(tooltip)
            spin = Gtk.SpinButton()
            spin.set_tooltip_markup(tooltip)
            self._connect_focus_handlers(spin)
            spin.connect("scroll-event", lambda widget, event: True)
            spin.set_range(min_val, max_val)
            spin.set_increments(1, 5)
            spin.set_halign(Gtk.Align.START)
            row = i // 2
            col = (i % 2) * 2
            grid2.attach(label, col, row, 1, 1)
            grid2.attach(spin, col + 1, row, 1, 1)
            self.layout_spins[key] = spin
        # 自定义样式（CSS）
        css_expander = Gtk.Expander(label="自定义样式（CSS）")
        vbox.pack_start(css_expander, True, True, 0)
        css_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        css_vbox.set_margin_start(10)
        css_vbox.set_margin_end(10)
        css_vbox.set_margin_top(5)
        css_vbox.set_margin_bottom(10)
        css_expander.add(css_vbox)
        self.css_textviews = {}
        css_configs = [
            ("processing_dialog_css", "处理中面板样式"), 
            ("notification_css", "通知面板样式"),
            ("dialog_css", "对话框样式"),
            ("mask_css", "遮罩层样式"),
            ("info_panel_css", "信息面板样式"),
            ("instruction_panel_css", "提示面板样式"),
            ("simulated_window_css", "模拟窗口通用样式")
        ]
        for key, desc in css_configs:
            label = Gtk.Label(label=f"{desc}:", xalign=0)
            label.set_tooltip_markup("在此处输入自定义 CSS 代码以调整组件外观")
            scrolled_css = Gtk.ScrolledWindow()
            scrolled_css.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scrolled_css.set_size_request(-1, 190)
            scrolled_css.set_margin_start(10)
            scrolled_css.set_margin_end(5)
            scrolled_css.set_margin_top(5)
            scrolled_css.set_margin_bottom(5)
            textview = Gtk.TextView()
            textview.set_wrap_mode(Gtk.WrapMode.WORD)
            scrolled_css.add(textview)
            self._connect_focus_handlers(textview)
            frame = Gtk.Frame()
            frame.set_shadow_type(Gtk.ShadowType.IN)
            frame.add(scrolled_css)
            css_vbox.pack_start(label, False, False, 0)
            css_vbox.pack_start(frame, True, True, 0)
            self.css_textviews[key] = textview
        self.theme_layout_page = scrolled
        self.stack.add_titled(scrolled, "theme", "主题与布局")

    def _discover_sound_themes(self):
        """扫描 /usr/share/sounds 目录，找出所有可用的主题和音效"""
        sound_base_path = Path("/usr/share/sounds")
        themes = {}
        if not sound_base_path.is_dir():
            logging.warning(f"声音目录 {sound_base_path} 不存在，无法扫描主题")
            return themes
        for theme_path in sound_base_path.iterdir():
            stereo_path = theme_path / "stereo"
            if theme_path.is_dir() and stereo_path.is_dir():
                theme_name = theme_path.name
                sounds = []
                for sound_file in stereo_path.iterdir():
                    if sound_file.is_file() and sound_file.suffix in ['.oga', '.wav', '.ogg']:
                        sounds.append(sound_file.stem)
                if sounds:
                    themes[theme_name] = sorted(sounds)
        logging.info(f"发现 {len(themes)} 个声音主题")
        return themes

    def _on_sound_theme_changed(self, combo):
        selected_theme = combo.get_active_id()
        if not selected_theme or selected_theme not in self.sound_data:
            return
        sound_list = self.sound_data[selected_theme]
        sound_combos = [
            self.sound_entries['capture_sound'],
            self.sound_entries['undo_sound'],
            self.sound_entries['finalize_sound']
        ]
        for sound_combo in sound_combos:
            current_value = sound_combo.get_active_id()
            sound_combo.remove_all()
            for sound in sound_list:
                sound_combo.append(sound, sound)
            if current_value in sound_list:
                sound_combo.set_active_id(current_value)

    def _on_play_sound_clicked(self, button, sound_combo):
        theme_combo = self.sound_entries['sound_theme']
        theme_name = theme_combo.get_active_id()
        sound_name = sound_combo.get_active_id()
        if theme_name and sound_name:
            logging.info(f"试听音效: 主题='{theme_name}', 声音='{sound_name}'")
            play_sound(sound_name, theme_name=theme_name)
        elif not theme_name:
            logging.warning("无法试听：请先选择一个声音主题")
        else:
            logging.warning("无法试听：请先选择一个音效")

    def _create_system_performance_page(self):
        """创建系统与性能页面（高级）"""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        vbox.set_margin_start(20)
        vbox.set_margin_end(20)
        vbox.set_margin_top(20)
        vbox.set_margin_bottom(20)
        scrolled.add(vbox)
        system_perf_settings = [
            ('System', 'copy_to_clipboard_on_finish'),
            ('System', 'notification_click_action'),
            ('System', 'large_image_opener'),
            ('System', 'sound_theme'),
            ('System', 'capture_sound'),
            ('System', 'undo_sound'),
            ('System', 'finalize_sound'),
            ('Performance', 'auto_scroll_ticks_per_step'),
            ('Performance', 'max_scroll_per_tick'),
            ('Performance', 'min_scroll_per_tick'),
            ('Performance', 'max_viewer_dimension'),
            ('System', 'log_file'),
            ('System', 'temp_directory_base'),
        ]
        restore_button = Gtk.Button(label="恢复本页默认设置")
        restore_button.set_halign(Gtk.Align.END)
        restore_button.set_margin_top(10)
        restore_button.connect("clicked", self._on_restore_defaults_clicked, system_perf_settings)
        vbox.pack_end(restore_button, False, False, 0)
        # 系统交互
        frame1 = Gtk.Frame(label="系统交互")
        vbox.pack_start(frame1, False, False, 0)
        grid1 = Gtk.Grid()
        grid1.set_margin_start(15)
        grid1.set_margin_end(15)
        grid1.set_margin_top(10)
        grid1.set_margin_bottom(15)
        grid1.set_row_spacing(10)
        grid1.set_column_spacing(10)
        frame1.add(grid1)
        # 完成后复制到剪贴板
        self.clipboard_checkbox = Gtk.CheckButton(label="完成后复制到剪贴板")
        self.clipboard_checkbox.set_tooltip_markup("拼接完成后，是否自动将最终生成的图片复制到系统剪贴板")
        grid1.attach(self.clipboard_checkbox, 0, 0, 2, 1)
        # 点击通知时
        label = Gtk.Label(label="通知点击行为:", xalign=0)
        label.set_tooltip_markup("设置点击“截图完成”的系统通知后，执行的操作")
        self.notification_combo = Gtk.ComboBoxText()
        self.notification_combo.set_tooltip_markup(label.get_tooltip_markup())
        self.notification_combo.connect("scroll-event", lambda widget, event: True)
        self.notification_combo.append("none", "无操作")
        self.notification_combo.append("open_file", "打开文件")
        self.notification_combo.append("open_directory", "打开目录")
        grid1.attach(label, 0, 1, 1, 1)
        grid1.attach(self.notification_combo, 1, 1, 1, 1)
        # 大尺寸图片打开方式
        label = Gtk.Label(label="大尺寸图片打开命令:", xalign=0)
        label.set_tooltip_markup("当生成图片长或宽超过下方阈值时，使用此终端命令打开图片\n<b>{filepath}</b> 会被替换为图片文件路径，示例：shotwell \"{filepath}\"\n直接设为 <b>default_browser</b> 可用浏览器打开")
        self.large_opener_entry = Gtk.Entry()
        self.large_opener_entry.set_tooltip_markup(label.get_tooltip_markup())
        help_label = Gtk.Label(label="可用变量: {filepath}, default_browser")
        help_label.set_markup("<small>可用变量: {filepath}, default_browser</small>")
        grid1.attach(label, 0, 2, 1, 1)
        grid1.attach(self.large_opener_entry, 1, 2, 1, 1)
        self._connect_focus_handlers(self.large_opener_entry)
        grid1.attach(help_label, 1, 3, 1, 1)
        # 声音主题
        frame2 = Gtk.Frame(label="声音主题")
        vbox.pack_start(frame2, False, False, 0)
        grid2 = Gtk.Grid()
        grid2.set_margin_start(15)
        grid2.set_margin_end(15)
        grid2.set_margin_top(10)
        grid2.set_margin_bottom(15)
        grid2.set_row_spacing(10)
        grid2.set_column_spacing(10)
        frame2.add(grid2)
        self.sound_entries = {}
        label = Gtk.Label(label="声音主题:", xalign=0)
        theme_combo = Gtk.ComboBoxText()
        theme_combo.connect("scroll-event", lambda widget, event: True)
        for theme_name in sorted(self.sound_data.keys()):
            theme_combo.append(theme_name, theme_name)
        theme_combo.connect("changed", self._on_sound_theme_changed)
        self.sound_entries['sound_theme'] = theme_combo
        grid2.attach(label, 0, 0, 1, 1)
        grid2.attach(theme_combo, 1, 0, 1, 1)
        sound_configs = [
            ("capture_sound", "截图音效"),
            ("undo_sound", "撤销音效"),
            ("finalize_sound", "完成音效")
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
            self.sound_entries[key] = sound_combo
            grid2.attach(label, 0, i + 1, 1, 1)
            grid2.attach(hbox, 1, i + 1, 1, 1)
        # 性能调优
        frame3 = Gtk.Frame(label="性能调优")
        vbox.pack_start(frame3, False, False, 0)
        grid3 = Gtk.Grid()
        grid3.set_margin_start(15)
        grid3.set_margin_end(15)
        grid3.set_margin_top(10)
        grid3.set_margin_bottom(15)
        grid3.set_row_spacing(10)
        grid3.set_column_spacing(10)
        frame3.add(grid3)
        performance_configs = [
            ("grid_matching_max_overlap", "整格模式误差修正范围", 10, 20, "<b>整格模式</b>下的误差修正设置最大搜索范围，单位：缓冲区px"),
            ("free_scroll_matching_max_overlap", "自由模式误差修正范围", 20, 300, "<b>自由模式</b>下的误差修正设置最大搜索范围\n值越大，处理用时越长，单位：缓冲区px"),
            ("auto_scroll_ticks_per_step", "自动滚动步长（格数）", 1, 8, "自动模式下，每一步滚动几格\n值越大滚动越快"),
            ("max_scroll_per_tick", "自动截图高度（每格）", 120, 500, "自动模式下，对应滚动一格的截图高度 (px)\n总截图高度 = 此值 * 滚动格数，单位：缓冲区px"),
            ("min_scroll_per_tick", "最小滚动像素", 1, 60, "用于匹配和校准的最小滚动阈值 (px)，单位：缓冲区px"),
            ("max_viewer_dimension", "图片尺寸阈值", -1, 131071, "最终图片长或宽超过此值时，会使用上面的“大尺寸图片打开命令”\n设为 <b>-1</b> 禁用此功能，总是用系统默认方式打开图片\n设为 <b>0</b> 总是用自定义命令打开图片"),
            ("preview_drag_sensitivity", "预览拖动灵敏度", 0.5, 10.0, "预览面板中按住左键拖动图像的速度倍数")
        ]
        self.performance_spins = {}
        num_items = len(performance_configs)
        mid_point = (num_items + 1) // 2
        for i, (key, desc, min_val, max_val, tooltip) in enumerate(performance_configs):
            row = i % mid_point
            col_base = (i // mid_point) * 2
            label = Gtk.Label(label=f"{desc}:", xalign=0)
            label.set_tooltip_markup(tooltip)
            spin = Gtk.SpinButton()
            spin.set_tooltip_markup(tooltip)
            self._connect_focus_handlers(spin)
            spin.connect("scroll-event", lambda widget, event: True)
            spin.set_range(min_val, max_val)
            spin.set_increments(1, 10)
            if isinstance(min_val, float) or isinstance(max_val, float):
                spin.set_digits(1)
                spin.set_increments(0.1, 1.0)
            spin.set_halign(Gtk.Align.START)
            grid3.attach(label, col_base, row, 1, 1)
            grid3.attach(spin, col_base + 1, row, 1, 1)
            self.performance_spins[key] = spin
        # 路径
        frame4 = Gtk.Frame(label="路径")
        vbox.pack_start(frame4, False, False, 0)
        grid4 = Gtk.Grid()
        grid4.set_margin_start(15)
        grid4.set_margin_end(15)
        grid4.set_margin_top(10)
        grid4.set_margin_bottom(15)
        grid4.set_row_spacing(10)
        grid4.set_column_spacing(10)
        frame4.add(grid4)
        path_configs = [
            ("log_file", "日志文件路径", "指定日志文件的保存路径，支持使用 ~ 代表用户主目录"),
            ("temp_directory_base", "临时目录模板", "定义用于存放单次会话截图的目录模板\n变量 <b>{pid}</b> 会被替换为进程ID")
        ]
        self.path_entries = {}
        for i, (key, desc, tooltip) in enumerate(path_configs):
            label = Gtk.Label(label=f"{desc}:", xalign=0)
            label.set_tooltip_markup(tooltip)
            entry = Gtk.Entry()
            entry.set_tooltip_markup(tooltip)
            self._connect_focus_handlers(entry)
            entry.set_hexpand(True)
            grid4.attach(label, 0, i, 1, 1)
            grid4.attach(entry, 1, i, 1, 1)
            self.path_entries[key] = entry
        self.system_performance_page = scrolled
        self.stack.add_titled(scrolled, "system", "系统与性能")

    def _create_grid_calibration_page(self):
        """创建整格模式校准页面"""
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        vbox.set_margin_start(20)
        vbox.set_margin_end(20)
        vbox.set_margin_top(20)
        vbox.set_margin_bottom(20)
        info_label = Gtk.Label(label="用于手动添加或调整应用的滚动单位 (缓冲区px)")
        info_label.set_xalign(0)
        vbox.pack_start(info_label, False, False, 0)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_shadow_type(Gtk.ShadowType.IN)
        vbox.pack_start(scrolled, True, True, 0)
        self.grid_listbox = Gtk.ListBox()
        self.grid_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        scrolled.add(self.grid_listbox)
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        button_box.set_halign(Gtk.Align.END)
        vbox.pack_start(button_box, False, False, 0)
        add_button = Gtk.Button(label="添加")
        add_button.connect("clicked", self._on_grid_add)
        remove_button = Gtk.Button(label="删除选中项")
        remove_button.connect("clicked", self._on_grid_remove)
        button_box.pack_start(add_button, False, False, 0)
        button_box.pack_start(remove_button, False, False, 0)
        self.grid_calibration_page = vbox
        self.stack.add_titled(vbox, "grid", "整格模式校准")

    def _add_grid_row(self, app_class="", unit=0, matching_enabled=False):
        """向整格校准列表框中添加一行"""
        row = Gtk.ListBoxRow()
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        hbox.set_margin_start(10)
        hbox.set_margin_end(10)
        hbox.set_margin_top(5)
        hbox.set_margin_bottom(5)
        row.add(hbox)
        entry = Gtk.Entry()
        self._connect_focus_handlers(entry)
        entry.connect("button-press-event", self._on_grid_row_child_clicked)
        entry.set_placeholder_text("应用程序类名")
        entry.set_text(app_class)
        entry.set_hexpand(True)
        spin = Gtk.SpinButton()
        self._connect_focus_handlers(spin)
        spin.connect("button-press-event", self._on_grid_row_child_clicked)
        spin.connect("scroll-event", lambda widget, event: True)
        spin.set_range(1, 300)
        spin.set_increments(1, 10)
        spin.set_value(unit)
        check = Gtk.CheckButton(label="修正误差")
        check.set_tooltip_markup("启用模板匹配修正滚动误差\n启用后，请确保滚动距离小于截图区高度，否则修正无效")
        check.connect("button-press-event", self._on_grid_row_child_clicked)
        check.set_active(matching_enabled)
        hbox.pack_start(entry, True, True, 0)
        hbox.pack_start(spin, False, False, 0)
        hbox.pack_start(check, False, False, 0)
        self.grid_listbox.add(row)
        row.show_all()

    def _on_grid_add(self, widget):
        self._add_grid_row()

    def _on_grid_remove(self, widget):
        selected_row = self.grid_listbox.get_selected_row()
        if selected_row:
            self.grid_listbox.remove(selected_row)

    def _on_grid_row_child_clicked(self, widget, event):
        parent = widget.get_parent()
        while parent and not isinstance(parent, Gtk.ListBoxRow):
            parent = parent.get_parent()
        if isinstance(parent, Gtk.ListBoxRow):
            self.grid_listbox.select_row(parent)
        return False

    def _create_interface_strings_page(self):
        """创建界面文本自定义页面"""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_tooltip_markup("自定义程序界面中显示的各种文本")
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        vbox.set_margin_start(20)
        vbox.set_margin_end(20)
        vbox.set_margin_top(20)
        vbox.set_margin_bottom(20)
        scrolled.add(vbox)
        frame = Gtk.Frame(label="界面文本")
        vbox.pack_start(frame, False, False, 0)
        grid = Gtk.Grid()
        grid.set_margin_start(15)
        grid.set_margin_end(15)
        grid.set_margin_top(10)
        grid.set_margin_bottom(15)
        grid.set_row_spacing(10)
        grid.set_column_spacing(10)
        frame.add(grid)
        string_configs = [
            ("dialog_quit_title", "退出确认标题"),
            ("dialog_quit_message", "退出确认消息"),
            ("dialog_quit_button_yes", "退出确认按钮 (是)"),
            ("dialog_quit_button_no", "退出确认按钮 (否)"),
            ("capture_count_format", "截图数量格式"),
            ("processing_dialog_text", "处理中对话框文本"),
        ]
        self.string_entries = {}
        for i, (key, desc) in enumerate(string_configs):
            label = Gtk.Label(label=f"{desc}:", xalign=0)
            entry = Gtk.Entry()
            self._connect_focus_handlers(entry)
            entry.set_hexpand(True)
            grid.attach(label, 0, i, 1, 1)
            grid.attach(entry, 1, i, 1, 1)
            self.string_entries[key] = entry
        self.managed_settings.extend([('Interface.Strings', key) for key in self.string_entries.keys()])
        self.interface_strings_page = scrolled
        strings_page_settings = [('Interface.Strings', key) for key in self.string_entries.keys()]
        restore_button = Gtk.Button(label="恢复本页默认设置")
        restore_button.set_halign(Gtk.Align.END)
        restore_button.set_margin_top(10)
        restore_button.connect("clicked", self._on_restore_defaults_clicked, strings_page_settings)
        vbox.pack_end(restore_button, False, False, 0)
        self.stack.add_titled(scrolled, "strings", "界面文本")

    def _on_advanced_toggle(self, switch, gparam):
        self.show_advanced = switch.get_active()
        self._update_advanced_visibility()

    def _update_advanced_visibility(self):
        """根据高级开关状态更新UI元素的可见性"""
        # 页面级可见性
        advanced_pages = [
            self.theme_layout_page,
            self.system_performance_page,
            self.grid_calibration_page,
            self.interface_strings_page
        ]
        for page_widget in advanced_pages:
            page_widget.set_visible(self.show_advanced)
        # 页面内组件的可见性
        self.output_advanced_frame.set_visible(self.show_advanced)
        self.behavior_advanced_frame.set_visible(self.show_advanced)

    def _on_format_changed(self, combo):
        is_jpeg = combo.get_active_text() == "JPEG"
        self.jpeg_label.set_sensitive(is_jpeg)
        self.jpeg_quality_spin.set_sensitive(is_jpeg)
        self._update_filename_preview()

    def _on_hotkey_changed(self, widget, key):
        text = widget.get_text()
        self.config.save_setting('Hotkeys', key, text)

    def _on_behavior_toggled(self, widget, key):
        is_active = widget.get_active()
        self.config.save_setting('Behavior', key, str(is_active).lower())
    def _on_component_toggled(self, widget, key):
        is_active = widget.get_active()
        self.config.save_setting('Interface.Components', key, str(is_active).lower())

    def _on_restore_defaults_clicked(self, button, settings_to_restore):
        p_default = self.default_parser
        p_current = self.config.parser
        for section, key in settings_to_restore:
            default_value = p_default.get(section, key, raw=True)
            p_current.set(section, key, default_value)
            widget = self._find_widget_for_setting(section, key)
            if widget:
                self._update_widget_value(widget, key, default_value)
        page_keys = [item[1] for item in settings_to_restore]
        if 'filename_template' in page_keys:
            self._update_filename_preview()

    def _find_widget_for_setting(self, section, key):
        key_to_widget_map = {
            'save_directory': self.save_dir_entry,
            'save_format': self.format_combo,
            'jpeg_quality': self.jpeg_quality_spin,
            'filename_template': self.filename_entry,
            'filename_timestamp_format': self.timestamp_entry,
            'border_color': self.border_color_button,
            'matching_indicator_color': self.indicator_color_button,
            'border_width': self.border_width_spin,
            'copy_to_clipboard_on_finish': self.clipboard_checkbox,
            'notification_click_action': self.notification_combo,
            'large_image_opener': self.large_opener_entry,
            'capture_with_cursor': self.cursor_checkbox,
            'enable_free_scroll_matching': self.free_scroll_matching_checkbox,
            'scroll_method': self.scroll_method_combo,
            'reuse_invisible_cursor': self.reuse_cursor_checkbox,
            'forward_action': self.forward_combo,
            'backward_action': self.backward_combo,
        }
        if key in key_to_widget_map: return key_to_widget_map[key]
        if key in self.hotkey_buttons: return self.hotkey_buttons[key]
        if key in self.component_checkboxes: return self.component_checkboxes[key]
        if key in self.layout_spins: return self.layout_spins[key]
        if key in self.css_textviews: return self.css_textviews[key]
        if key in self.sound_entries: return self.sound_entries[key]
        if key in self.performance_spins: return self.performance_spins[key]
        if key in self.path_entries: return self.path_entries[key]
        if key in self.string_entries: return self.string_entries[key]
        logging.warning(f"在_find_widget_for_setting中未找到key '{key}'对应的控件")
        return None

    def _get_widget_value(self, widget, key):
        if isinstance(widget, Gtk.CheckButton) or isinstance(widget, Gtk.Switch):
            return str(widget.get_active()).lower()
        elif isinstance(widget, CustomColorButton):
            rgba = widget.get_rgba()
            return f"{rgba.red:.2f}, {rgba.green:.2f}, {rgba.blue:.2f}, {rgba.alpha:.2f}"
        elif isinstance(widget, Gtk.Button):
            label = widget.get_label()
            if "请按下" in label:
                return self.config.parser.get('Hotkeys', key, fallback="")
            return label
        elif isinstance(widget, Gtk.Entry):
            return widget.get_text()
        elif isinstance(widget, Gtk.ComboBoxText):
            return widget.get_active_id()
        elif isinstance(widget, Gtk.SpinButton):
            if key in ('preview_drag_sensitivity'):
                 return f"{widget.get_value():.1f}"
            else:
                 return str(widget.get_value_as_int())
        elif isinstance(widget, Gtk.TextView):
            buffer = widget.get_buffer()
            start, end = buffer.get_bounds()
            return buffer.get_text(start, end, False)
        logging.warning(f"在_get_widget_value中未处理控件类型: {type(widget)} for key '{key}'")
        return None

    def _update_widget_value(self, widget, key, value):
        if isinstance(widget, Gtk.Switch) or isinstance(widget, Gtk.CheckButton):
            widget.set_active(value.lower() == 'true')
        elif isinstance(widget, CustomColorButton):
            if value and value.count(',') == 3:
                try:
                    r, g, b, a = [float(c.strip()) for c in value.split(',')]
                    widget.set_rgba(Gdk.RGBA(r, g, b, a))
                except ValueError:
                    logging.warning(f"配置文件中的颜色值 '{value}' 包含非数字内容，无法解析")
            elif value:
                logging.warning(f"配置文件中的颜色值 '{value}' 格式错误，应为 'r, g, b, a'。跳过设置")
        elif isinstance(widget, (Gtk.Entry, Gtk.Button)):
            if key == 'save_directory':
                widget.set_text(str(Path(value).expanduser()))
            else:
                widget.set_label(value) if isinstance(widget, Gtk.Button) else widget.set_text(value)
        elif isinstance(widget, Gtk.ComboBoxText):
            widget.set_active_id(value)
        elif isinstance(widget, Gtk.SpinButton):
            widget.set_value(float(value))
        elif isinstance(widget, Gtk.TextView):
            widget.get_buffer().set_text(value.lstrip())
        else:
            logging.warning(f"在_update_widget_value中未处理控件类型: {type(widget)} for key '{key}'")

    def _load_config_values(self):
        """从config对象加载所有值并设置到UI控件"""
        p = self.config.parser
        sound_keys_to_skip = ['sound_theme', 'capture_sound', 'undo_sound', 'finalize_sound']
        for section, key in self.managed_settings:
            if key in sound_keys_to_skip:
                continue
            widget = self._find_widget_for_setting(section, key)
            if widget:
                value = p.get(section, key, raw=True, fallback="")
                self._update_widget_value(widget, key, value)
        theme_widget = self.sound_entries['sound_theme']
        theme_value = p.get('System', 'sound_theme', fallback="")
        if theme_value:
            theme_widget.set_active_id(theme_value)
        self._on_sound_theme_changed(theme_widget)
        for key in ['capture_sound', 'undo_sound', 'finalize_sound']:
            widget = self.sound_entries[key]
            value = p.get('System', key, fallback="")
            if value:
                widget.set_active_id(value)
        self.grid_listbox.foreach(lambda child: self.grid_listbox.remove(child))
        if p.has_section('ApplicationScrollUnits'):
            for app, value_str in p.items('ApplicationScrollUnits'):
                parts = [p.strip() for p in value_str.split(',')]
                try:
                    unit = int(parts[0])
                    enabled = parts[1].lower() == 'true' if len(parts) > 1 else False
                    self._add_grid_row(app, unit, enabled)
                except (ValueError, IndexError):
                    self._add_grid_row(app, 0, False)
        self._update_filename_preview()
        self._on_format_changed(self.format_combo)

    def _save_all_configs(self):
        """将所有UI控件的值保存回config对象"""
        p = self.config.parser
        for section, key in self.managed_settings:
            widget = self._find_widget_for_setting(section, key)
            if widget:
                value = self._get_widget_value(widget, key)
                if value is not None:
                    p.set(section, key, value)
        if p.has_section('ApplicationScrollUnits'):
            p.remove_section('ApplicationScrollUnits')
        p.add_section('ApplicationScrollUnits')
        for row in self.grid_listbox.get_children():
            hbox = row.get_child()
            entry, spin, check = hbox.get_children()
            app_class = entry.get_text().strip().lower()
            if app_class:
                unit = spin.get_value_as_int()
                enabled = check.get_active()
                value_to_save = f"{unit},{str(enabled).lower()}"
                p.set('ApplicationScrollUnits', app_class, value_to_save)
        try:
            with open(self.config.config_path, 'w') as configfile:
                p.write(configfile)
            logging.info(f"所有配置已成功保存到 {self.config.config_path}")
        except Exception as e:
            logging.error(f"写入配置文件失败: {e}")

class PreviewPanel(SimulatedWindow):
    """显示截图预览的滚动窗口"""
    ZOOM_FACTOR = 1.26  # 缩放系数
    MIN_ZOOM = 0.25     # 最小缩放比例
    MAX_ZOOM = 4.0      # 最大缩放比例
    def __init__(self, model: StitchModel, config: Config, parent_overlay: 'CaptureOverlay'):
        super().__init__(parent_overlay, title="长图预览", css_class="simulated-window", resizable=True)
        self.model = model
        self.config = config
        self.zoom_level = 1.0
        self.manual_zoom_active = False
        self.effective_scale_factor = 1.0
        # 逻辑px
        self.drawing_area_width = 1
        self.drawing_area_height = 1
        self.center_vertically = False
        self.was_at_bottom = True
        self.display_total_height = 0
        self.last_viewport_width = -1
        self.last_viewport_height = -1
        self.is_dragging = False
        self.drag_start_x = 0 # 窗口坐标
        self.drag_start_y = 0 # 窗口坐标
        self.drag_start_hadj_value = 0
        self.drag_start_vadj_value = 0
        self.is_selection_mode = False
        self.is_drawing_selection = False
        self.is_resizing_selection = None
        # 缓冲区px
        self.selection_absolute_start_y = None
        self.selection_absolute_end_y = None
        # 逻辑px
        self.resize_handle_size = 10 # 边缘拖动手柄的像素容差
        self.selection_autoscroll_timer = None
        self.selection_autoscroll_velocity = 0.0
        self.AUTOSCROLL_SENSITIVITY = 1.0
        self.AUTOSCROLL_INTERVAL = 50
        self.initial_y_offset = 0
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
        self.btn_cancel_selection.connect("clicked", self._on_cancel_selection_mode)
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
        self.btn_undo_mod.connect("clicked", self._on_undo_mod_clicked)
        self.btn_redo_mod = Gtk.Button.new_from_icon_name("edit-redo-symbolic", Gtk.IconSize.BUTTON)
        self.btn_redo_mod.set_tooltip_text("重做上一步编辑 (删除/恢复)")
        self.btn_redo_mod.connect("clicked", self._on_redo_mod_clicked)
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
        self.btn_scroll_top.connect("clicked", self._scroll_to_top)
        button_hbox.pack_start(self.btn_scroll_top, False, False, 0)
        self.btn_scroll_bottom = Gtk.Button.new_from_icon_name("go-bottom-symbolic", Gtk.IconSize.BUTTON)
        self.btn_scroll_bottom.set_tooltip_text("滚动到底部")
        self.btn_scroll_bottom.connect("clicked", self._scroll_to_bottom)
        button_hbox.pack_start(self.btn_scroll_bottom, False, False, 0)
        separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        button_hbox.pack_start(separator, False, False, 5)
        self.btn_zoom_out = Gtk.Button.new_from_icon_name("zoom-out-symbolic", Gtk.IconSize.BUTTON)
        self.btn_zoom_out.set_tooltip_text("缩小")
        self.btn_zoom_out.connect("clicked", self._zoom_out)
        button_hbox.pack_start(self.btn_zoom_out, False, False, 0)
        self.btn_zoom_reset = Gtk.Button.new_from_icon_name("zoom-original-symbolic", Gtk.IconSize.BUTTON)
        self.btn_zoom_reset.set_tooltip_text("重置缩放 (100%)")
        self.btn_zoom_reset.connect("clicked", self._reset_zoom)
        button_hbox.pack_start(self.btn_zoom_reset, False, False, 0)
        self.btn_zoom_in = Gtk.Button.new_from_icon_name("zoom-in-symbolic", Gtk.IconSize.BUTTON)
        self.btn_zoom_in.set_tooltip_text("放大")
        self.btn_zoom_in.connect("clicked", self._zoom_in)
        button_hbox.pack_start(self.btn_zoom_in, False, False, 0)
        self.zoom_label = Gtk.Label(label="100%")
        self.zoom_label.set_margin_start(5)
        button_hbox.pack_start(self.zoom_label, False, False, 0)
        self.model_update_handler_id = self.model.connect("model-updated", self.on_model_updated)
        self.model_mod_handler_id = self.model.connect('modification-stack-changed', self._on_modification_stack_changed)
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
        self._setup_cursors()
        self.on_model_updated(self.model)
        self._update_button_sensitivity()
        self.show_all()
        min_req_size, _ = self.get_preferred_size()
        self.min_width_calculated = min_req_size.width
        self._resize_limit_w = self.min_width_calculated
        initial_w = self.min_width_calculated + 60
        initial_h = 750
        self.set_size_request(initial_w, initial_h)

    def cleanup(self):
        if self.model and self.model_update_handler_id:
            try:
                self.model.disconnect(self.model_update_handler_id)
                self.model_update_handler_id = None
            except Exception as e:
                logging.warning(f"{e}")
        if self.model and self.model_mod_handler_id:
            try:
                self.model.disconnect(self.model_mod_handler_id)
                self.model_mod_handler_id = None
            except Exception as e:
                logging.warning(f"{e}")
        if self.selection_autoscroll_timer:
            GLib.source_remove(self.selection_autoscroll_timer)
            self.selection_autoscroll_timer = None

    def _setup_cursors(self):
        """获取并存储拖动所需的光标"""
        display = Gdk.Display.get_default()
        self.cursors = {
            'default': None,
            'grab': Gdk.Cursor.new_from_name(display, "grab"),
            'grabbing': Gdk.Cursor.new_from_name(display, "grabbing"),
            'crosshair': Gdk.Cursor.new_from_name(display, "crosshair"),
            'n-resize': Gdk.Cursor.new_from_name(display, "n-resize"),
            's-resize': Gdk.Cursor.new_from_name(display, "s-resize"),
        }

    def _on_key_press(self, widget, event):
        """处理预览面板的按键事件"""
        if not self.get_visible():
            return False
        keyval = event.keyval
        state = event.state & self.config.GTK_MODIFIER_MASK
        def is_match(hotkey_config):
            return keyval in hotkey_config['gtk_keys'] and state == hotkey_config['gtk_mask']
        if is_match(self.config.HOTKEY_PREVIEW_ZOOM_IN):
            self._zoom_in()
            return True
        elif is_match(self.config.HOTKEY_PREVIEW_ZOOM_OUT):
            self._zoom_out()
            return True
        return False

    # 逻辑px {
    def _get_center_ratios(self, viewport_w=None, viewport_h=None):
        """计算当前视口中心相对于内容的归一化坐标"""
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
        """根据归一化中心坐标设置滚动位置"""
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

    def _set_zoom_level_centered(self, new_zoom):
        """设置缩放级别并保持视觉中心不变"""
        if abs(new_zoom - self.zoom_level) < 1e-5:
            return
        center_x_ratio, center_y_ratio = self._get_center_ratios()
        self.zoom_level = new_zoom
        self.manual_zoom_active = True
        self._update_drawing_area_size()
        self._update_button_sensitivity()
        self._update_zoom_label()
        def update_scroll():
            self._set_scroll_from_ratios(center_x_ratio, center_y_ratio)
            return False
        GLib.idle_add(update_scroll)

    def _zoom_in(self, button=None):
        current_base_zoom = self.zoom_level if self.manual_zoom_active else self.effective_scale_factor
        new_zoom = current_base_zoom * self.ZOOM_FACTOR
        if new_zoom > self.MAX_ZOOM:
            new_zoom = self.MAX_ZOOM
        self._set_zoom_level_centered(new_zoom)

    def _zoom_out(self, button=None):
        current_base_zoom = self.zoom_level if self.manual_zoom_active else self.effective_scale_factor
        new_zoom = current_base_zoom / self.ZOOM_FACTOR
        if new_zoom < self.MIN_ZOOM:
            new_zoom = self.MIN_ZOOM
        self._set_zoom_level_centered(new_zoom)

    def _reset_zoom(self, button=None):
        if abs(self.effective_scale_factor - 1.0) > 1e-5 or not self.manual_zoom_active:
            self._set_zoom_level_centered(1.0)

    def _update_zoom_label(self):
        self.zoom_label.set_text(f"{self.effective_scale_factor * 100:.0f}%")
        return GLib.SOURCE_REMOVE

    def on_viewport_resized(self, widget, allocation):
        if allocation.width != self.last_viewport_width or allocation.height != self.last_viewport_height:
            old_w = self.last_viewport_width if self.last_viewport_width > 0 else allocation.width
            old_h = self.last_viewport_height if self.last_viewport_height > 0 else allocation.height
            center_x_ratio, center_y_ratio = self._get_center_ratios(viewport_w=old_w, viewport_h=old_h)
            self.last_viewport_width = allocation.width
            self.last_viewport_height = allocation.height
            self._update_drawing_area_size(scroll_if_needed=False)
            GLib.idle_add(self._set_scroll_from_ratios, center_x_ratio, center_y_ratio)

    def _on_drawing_area_size_allocate(self, widget, allocation):
        widget.queue_draw()

    def on_model_updated(self, model_instance):
        logging.info("预览面板收到模型更新信号，准备更新尺寸并重绘")
        if self.model.capture_count == 0 and self.is_selection_mode:
            logging.info("预览面板：模型已空，自动退出选择模式")
            self._on_cancel_selection_mode(None)
        v_adj = self.scrolled_window.get_vadjustment()
        old_upper = v_adj.get_upper()
        should_scroll_now = False
        if old_upper > 0:
            is_currently_at_bottom = v_adj.get_value() + v_adj.get_page_size() >= old_upper - 5
            should_scroll_now = self.was_at_bottom
        else:
            self.was_at_bottom = True
            should_scroll_now = True
        self._update_drawing_area_size(scroll_if_needed=should_scroll_now)
    # 逻辑px }

    def _scroll_to_top(self, button):
        v_adj = self.scrolled_window.get_vadjustment()
        v_adj.set_value(v_adj.get_lower())

    def _scroll_to_bottom(self, button):
        v_adj = self.scrolled_window.get_vadjustment()
        target_value = v_adj.get_upper() - v_adj.get_page_size()
        v_adj.set_value(max(v_adj.get_lower(), target_value))

    def _get_selection_absolute_bounds(self):
        if self.selection_absolute_start_y is None or self.selection_absolute_end_y is None:
            return None, None
        y1_model = min(self.selection_absolute_start_y, self.selection_absolute_end_y)
        y2_model = max(self.selection_absolute_start_y, self.selection_absolute_end_y)
        return y1_model, y2_model

    def _update_button_sensitivity(self, adjustment=None):
        y1_abs, y2_abs = self._get_selection_absolute_bounds() # 缓冲区px
        has_valid_selection = y1_abs is not None and y2_abs is not None and abs(y1_abs - y2_abs) > 1e-5
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
        self.btn_zoom_in.set_sensitive(self.zoom_level < self.MAX_ZOOM)
        self.btn_zoom_out.set_sensitive(self.zoom_level > self.MIN_ZOOM)
        self.btn_zoom_reset.set_sensitive(abs(self.effective_scale_factor - 1.0) > 1e-5)

    def _update_drawing_area_size(self, scroll_if_needed=False):
        """根据模型数据、缩放级别和视口大小计算绘制区域尺寸和缩放因子"""
        monitor_scale = self.parent_overlay.scale
        # 缓冲区px
        image_width = self.model.image_width
        virtual_height = self.model.total_virtual_height
        if image_width <= 0 or virtual_height <= 0:
            # 逻辑px
            viewport_width = self.scrolled_window.get_allocated_width()
            viewport_height = self.scrolled_window.get_allocated_height()
            if viewport_width <= 0:
                 viewport_width, viewport_height = self.get_default_size()
            self.drawing_area_width = max(1, viewport_width)
            self.drawing_area_height = max(1, viewport_height)
            if self.manual_zoom_active:
                self.effective_scale_factor = self.zoom_level
            else:
                self.effective_scale_factor = 1.0
            self.center_vertically = True
            self.display_total_height = self.drawing_area_height
        else:
            # 缓冲区px -> 逻辑px
            logical_img_w = image_width / monitor_scale
            logical_img_h = virtual_height / monitor_scale
            viewport_width = self.scrolled_window.get_allocated_width()
            viewport_height = self.scrolled_window.get_allocated_height()
            if viewport_width <= 0:
                viewport_width, _ = self.get_default_size()
            auto_scale_factor = 1.0
            if logical_img_w > viewport_width and viewport_width > 0:
                auto_scale_factor = viewport_width / logical_img_w
            if self.manual_zoom_active:
                self.effective_scale_factor = self.zoom_level
            else:
                self.effective_scale_factor = auto_scale_factor
            self.drawing_area_width = math.ceil(logical_img_w * self.effective_scale_factor)
            self.drawing_area_height = math.ceil(logical_img_h * self.effective_scale_factor)
            self.center_vertically = self.drawing_area_height < viewport_height
            self.display_total_height = self.drawing_area_height
        self.initial_y_offset = (viewport_height - self.drawing_area_height) / 2 if self.center_vertically else 0
        self.initial_y_offset = max(0, self.initial_y_offset)
        GLib.idle_add(self._update_zoom_label)
        self.drawing_area.set_size_request(self.drawing_area_width, self.drawing_area_height)
        self.drawing_area.queue_draw()
        GLib.idle_add(self._update_button_sensitivity)
        if scroll_if_needed and self.drawing_area_height > 0 and not self.is_dragging:
            GLib.idle_add(self._scroll_to_bottom_if_needed)

    def _scroll_to_bottom_if_needed(self):
        """检查并滚动到 Adjustment 的底部"""
        # 逻辑px
        v_adj = self.scrolled_window.get_vadjustment()
        if not v_adj:
             return GLib.SOURCE_REMOVE
        new_upper = v_adj.get_upper()
        page_size = v_adj.get_page_size()
        if new_upper > page_size:
             target_value = new_upper - page_size
             current_value = v_adj.get_value()
             if abs(current_value - target_value) > 1:
                  v_adj.set_value(target_value)
                  self.was_at_bottom = True
        else:
             self.was_at_bottom = True
        return GLib.SOURCE_REMOVE

    def on_scroll_changed(self, adjustment):
        is_now_at_bottom = adjustment.get_value() + adjustment.get_page_size() >= adjustment.get_upper() - 5
        if self.was_at_bottom and not is_now_at_bottom:
             self.was_at_bottom = False
        elif not self.was_at_bottom and is_now_at_bottom:
             self.was_at_bottom = True

    def _drawing_y_to_render_y(self, drawing_y):
        if self.effective_scale_factor == 0: return 0
        monitor_scale = self.parent_overlay.scale
        return ((drawing_y - self.initial_y_offset) / self.effective_scale_factor) * monitor_scale # 缩放后的逻辑px -> 逻辑px -> 缓冲区px

    def _absolute_y_to_render_y(self, absolute_y):
        # 缓冲区px
        if not self.model.render_plan:
            return absolute_y
        for piece in self.model.render_plan:
            if piece['absolute_y_start'] <= absolute_y < piece['absolute_y_end']:
                offset = absolute_y - piece['absolute_y_start']
                return piece['render_y_start'] + offset
        for piece in self.model.render_plan:
            if piece['absolute_y_start'] > absolute_y:
                return piece['render_y_start']
        if self.model.render_plan:
            last = self.model.render_plan[-1]
            return last['render_y_start'] + last['height']
        return 0

    def _render_y_to_absolute_y(self, render_y):
        # 缓冲区px
        if not self.model.render_plan:
            return render_y
        piece_render_y_starts = [p['render_y_start'] for p in self.model.render_plan]
        index = bisect.bisect_right(piece_render_y_starts, render_y) - 1
        index = max(0, index)
        if index >= len(self.model.render_plan):
            if self.model.render_plan:
                last_piece = self.model.render_plan[-1]
                offset = render_y - last_piece['render_y_start']
                return last_piece['absolute_y_start'] + offset
            else:
                return render_y
        piece = self.model.render_plan[index]
        if render_y >= piece['render_y_start'] + piece['height']:
            if index + 1 < len(self.model.render_plan):
                next_piece = self.model.render_plan[index + 1]
                return next_piece['absolute_y_start']
            else:
                return piece['absolute_y_end']
        offset_in_render_piece = render_y - piece['render_y_start']
        absolute_y = piece['absolute_y_start'] + offset_in_render_piece
        return absolute_y

    def _get_hovered_resize_handle(self, y):
        # 缓冲区px
        render_plan_y_at_mouse = self._drawing_y_to_render_y(y)
        if self.is_drawing_selection or self.effective_scale_factor == 0:
            return None
        monitor_scale = self.parent_overlay.scale
        y_top_orig, y_bottom_orig = self._get_selection_absolute_bounds()
        if y_top_orig is None:
            return None
        y_top_render = self._absolute_y_to_render_y(y_top_orig)
        y_bottom_render = self._absolute_y_to_render_y(y_bottom_orig)
        handle_size_render_space = (self.resize_handle_size * monitor_scale) / self.effective_scale_factor # 缩放后的逻辑px -> 逻辑px -> 缓冲区px
        if abs(render_plan_y_at_mouse - y_top_render) < handle_size_render_space:
            return 'top'
        if abs(render_plan_y_at_mouse - y_bottom_render) < handle_size_render_space:
            return 'bottom'
        return None

    def _on_start_selection_mode(self, button):
        if self.is_selection_mode:
            return
        self.is_selection_mode = True
        if self.is_dragging:
            self.is_dragging = False
            self.drawing_area.get_window().set_cursor(self.cursors['default'])
        self.drawing_area.get_window().set_cursor(self.cursors['crosshair'])
        self._update_button_sensitivity()
        self.drawing_area.queue_draw()

    def _on_cancel_selection_mode(self, button):
        if not self.is_selection_mode:
            return
        self.is_selection_mode = False
        self.selection_absolute_start_y = None
        self.selection_absolute_end_y = None
        self.is_drawing_selection = False
        self.is_resizing_selection = None
        if self.selection_autoscroll_timer:
            GLib.source_remove(self.selection_autoscroll_timer)
            self.selection_autoscroll_timer = None
        self.selection_autoscroll_velocity = 0.0
        self.drawing_area.get_window().set_cursor(self.cursors['default'])
        self._update_button_sensitivity()
        self.drawing_area.queue_draw()

    def _on_delete_clicked(self, button):
        """处理删除按钮点击事件"""
        # 缓冲区px
        y1_abs, y2_abs = self._get_selection_absolute_bounds()
        if y1_abs is None or y2_abs is None or abs(y1_abs - y2_abs) < 1e-5:
            logging.warning("_on_delete_clicked: 选区无效，不执行任何操作")
            return
        mod = {
            'type': 'delete',
            'y_start_abs': min(y1_abs, y2_abs),
            'y_end_abs': max(y1_abs, y2_abs)
        }
        if self.model.modifications and self.model.modifications[-1] == mod:
            logging.info("StitchModel: 跳过添加重复的 'delete' 修改")
            return
        self.model.add_modification(mod)
        self.drawing_area.queue_draw()

    def _on_restore_clicked(self, button):
        # 缓冲区px
        """处理恢复按钮点击事件"""
        y1_abs, y2_abs = self._get_selection_absolute_bounds()
        if y1_abs is None or y2_abs is None or abs(y1_abs - y2_abs) < 1e-5:
            logging.warning("_on_restore_clicked: 选区无效，不执行任何操作")
            return
        selection_start_abs = min(y1_abs, y2_abs)
        selection_end_abs = max(y1_abs, y2_abs)
        mods_added = 0
        restored_seams_indices = {mod['seam_index'] for mod in self.model.modifications if mod['type'] == 'restore'}
        for i, abs_piece in enumerate(self.model.absolute_plan[:-1]):
            original_overlap = abs_piece['overlap_with_next']
            if original_overlap == 0:
                continue
            next_piece = self.model.absolute_plan[i+1]
            seam_start_abs = next_piece['absolute_y_start']
            seam_end_abs = next_piece['absolute_y_start'] + original_overlap
            if (seam_start_abs >= selection_start_abs) and (seam_start_abs < selection_end_abs):
                if i in restored_seams_indices:
                    logging.info(f"接缝 {i} (a-y {seam_start_abs:.0f}) 已被恢复，跳过重复添加。")
                    continue
                seam_deleted = False
                for mod in self.model.modifications:
                    if mod['type'] == 'delete':
                        if mod['y_start_abs'] <= seam_start_abs and mod['y_end_abs'] > seam_start_abs:
                            seam_deleted = True
                            break
                if seam_deleted:
                    logging.info(f"接缝 {i} (a-y {seam_start_abs:.0f}) 在选区内，但已被删除，跳过恢复。")
                    continue
                logging.info(f"选区 ({selection_start_abs:.0f}, {selection_end_abs:.0f}) 触碰了接缝 {i} (a-y {seam_start_abs:.0f})。添加恢复修改。")
                mod = {
                    'type': 'restore',
                    'seam_index': i,
                    'original_overlap': original_overlap
                }
                self.model.add_modification(mod)
                mods_added += 1
        if mods_added > 0:
            logging.info(f"已为 {mods_added} 个接缝添加了恢复操作")
        else:
            logging.info("选区内未发现可恢复的接缝")

    def _on_undo_mod_clicked(self, button):
        self.model.undo()

    def _on_redo_mod_clicked(self, button):
        self.model.redo()

    def _on_modification_stack_changed(self, model):
        self._update_button_sensitivity()

    def on_draw(self, widget, cr):
         """绘制 DrawingArea 的内容"""
         # 逻辑px
         widget_width = widget.get_allocated_width()
         widget_height = widget.get_allocated_height()
         cr.set_source_rgb(0.1, 0.1, 0.1)
         cr.paint()
         if not self.model.render_plan:
             cr.set_source_rgb(0.8, 0.8, 0.8)
             layout = PangoCairo.create_layout(cr)
             font_desc = Pango.FontDescription("Sans 24")
             layout.set_font_description(font_desc)
             layout.set_text("暂无截图", -1)
             text_width, text_height = layout.get_pixel_size()
             x = (widget_width - text_width) / 2
             y = (widget_height - text_height) / 2
             cr.move_to(x, y)
             PangoCairo.show_layout(cr, layout)
             return
         monitor_scale = self.parent_overlay.scale
         zoom_level = self.effective_scale_factor
         draw_area_w = self.drawing_area_width
         draw_area_h = self.drawing_area_height
         draw_x_offset = (widget_width - draw_area_w) / 2 if widget_width > draw_area_w else 0
         draw_y_offset = (widget_height - draw_area_h) / 2 if self.center_vertically else 0
         draw_y_offset = max(0, draw_y_offset)
         cr.translate(draw_x_offset, draw_y_offset)
         final_scale = zoom_level / monitor_scale
         cr.scale(final_scale, final_scale) # 缓冲区px -> 逻辑px -> 缩放后的逻辑px
         # 缓冲区px
         clip_x1, visible_y1_widget, clip_x2, visible_y2_widget = cr.clip_extents()
         visible_y1_model, visible_y2_model = cr.clip_extents()[1::2]
         model_y_positions = [p['render_y_start'] for p in self.model.render_plan]
         first_index = max(0, bisect.bisect_right(model_y_positions, visible_y1_model) - 1)
         drawn_count = 0
         for i in range(first_index, len(self.model.render_plan)):
             piece = self.model.render_plan[i]
             filepath = piece.get('filepath')
             src_y = piece.get('src_y', 0)
             src_height = piece.get('height', 0)
             dest_y = model_y_positions[i]
             dest_h = src_height
             if dest_y >= visible_y2_model:
                 break
             if dest_y + dest_h <= visible_y1_model:
                 continue
             pixbuf = self.model._get_cached_pixbuf(filepath)
             if not pixbuf:
                 logging.error(f"无法为 {Path(filepath).name} 获取 Pixbuf")
                 continue
             original_width = pixbuf.get_width()
             try:
                 cr.save()
                 cr.translate(0, dest_y)
                 Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, -src_y)
                 cr.rectangle(0, 0, original_width, src_height)
                 cr.clip()
                 cr.paint()
                 cr.restore()
                 drawn_count += 1
             except Exception as e:
                 logging.error(f"绘制 Pixbuf {Path(filepath).name} (src_y={src_y}) 时出错: {e}")
                 try: cr.restore()
                 except cairo.Error: pass
         # 如果在选择模式下，绘制蒙版和选框
         current_scale = final_scale
         if self.is_selection_mode:
             cr.set_source_rgba(0.1, 0.1, 0.1, 0.6)
             total_draw_y_start = 0
             total_draw_height = self.model.total_virtual_height
             total_draw_x_start = 0
             total_draw_width = self.model.image_width
             sel_y1_orig, sel_y2_orig = self._get_selection_absolute_bounds()
             if sel_y1_orig is None:
                  cr.rectangle(total_draw_x_start, total_draw_y_start, total_draw_width, total_draw_height)
                  cr.fill()
             else:
                 sel_y1_render = self._absolute_y_to_render_y(sel_y1_orig)
                 sel_y2_render = self._absolute_y_to_render_y(sel_y2_orig)
                 sel_h_render = abs(sel_y2_render - sel_y1_render)
                 height_above = max(0, min(sel_y1_render, sel_y2_render) - total_draw_y_start)
                 if height_above > 0.1:
                     cr.rectangle(total_draw_x_start, total_draw_y_start, total_draw_width, height_above)
                     cr.fill()
                 height_below = max(0, (total_draw_y_start + total_draw_height) - max(sel_y1_render, sel_y2_render))
                 if height_below > 0.1:
                     cr.rectangle(total_draw_x_start, max(sel_y1_render, sel_y2_render), total_draw_width, height_below)
                     cr.fill()
                 if sel_h_render > 0.1:
                     cr.set_line_width(2.0 / current_scale) # 缩放后的逻辑px -> 逻辑px -> 缓冲区px
                     if self.is_drawing_selection:
                         cr.set_source_rgba(1.0, 1.0, 1.0, 0.9)
                         cr.set_dash([6.0 / current_scale, 4.0 / current_scale])
                     else:
                         cr.set_source_rgba(0.9, 0.9, 0.9, 0.8)
                         cr.set_dash([])
                     cr.rectangle(total_draw_x_start, min(sel_y1_render, sel_y2_render), total_draw_width, sel_h_render)
                     cr.stroke()
                     cr.set_dash([])
                 elif sel_h_render < 0.1 and not self.is_drawing_selection and not self.is_resizing_selection:
                     cr.set_line_width(3.0 / current_scale)
                     cr.set_source_rgba(1.0, 0.1, 0.1, 0.7)
                     cr.set_dash([8.0 / current_scale, 6.0 / current_scale])
                     seam_y_render = sel_y1_render
                     cr.move_to(total_draw_x_start, seam_y_render)
                     cr.line_to(total_draw_x_start + total_draw_width, seam_y_render)
                     cr.stroke()
                     cr.set_dash([])
                 selection_start_abs = min(sel_y1_orig, sel_y2_orig)
                 selection_end_abs = max(sel_y1_orig, sel_y2_orig)
                 delete_regions = [
                     (mod['y_start_abs'], mod['y_end_abs']) 
                     for mod in self.model.modifications 
                     if mod['type'] == 'delete'
                 ]
                 restored_seam_indices = {
                     mod['seam_index'] 
                     for mod in self.model.modifications 
                     if mod['type'] == 'restore'
                 }
                 cr.set_line_width(3.0 / current_scale)
                 cr.set_dash([8.0 / current_scale, 6.0 / current_scale])
                 total_draw_width = self.model.image_width
                 total_draw_x_start = 0
                 for i, abs_piece in enumerate(self.model.absolute_plan[:-1]):
                     seam_start_abs = self.model.absolute_plan[i+1]['absolute_y_start']
                     if (seam_start_abs >= selection_start_abs) and (seam_start_abs < selection_end_abs):
                         is_deleted = False
                         for del_start, del_end in delete_regions:
                             if del_start <= seam_start_abs < del_end:
                                 is_deleted = True
                                 break
                         if is_deleted:
                             continue
                         original_overlap = abs_piece['overlap_with_next']
                         is_restored = (i in restored_seam_indices)
                         is_originally_seamless = (original_overlap <= 1e-5)
                         if is_originally_seamless or is_restored:
                             cr.set_source_rgba(0.7, 0.3, 0.8, 0.8) 
                         else:
                             cr.set_source_rgba(0.2, 0.5, 1.0, 0.8)
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
                absolute_model_y = self._render_y_to_absolute_y(render_plan_y)
                handle = self._get_hovered_resize_handle(event.y)
                if handle:
                    self.is_resizing_selection = handle
                    self.is_drawing_selection = False
                    y1_abs, y2_abs = self._get_selection_absolute_bounds()
                    if handle == 'top':
                        self.selection_absolute_start_y = absolute_model_y
                        self.selection_absolute_end_y = y2_abs
                    else:
                        self.selection_absolute_start_y = y1_abs
                        self.selection_absolute_end_y = absolute_model_y
                    cursor_name = 'n-resize' if handle == 'top' else 's-resize'
                    self.drawing_area.get_window().set_cursor(self.cursors[cursor_name])
                    logging.info(f"开始调整选区手柄: {handle}")
                else:
                    self.is_drawing_selection = True
                    self.is_resizing_selection = None
                    self.selection_absolute_start_y = absolute_model_y
                    self.selection_absolute_end_y = absolute_model_y
                    self.drawing_area.get_window().set_cursor(self.cursors['grabbing'])
                    self.drawing_area.queue_draw()
                return True
            # 逻辑px
            hadj = self.scrolled_window.get_hadjustment()
            vadj = self.scrolled_window.get_vadjustment()
            can_scroll_h = hadj and hadj.get_upper() > hadj.get_page_size()
            can_scroll_v = vadj and vadj.get_upper() > vadj.get_page_size()
            if can_scroll_h or can_scroll_v:
                self.is_dragging = True
                win_x, win_y = widget.translate_coordinates(self.parent_overlay, event.x, event.y)
                self.drag_start_x = win_x
                self.drag_start_y = win_y
                self.drag_start_hadj_value = hadj.get_value() if hadj else 0
                self.drag_start_vadj_value = vadj.get_value() if vadj else 0
                self.drawing_area.get_window().set_cursor(self.cursors['grab'])
                return True
        return False

    def _check_and_trigger_autoscroll(self, event):
        # 逻辑px
        if not (self.is_drawing_selection or self.is_resizing_selection):
            if self.selection_autoscroll_timer:
                GLib.source_remove(self.selection_autoscroll_timer)
                self.selection_autoscroll_timer = None
            self.selection_autoscroll_velocity = 0.0
            return
        vadj = self.scrolled_window.get_vadjustment()
        if not vadj:
            return
        viewport_y = vadj.get_value()
        viewport_h = vadj.get_page_size()
        viewport_bottom = viewport_y + viewport_h
        mouse_y_in_drawing_area = event.y 
        velocity = 0.0
        if mouse_y_in_drawing_area < viewport_y:
            diff = viewport_y - mouse_y_in_drawing_area
            velocity = -(diff * self.AUTOSCROLL_SENSITIVITY)
        elif mouse_y_in_drawing_area > viewport_bottom:
            diff = mouse_y_in_drawing_area - viewport_bottom
            velocity = diff * self.AUTOSCROLL_SENSITIVITY
        current_val = vadj.get_value()
        max_val = vadj.get_upper() - vadj.get_page_size()
        min_val = vadj.get_lower()
        if (velocity > 0 and current_val >= max_val - 1.0) or \
           (velocity < 0 and current_val <= min_val + 1.0):
            velocity = 0.0
        self.selection_autoscroll_velocity = velocity
        should_run = abs(self.selection_autoscroll_velocity) > 0.1
        if should_run and self.selection_autoscroll_timer is None:
            logging.info(f"启动自动滚动定时器")
            self.selection_autoscroll_timer = GLib.timeout_add(
                self.AUTOSCROLL_INTERVAL, 
                self._auto_scroll_selection
            )
        elif not should_run and self.selection_autoscroll_timer is not None:
            logging.info("速度归零，停止自动滚动定时器")
            GLib.source_remove(self.selection_autoscroll_timer)
            self.selection_autoscroll_timer = None

    def _on_drawing_area_motion_notify(self, widget, event):
        def get_clamped_y(raw_y):
            y_offset = self.initial_y_offset
            min_y = y_offset
            max_y = y_offset + self.display_total_height
            return max(min_y, min(raw_y, max_y))
        if self.is_resizing_selection:
            clamped_drawing_y = get_clamped_y(event.y) # 逻辑px
            # 缓冲区px
            render_plan_y = self._drawing_y_to_render_y(clamped_drawing_y)
            absolute_model_y = self._render_y_to_absolute_y(render_plan_y)
            if self.is_resizing_selection == 'top':
                self.selection_absolute_start_y = absolute_model_y
            else:
                self.selection_absolute_end_y = absolute_model_y
            self._check_and_trigger_autoscroll(event)
            GLib.idle_add(self.drawing_area.queue_draw)
            return True
        if self.is_drawing_selection:
            clamped_drawing_y = get_clamped_y(event.y)
            render_plan_y = self._drawing_y_to_render_y(clamped_drawing_y)
            absolute_model_y = self._render_y_to_absolute_y(render_plan_y)
            self.selection_absolute_end_y = absolute_model_y
            self._check_and_trigger_autoscroll(event)
            GLib.idle_add(self.drawing_area.queue_draw)
            return True
        if self.is_selection_mode:
            handle = self._get_hovered_resize_handle(event.y)
            if handle == 'top':
                self.drawing_area.get_window().set_cursor(self.cursors['n-resize'])
            elif handle == 'bottom':
                self.drawing_area.get_window().set_cursor(self.cursors['s-resize'])
            else:
                self.drawing_area.get_window().set_cursor(self.cursors['crosshair'])
            return True
        if self.is_dragging:
            # 逻辑px
            hadj = self.scrolled_window.get_hadjustment()
            vadj = self.scrolled_window.get_vadjustment()
            current_hadj_before = hadj.get_value() if hadj else 0
            current_vadj_before = vadj.get_value() if vadj else 0
            drag_sensitivity = self.config.PREVIEW_DRAG_SENSITIVITY
            win_x, win_y = widget.translate_coordinates(self.parent_overlay, event.x, event.y)
            delta_x = win_x - self.drag_start_x
            delta_y = win_y - self.drag_start_y
            if hadj:
                new_h_value = self.drag_start_hadj_value - (delta_x * drag_sensitivity)
                new_h_value_clamped = max(hadj.get_lower(), min(new_h_value, hadj.get_upper() - hadj.get_page_size()))
                hadj.set_value(new_h_value_clamped)
            if vadj:
                new_v_value = self.drag_start_vadj_value - (delta_y * drag_sensitivity)
                new_v_value_clamped = max(vadj.get_lower(), min(new_v_value, vadj.get_upper() - vadj.get_page_size()))
                vadj.set_value(new_v_value_clamped)
            actual_h_after = hadj.get_value() if hadj else 0
            actual_v_after = vadj.get_value() if vadj else 0
            self.drawing_area.get_window().set_cursor(self.cursors['grabbing'])
            return True
        return False

    def _on_drawing_area_button_release(self, widget, event):
        if event.button == 1:
            if self.selection_autoscroll_timer:
                GLib.source_remove(self.selection_autoscroll_timer)
                self.selection_autoscroll_timer = None
                logging.info("鼠标释放，停止自动滚动")
            self.selection_autoscroll_velocity = 0.0
            if self.is_drawing_selection:
                self.is_drawing_selection = False
                # 缓冲区px
                y1_abs, y2_abs = self._get_selection_absolute_bounds()
                if y1_abs is not None and y2_abs is not None and abs(y1_abs - y2_abs) < 1e-5:
                        self.selection_absolute_start_y = None
                        self.selection_absolute_end_y = None
                self.drawing_area.get_window().set_cursor(self.cursors['crosshair'])
                self._update_button_sensitivity()
                GLib.idle_add(self.drawing_area.queue_draw)
                return True
            if self.is_resizing_selection:
                self.is_resizing_selection = None
                self.drawing_area.get_window().set_cursor(self.cursors['crosshair'])
                self._update_button_sensitivity()
                GLib.idle_add(self.drawing_area.queue_draw)
                return True
            if self.is_dragging:
                self.is_dragging = False
                self.drawing_area.get_window().set_cursor(self.cursors['default'])
                return True
        return False

    def _auto_scroll_selection(self):
        """定时器回调，用于在选择时自动滚动视口"""
        if not (self.is_drawing_selection or self.is_resizing_selection):
            self.selection_autoscroll_timer = None
            self.selection_autoscroll_velocity = 0.0
            logging.warning("_auto_scroll_selection 触发，但已不在选择/调整大小状态")
            return False
        if abs(self.selection_autoscroll_velocity) < 0.1:
            self.selection_autoscroll_timer = None
            logging.warning("_auto_scroll_selection 触发，但滚动速度接近 0")
            return False
        # 逻辑px
        vadj = self.scrolled_window.get_vadjustment()
        current_value = vadj.get_value()
        step = self.selection_autoscroll_velocity
        new_value = current_value + step
        lower = vadj.get_lower()
        upper = vadj.get_upper() - vadj.get_page_size()
        if new_value < lower:
            new_value_clamped = lower
        elif new_value > upper:
            new_value_clamped = upper
        else:
            new_value_clamped = new_value
        actual_scroll_amount_drawing = new_value_clamped - current_value
        if abs(actual_scroll_amount_drawing) > 1e-3:
            vadj.set_value(new_value_clamped)
            mouse_offset = self.selection_autoscroll_velocity / self.AUTOSCROLL_SENSITIVITY
            base_y = vadj.get_value() + vadj.get_page_size() if self.selection_autoscroll_velocity > 0 else vadj.get_value()
            new_drawing_y = base_y + mouse_offset
            # 缓冲区px
            new_render_y = self._drawing_y_to_render_y(new_drawing_y)
            new_absolute_y = self._render_y_to_absolute_y(new_render_y)
            if self.is_resizing_selection == 'top':
                self.selection_absolute_start_y = new_absolute_y
            elif self.is_resizing_selection == 'bottom':
                self.selection_absolute_end_y = new_absolute_y
            elif self.is_drawing_selection:
                self.selection_absolute_end_y = new_absolute_y
            self.drawing_area.queue_draw()
        if abs(new_value_clamped - new_value) > 1e-3:
            logging.info("自动滚动到达边缘，定时器停止")
            self.selection_autoscroll_timer = None
            self.selection_autoscroll_velocity = 0.0
            return False
        return True

class CaptureOverlay(Gtk.Window):
    def __init__(self, config: Config, window_manager: WindowManagerBase, frame_grabber: FrameGrabberBase):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        global GLOBAL_OVERLAY
        GLOBAL_OVERLAY = self
        self.fixed_container = Gtk.Fixed()
        self.add(self.fixed_container)
        self.fixed_container.show()
        self.session = CaptureSession()
        self.controller = ActionController(self.session, self, config)
        # 逻辑px
        self.monitor_offset_x = 0
        self.monitor_offset_y = 0
        self.is_selection_done = False
        self.is_finished = False
        self.is_calibration_done = False
        self._initial_grab_done = False
        self.start_x_rel = 0 # 窗口坐标
        self.start_y_rel = 0
        self.current_x_rel = 0
        self.current_y_rel = 0
        self.is_dragging_selection = False
        self.preview_panel = None
        self.config_panel = None
        self.stitch_model = self.controller.stitch_model
        self.stitch_model.connect('model-updated', self.on_model_updated_ui)
        self.evdev_wheel_scroller = None
        self.invisible_scroller = None
        self.screen_rect = None # 全局坐标
        self.scale = self.get_scale_factor()
        self.is_dialog_open = False
        self.show_side_panel = True
        self.show_button_panel = True
        self.side_panel_on_left = True
        self.current_notification_widget = None
        self.notification_timeout_id = None
        self.instruction_panel = None
        self._instr_panel_natural_h = 0
        self._instr_panel_natural_w = 0
        self.user_wants_instruction_panel = config.SHOW_INSTRUCTION_PANEL_ON_START
        WINDOW_MANAGER.setup_overlay_window(self)
        self.apply_global_styles()
        self.create_panels()
        self._setup_overlay_mask()
        self.side_panel.hide()
        self.button_panel.hide()
        self._initialize_cursors()
        self._connect_events()
        logging.info(f"GTK 覆盖层已创建")

    def _setup_overlay_mask(self):
        """创建一个全屏半透明遮罩层，用于模拟模态对话框"""
        self.overlay_mask = Gtk.EventBox()
        self.overlay_mask.set_visible_window(False)
        self.overlay_mask.set_app_paintable(True)
        self.overlay_mask.get_style_context().add_class("mask-layer")
        self.overlay_mask.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK)
        self.overlay_mask.connect("button-press-event", lambda w, e: True) 
        self.overlay_mask.connect("draw", self._on_draw_overlay_mask)
        self.overlay_mask.set_visible(False)
        self.fixed_container.put(self.overlay_mask, 0, 0) # 窗口坐标

    def get_all_monitors_geometry(self):
        """获取所有显示器的合并全局几何范围 (x_min, y_min, x_max, y_max)"""
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

    def window_to_monitor(self, wx, wy):
        """窗口坐标 -> 显示器坐标"""
        # 逻辑px
        return wx + self.monitor_offset_x, wy + self.monitor_offset_y

    def window_to_global(self, wx, wy):
        """窗口坐标 -> 全局坐标"""
        # 逻辑px
        mx, my = self.window_to_monitor(wx, wy)
        if self.screen_rect:
            return mx + self.screen_rect.x, my + self.screen_rect.y
        return mx, my

    def _on_draw_overlay_mask(self, widget, cr):
        """手动绘制半透明遮罩"""
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.6)
        cr.set_operator(cairo.OPERATOR_OVER)
        cr.paint()
        return True

    def _initialize_cursors(self):
        """一次性创建所有需要的光标并缓存"""
        display = self.get_display()
        cursor_names = [
            'default', 'n-resize', 's-resize', 'w-resize', 'e-resize',
            'nw-resize', 'se-resize', 'ne-resize', 'sw-resize',
            'grab', 'grabbing', "crosshair"
        ]
        self.cursors = {
            name: Gdk.Cursor.new_from_name(display, name) for name in cursor_names
        }
        surface = cairo.ImageSurface(cairo.Format.ARGB32, 1, 1)
        self.cursors['blank'] = Gdk.Cursor.new_from_surface(display, surface, 0, 0)

    def show_embedded_notification(self, title, message, level, action_path, controller=None, width=0, height=0):
        """在窗口顶部中间显示内嵌通知"""
        # 窗口坐标
        current_alloc = self.get_allocation()
        if not self.screen_rect and current_alloc.width <= 1:
            logging.info(f"窗口尚未布局 (width={current_alloc.width})，推迟显示通知: {title}")
            GLib.timeout_add(300, self.show_embedded_notification, title, message, level, action_path, controller, width, height)
            return
        if self.current_notification_widget:
            self.dismiss_notification(self.current_notification_widget, trigger_cleanup=False)
        panel = EmbeddedNotificationPanel(self, title, message, level, action_path, width, height)
        panel.controller_ref = controller
        self.fixed_container.put(panel, 0, 0)
        panel.show_all()
        _, nat_size = panel.get_preferred_size()
        panel_w, panel_h = nat_size.width, nat_size.height
        win_w = panel_w
        if self.screen_rect:
            win_w, _ = self.screen_rect.width, self.screen_rect.height
        x = (win_w - panel_w) // 2
        y = 40
        self.fixed_container.move(panel, x, y)
        self.current_notification_widget = panel
        timeout_sec = 8 if level == "warning" or level == "success" else 3
        if level == "critical": timeout_sec = 0
        if timeout_sec > 0:
            self.notification_timeout_id = GLib.timeout_add_seconds(timeout_sec, lambda: self.dismiss_notification(panel))
        self._update_input_shape()

    def enter_notification_mode(self):
        """仅显示通知：隐藏所有面板，停止绘制选区"""
        logging.info("进入通知驻留模式")
        self.is_finished = True
        self.side_panel.hide()
        self.button_panel.hide()
        self.instruction_panel.hide()
        if hasattr(self, 'overlay_mask'):
            self.overlay_mask.hide()
        if self.preview_panel: self.preview_panel.hide()
        if self.config_panel: self.config_panel.hide()
        self.queue_draw()
        self._update_input_shape()

    def dismiss_notification(self, widget, trigger_cleanup=True):
        if self.notification_timeout_id:
            GLib.source_remove(self.notification_timeout_id)
            self.notification_timeout_id = None
        if widget:
            widget.destroy()
        if self.current_notification_widget == widget:
            self.current_notification_widget = None
            if self.get_window():
                if not self.is_selection_done:
                    self.get_window().set_cursor(self.cursors['crosshair'])
                else:
                    self.get_window().set_cursor(None)
            if trigger_cleanup and hasattr(widget, 'controller_ref') and widget.controller_ref:
                if self.is_finished:
                    logging.info("通知关闭且处于完成状态，触发最终退出")
                    widget.controller_ref._perform_cleanup()

    def _connect_events(self):
        """连接所有Gtk信号和事件"""
        self.connect("map-event", self.on_map_event)
        self.connect("draw", self.on_draw)
        self.connect("size-allocate", self.on_size_allocate)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK |
                        Gdk.EventMask.BUTTON_RELEASE_MASK |
                        Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect("button-press-event", self.on_button_press)
        self.connect("button-release-event", self.on_button_release)
        self.connect("motion-notify-event", self.on_motion_notify)
        self.connect("key-press-event", self.on_key_press_event)
        self.connect("key-release-event", self.on_key_release_event)

    def apply_global_styles(self):
        """应用所有全局 CSS 样式"""
        screen = Gdk.Screen.get_default()
        priority = Gtk.STYLE_PROVIDER_PRIORITY_USER
        def load_css(css_data, name):
            if not css_data:
                return
            try:
                provider = Gtk.CssProvider()
                provider.load_from_data(css_data.encode('utf-8'))
                Gtk.StyleContext.add_provider_for_screen(screen, provider, priority)
            except Exception as e:
                logging.error(f"应用 {name} CSS 失败: {e}")
        load_css(config.NOTIFICATION_CSS, "Notification")
        load_css(config.DIALOG_CSS, "Dialog")
        load_css(config.MASK_CSS, "Mask")
        load_css(config.PROCESSING_DIALOG_CSS, "Processing Dialog")
        load_css(config.INFO_PANEL_CSS, "Info Panel")
        load_css(config.INSTRUCTION_PANEL_CSS, "Instruction Panel")
        load_css(config.SIMULATED_WINDOW_CSS, "Simulated Window")
        button_active_css = """
        button.force-active-style {
            color: @theme_fg_color;
            background-image: image(@theme_bg_color);
            border-color: @borders;
            text-shadow: none;
            -gtk-icon-shadow: none;
        }
        button.force-active-style:hover {
            background-image: image(shade(@theme_bg_color, 1.05));
            border-color: shade(@borders, 1.1);
        }
        button.force-active-style:active,
        button.force-active-style:checked {
            background-image: image(shade(@theme_bg_color, 0.95));
        }
        button.force-active-style:disabled {
            color: @insensitive_fg_color;
            background-image: image(@insensitive_bg_color);
            border-color: @insensitive_borders;
            opacity: 0.9;
            text-shadow: none;
            -gtk-icon-shadow: none;
        }
        """
        load_css(button_active_css, "Button Active Style")
        no_padding_css = """
        .no-padding { padding: 0px; }
        """
        load_css(no_padding_css, "No Padding Style")
        logging.info("已应用所有全局 CSS 样式")

    def on_key_press_event(self, widget, event):
        if self.is_dialog_open:
            self.on_dialog_key_press(widget, event)
            return True
        if self.preview_panel and self.preview_panel.get_visible():
            if self.preview_panel._on_key_press(widget, event):
                return True
        if self.config_panel and self.config_panel.get_visible():
            if self.config_panel.capturing_hotkey_button:
                return True
        if not self.is_selection_done:
            keyval = event.keyval
            if keyval == Gdk.KEY_Escape:
                logging.info("选择被 Esc 取消")
                Gdk.Display.get_default().get_default_seat().ungrab()
                self.destroy()
                return True
            state = event.state & config.GTK_MODIFIER_MASK
            if keyval in config.HOTKEY_TOGGLE_INSTRUCTION_PANEL['gtk_keys'] and \
               state == config.HOTKEY_TOGGLE_INSTRUCTION_PANEL['gtk_mask']:
                self.toggle_instruction_panel()
                return True
            return False
        else:
            return self.controller.handle_key_press(event)

    def on_key_release_event(self, widget, event):
        if self.config_panel and self.config_panel.get_visible():
            if self.config_panel.handle_key_release(widget, event):
                return True
        return False

    def _grab_for_selection(self):
        display = Gdk.Display.get_default()
        seat = display.get_default_seat()
        capabilities = (Gdk.SeatCapabilities.POINTER |
                        Gdk.SeatCapabilities.KEYBOARD)
        def _set_cursor_delayed():
            if self.get_window():
                self.get_window().set_cursor(self.cursors["crosshair"])
            return False
        GLib.timeout_add(250, _set_cursor_delayed)
        grab_status = seat.grab(
            self.get_window(), capabilities,
            True, None, None, None, None
        )
        if grab_status != Gdk.GrabStatus.SUCCESS:
            logging.error(f"无法抓取输入: {grab_status}")
            GLib.idle_add(self.controller.quit_and_cleanup)
        else:
            self.get_window().set_cursor(self.cursors["crosshair"])
            logging.info("窗口已映射，成功抓取输入用于区域选择")

    def on_map_event(self, widget, event):
        if self.screen_rect is None:
            logging.info("窗口首次映射，正在初始化屏幕几何信息...")
            # 逻辑px全局坐标
            self.screen_rect = WINDOW_MANAGER.get_screen_geometry(self)
            FRAME_GRABBER.set_global_offset(self.screen_rect.x, self.screen_rect.y)
            g_min_x, g_min_y, g_max_x, g_max_y = self.get_all_monitors_geometry()
            if IS_WAYLAND:
                logging.info("尝试通过视频流校准真实缩放比例...")
                wait_start = time.time()
                # 缓冲区px
                detected_buf_h = 0
                detected_buf_w = 0
                while time.time() - wait_start < 2.0:
                    with FRAME_GRABBER.frame_lock:
                        if FRAME_GRABBER.latest_frame is not None:
                            detected_buf_h, detected_buf_w, _ = FRAME_GRABBER.latest_frame.shape
                            break
                    time.sleep(0.05)
                if detected_buf_w > 0 and self.screen_rect.width > 0:
                    self.scale = detected_buf_w / self.screen_rect.width
                    logging.info(f"校准成功: 缓冲区px宽度 {detected_buf_w} / 逻辑px宽度 {self.screen_rect.width} = 真实Scale {self.scale:.6f}")
                else:
                    logging.warning("校准失败: 未能及时获取视频帧或宽度无效，保持默认 Scale")
            try:
                # 逻辑px -> 缓冲区px
                g_min_x_buf = math.ceil(g_min_x * self.scale)
                g_min_y_buf = math.ceil(g_min_y * self.scale)
                g_max_x_buf = int(g_max_x * self.scale)
                g_max_y_buf = int(g_max_y * self.scale)
                if IS_WAYLAND:
                    logging.info("Wayland 下 'invisible_cursor' 模式不可用")
                    self.invisible_scroller = None
                    if EVDEV_AVAILABLE:
                        self.evdev_wheel_scroller = EvdevWheelScroller()
                        if self.screen_rect.width > 0:
                            self.controller.scroll_manager.evdev_abs_mouse = EvdevAbsoluteMouse(g_min_x_buf, g_min_y_buf, g_max_x_buf, g_max_y_buf)
                        else:
                            logging.error("无法获取图片分辨率，Wayland 下鼠标移动功能将无法工作。")
                    else:
                        logging.error("Wayland 下 evdev 不可用，鼠标移动以及滚动功能将无法工作。")
                        self.evdev_wheel_scroller = None
                        self.controller.scroll_manager.evdev_abs_mouse = None
                else:
                    if EVDEV_AVAILABLE:
                        if config.SCROLL_METHOD == 'invisible_cursor':
                            park_x = self.screen_rect.x + self.screen_rect.width - 1 # 显示器坐标 -> 全局坐标
                            park_y = self.screen_rect.y + self.screen_rect.height - 1
                            # 逻辑px -> 缓冲区px
                            park_x_buf = int(park_x * self.scale)
                            park_y_buf = int(park_y * self.scale)
                            self.invisible_scroller = InvisibleCursorScroller(
                                g_min_x_buf, g_min_y_buf, g_max_x_buf, g_max_y_buf, park_x_buf, park_y_buf, config
                            )
                            self.invisible_scroller.setup()
                            logging.info("InvisibleCursorScroller.setup() 正在后台线程中执行")
                        else:
                            self.evdev_wheel_scroller = EvdevWheelScroller()
                    else:
                        logging.info("Evdev 未导入，X11 下将默认使用 XTest 进行滚动")
                        self.evdev_wheel_scroller = None
                        self.invisible_scroller = None
            except Exception as err:
                logging.error(f"创建虚拟滚动设备失败: {err}")
                send_desktop_notification(
                    "设备错误", f"无法创建虚拟设备: {err}，基于 evdev 的滚动功能将不可用", level="critical"
                )
                self.evdev_wheel_scroller = None
                self.invisible_scroller = None
            if not self.is_selection_done:
                self.resize(self.screen_rect.width, self.screen_rect.height)
                self.fixed_container.set_size_request(self.screen_rect.width, self.screen_rect.height)
                if not IS_WAYLAND:
                    self.move(self.screen_rect.x, self.screen_rect.y) # 全局坐标
        if not self.is_selection_done and not self._initial_grab_done:
            self.present_with_time(Gtk.get_current_event_time())
            self.user_wants_instruction_panel = config.SHOW_INSTRUCTION_PANEL_ON_START
            self.update_layout()
            self._grab_for_selection()
            self._initial_grab_done = True
            self._initiate_calibration()
                
    def _initiate_calibration(self):
        logging.info("初始化坐标校准程序...")
        cal_widget = CalibrationWidget()
        target_log_x = 30
        target_log_y = 30
        self.fixed_container.put(cal_widget, target_log_x, target_log_y) # 窗口坐标
        cal_widget.show()
        while Gtk.events_pending():
            Gtk.main_iteration()
        threading.Thread(target=self._run_calibration_thread, args=(cal_widget, target_log_x, target_log_y), daemon=True).start()

    def _run_calibration_thread(self, widget, log_x, log_y):
        # 显示器坐标
        time.sleep(0.6)
        full_img = None
        if IS_WAYLAND:
            with FRAME_GRABBER.frame_lock:
                if FRAME_GRABBER.latest_frame is not None:
                    full_img = FRAME_GRABBER.latest_frame.copy()
        else:
            temp_path = config.TMP_DIR / "cal_temp.png"
            rect = self.screen_rect
            event = threading.Event()
            success = [False]
            def main_thread_capture():
                try: success[0] = FRAME_GRABBER.capture(0, 0, rect.width, rect.height, temp_path, scale=1.0, include_cursor=False)
                except Exception as e:
                    logging.error(f"校准截图调用失败: {e}")
                finally: event.set()
                return False
            GLib.idle_add(main_thread_capture)
            event.wait()
            if success[0] and temp_path.exists():
                full_img = cv2.imread(str(temp_path))
                try: os.remove(temp_path)
                except: pass
        if full_img is None:
            logging.warning("校准失败: 无法获取屏幕帧")
            GLib.idle_add(widget.destroy)
            self.is_calibration_done = True
            return
        logic_w = 48 * widget.pixel_scale
        logic_h = 16 * widget.pixel_scale
        bitmap = widget.get_calibration_bitmap()
        bg_gray = int(0.15 * 255)
        template = np.full((logic_h, logic_w), bg_gray, dtype=np.uint8)
        for r in range(16):
            for c in range(48):
                if bitmap[r, c]:
                    template[r*widget.pixel_scale : (r+1)*widget.pixel_scale, c*widget.pixel_scale : (c+1)*widget.pixel_scale] = 255
        if self.scale != 1.0:
            template = cv2.resize(template, None, fx=self.scale, fy=self.scale, interpolation=cv2.INTER_NEAREST) # 逻辑px -> 缓冲区px
        screen_gray = cv2.cvtColor(full_img, cv2.COLOR_BGR2GRAY)
        # 缓冲区px
        screen_h, screen_w = screen_gray.shape
        template_h, template_w = template.shape
        # 逻辑px -> 缓冲区px
        expected_x_buf = math.ceil((log_x + widget.padding) * self.scale)
        expected_y_buf = math.ceil((log_y + widget.padding) * self.scale)
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
                logging.info(f"校准优化: 在 [{name}] 阶段找到匹配，相似度 {max_val:.4f}")
                final_match_loc = (x1 + max_loc[0], y1 + max_loc[1])
                break
        if final_max_val > 0.90 and final_match_loc is not None:
            # 缓冲区px
            found_x, found_y = final_match_loc
            # 缓冲区px -> 逻辑px
            self.monitor_offset_x = math.ceil((found_x - expected_x_buf) / self.scale)
            self.monitor_offset_y = math.ceil((found_y - expected_y_buf) / self.scale)
            logging.info(f"坐标校准完成: 窗口坐标偏差 offset=({self.monitor_offset_x}, {self.monitor_offset_y}) 逻辑px")
        else:
            logging.warning(f"校准失败: 未找到匹配图案 (最高相似度={final_max_val:.2f})")
        self.is_calibration_done = True
        GLib.idle_add(widget.destroy)
        GLib.idle_add(self.update_layout)
        return False

    def on_model_updated_ui(self, model_instance):
        """模型更新时刷新界面元素 (连接到 StitchModel 的信号)"""
        if self.is_selection_done:
            self.controller.update_info_panel()
            can_undo = model_instance.capture_count > 0 and not self.controller.is_auto_scrolling
            if self.show_button_panel:
                self.button_panel.set_undo_sensitive(can_undo)
            self.queue_draw()

    # 窗口坐标 {
    def _position_and_show_preview(self):
        """计算预览面板的位置并显示它"""
        # 逻辑px
        if not self.preview_panel: return
        rect = self.screen_rect
        screen_w = rect.width if rect else self.get_allocated_width()
        screen_h = rect.height if rect else self.get_allocated_height()
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
        # 显示器坐标 -> 窗口坐标
        valid_screen_w = screen_w - self.monitor_offset_x
        valid_screen_h = screen_h - self.monitor_offset_y
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
        self.fixed_container.move(self.preview_panel, target_x, target_y)
        self.preview_panel.show()
        self._update_input_shape()
        self.preview_panel.user_has_moved = False
        return False

    def show_quit_confirmation_dialog(self):
        """显示退出确认对话框并返回用户的响应"""
        if self.is_dialog_open:
            logging.warning("退出对话框已打开，忽略重复请求")
            return Gtk.ResponseType.NONE
        GLib.idle_add(self.present)
        self.is_dialog_open = True
        global hotkey_listener
        if hotkey_listener:
            logging.info("打开退出对话框，进入 Dialog 快捷键模式")
            hotkey_listener.set_dialog_mode(True)
        if self.screen_rect:
            target_w, target_h = self.screen_rect.width, self.screen_rect.height
        else:
            target_w, target_h = self.get_size()
        self.resize(target_w, target_h)
        dialog_container = Gtk.EventBox()
        dialog_container.set_visible_window(False)
        dialog_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        dialog_panel.get_style_context().add_class("embedded-dialog")
        lbl_title = Gtk.Label(label=config.DIALOG_QUIT_TITLE)
        lbl_title.get_style_context().add_class("dialog-title")
        dialog_panel.pack_start(lbl_title, False, False, 0)
        msg = config.DIALOG_QUIT_MESSAGE.format(count=self.stitch_model.capture_count)
        lbl_msg = Gtk.Label(label=msg)
        lbl_msg.get_style_context().add_class("dialog-message")
        dialog_panel.pack_start(lbl_msg, False, False, 0)
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        btn_box.set_halign(Gtk.Align.CENTER)
        yes_label = config.DIALOG_QUIT_BTN_YES.format(key=config.str_dialog_confirm.upper())
        no_label = config.DIALOG_QUIT_BTN_NO.format(key=config.str_dialog_cancel.upper())
        btn_yes = Gtk.Button(label=yes_label)
        btn_yes.get_style_context().add_class("dialog-btn")
        btn_yes.get_style_context().add_class("force-active-style")
        btn_no = Gtk.Button(label=no_label)
        btn_no.get_style_context().add_class("dialog-btn")
        btn_no.get_style_context().add_class("force-active-style")
        btn_box.pack_start(btn_yes, False, False, 0)
        btn_box.pack_start(btn_no, False, False, 0)
        dialog_panel.pack_start(btn_box, False, False, 0)
        dialog_container.add(dialog_panel)
        self.overlay_mask.set_size_request(target_w, target_h)
        self.overlay_mask.show()
        geo = self.session.geometry
        dialog_container.show_all()
        _, nat_size = dialog_container.get_preferred_size()
        d_w, d_h = nat_size.width, nat_size.height
        center_x = geo['x'] + geo['w'] // 2 - d_w // 2
        center_y = geo['y'] + geo['h'] // 2 - d_h // 2
        self.fixed_container.put(dialog_container, center_x, center_y)
        self._dialog_response = Gtk.ResponseType.NONE
        self._dialog_open_time = time.time()
        nested_loop = GLib.MainLoop()
        def on_yes(b):
            self._dialog_response = Gtk.ResponseType.YES
            nested_loop.quit()
        def on_no(b):
            self._dialog_response = Gtk.ResponseType.NO
            nested_loop.quit()
        btn_yes.connect("clicked", on_yes)
        btn_no.connect("clicked", on_no)
        self._current_dialog_loop = nested_loop
        self._current_dialog_setter = lambda r: setattr(self, '_dialog_response', r)
        nested_loop.run()
        self._current_dialog_loop = None
        self._current_dialog_setter = None
        dialog_container.destroy()
        self.overlay_mask.hide()
        self.is_dialog_open = False
        if hotkey_listener:
            logging.info("关闭退出对话框，退出 Dialog 快捷键模式")
            hotkey_listener.set_dialog_mode(False)
        return self._dialog_response

    def toggle_instruction_panel(self):
        self.user_wants_instruction_panel = not self.user_wants_instruction_panel
        state = "显示" if self.user_wants_instruction_panel else "隐藏"
        logging.info(f"用户切换提示面板: {state}")
        self.update_layout()

    def create_panels(self):
        self.side_panel = SidePanel()
        self.fixed_container.put(self.side_panel, 0, 0)
        self.side_panel.connect("toggle-grid-mode-clicked", lambda w: self.controller.grid_mode_controller.toggle())
        self.side_panel.connect("toggle-preview-clicked", lambda w: self.toggle_preview_panel())
        self.side_panel.connect("open-config-clicked", lambda w: self.toggle_config_panel())
        self.side_panel.connect("toggle-hotkeys-clicked", lambda w: toggle_all_hotkeys())
        self.button_panel = ButtonPanel()
        self.button_panel.connect("grid-backward-clicked", lambda w: self.controller.handle_movement_action('up', source='button'))
        self.button_panel.connect("grid-forward-clicked", lambda w: self.controller.handle_movement_action('down', source='button'))
        self.button_panel.connect("auto-scroll-start-clicked", lambda w: self.controller.start_auto_scroll(widget=w, source='button'))
        self.button_panel.connect("auto-scroll-stop-clicked", lambda w: self.controller.stop_auto_scroll())
        self.button_panel.connect("capture-clicked", self.controller.take_capture)
        self.button_panel.connect("undo-clicked", self.controller.delete_last_capture)
        self.button_panel.connect("finalize-clicked", self.controller.finalize_and_quit)
        self.button_panel.connect("cancel-clicked", self.controller.quit_and_cleanup)
        self.fixed_container.put(self.button_panel, 0, 0)
        self.instruction_panel = InstructionPanel()
        self.fixed_container.put(self.instruction_panel, 0, 0)
        _, nat_size = self.instruction_panel.get_preferred_size()
        self._instr_panel_natural_w, self._instr_panel_natural_h = nat_size.width, nat_size.height
        logging.info(f"缓存的 InstructionPanel 自然高度: {self._instr_panel_natural_h} 逻辑px")
        self.instruction_panel.connect("size-allocate", lambda w, a: self._update_input_shape())
        self.instruction_panel.hide()
        self.preview_panel = PreviewPanel(self.controller.stitch_model, config, self)
        self.fixed_container.put(self.preview_panel, 0, 0)
        self.preview_panel.hide()
        self.config_panel = ConfigPanel(config, self)
        self.fixed_container.put(self.config_panel, 0, 0)
        self.fixed_container.put(self.config_panel.file_chooser_panel, 0, 0)
        self.fixed_container.put(self.config_panel.color_chooser_panel, 0, 0)
        self.config_panel.hide()

    def toggle_config_panel(self):
        """切换配置面板的可见性"""
        if not self.config_panel: return
        if self.config_panel.get_visible():
            logging.info("隐藏配置面板")
            self.config_panel.hide()
            self._update_input_shape()
        else:
            logging.info("显示配置面板")
            if not self.config_panel.user_has_moved:
                if self.screen_rect:
                    screen_w, screen_h = self.screen_rect.width, self.screen_rect.height
                else:
                    screen_w, screen_h = self.get_allocated_width(), self.get_allocated_height()
                # 显示器坐标 -> 窗口坐标
                valid_w = screen_w - self.monitor_offset_x
                valid_h = screen_h - self.monitor_offset_y
                req_w, req_h = self.config_panel.get_size_request()
                x = (valid_w - req_w) // 2
                y = (valid_h - req_h) // 2
                self.fixed_container.move(self.config_panel, max(0, x), max(0, y))
            self.config_panel.show()
            self.config_panel.ensure_z_order()
            self._update_input_shape()

    def toggle_preview_panel(self):
        """创建或切换预览面板的可见性"""
        if self.preview_panel is None:
            logging.error("PreviewPanel 尚未初始化")
            return
        if self.preview_panel.get_visible():
            logging.info("隐藏预览面板")
            self.preview_panel.hide()
            self._update_input_shape()
        else:
            logging.info("显示预览面板")
            if not self.preview_panel.user_has_moved:
                self._position_and_show_preview()
            else:
                self.preview_panel.show()
                self._update_input_shape()
            if self.config_panel and self.config_panel.get_visible():
                self.config_panel.ensure_z_order()

    def on_dialog_key_press(self, widget, event):
        """处理确认对话框的按键事件"""
        if not self.is_dialog_open or not getattr(self, '_current_dialog_loop', None):
             return False
        if time.time() - getattr(self, '_dialog_open_time', 0) < 0.2:
            return False
        keyval = event.keyval
        state = event.state & config.GTK_MODIFIER_MASK
        def is_match(hotkey_config):
            return keyval in hotkey_config['gtk_keys'] and state == hotkey_config['gtk_mask']
        if is_match(config.HOTKEY_DIALOG_CONFIRM):
            self._current_dialog_setter(Gtk.ResponseType.YES)
            self._current_dialog_loop.quit()
            return True
        elif is_match(config.HOTKEY_DIALOG_CANCEL):
            self._current_dialog_setter(Gtk.ResponseType.NO)
            self._current_dialog_loop.quit()
            return True
        return False

    def _show_processing_panel(self):
        if self.screen_rect:
            target_w, target_h = self.screen_rect.width, self.screen_rect.height
        else:
            target_w, target_h = self.get_size()
        self.resize(target_w, target_h)
        self.overlay_mask.set_size_request(target_w, target_h)
        self.fixed_container.move(self.overlay_mask, 0, 0)
        self.overlay_mask.show()
        panel, progress_bar = create_feedback_panel(text=config.STR_PROCESSING_TEXT, show_progress_bar=True)
        capture_center_x = self.session.geometry['x'] + self.session.geometry['w'] // 2
        capture_center_y = self.session.geometry['y'] + self.session.geometry['h'] // 2
        p_w = config.PROCESSING_DIALOG_WIDTH
        p_h = config.PROCESSING_DIALOG_HEIGHT
        x = capture_center_x - p_w // 2
        y = capture_center_y - p_h // 2
        self.fixed_container.put(panel, x, y)
        return panel, progress_bar

    def on_draw(self, widget, cr):
        if self.is_finished:
            cr.set_source_rgba(0, 0, 0, 0)
            cr.set_operator(cairo.OPERATOR_SOURCE)
            cr.paint()
            return False
        if not self.is_selection_done:
            cr.set_source_rgba(0.0, 0.0, 0.0, 0.4)
            if self.screen_rect:
                cr.rectangle(0, 0, self.screen_rect.width, self.screen_rect.height)
                cr.fill()
            else:
                cr.paint()
            if self.is_dragging_selection:
                x1 = self.start_x_rel
                y1 = self.start_y_rel
                x2 = self.current_x_rel
                y2 = self.current_y_rel
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
        main_r, main_g, main_b, main_a = config.BORDER_COLOR
        ind_r, ind_g, ind_b, ind_a = config.MATCHING_INDICATOR_COLOR
        border_width = config.BORDER_WIDTH
        cr.set_line_width(border_width)
        selection_x_rel = self.session.geometry['x']
        selection_y_rel = self.session.geometry['y']
        border_x_rel = selection_x_rel - config.BORDER_WIDTH
        border_y_rel = selection_y_rel - config.BORDER_WIDTH
        capture_h = self.session.geometry['h']
        capture_w = self.session.geometry['w']
        rect_x = border_x_rel + border_width / 2
        rect_y = border_y_rel + border_width / 2
        rect_w = capture_w + border_width
        rect_h = capture_h + border_width
        is_grid_mode_matching = self.session.is_matching_enabled and self.session.detected_app_class is not None
        is_free_scroll_matching = self.session.detected_app_class is None and config.ENABLE_FREE_SCROLL_MATCHING
        draw_indicator = use_matching = is_grid_mode_matching or is_free_scroll_matching
        if not draw_indicator:
            cr.set_source_rgba(main_r, main_g, main_b, main_a)
            cr.rectangle(rect_x, rect_y, rect_w, rect_h)
            cr.stroke()
            return
        scale = self.scale
        if is_grid_mode_matching:
            overlap_height_buf = config.GRID_MATCHING_MAX_OVERLAP # 缓冲区px
        else:
            overlap_height_buf = config.FREE_SCROLL_MATCHING_MAX_OVERLAP # 缓冲区px
        overlap_height_logical = int(overlap_height_buf / scale) # 缓冲区px -> 逻辑px
        effective_overlap_h = min(overlap_height_logical, capture_h / 2)
        cr.set_source_rgba(main_r, main_g, main_b, main_a)
        cr.move_to(rect_x - border_width/2, rect_y)
        cr.line_to(rect_x + rect_w + border_width/2, rect_y)
        cr.stroke()
        cr.move_to(rect_x - border_width/2, rect_y + rect_h)
        cr.line_to(rect_x + rect_w + border_width/2, rect_y + rect_h)
        cr.stroke()
        y_vertical_start = border_y_rel + border_width
        y_vertical_end = border_y_rel + border_width + capture_h
        y_top_end = y_vertical_start + effective_overlap_h
        y_bottom_start = y_vertical_end - effective_overlap_h
        if y_top_end > y_bottom_start:
            y_top_end = y_bottom_start = y_vertical_start + (y_vertical_end - y_vertical_start) / 2
        left_x = border_x_rel + border_width / 2
        cr.set_source_rgba(ind_r, ind_g, ind_b, ind_a)
        cr.move_to(left_x, y_vertical_start - border_width/2)
        cr.line_to(left_x, y_top_end)
        cr.stroke()
        cr.set_source_rgba(main_r, main_g, main_b, main_a)
        cr.move_to(left_x, y_top_end)
        cr.line_to(left_x, y_bottom_start)
        cr.stroke()
        cr.set_source_rgba(ind_r, ind_g, ind_b, ind_a)
        cr.move_to(left_x, y_bottom_start)
        cr.line_to(left_x, y_vertical_end + border_width/2)
        cr.stroke()
        right_x = border_x_rel + capture_w + border_width + border_width / 2
        cr.set_source_rgba(ind_r, ind_g, ind_b, ind_a)
        cr.move_to(right_x, y_vertical_start - border_width/2)
        cr.line_to(right_x, y_top_end)
        cr.stroke()
        cr.set_source_rgba(main_r, main_g, main_b, main_a)
        cr.move_to(right_x, y_top_end)
        cr.line_to(right_x, y_bottom_start)
        cr.stroke()
        cr.set_source_rgba(ind_r, ind_g, ind_b, ind_a)
        cr.move_to(right_x, y_bottom_start)
        cr.line_to(right_x, y_vertical_end + border_width/2)
        cr.stroke()

    def on_size_allocate(self, widget, allocation):
        self._update_input_shape()

    # 逻辑px {
    def _update_input_shape(self):
        if not self.get_window():
            logging.info("_update_input_shape: 窗口尚未 realize, 无法设置 input_shape")
            return
        if self.screen_rect:
            win_w, win_h = self.screen_rect.width, self.screen_rect.height
        else:
            win_w, win_h = self.get_size()
        if self.is_finished:
            notify_region = cairo.Region()
            if self.current_notification_widget and self.current_notification_widget.get_visible():
                alloc = self.current_notification_widget.get_allocation()
                x = self.fixed_container.child_get_property(self.current_notification_widget, "x")
                y = self.fixed_container.child_get_property(self.current_notification_widget, "y")
                notify_region.union(cairo.RectangleInt(x, y, alloc.width, alloc.height))
            self.get_window().input_shape_combine_region(notify_region, 0, 0)
            return
        if not self.is_selection_done:
            full_region = cairo.Region(cairo.RectangleInt(0, 0, win_w, win_h))
            self.get_window().input_shape_combine_region(full_region, 0, 0)
            return
        final_input_region = cairo.Region()
        selection_x_rel = self.session.geometry.get('x', 0)
        selection_y_rel = self.session.geometry.get('y', 0)
        selection_w = self.session.geometry.get('w', 0)
        selection_h = self.session.geometry.get('h', 0)
        border_area_x_rel = selection_x_rel - config.BORDER_WIDTH
        border_area_y_rel = selection_y_rel - config.BORDER_WIDTH
        border_area_width = selection_w + 2 * config.BORDER_WIDTH
        border_area_height = selection_h + 2 * config.BORDER_WIDTH
        border_full_region = cairo.Region(
            cairo.RectangleInt(border_area_x_rel, border_area_y_rel, border_area_width, border_area_height)
        )
        inner_transparent_region = cairo.Region(
            cairo.RectangleInt(border_area_x_rel + config.BORDER_WIDTH, border_area_y_rel + config.BORDER_WIDTH, selection_w, selection_h)
        )
        border_full_region.subtract(inner_transparent_region)
        final_input_region.union(border_full_region)
        def _union_widget_region(widget):
            if widget and widget.get_visible():
                try:
                    alloc = widget.get_allocation()
                    if alloc.width > 0 and alloc.height > 0:
                        x = self.fixed_container.child_get_property(widget, "x")
                        y = self.fixed_container.child_get_property(widget, "y")
                        reg = cairo.Region(cairo.RectangleInt(x, y, alloc.width, alloc.height))
                        final_input_region.union(reg)
                except TypeError:
                    pass
        widgets_to_process = [
            self.side_panel if self.show_side_panel else None,
            self.button_panel if self.show_button_panel else None,
            self.instruction_panel,
            self.preview_panel,
            self.config_panel,
            self.current_notification_widget
        ]
        if self.config_panel:
            widgets_to_process.append(self.config_panel.file_chooser_panel)
            widgets_to_process.append(self.config_panel.color_chooser_panel)
        if self.controller.grid_mode_controller.calibration_state:
            widgets_to_process.append(self.controller.grid_mode_controller.calibration_state.get("panel"))
        for w in widgets_to_process:
            _union_widget_region(w)
        if hasattr(self, 'overlay_mask') and self.overlay_mask.get_visible():
             mask_region = cairo.Region(cairo.RectangleInt(0, 0, win_w, win_h))
             final_input_region.union(mask_region)
        self.get_window().input_shape_combine_region(final_input_region, 0, 0)

    def get_cursor_edge(self, x, y):
        rect = self.screen_rect
        win_w = rect.width if rect else self.get_allocated_width()
        win_h = rect.height if rect else self.get_allocated_height()
        handle_size = config.HANDLE_HEIGHT 
        if not self.is_selection_done: return None
        selection_y_rel = self.session.geometry.get('y', 0)
        border_area_y_rel = selection_y_rel - config.BORDER_WIDTH
        selection_h = self.session.geometry.get('h', 0)
        on_top = border_area_y_rel <= y < border_area_y_rel + handle_size
        on_bottom = border_area_y_rel + selection_h + 2 * config.BORDER_WIDTH - handle_size < y <= border_area_y_rel + selection_h + 2 * config.BORDER_WIDTH
        edge_y = ''
        if on_top: edge_y = 'top'
        elif on_bottom: edge_y = 'bottom'
        edge_x = ''
        if not self.session.is_horizontally_locked:
            selection_x_rel = self.session.geometry.get('x', 0)
            border_area_x_rel = selection_x_rel - config.BORDER_WIDTH
            selection_w = self.session.geometry.get('w', 0)
            border_x_start = border_area_x_rel
            border_x_end = border_x_start + selection_w + 2 * config.BORDER_WIDTH
            on_left = border_x_start <= x < border_x_start + handle_size
            on_right = border_x_end - handle_size < x <= border_x_end
            if on_left: edge_x = 'left'
            elif on_right: edge_x = 'right'
        edge = edge_y + ('-' + edge_x if edge_x and edge_y else edge_x)
        return edge if edge else None

    def on_button_press(self, widget, event):
        if self.current_notification_widget:
            logging.info("检测到点击通知外部，关闭当前通知")
            self.dismiss_notification(self.current_notification_widget)
        if not self.is_selection_done:
            if event.button == 1:
                self.is_dragging_selection = True
                self.start_x_rel, self.start_y_rel = event.x, event.y
                self.current_x_rel, self.current_y_rel = event.x, event.y
                self.queue_draw()
                return True
            return False
        else:
            return self.controller.handle_button_press(event)
    # 逻辑px }

    def _trigger_wayland_layout_refresh(self, remaining_retries=0):
        w, h = self.screen_rect.width, self.screen_rect.height
        self.resize(w + 1, h + 1)
        def _restore_size():
            self.resize(w, h)
            return False
        GLib.idle_add(_restore_size)
        if remaining_retries > 0:
            GLib.timeout_add(150, self._trigger_wayland_layout_refresh, remaining_retries - 1)

    def on_button_release(self, widget, event):
        if not self.is_selection_done:
            if event.button == 1 and self.is_dragging_selection:
                self.is_dragging_selection = False
                # 逻辑px
                x1, y1 = self.start_x_rel, self.start_y_rel
                x2, y2 = self.current_x_rel, self.current_y_rel
                final_x = min(x1, x2)
                final_y = min(y1, y2)
                raw_w = abs(x1 - x2)
                raw_h = abs(y1 - y2)
                min_size = 2 * config.HANDLE_HEIGHT
                final_w = max(raw_w, min_size)
                final_h = max(raw_h, min_size)
                if raw_w < min_size or raw_h < min_size:
                    logging.info("选区太小，保持在选择阶段")
                    self.queue_draw()
                    return True
                geometry = {'x': round(final_x), 'y': round(final_y), 'w': round(final_w), 'h': round(final_h)}
                scale = self.scale
                buf_w, buf_h = int(geometry['w'] * scale), int(geometry['h'] * scale) # 缓冲区px
                logging.info(f"选区完成 逻辑px: {geometry} (Scale={scale}) -> 缓冲区px: w={buf_w}, h={buf_h}")
                self.is_selection_done = True
                self.session.update_geometry(geometry)
                Gdk.Display.get_default().get_default_seat().ungrab()
                self.update_layout()
                self.controller.update_info_panel()
                if IS_WAYLAND:
                    self._trigger_wayland_layout_refresh(remaining_retries=1)
            if config.SHOW_PREVIEW_ON_START:
                self.toggle_preview_panel()
            else:
                logging.info("配置项 'show_preview_on_start' 为 false，启动时不创建预览窗口。")
            return False
        else:
            return self.controller.handle_button_release(event)

    @property
    def left_panel_w(self):
        if self.show_side_panel and self.side_panel_on_left:
            return config.SIDE_PANEL_WIDTH
        return 0

    def update_layout(self):
        """根据屏幕和选区位置，动态计算并应用窗口布局和几何属性"""
        # 逻辑px
        if self.screen_rect:
            screen_w = self.screen_rect.width
            screen_h = self.screen_rect.height
        else:
            screen_w, screen_h = self.get_size()
        if not self.is_selection_done:
            blocking_rects = []
            if self.is_dragging_selection:
                x1, y1 = self.start_x_rel, self.start_y_rel
                x2, y2 = self.current_x_rel, self.current_y_rel
                rect = (min(x1, x2), min(y1, y2), abs(x1 - x2), abs(y1 - y2))
                blocking_rects.append(rect)
            self._update_instruction_panel_layout(screen_w, screen_h, blocking_rects)
            return
        side_panel_needed_w = config.SIDE_PANEL_WIDTH
        button_panel_needed_w = config.BUTTON_PANEL_WIDTH
        should_show_side_panel_base = config.ENABLE_SIDE_PANEL
        should_show_button_panel_base = config.ENABLE_BUTTONS
        has_space_right_for_button_panel = (self.session.geometry['x'] + self.session.geometry['w'] + config.BORDER_WIDTH + button_panel_needed_w) <= screen_w
        has_space_right_for_side_panel = (self.session.geometry['x'] + self.session.geometry['w'] + config.BORDER_WIDTH + side_panel_needed_w) <= screen_w
        has_space_left_for_side_panel = (self.session.geometry['x'] - config.BORDER_WIDTH - side_panel_needed_w) >= 0
        self.show_side_panel = False
        self.show_button_panel = False
        self.side_panel_on_left = True
        if should_show_side_panel_base and has_space_left_for_side_panel:
            self.show_side_panel = True
            self.side_panel_on_left = True
            if should_show_button_panel_base and has_space_right_for_button_panel:
                self.show_button_panel = True
            else:
                self.show_button_panel = False
        elif should_show_side_panel_base and has_space_right_for_side_panel:
            self.show_side_panel = True
            self.side_panel_on_left = False
            self.show_button_panel = False
        elif should_show_button_panel_base and has_space_right_for_button_panel:
            self.show_side_panel = False
            self.show_button_panel = True
        else:
            self.show_side_panel = False
            self.show_button_panel = False
        left_total_w = 0
        if self.show_side_panel and self.side_panel_on_left:
            left_total_w = side_panel_needed_w
        right_total_w = 0
        if self.show_button_panel:
            right_total_w = button_panel_needed_w
        elif self.show_side_panel and not self.side_panel_on_left:
            right_total_w = side_panel_needed_w
        selection_x_rel = self.session.geometry.get('x', 0)
        selection_y_rel = self.session.geometry.get('y', 0)
        selection_w = self.session.geometry.get('w', 0)
        selection_h = self.session.geometry.get('h', 0)
        blocking_rects = []
        blocking_rects.append((
            selection_x_rel - config.BORDER_WIDTH,
            selection_y_rel - config.BORDER_WIDTH,
            selection_w + 2 * config.BORDER_WIDTH,
            selection_h + 2 * config.BORDER_WIDTH
        ))
        # 更新子组件的可见性和位置
        if self.show_side_panel:
            self.side_panel.update_visibility_by_height(selection_h, self.controller.grid_mode_controller.is_active)
            if self.side_panel_on_left:
                panel_x_rel = selection_x_rel - left_total_w - config.BORDER_WIDTH
            else:
                panel_x_rel = selection_x_rel + selection_w + config.BORDER_WIDTH
            panel_y_rel = selection_y_rel - config.BORDER_WIDTH
            self.fixed_container.move(self.side_panel, panel_x_rel, panel_y_rel)
            self.side_panel.show()
            _, nat_h = self.side_panel.get_preferred_height()
            blocking_rects.append((panel_x_rel, panel_y_rel, config.SIDE_PANEL_WIDTH, nat_h))
        else:
            self.side_panel.hide()
        is_button_panel_visible_vertically = self.button_panel.update_visibility_by_height(selection_h, self.controller.grid_mode_controller.is_active)
        if self.show_button_panel and is_button_panel_visible_vertically:
            panel_x_rel = selection_x_rel + selection_w + config.BORDER_WIDTH
            panel_y_rel = selection_y_rel - config.BORDER_WIDTH
            self.fixed_container.move(self.button_panel, panel_x_rel, panel_y_rel)
            self.button_panel.show()
            _, nat_h = self.button_panel.get_preferred_height()
            blocking_rects.append((panel_x_rel, panel_y_rel, config.BUTTON_PANEL_WIDTH, nat_h))
        else:
            self.button_panel.hide()
        self._update_instruction_panel_layout(screen_w, screen_h, blocking_rects)
        self._update_input_shape()

    def _update_instruction_panel_layout(self, screen_w, screen_h, blocking_rects):
        if not self.instruction_panel:
            return
        if not self.user_wants_instruction_panel:
            self.instruction_panel.hide()
            return
        if not self.is_calibration_done:
            self.instruction_panel.hide()
            return
        margin = 20
        panel_w = self._instr_panel_natural_w
        panel_h = self._instr_panel_natural_h
        # 显示器坐标 -> 窗口坐标
        valid_screen_w = screen_w - self.monitor_offset_x
        valid_screen_h = screen_h - self.monitor_offset_y
        if valid_screen_h < panel_h + margin * 2 or valid_screen_w < panel_w + margin * 2:
             self.instruction_panel.hide()
             return
        target_x = margin
        target_y = valid_screen_h - panel_h - margin
        is_obstructed = False
        panel_rect = (target_x, target_y, panel_w, panel_h)
        def rects_intersect(r1, r2):
            return not (r1[0] >= r2[0] + r2[2] or 
                        r1[0] + r1[2] <= r2[0] or 
                        r1[1] >= r2[1] + r2[3] or 
                        r1[1] + r1[3] <= r2[1])
        for r in blocking_rects:
            if rects_intersect(panel_rect, r):
                is_obstructed = True
                break
        should_be_visible = not is_obstructed
        current_visible = self.instruction_panel.get_visible()
        if current_visible != should_be_visible:
            if should_be_visible:
                self.instruction_panel.show()
            else:
                self.instruction_panel.hide()
                self.queue_draw_area(target_x, target_y, panel_w, panel_h)
        if should_be_visible:
            cur_x = self.fixed_container.child_get_property(self.instruction_panel, "x")
            cur_y = self.fixed_container.child_get_property(self.instruction_panel, "y")
            if cur_x != target_x or cur_y != target_y:
                self.fixed_container.move(self.instruction_panel, target_x, target_y)

    def on_motion_notify(self, widget, event):
        if not self.is_selection_done:
            if self.is_dragging_selection:
                self.current_x_rel, self.current_y_rel = event.x, event.y
                self.update_layout()
                self.queue_draw()
                return True
            return False
        else:
            if self.controller.is_dragging:
                self.controller.handle_motion(event)
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
    # 窗口坐标 }

class XlibHotkeyInterceptor(threading.Thread):
    """使用 Xlib (XGrabKey) 在后台线程中拦截全局热键，并支持动态启用/禁用"""
    def __init__(self, overlay, callbacks_map, keymap_tuples):
        super().__init__(daemon=True)
        self.overlay = overlay
        self.callbacks_map = callbacks_map
        self.keymap_tuples = keymap_tuples
        self.running = False
        self.disp = None
        self.root = None
        self.lock = threading.Lock()
        self.is_paused = False
        self.in_dialog = False
        self.monitor_mouse = False
        self.mouse_grabbed = False
        self.currently_grabbed = set()
        self.debug_key_map = {}
        self.normal_lookup = {}
        self.dialog_lookup = {}
        self.toggle_lookup = {}
        for kc, mod, is_toggle, name in keymap_tuples:
            key_id = (kc, mod)
            if key_id not in self.debug_key_map:
                 self.debug_key_map[key_id] = name
            callback = self.callbacks_map.get(name)
            if not callback:
                continue
            if is_toggle:
                self.toggle_lookup[key_id] = callback
            elif name in ('dialog_confirm', 'dialog_cancel'):
                self.dialog_lookup[key_id] = callback
            else:
                self.normal_lookup[key_id] = callback
        self.toggle_keys = [k for k in self.keymap_tuples if k[2]]
        self.dialog_keys = [k for k in self.keymap_tuples if not k[2] and k[3] in ('dialog_confirm', 'dialog_cancel')]
        self.normal_keys = [k for k in self.keymap_tuples if not k[2] and k[3] not in ('dialog_confirm', 'dialog_cancel')]
        self.pending_mod_release = None

    def enable_mouse_click_stop(self, enabled: bool):
        with self.lock:
            self.monitor_mouse = enabled
            self._schedule_update()

    def set_normal_keys_grabbed(self, grab_state: bool):
        with self.lock:
            self.is_paused = not grab_state
            self._schedule_update()

    def set_dialog_mode(self, active: bool):
        with self.lock:
            self.in_dialog = active
            self._schedule_update()

    def _schedule_update(self):
        if not self.running: return
        target_keys = []
        target_keys.extend(self.toggle_keys)
        if are_hotkeys_enabled and not self.is_paused:
            if self.in_dialog:
                target_keys.extend(self.dialog_keys)
            else:
                target_keys.extend(self.normal_keys)
        threading.Thread(target=self._apply_grab_state, args=(target_keys,), daemon=True).start()

    def _apply_grab_state(self, target_key_tuples):
        with self.lock:
            if not self.disp or not self.root:
                return
            if self.monitor_mouse and not self.mouse_grabbed:
                try:
                    self.root.grab_button(1, X.AnyModifier, 0, X.ButtonPressMask, 
                                          X.GrabModeAsync, X.GrabModeAsync, X.NONE, X.NONE)
                    self.mouse_grabbed = True
                    logging.info("Xlib: 已抓取鼠标左键用于停止自动滚动")
                except Exception as e:
                    logging.warning(f"Xlib GrabButton 失败: {e}")
            elif not self.monitor_mouse and self.mouse_grabbed:
                try:
                    self.root.ungrab_button(1, X.AnyModifier)
                    self.mouse_grabbed = False
                    logging.info("Xlib: 已释放鼠标左键抓取")
                except Exception as e:
                    logging.warning(f"Xlib UngrabButton 失败: {e}")
            target_set = set((k[0], k[1]) for k in target_key_tuples)
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

    def run(self):
        """线程主循环，监听 X events"""
        try:
            self.disp = display.Display()
            self.root = self.disp.screen().root
            self.mod_keycodes = set()
            for name in ['Shift_L', 'Shift_R', 'Control_L', 'Control_R', 
                         'Alt_L', 'Alt_R', 'Super_L', 'Super_R', 'Meta_L', 'Meta_R']:
                keysym = XK.string_to_keysym(name)
                if keysym:
                    kc = self.disp.keysym_to_keycode(keysym)
                    if kc: self.mod_keycodes.add(kc)
            self.running = True
        except Exception as e:
            logging.error(f"XlibHotkeyInterceptor 线程初始化 Display 失败: {e}")
            self.running = False
            return
        self._schedule_update()
        logging.info("Xlib 热键拦截线程已启动")
        while self.running:
            try:
                event = self.disp.next_event()
                if event.type == X.ButtonPress and self.running:
                    if self.monitor_mouse and event.detail == 1:
                        logging.info("Xlib: 检测到鼠标左键点击，停止自动滚动")
                        GLib.idle_add(self.overlay.controller.stop_auto_scroll, "用户点击鼠标左键停止")
                        self.enable_mouse_click_stop(False)
                if event.type == X.KeyPress and self.running:
                    keycode = event.detail
                    clean_state = event.state & (X.ShiftMask | X.ControlMask | X.Mod1Mask | X.Mod4Mask)
                    key_id = (keycode, clean_state)
                    key_name = self.debug_key_map.get(key_id, "UnknownKey")
                    log_key_str = f"key='{key_name}' (kc={keycode})"
                    if key_id in self.toggle_lookup:
                         logging.info(f"Xlib 拦截到切换键 ({log_key_str}, state={clean_state})")
                         callback = self.toggle_lookup[key_id]
                         if keycode in self.mod_keycodes:
                             self.pending_mod_release = (keycode, callback)
                             logging.info("修饰键按下，等待松开...")
                         else:
                             if self.pending_mod_release:
                                 self.pending_mod_release = None
                             GLib.idle_add(callback)
                         continue
                    if are_hotkeys_enabled:
                        callback = None
                        if self.in_dialog:
                            if key_id in self.dialog_lookup:
                                callback = self.dialog_lookup[key_id]
                        else:
                            if key_id in self.normal_lookup:
                                callback = self.normal_lookup[key_id]
                        if callback:
                            if keycode in self.mod_keycodes:
                                self.pending_mod_release = (keycode, callback)
                                logging.info(f"修饰键热键 {log_key_str} 按下，等待松开...")
                            else:
                                if self.pending_mod_release:
                                    logging.info("检测到组合键，取消待定的修饰键单键动作")
                                    self.pending_mod_release = None
                                logging.info(f"Xlib 拦截到热键 ({log_key_str}) 并执行回调")
                                GLib.idle_add(callback)
                elif event.type == X.KeyRelease and self.running:
                    if self.pending_mod_release:
                        waiting_keycode, waiting_callback = self.pending_mod_release
                        if event.detail == waiting_keycode:
                            logging.info("修饰键松开且未被组合使用，执行动作")
                            GLib.idle_add(waiting_callback)
                            self.pending_mod_release = None
            except Exception as e:
                if self.running:
                    logging.error(f"Xlib 事件循环错误: {e}")
                    time.sleep(0.1)
        logging.info("Xlib 热键拦截线程正在停止...")
        with self.lock:
            self._apply_grab_state([])
        if self.disp:
            try:
                self.disp.close()
            except Exception as e:
                 logging.error(f"关闭 X Display 连接时出错: {e}")
        logging.info("Xlib 热键拦截线程已停止")

    def stop(self):
        """请求线程停止"""
        logging.info("收到停止 Xlib 拦截线程的请求...")
        self.running = False
        if self.disp and self.root:
            try:
                client_event = protocol.event.ClientMessage(
                    window=self.root,
                    client_type=self.disp.intern_atom("_STOP_THREAD"),
                    data=(8, [0] * 20)
                )
                self.disp.send_event(self.root, client_event, event_mask=X.NoEventMask)
                self.disp.flush()
                logging.info("已发送 ClientMessage 事件以唤醒 Xlib 事件循环")
            except Exception as e:
                logging.warning(f"发送唤醒事件失败: {e}")
        else:
            logging.warning("无法发送唤醒事件，Display 尚未初始化或已关闭")

class EvdevHotkeyListener(threading.Thread):
    """在 Wayland/Console 环境下使用 evdev 直接读取输入设备实现全局热键"""
    def __init__(self, overlay, config_obj, callback_map):
        super().__init__(daemon=True)
        self.overlay = overlay
        self.config = config_obj
        self.callback_map = callback_map
        self.running = False
        self.devices = []
        self.key_map = {}
        self.active_mods = set()
        self.listening_active = True
        self.in_dialog = False
        self.mouse_click_stop_enabled = False
        self.last_trigger_time = 0
        self.mod_codes = {
            'shift': {e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT},
            'ctrl': {e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL},
            'control': {e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL},
            'alt': {e.KEY_LEFTALT, e.KEY_RIGHTALT},
            'super': {e.KEY_LEFTMETA, e.KEY_RIGHTMETA},
            'win': {e.KEY_LEFTMETA, e.KEY_RIGHTMETA},
            'meta': {e.KEY_LEFTMETA, e.KEY_RIGHTMETA},
        }
        self.code_to_mod_name = {}
        for name, codes in self.mod_codes.items():
            for code in codes:
                self.code_to_mod_name[code] = name
        self.pending_mod_action = None
        self._parse_config()

    def enable_mouse_click_stop(self, enabled: bool):
        self.mouse_click_stop_enabled = enabled
        logging.info(f"Evdev: 鼠标点击停止功能已 {'启用' if enabled else '禁用'}")

    def _parse_config(self):
        """解析 Config 对象中的字符串到 evdev codes"""
        logging.info(f"Evdev 正在注册热键...")
        for action_name, _ in self.callback_map:
            hotkey_str = self.config.parser.get('Hotkeys', action_name, fallback='')
            if not hotkey_str: continue
            parts = [p.strip().lower().replace('<','').replace('>','') for p in hotkey_str.split('+')]
            mods = set()
            main_key = None
            if len(parts) == 1 and parts[0] in self.mod_codes:
                main_key = parts[0]
                if main_key == 'control': main_key = 'ctrl'
                if main_key == 'win' or main_key == 'meta': main_key = 'super'
                mods.add(main_key)
            else:
                for part in parts:
                    if part in self.mod_codes:
                        if part == 'control': part = 'ctrl'
                        if part == 'win' or part == 'meta': part = 'super'
                        mods.add(part)
                    else:
                        main_key = part
            if main_key:
                key_codes = []
                if main_key in self.mod_codes:
                    key_codes.extend(self.mod_codes[main_key])
                else:
                    key_code = None
                    candidate = f"KEY_{main_key.upper()}"
                    if hasattr(e, candidate):
                        key_code = getattr(e, candidate)
                    if key_code is None:
                        special_map = {
                            'enter': e.KEY_ENTER, 'return': e.KEY_ENTER,
                            'esc': e.KEY_ESC, 'escape': e.KEY_ESC,
                            'minus': e.KEY_MINUS, 'equal': e.KEY_EQUAL,
                            'backspace': e.KEY_BACKSPACE, 'space': e.KEY_SPACE,
                            'left': e.KEY_LEFT, 'right': e.KEY_RIGHT, 'up': e.KEY_UP, 'down': e.KEY_DOWN,
                            'pageup': e.KEY_PAGEUP, 'pagedown': e.KEY_PAGEDOWN
                        }
                        key_code = special_map.get(main_key)
                    if key_code:
                        key_codes.append(key_code)
                if key_codes:
                    for kc in key_codes:
                        key_combo = (frozenset(mods), kc)
                        if key_combo not in self.key_map:
                            self.key_map[key_combo] = []
                        self.key_map[key_combo].append(action_name)
                else:
                    logging.warning(f"Evdev 无法解析按键: {main_key} (完整串: {hotkey_str})")

    def set_normal_keys_grabbed(self, grab_state: bool):
        self.listening_active = grab_state
        logging.info(f"Evdev 内部监听状态已设置为: {'活动' if grab_state else '暂停'}")

    def set_dialog_mode(self, active: bool):
        self.in_dialog = active
        logging.info(f"Evdev 内部对话框模式: {active}")

    def _find_input_devices(self):
        """查找所有具有键盘特性或鼠标左键的设备"""
        devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
        valid_devices = []
        for dev in devices:
            caps = dev.capabilities()
            if e.EV_KEY in caps:
                keys = caps[e.EV_KEY]
                if e.KEY_ENTER in keys or e.BTN_LEFT in keys:
                    valid_devices.append(dev)
        return valid_devices

    def run(self):
        if not EVDEV_AVAILABLE:
            logging.error("缺少 evdev 库，无法启动 EvdevHotkeyListener")
            return
        try:
            self.devices = self._find_input_devices()
            if not self.devices:
                logging.warning("未检测到输入设备，Evdev 监听器无法工作")
                return
            fds = {dev.fd: dev for dev in self.devices}
            self.running = True
            logging.info(f"Evdev 监听器启动，监控 {len(self.devices)} 个设备")
            while self.running:
                r, w, x = select.select(fds, [], [], 0.5)
                for fd in r:
                    dev = fds[fd]
                    try:
                        for event in dev.read():
                            if event.type == e.EV_KEY:
                                self._process_key_event(event)
                    except OSError:
                        del fds[fd]
        except Exception as err:
            logging.error(f"Evdev 监听循环发生错误: {e}")
        finally:
            for dev in self.devices:
                try: dev.close()
                except: pass

    def _process_key_event(self, event):
        if event.value == 1 or event.value == 2:
            if event.code == e.BTN_LEFT and self.mouse_click_stop_enabled:
                logging.info("Evdev: 检测到鼠标左键点击，停止自动滚动")
                GLib.idle_add(self.overlay.controller.stop_auto_scroll, "用户点击鼠标左键停止")
                self.mouse_click_stop_enabled = False
                return
            is_modifier_key = event.code in self.code_to_mod_name
            if event.code in self.code_to_mod_name:
                mod_name = self.code_to_mod_name[event.code]
                if mod_name in ['control', 'ctrl']: self.active_mods.add('ctrl')
                if mod_name == 'shift': self.active_mods.add('shift')
                if mod_name == 'alt': self.active_mods.add('alt')
                if mod_name in ['super', 'win', 'meta']: self.active_mods.add('super')
            if not is_modifier_key and self.pending_mod_action:
                self.pending_mod_action = None
            if self.listening_active:
                current_trigger = (frozenset(self.active_mods), event.code)
                current_time = time.time()
                if current_time - self.last_trigger_time < 0.25:
                    return
                if current_trigger in self.key_map:
                    possible_actions = self.key_map[current_trigger]
                    for action_name in possible_actions:
                        allow = False
                        if action_name == 'toggle_hotkeys_enabled':
                            allow = True
                        elif are_hotkeys_enabled:
                            if self.in_dialog:
                                if action_name in ('dialog_confirm', 'dialog_cancel'): allow = True
                            else:
                                if action_name not in ('dialog_confirm', 'dialog_cancel'): allow = True
                        if allow:
                            for name, callback in setup_hotkey_listener.callback_list:
                                if name == action_name:
                                    if is_modifier_key:
                                        self.pending_mod_action = (event.code, callback)
                                        logging.info(f"Evdev: 修饰键 {action_name} 按下，等待松开")
                                    else:
                                        logging.info(f"Evdev 触发热键: {action_name}")
                                        self.last_trigger_time = current_time
                                        GLib.idle_add(callback)
                                    return
        elif event.value == 0:
            if self.pending_mod_action:
                waiting_code, waiting_cb = self.pending_mod_action
                if event.code == waiting_code:
                    logging.info("Evdev: 修饰键松开且未被组合使用，执行动作")
                    self.last_trigger_time = time.time()
                    GLib.idle_add(waiting_cb)
                self.pending_mod_action = None
            if event.code in self.code_to_mod_name:
                mod_name = self.code_to_mod_name[event.code]
                if mod_name in ['control', 'ctrl'] and 'ctrl' in self.active_mods: self.active_mods.remove('ctrl')
                if mod_name == 'shift' and 'shift' in self.active_mods: self.active_mods.remove('shift')
                if mod_name == 'alt' and 'alt' in self.active_mods: self.active_mods.remove('alt')
                if mod_name in ['super', 'win', 'meta'] and 'super' in self.active_mods: self.active_mods.remove('super')
    
    def stop(self):
        self.running = False

def toggle_all_hotkeys():
    """切换快捷键（包括全局和窗口焦点）的启用状态"""
    global are_hotkeys_enabled
    are_hotkeys_enabled = not are_hotkeys_enabled
    state_str = "启用" if are_hotkeys_enabled else "禁用"
    if hotkey_listener and hasattr(hotkey_listener, '_schedule_update'):
        hotkey_listener._schedule_update()
    title = "快捷键状态"
    message = f"截图会话的快捷键当前已{state_str}"
    GLib.idle_add(send_desktop_notification, title, message)
    logging.info(f"快捷键状态已切换为: {state_str}")

def setup_hotkey_listener(overlay):
    global hotkey_listener
    def global_dialog_confirm():
        if overlay.is_dialog_open and hasattr(overlay, '_current_dialog_setter') and overlay._current_dialog_setter:
            logging.info("全局热键触发: 确认退出")
            GLib.idle_add(overlay._current_dialog_setter, Gtk.ResponseType.YES)
            if hasattr(overlay, '_current_dialog_loop') and overlay._current_dialog_loop:
                GLib.idle_add(overlay._current_dialog_loop.quit)

    def global_dialog_cancel():
        if overlay.is_dialog_open and hasattr(overlay, '_current_dialog_setter') and overlay._current_dialog_setter:
            logging.info("全局热键触发: 取消退出")
            GLib.idle_add(overlay._current_dialog_setter, Gtk.ResponseType.NO)
            if hasattr(overlay, '_current_dialog_loop') and overlay._current_dialog_loop:
                GLib.idle_add(overlay._current_dialog_loop.quit)
    hotkey_key_callback_list = [
        ('capture', overlay.controller.take_capture),
        ('finalize', overlay.controller.finalize_and_quit),
        ('undo', overlay.controller.delete_last_capture),
        ('cancel', overlay.controller.quit_and_cleanup),
        ('grid_backward', lambda: overlay.controller.handle_movement_action('up', source='hotkey')),
        ('grid_forward', lambda: overlay.controller.handle_movement_action('down', source='hotkey')),
        ('auto_scroll_start', lambda: overlay.controller.start_auto_scroll(source='hotkey')),
        ('auto_scroll_stop', lambda: overlay.controller.stop_auto_scroll()),
        ('toggle_grid_mode', overlay.controller.grid_mode_controller.toggle),
        ('configure_scroll_unit', overlay.controller.grid_mode_controller.start_calibration),
        ('toggle_preview', overlay.toggle_preview_panel),
        ('open_config_editor', overlay.toggle_config_panel),
        ('toggle_instruction_panel', overlay.toggle_instruction_panel),
        ('toggle_hotkeys_enabled', toggle_all_hotkeys),
        ('preview_zoom_in', lambda: overlay.preview_panel._zoom_in() if overlay.preview_panel and overlay.preview_panel.get_visible() else None),
        ('preview_zoom_out', lambda: overlay.preview_panel._zoom_out() if overlay.preview_panel and overlay.preview_panel.get_visible() else None),
        ('dialog_confirm', global_dialog_confirm),
        ('dialog_cancel', global_dialog_cancel),
    ]
    setup_hotkey_listener.callback_list = hotkey_key_callback_list
    if IS_WAYLAND:
        logging.info("检测到 Wayland 会话，尝试使用 Evdev 启动全局热键监听...")
        if not EVDEV_AVAILABLE:
            logging.error("未检测到 evdev 模块，Wayland 下无法使用全局快捷键。请安装 python-evdev。")
            return
        try:
            hotkey_listener = EvdevHotkeyListener(overlay, config, hotkey_key_callback_list)
            hotkey_listener.start()
        except Exception as e:
            logging.error(f"启动 Evdev 监听器失败: {e}")
        return
        
    def gdk_mask_to_x_mask(gdk_mask):
        x_mask = 0
        if gdk_mask & Gdk.ModifierType.CONTROL_MASK: x_mask |= X.ControlMask
        if gdk_mask & Gdk.ModifierType.SHIFT_MASK: x_mask |= X.ShiftMask
        if gdk_mask & Gdk.ModifierType.MOD1_MASK: x_mask |= X.Mod1Mask
        if gdk_mask & Gdk.ModifierType.SUPER_MASK: x_mask |= X.Mod4Mask
        return x_mask

    def get_keycode_from_keyval(keyval):
            try:
                gdk_disp = Gdk.Display.get_default()
                keymap = Gdk.Keymap.get_for_display(gdk_disp)
                found, keys = keymap.get_entries_for_keyval(keyval)
                if found and keys and len(keys) > 0:
                    keycode = keys[0].keycode
                    return keycode
                else:
                    lower_keyval = Gdk.keyval_to_lower(keyval)
                    if lower_keyval != keyval:
                        found, keys = keymap.get_entries_for_keyval(lower_keyval)
                        if found and keys and len(keys) > 0:
                            return keys[0].keycode
                logging.warning(f"Gdk.Keymap 无法为 keyval {keyval} (名称: {Gdk.keyval_name(keyval)}) 找到 keycode")
                return 0
            except Exception as e:
                logging.error(f"通过 Gdk.Keymap 获取 keycode 失败 (keyval={keyval}): {e}")
                try:
                     tmp_disp = display.Display()
                     keysym = XK.string_to_keysym(Gdk.keyval_name(keyval))
                     keycode = tmp_disp.keysym_to_keycode(keysym) if keysym else 0
                     tmp_disp.close()
                     if keycode:
                          logging.warning(f"GDK 获取 keycode 失败，回退到 Xlib 获取 keycode {keycode} for keyval {keyval}")
                          return keycode
                     else:
                          logging.error(f"Xlib 也无法为 keyval {keyval} 获取 keycode")
                          return 0
                except Exception as ex:
                     logging.error(f"Xlib 回退获取 keycode 时出错: {ex}")
                     return 0

    toggle_key_config_keys = ['toggle_hotkeys_enabled']
    callbacks_map = dict(hotkey_key_callback_list)
    keymap_tuples = []
    def get_key_mode(name):
        if name in toggle_key_config_keys: return 'toggle'
        if name in ('dialog_confirm', 'dialog_cancel'): return 'dialog'
        return 'normal'
    registered_keys = {} # (keycode, mask) -> [(mode, name)]
    for key_name, callback in hotkey_key_callback_list:
        hotkey_config_attr_name = f"HOTKEY_{key_name.upper()}"
        hotkey_config = getattr(config, hotkey_config_attr_name, None)
        if not hotkey_config or not hotkey_config.get('gtk_keys'):
            logging.warning(f"跳过无效或未找到的热键配置: {key_name}")
            continue
        for keyval in hotkey_config['gtk_keys']:
            keycode = get_keycode_from_keyval(keyval)
            if keycode == 0:
                logging.error(f"无法为 '{key_name}' (Keyval: {keyval}, Name: {Gdk.keyval_name(keyval)}) 找到 keycode，跳过此特定 keyval")
                continue
            x_mask = gdk_mask_to_x_mask(hotkey_config['gtk_mask'])
            key_id = (keycode, x_mask)
            is_toggle_key_flag = key_name in toggle_key_config_keys
            mode = get_key_mode(key_name)
            if key_id in registered_keys:
                for existing_mode, existing_name in registered_keys[key_id]:
                    conflict = False
                    if mode == 'toggle' or existing_mode == 'toggle': conflict = True
                    elif mode == existing_mode: conflict = True
                    if conflict:
                         logging.warning(f"热键冲突: {key_name} 与 {existing_name} 使用相同的键组合 (kc={keycode}, mask={x_mask}) 且模式冲突 ({mode} vs {existing_mode})")
            if key_id not in registered_keys:
                registered_keys[key_id] = []
            registered_keys[key_id].append((mode, key_name))
            keymap_tuples.append((keycode, x_mask, is_toggle_key_flag, key_name))
    if not any(k[2] for k in keymap_tuples):
         logging.warning("配置中未找到有效的切换键 (如 toggle_hotkeys_enabled)，热键启用/禁用功能可能无法通过键盘触发。")
    try:
        hotkey_listener = XlibHotkeyInterceptor(overlay, callbacks_map, keymap_tuples)
        hotkey_listener.start()
    except Exception as e:
        logging.error(f"启动 XlibHotkeyInterceptor 线程失败: {e}")

def cleanup_stale_temp_dirs(config):
    """在启动时清理由已退出的旧进程留下的临时目录"""
    try:
        raw_template = config.parser.get('System', 'temp_directory_base', fallback='/tmp/scroll_stitch_{pid}')
        template_path = Path(raw_template)
        parent_dir = template_path.parent
        name_template = template_path.name
        if not parent_dir.is_dir() or '{pid}' not in name_template:
            logging.warning("临时目录模板配置无效，跳过旧目录清理")
            return
        prefix, suffix = name_template.split('{pid}')
        current_pid = os.getpid()
        logging.info(f"正在扫描 {parent_dir} 中匹配 '{prefix}*{suffix}' 的残留目录...")
        for item in parent_dir.glob(f'{prefix}*{suffix}'):
            if not item.is_dir():
                continue
            try:
                pid_str = item.name[len(prefix):-len(suffix) if suffix else None]
                pid = int(pid_str)
            except (ValueError, IndexError):
                continue
            if pid == current_pid:
                continue
            # 检查旧PID对应的进程是否存在
            if not Path(f"/proc/{pid}").exists():
                logging.info(f"发现残留目录 {item} (来自已退出的进程 {pid})，正在清理...")
                try:
                    shutil.rmtree(item)
                except OSError as e:
                    logging.error(f"清理目录 {item} 失败: {e}")
            else:
                logging.info(f"发现来自另一正在运行的实例(PID:{pid})的目录 {item}，予以保留")
    except Exception as e:
        logging.error(f"执行残留目录清理时发生未知错误: {e}")

def check_dependencies():
    """在脚本启动时检查所有必需和可选的命令行依赖项"""
    optional_deps = {
        'paplay': '用于播放截图、撤销和完成时的音效',
        'xdg-open': '用于在截图完成后从通知中打开文件或目录',
    }
    if not IS_WAYLAND:
        optional_deps['xinput'] = '用于 X11 下“隐形光标”滚动模式，提供无干扰的滚动体验'
    missing_optional = []
    for dep, reason in optional_deps.items():
        if not shutil.which(dep):
            missing_optional.append(f"可选依赖 '{dep}' 缺失: {reason}")
    if missing_optional:
        logging.warning("警告：检测到缺少可选依赖项，部分功能可能无法使用或表现异常")
        for item in missing_optional:
            logging.warning(item)

def main():
    parser = argparse.ArgumentParser(description="一个自动/辅助式长截图工具")
    parser.add_argument(
        '-c', '--config',
        type=Path,
        help="指定一个自定义配置文件的路径"
    )
    args = parser.parse_args()
    global config
    config = Config(custom_path=args.config) 
    global log_queue
    log_queue = queue.Queue()
    cleanup_stale_temp_dirs(config)
    root_logger = logging.getLogger()
    if root_logger.hasHandlers():
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
    root_logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler(config.LOG_FILE, mode='w')
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    queue_handler = QueueHandler(log_queue)
    queue_handler.setFormatter(formatter)
    root_logger.addHandler(queue_handler)
    stdout_logger = logging.getLogger('STDOUT')
    sys.stdout = StreamToLoggerRedirector(stdout_logger, logging.INFO)
    stderr_logger = logging.getLogger('STDERR')
    sys.stderr = StreamToLoggerRedirector(stderr_logger, logging.ERROR)
    logging.info("标准输出和标准错误已被重定向到日志系统")
    check_dependencies()
    global WINDOW_MANAGER, FRAME_GRABBER
    if IS_WAYLAND:
        logging.info("检测到 Wayland 会话。正在检查 GtkLayerShell 支持...")
        if not GTK_LAYER_SHELL_AVAILABLE:
            logging.warning("未找到 'gtk-layer-shell' 库")
        elif not GtkLayerShell.is_supported():
            logging.warning("当前的 Wayland 合成器不支持 'wlr-layer-shell-unstable-v1' 协议。")
        WINDOW_MANAGER = WaylandWindowManager()
        FRAME_GRABBER = WaylandFrameGrabber()
        if not FRAME_GRABBER.prepare_sync():
            logging.error("用户未选择屏幕或授权失败，程序退出。")
            sys.exit(1)
    else:
        logging.info("检测到 X11 会话，加载 X11 后端")
        WINDOW_MANAGER = X11WindowManager()
        FRAME_GRABBER = X11FrameGrabber()
        try:
            X11 = ctypes.cdll.LoadLibrary('libX11.so.6')
            X11.XInitThreads()
            logging.info("已调用 XInitThreads() 以确保多线程安全")
        except Exception as e:
            logging.warning(f"无法调用 XInitThreads(): {e}。应用可能不稳定")
    display = Gdk.Display.get_default()
    if display is None:
        logging.error("无法获取 GDK Display，程序无法运行。")
        sys.exit(1)
    logging.info("启动全屏覆盖窗口，等待用户选择区域...")
    config.TMP_DIR.mkdir(parents=True, exist_ok=True)
    overlay = CaptureOverlay(config, WINDOW_MANAGER, FRAME_GRABBER)
    overlay.connect("destroy", Gtk.main_quit)
    overlay.show()
    setup_hotkey_listener(overlay)
    Gtk.main()

if __name__ == "__main__":
    main()
