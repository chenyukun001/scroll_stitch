#!/usr/bin/env python3
import sys
import ctypes
import webbrowser
import os
import shlex
import shutil
import re
import subprocess
import multiprocessing
import logging
import queue
import threading
from pathlib import Path
from datetime import datetime
import time
from PIL import Image
import cv2
import numpy as np
import configparser
import argparse
# GTK3 与 Cairo 导入
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('Notify', '0.7')
from gi.repository import Gtk, Gdk, GLib, GObject, Notify, Pango, GdkPixbuf
import cairo
# Pynput 用于全局热键与鼠标控制
from pynput import keyboard, mouse
from Xlib import display, X
# 全局实例
hotkey_listener = None
mouse_controller = mouse.Controller() # pynput 鼠标控制器
keyboard_controller = keyboard.Controller() # pynput 键盘控制器
config_window_instance = None
are_hotkeys_enabled = True
log_queue = None
EVDEV_AVAILABLE = False
active_notification = None

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
            self.parser.read(self.config_path)
        else:
            self.config_path = default_config_path
            self._create_default_config()
            self.parser.read(self.config_path)
        self._pynput_modifier_map = {
            'ctrl': keyboard.Key.ctrl, 'control': keyboard.Key.ctrl,
            'shift': keyboard.Key.shift,
            'alt': keyboard.Key.alt,
        }
        self._gtk_modifier_map = {
            'ctrl': Gdk.ModifierType.CONTROL_MASK, 'control': Gdk.ModifierType.CONTROL_MASK,
            'shift': Gdk.ModifierType.SHIFT_MASK,
            'alt': Gdk.ModifierType.MOD1_MASK,
        }
        self.GTK_MODIFIER_MASK = (
            Gdk.ModifierType.CONTROL_MASK | 
            Gdk.ModifierType.SHIFT_MASK | 
            Gdk.ModifierType.MOD1_MASK
        )
        self._key_map_pynput_special = {
            'space': keyboard.Key.space, 'enter': keyboard.Key.enter,
            'backspace': keyboard.Key.backspace, 'esc': keyboard.Key.esc,
            'up': keyboard.Key.up, 'down': keyboard.Key.down,
            'left': keyboard.Key.left, 'right': keyboard.Key.right,
            'f1': keyboard.Key.f1, 'f2': keyboard.Key.f2, 'f3': keyboard.Key.f3, 'f4': keyboard.Key.f4,
            'f5': keyboard.Key.f5, 'f6': keyboard.Key.f6, 'f7': keyboard.Key.f7, 'f8': keyboard.Key.f8,
            'f9': keyboard.Key.f9, 'f10': keyboard.Key.f10, 'f11': keyboard.Key.f11, 'f12': keyboard.Key.f12,
        }
        self._key_map_gtk_special = {
            'space': Gdk.KEY_space, 'enter': Gdk.KEY_Return,
            'backspace': Gdk.KEY_BackSpace, 'esc': Gdk.KEY_Escape,
            'up': Gdk.KEY_Up, 'down': Gdk.KEY_Down,
            'left': Gdk.KEY_Left, 'right': Gdk.KEY_Right,
            'f1': Gdk.KEY_F1, 'f2': Gdk.KEY_F2, 'f3': Gdk.KEY_F3, 'f4': Gdk.KEY_F4,
            'f5': Gdk.KEY_F5, 'f6': Gdk.KEY_F6, 'f7': Gdk.KEY_F7, 'f8': Gdk.KEY_F8,
            'f9': Gdk.KEY_F9, 'f10': Gdk.KEY_F10, 'f11': Gdk.KEY_F11, 'f12': Gdk.KEY_F12,
        }
        self._gtk_modifier_keyval_map = {
            'shift': (Gdk.KEY_Shift_L, Gdk.KEY_Shift_R),
            'ctrl': (Gdk.KEY_Control_L, Gdk.KEY_Control_R), 'control': (Gdk.KEY_Control_L, Gdk.KEY_Control_R),
            'alt': (Gdk.KEY_Alt_L, Gdk.KEY_Alt_R),
        }
        self._load_settings()

    def _parse_hotkey_string(self, hotkey_str: str):
        if not hotkey_str:
            return {'pynput_str': '', 'gtk_keys': tuple(), 'gtk_mask': 0, 'main_key_str': None}
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
        pynput_str_parts = []
        for part in clean_parts:
            key_name = part
            if key_name in self._pynput_modifier_map or key_name in self._key_map_pynput_special:
                pynput_str_parts.append(f"<{key_name}>")
            else:
                pynput_str_parts.append(key_name)
        pynput_str = "+".join(pynput_str_parts)
        return {
            'pynput_str': pynput_str,
            'gtk_keys': gtk_keys_tuple,
            'gtk_mask': gtk_mask,
            'main_key_str': main_key_str
        }

    def _load_settings(self):
        # Behavior
        self.ENABLE_FREE_SCROLL_MATCHING = self.parser.getboolean('Behavior', 'enable_free_scroll_matching', fallback=True)
        self.SCROLL_METHOD = self.parser.get('Behavior', 'scroll_method', fallback='move_user_cursor')
        self.CAPTURE_WITH_CURSOR = self.parser.getboolean('Behavior', 'capture_with_cursor', fallback=False)
        self.FORWARD_ACTION = self.parser.get('Behavior', 'forward_action', fallback='scroll_capture')
        self.BACKWARD_ACTION = self.parser.get('Behavior', 'backward_action', fallback='scroll_delete')
        # Interface.Components
        self.ENABLE_BUTTONS = self.parser.getboolean('Interface.Components', 'enable_buttons', fallback=True)
        self.ENABLE_SCROLL_BUTTONS = self.parser.getboolean('Interface.Components', 'enable_scroll_buttons', fallback=True)
        self.ENABLE_FREE_SCROLL = self.parser.getboolean('Interface.Components', 'enable_free_scroll', fallback=True)
        self.ENABLE_SLIDER = self.parser.getboolean('Interface.Components', 'enable_slider', fallback=True)
        self.SHOW_CAPTURE_COUNT = self.parser.getboolean('Interface.Components', 'show_capture_count', fallback=True)
        self.SHOW_TOTAL_DIMENSIONS = self.parser.getboolean('Interface.Components', 'show_total_dimensions', fallback=True)
        self.SHOW_INSTRUCTION_NOTIFICATION = self.parser.getboolean('Interface.Components', 'show_instruction_notification', fallback=True)
        # Interface.Layout
        self.BORDER_WIDTH = self.parser.getint('Interface.Layout', 'border_width', fallback=5)
        self.HANDLE_HEIGHT = self.parser.getint('Interface.Layout', 'handle_height', fallback=10)
        self.SLIDER_PANEL_WIDTH = self.parser.getint('Interface.Layout', 'slider_panel_width', fallback=55)
        self.SIDE_PANEL_WIDTH = self.parser.getint('Interface.Layout', 'side_panel_width', fallback=100)
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
        self.SLIDER_MARKS_PER_SIDE = self.parser.getint('Interface.Theme', 'slider_marks_per_side', fallback=4)
        self.SLIDER_MIN = -100
        self.SLIDER_MAX = 100
        self.SLIDER_PANEL_CSS = self.parser.get('Interface.Theme', 'slider_panel_css', fallback="""
 scale { color: #c0c0c0; font-size: 26px; }
 scale.vertical trough { background-color: rgba(30, 30, 50, 0.7); border: 1px solid #505070; border-radius: 5px; min-width: 5px; }
 scale mark indicator { background-color: #00d0ff; min-height: 2px; min-width: 8px; border-radius: 1px; }
 scale.vertical highlight { background-color: rgba(0, 208, 255, 0.6); border-radius: 5px; }
 scale label { color: white; text-shadow: 1px 1px 2px black; }
        """.strip()).lstrip()
        self.PROCESSING_DIALOG_CSS = self.parser.get('Interface.Theme', 'processing_dialog_css', fallback="""
 .background { background-color: rgba(20, 20, 30, 0.85); border-radius: 8px; color: white; font-size: 14px; }
        """.strip()).lstrip()
        self.INFO_PANEL_CSS = self.parser.get('Interface.Theme', 'info_panel_css', fallback="""
 .info-panel { background-color: rgba(43, 42, 51, 0.8); border: 1px solid #505070; border-radius: 8px; padding: 5px; color: #e0e0e0; }
 .info-panel label { font-weight: bold; }
 .info-panel #label_dimensions { font-size: 26px; color: #948bc1; }
 .info-panel #label_count { font-size: 24px; opacity: 0.9; }
        """.strip()).lstrip()
        # Interface.Strings
        self.DIALOG_QUIT_TITLE = self.parser.get('Interface.Strings', 'dialog_quit_title', fallback='确认放弃截图？')
        self.DIALOG_QUIT_MESSAGE = self.parser.get('Interface.Strings', 'dialog_quit_message', fallback='您已经截取了 {count} 张图片。确定要放弃它们吗？')
        self.DIALOG_QUIT_BTN_YES = self.parser.get('Interface.Strings', 'dialog_quit_button_yes', fallback='是 ({key})')
        self.DIALOG_QUIT_BTN_NO = self.parser.get('Interface.Strings', 'dialog_quit_button_no', fallback='否 ({key})')
        self.STR_CAPTURE_COUNT_FORMAT = self.parser.get('Interface.Strings', 'capture_count_format', fallback='截图: {count}')
        self.STR_PROCESSING_TEXT = self.parser.get('Interface.Strings', 'processing_dialog_text', fallback='正在处理…')
        self.STR_SLIDER_MARK_MIDDLE = self.parser.get('Interface.Strings', 'slider_mark_middle', fallback='中')
        self.STR_SLIDER_MARK_BOTTOM = self.parser.get('Interface.Strings', 'slider_mark_bottom', fallback='上')
        self.STR_SLIDER_MARK_TOP = self.parser.get('Interface.Strings', 'slider_mark_top', fallback='下')
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
        self.GRID_MATCHING_MAX_OVERLAP = self.parser.getint('Performance', 'grid_matching_max_overlap', fallback=20)
        self.FREE_SCROLL_MATCHING_MAX_OVERLAP = self.parser.getint('Performance', 'free_scroll_matching_max_overlap', fallback=200)
        self.MATCHING_MAX_OVERLAP = self.parser.getint('Performance', 'matching_max_overlap', fallback=20)
        self.SLIDER_SENSITIVITY = self.parser.getfloat('Performance', 'slider_sensitivity', fallback=1.8)
        self.MOUSE_MOVE_TOLERANCE = self.parser.getint('Performance', 'mouse_move_tolerance', fallback=5)
        self.MAX_VIEWER_DIMENSION = self.parser.getint('Performance', 'max_viewer_dimension', fallback=32767)
        self.FREE_SCROLL_DISTANCE_PX = self.parser.getint('Performance', 'free_scroll_distance_px', fallback=17)
        # Hotkeys
        self.str_capture = self.parser.get('Hotkeys', 'capture', fallback='space')
        self.str_finalize = self.parser.get('Hotkeys', 'finalize', fallback='enter')
        self.str_undo = self.parser.get('Hotkeys', 'undo', fallback='backspace')
        self.str_cancel = self.parser.get('Hotkeys', 'cancel', fallback='esc')
        self.str_dialog_confirm = self.parser.get('Hotkeys', 'dialog_confirm', fallback='space')
        self.str_dialog_cancel = self.parser.get('Hotkeys', 'dialog_cancel', fallback='esc')
        self.str_scroll_up = self.parser.get('Hotkeys', 'scroll_up', fallback='b')
        self.str_scroll_down = self.parser.get('Hotkeys', 'scroll_down', fallback='f')
        self.str_configure_scroll_unit = self.parser.get('Hotkeys', 'configure_scroll_unit', fallback='s')
        self.str_toggle_grid_mode = self.parser.get('Hotkeys', 'toggle_grid_mode', fallback='<shift>')
        self.str_open_config_editor = self.parser.get('Hotkeys', 'open_config_editor', fallback='g')
        self.str_toggle_hotkeys_enabled = self.parser.get('Hotkeys', 'toggle_hotkeys_enabled', fallback='f4')
        self.HOTKEY_CAPTURE = self._parse_hotkey_string(self.str_capture)
        self.HOTKEY_FINALIZE = self._parse_hotkey_string(self.str_finalize)
        self.HOTKEY_UNDO = self._parse_hotkey_string(self.str_undo)
        self.HOTKEY_CANCEL = self._parse_hotkey_string(self.str_cancel)
        self.HOTKEY_SCROLL_UP = self._parse_hotkey_string(self.str_scroll_up)
        self.HOTKEY_SCROLL_DOWN = self._parse_hotkey_string(self.str_scroll_down)
        self.HOTKEY_CONFIGURE_SCROLL_UNIT = self._parse_hotkey_string(self.str_configure_scroll_unit)
        self.HOTKEY_TOGGLE_GRID_MODE = self._parse_hotkey_string(self.str_toggle_grid_mode)
        self.HOTKEY_OPEN_CONFIG_EDITOR = self._parse_hotkey_string(self.str_open_config_editor)
        self.HOTKEY_TOGGLE_HOTKEYS_ENABLED = self._parse_hotkey_string(self.str_toggle_hotkeys_enabled)
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
forward_action = scroll_capture
backward_action = scroll_delete

[Interface.Components]
enable_buttons = true
enable_scroll_buttons = true
enable_free_scroll = true
enable_slider = true
show_capture_count = true
show_total_dimensions = true
show_instruction_notification = true

[Interface.Layout]
border_width = 5
handle_height = 10
slider_panel_width = 55
side_panel_width = 100
button_spacing = 5
processing_dialog_width = 200
processing_dialog_height = 90
processing_dialog_spacing = 15
processing_dialog_border_width = 20

[Interface.Theme]
border_color = 0.73, 0.25, 0.25, 1.00
matching_indicator_color = 0.60, 0.76, 0.95, 1.00
slider_marks_per_side = 4
slider_panel_css =
    scale { color: #c0c0c0; font-size: 26px; }
    scale.vertical trough { background-color: rgba(30, 30, 50, 0.7); border: 1px solid #505070; border-radius: 5px; min-width: 5px; }
    scale mark indicator { background-color: #00d0ff; min-height: 2px; min-width: 8px; border-radius: 1px; }
    scale.vertical highlight { background-color: rgba(0, 208, 255, 0.6); border-radius: 5px; }
    scale label { color: white; text-shadow: 1px 1px 2px black; }
processing_dialog_css =
    .background { background-color: rgba(20, 20, 30, 0.85); border-radius: 8px; color: white; font-size: 14px; }
info_panel_css =
    .info-panel { background-color: rgba(43, 42, 51, 0.8); border: 1px solid #505070; border-radius: 8px; padding: 5px; color: #e0e0e0; }
    .info-panel label { font-weight: bold; }
    .info-panel #label_dimensions { font-size: 26px; color: #948bc1; }
    .info-panel #label_count { font-size: 24px; opacity: 0.9; }

[Interface.Strings]
dialog_quit_title = 确认放弃截图？
dialog_quit_message = 您已经截取了 {count} 张图片。确定要放弃它们吗？
dialog_quit_button_yes = 是 ({key})
dialog_quit_button_no = 否 ({key})
processing_dialog_text = 正在处理…
slider_mark_middle = 中
slider_mark_top = 下
slider_mark_bottom = 上
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
slider_sensitivity = 1.8
mouse_move_tolerance = 5
max_viewer_dimension = 32767
free_scroll_distance_px = 17

[Hotkeys]
capture = space
finalize = enter
undo = backspace
cancel = esc
scroll_up = b
scroll_down = f
configure_scroll_unit = s
toggle_grid_mode = <shift>
open_config_editor = g
toggle_hotkeys_enabled = f4
dialog_confirm = space
dialog_cancel = esc

[ApplicationScrollUnits]
        """.strip()

    def _create_default_config(self):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, 'w') as f:
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
        try:
            if not self.parser.has_section('ApplicationScrollUnits'):
                self.parser.add_section('ApplicationScrollUnits')
            value_to_save = f"{unit_value},{str(matching_enabled).lower()}"
            self.parser.set('ApplicationScrollUnits', app_class, value_to_save)
            with open(self.config_path, 'w') as configfile:
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
            with open(self.config_path, 'w') as configfile:
                self.parser.write(configfile)
            logging.info(f"成功将配置 '{key} = {value}' 写入 [{section}]")
            return True
        except Exception as e:
            logging.error(f"写入配置文件失败: {e}")
            return False

class EvdevTouchpadSimulator:
    def __init__(self, uinput_device, touchpad_width=1023, touchpad_height=767):
        self.ui = uinput_device
        self.TOUCHPAD_WIDTH = touchpad_width
        self.TOUCHPAD_HEIGHT = touchpad_height

    def scroll(self, distance, steps=25):
        from evdev import ecodes as e
        e = e
        max_swipe_dist = self.TOUCHPAD_HEIGHT // 3 
        remaining_distance = distance
        sign = 1 if distance > 0 else -1
        while abs(remaining_distance) > 0.1:
            current_swipe_dist = min(max_swipe_dist, abs(remaining_distance)) * sign
            remaining_distance -= current_swipe_dist
            start_x1, start_y1 = self.TOUCHPAD_WIDTH // 2 - 50, self.TOUCHPAD_HEIGHT // 2
            start_x2, start_y2 = self.TOUCHPAD_WIDTH // 2 + 50, self.TOUCHPAD_HEIGHT // 2
            base_tracking_id = int(time.time()) & 0xFFFF
            pressure, touch_major = 128, 15
            # 触摸开始
            # 触摸点 1
            self.ui.write(e.EV_ABS, e.ABS_MT_SLOT, 0)
            self.ui.write(e.EV_ABS, e.ABS_MT_TRACKING_ID, base_tracking_id)
            self.ui.write(e.EV_ABS, e.ABS_MT_POSITION_X, start_x1)
            self.ui.write(e.EV_ABS, e.ABS_MT_POSITION_Y, start_y1)
            self.ui.write(e.EV_ABS, e.ABS_MT_PRESSURE, pressure)
            self.ui.write(e.EV_ABS, e.ABS_MT_TOUCH_MAJOR, touch_major)
            # 触摸点 2
            self.ui.write(e.EV_ABS, e.ABS_MT_SLOT, 1)
            self.ui.write(e.EV_ABS, e.ABS_MT_TRACKING_ID, base_tracking_id + 1)
            self.ui.write(e.EV_ABS, e.ABS_MT_POSITION_X, start_x2)
            self.ui.write(e.EV_ABS, e.ABS_MT_POSITION_Y, start_y2)
            self.ui.write(e.EV_ABS, e.ABS_MT_PRESSURE, pressure)
            self.ui.write(e.EV_ABS, e.ABS_MT_TOUCH_MAJOR, touch_major)
            self.ui.write(e.EV_KEY, e.BTN_TOUCH, 1)
            self.ui.write(e.EV_KEY, e.BTN_TOOL_DOUBLETAP, 1)
            self.ui.syn()
            time.sleep(0.05)
            # 移动过程
            for i in range(1, steps + 1):
                progress = i / steps
                delta_y = int(current_swipe_dist * progress)
                current_y1 = max(0, min(self.TOUCHPAD_HEIGHT, start_y1 + delta_y))
                current_y2 = max(0, min(self.TOUCHPAD_HEIGHT, start_y2 + delta_y))
                self.ui.write(e.EV_ABS, e.ABS_MT_SLOT, 0)
                self.ui.write(e.EV_ABS, e.ABS_MT_POSITION_Y, current_y1)
                self.ui.write(e.EV_ABS, e.ABS_MT_SLOT, 1)
                self.ui.write(e.EV_ABS, e.ABS_MT_POSITION_Y, current_y2)
                self.ui.syn()
                time.sleep(0.008)
            # 触摸结束
            self.ui.write(e.EV_ABS, e.ABS_MT_SLOT, 0)
            self.ui.write(e.EV_ABS, e.ABS_MT_TRACKING_ID, -1)
            self.ui.write(e.EV_ABS, e.ABS_MT_SLOT, 1)
            self.ui.write(e.EV_ABS, e.ABS_MT_TRACKING_ID, -1)
            self.ui.write(e.EV_KEY, e.BTN_TOOL_DOUBLETAP, 0)
            self.ui.write(e.EV_KEY, e.BTN_TOUCH, 0)
            self.ui.syn()
            if abs(remaining_distance) > 0.1:
                time.sleep(0.01)

class InvisibleCursorScroller:
    def __init__(self, screen_w, screen_h):
        self.screen_w = screen_w
        self.screen_h = screen_h
        self.master_id = None
        self.ui_mouse = None
        self.ui_touchpad = None
        self.unique_name = f"scroll-stitch-cursor-{int(time.time())}"
        self.park_position = (self.screen_w - 1, self.screen_h - 1)
        self.is_ready = False

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
            subprocess.check_call(['xinput', 'create-master', self.unique_name])
            output = subprocess.check_output(['xinput', 'list']).decode()
            match = re.search(fr'{re.escape(self.unique_name)} pointer\s+id=(\d+)', output)
            if not match:
                raise RuntimeError(f"未能找到新 master device 的 ID: {self.unique_name}")
            self.master_id = int(match.group(1))
            self._create_virtual_devices()
            mouse_dev_name = f"VirtualMouse-{self.unique_name}"
            touchpad_dev_name = f"VirtualTouchpad-{self.unique_name}"
            if not self._wait_for_device(mouse_dev_name) or not self._wait_for_device(touchpad_dev_name):
                 raise RuntimeError("一个或多个虚拟设备未能被 X Server 及时识别")
            subprocess.check_call(['xinput', 'reattach', mouse_dev_name, str(self.master_id)])
            subprocess.check_call(['xinput', 'reattach', touchpad_dev_name, str(self.master_id)])
            self.park()
            logging.info(f"已创建并附加隐形光标 (Master ID: {self.master_id})")
            self.is_ready = True
            return self
        except Exception as e:
            logging.error(f"创建隐形光标失败: {e}")
            self.cleanup()
            self.is_ready = False
            raise

    def park(self):
        self.move(*self.park_position)
        logging.info(f"隐形光标已停放至 {self.park_position}")

    def _create_virtual_devices(self):
        # 导入 evdev 库
        from evdev import UInput, ecodes as e, AbsInfo
        # 虚拟鼠标 (用于移动光标)
        mouse_caps = {
            e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT],
            e.EV_REL: [e.REL_WHEEL],
            e.EV_ABS: [
                (e.ABS_X, AbsInfo(value=0, min=0, max=self.screen_w - 1, fuzz=0, flat=0, resolution=0)),
                (e.ABS_Y, AbsInfo(value=0, min=0, max=self.screen_h - 1, fuzz=0, flat=0, resolution=0)),
            ],
        }
        self.ui_mouse = UInput(mouse_caps, name=f'VirtualMouse-{self.unique_name}')
        # 虚拟触摸板 (用于滚动)
        self.TOUCHPAD_WIDTH = 1023
        self.TOUCHPAD_HEIGHT = 767
        MAX_SLOTS = 5
        MAX_PRESSURE = 255
        MAX_TOUCH_MAJOR = 255
        touchpad_caps = {
            e.EV_KEY: [e.BTN_TOUCH, e.BTN_TOOL_FINGER, e.BTN_TOOL_DOUBLETAP],
            e.EV_ABS: [
                (e.ABS_MT_SLOT, AbsInfo(value=0, min=0, max=MAX_SLOTS-1, fuzz=0, flat=0, resolution=0)),
                (e.ABS_MT_TRACKING_ID, AbsInfo(value=0, min=0, max=65535, fuzz=0, flat=0, resolution=0)),
                (e.ABS_MT_POSITION_X, AbsInfo(value=0, min=0, max=self.TOUCHPAD_WIDTH, fuzz=0, flat=0, resolution=15)),
                (e.ABS_MT_POSITION_Y, AbsInfo(value=0, min=0, max=self.TOUCHPAD_HEIGHT, fuzz=0, flat=0, resolution=15)),
                (e.ABS_MT_PRESSURE, AbsInfo(value=0, min=0, max=MAX_PRESSURE, fuzz=0, flat=0, resolution=0)),
                (e.ABS_MT_TOUCH_MAJOR, AbsInfo(value=0, min=0, max=MAX_TOUCH_MAJOR, fuzz=0, flat=0, resolution=0)),
            ],
        }
        self.ui_touchpad = UInput(touchpad_caps, name=f'VirtualTouchpad-{self.unique_name}')
        self.touchpad_simulator = EvdevTouchpadSimulator(self.ui_touchpad)

    def move(self, x, y):
        from evdev import ecodes as e
        self.ui_mouse.write(e.EV_ABS, e.ABS_X, x)
        self.ui_mouse.write(e.EV_ABS, e.ABS_Y, y)
        self.ui_mouse.syn()

    def discrete_scroll(self, num_clicks):
        """模拟鼠标滚轮进行离散滚动"""
        from evdev import ecodes as e
        if num_clicks == 0:
            return
        value = -1 if num_clicks < 0 else 1
        for _ in range(abs(num_clicks)):
            self.ui_mouse.write(e.EV_REL, e.REL_WHEEL, value)
            self.ui_mouse.syn()
            time.sleep(0.01)

    def scroll(self, distance, steps=25):
        self.touchpad_simulator.scroll(distance, steps)

    def cleanup(self):
        if self.ui_mouse:
            self.ui_mouse.close()
        if self.ui_touchpad:
            self.ui_touchpad.close()
        if self.master_id is not None:
            try:
                output = subprocess.check_output(['xinput', 'list']).decode()
                core_ptr_match = re.search(r'Virtual core pointer\s+id=(\d+)', output)
                if core_ptr_match:
                    core_id = core_ptr_match.group(1)
                    subprocess.check_call(['xinput', 'remove-master', f'{self.unique_name} pointer', 'AttachToMaster', core_id])
                else:
                    subprocess.check_call(['xinput', 'remove-master', f'{self.unique_name} pointer'])
                logging.info(f"已移除隐形光标 (Master ID: {self.master_id})")
            except Exception as e:
                logging.error(f"清理隐形光标失败: {e}")
        self.master_id = None

class VirtualTouchpadController:
    """ 只有在其实例被创建时，才会尝试导入 evdev 库 """
    def __init__(self):
        try:
            from evdev import UInput, ecodes as e, AbsInfo
            self.e = e
        except ImportError:
            logging.error("可选依赖 evdev 未安装。滑块滚动功能将不可用")
            logging.info("你可以通过 'pip install evdev' 来安装它")
            raise ImportError("evdev library not found.")
        self.TOUCHPAD_WIDTH = 1023
        self.TOUCHPAD_HEIGHT = 767
        MAX_SLOTS = 5
        MAX_PRESSURE = 255
        MAX_TOUCH_MAJOR = 255
        capabilities = {
            e.EV_KEY: [e.BTN_TOUCH, e.BTN_TOOL_FINGER, e.BTN_TOOL_DOUBLETAP],
            e.EV_ABS: [
                (e.ABS_MT_SLOT, AbsInfo(value=0, min=0, max=MAX_SLOTS-1, fuzz=0, flat=0, resolution=0)),
                (e.ABS_MT_TRACKING_ID, AbsInfo(value=0, min=0, max=65535, fuzz=0, flat=0, resolution=0)),
                (e.ABS_MT_POSITION_X, AbsInfo(value=0, min=0, max=self.TOUCHPAD_WIDTH, fuzz=0, flat=0, resolution=15)),
                (e.ABS_MT_POSITION_Y, AbsInfo(value=0, min=0, max=self.TOUCHPAD_HEIGHT, fuzz=0, flat=0, resolution=15)),
                (e.ABS_MT_PRESSURE, AbsInfo(value=0, min=0, max=MAX_PRESSURE, fuzz=0, flat=0, resolution=0)),
                (e.ABS_MT_TOUCH_MAJOR, AbsInfo(value=0, min=0, max=MAX_TOUCH_MAJOR, fuzz=0, flat=0, resolution=0)),
            ],
        }
        # UInput 的初始化
        self.ui_device = UInput(capabilities, name='scroll_stitch-virtual-touchpad', version=0x1)
        self.simulator = EvdevTouchpadSimulator(self.ui_device)
        logging.info("VirtualTouchpadController 初始化成功，虚拟触摸板设备已创建")

    def scroll(self, distance, steps=25):
        self.simulator.scroll(distance, steps)

    def close(self):
        if self.ui_device:
            self.ui_device.close()
            logging.info("虚拟触摸板设备已关闭")

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

def send_desktop_notification(title, message, sound_name=None, level="normal", action_path=None, controller=None, width=0, height=0):
    global active_notification
    try:
        if active_notification:
            logging.info(f"正在关闭旧通知...")
            active_notification.close()
        if sound_name:
            play_sound(sound_name)
        notification = Notify.Notification.new(title, message, "dialog-information")
        if controller:
            controller.final_notification = notification
        action_type = config.NOTIFICATION_CLICK_ACTION
        if action_path and action_type != 'none':
            def on_action_clicked(n, action_id, user_data_path_str):
                try:
                    logging.info(f"通知动作被触发: {action_id}, 路径: {user_data_path_str}")
                    path_obj = Path(user_data_path_str)
                    is_large_image = False
                    if config.MAX_VIEWER_DIMENSION >= 0 and width > 0 and height > 0:
                        max_dim = max(width, height)
                        if max_dim > config.MAX_VIEWER_DIMENSION:
                            is_large_image = True
                            logging.info(f"图片最大边 {max_dim} 超出阈值 {config.MAX_VIEWER_DIMENSION}，将使用自定义命令打开")
                    if is_large_image:
                        opener_command = config.LARGE_IMAGE_OPENER
                        if opener_command == 'default_browser':
                            uri = path_obj.as_uri()
                            logging.info(f"使用 webbrowser 模块打开 URI: {uri}")
                            webbrowser.open(uri)
                        else:
                            command_str = opener_command.replace('{filepath}', str(path_obj))
                            command_list = shlex.split(command_str)
                            logging.info(f"执行自定义大图打开命令: {command_list}")
                            subprocess.Popen(command_list)
                    else:
                        command = ["xdg-open", str(path_obj)]
                        logging.info(f"执行默认打开命令: {command}")
                        subprocess.Popen(command)
                except Exception as e:
                    logging.error(f"执行通知动作失败: {e}")
                n.close()
            target_path = None
            action_label = ""
            if action_type == 'open_file' and action_path.is_file():
                target_path = str(action_path)
                action_label = "打开文件"
            elif action_type == 'open_directory' and action_path.exists():
                target_path = str(action_path.parent if action_path.is_file() else action_path)
                action_label = "打开目录"
            if target_path and action_label:
                # 添加动作按钮
                notification.add_action(
                    "open_action",       # 动作的唯一 ID
                    action_label,        # 按钮上显示的文本
                    on_action_clicked,   # 回调函数
                    target_path          # 传递给回调函数的数据
                )
                notification.add_action(
                    "default",
                    action_label,
                    on_action_clicked,
                    target_path
                )
        urgency_map = {"low": Notify.Urgency.LOW, "normal": Notify.Urgency.NORMAL, "critical": Notify.Urgency.CRITICAL}
        notification.set_urgency(urgency_map.get(level, Notify.Urgency.NORMAL))
        def on_closed(n):
            logging.info("通知已关闭，准备执行最终清理和退出程序")
            global active_notification
            if active_notification == n:
                active_notification = None
            if controller:
                controller.final_notification = None
                GLib.idle_add(controller._perform_cleanup)
        notification.connect("closed", on_closed)
        notification.show()
        active_notification = notification
        logging.info(f"已通过 libnotify 发送通知: '{title}' - '{message}'")
    except Exception as e:
        logging.error(f"使用 libnotify 发送通知失败: {e}")
        if controller:
            GLib.idle_add(controller._perform_cleanup)

def select_area():
    """使用 slop 选择一个区域，并返回其几何信息字符串"""
    try:
        border_width_str = str(config.BORDER_WIDTH)
        color_str = ",".join(map(str, config.BORDER_COLOR))
        logging.info(f"正在使用配置调用 slop: border={border_width_str}, color={color_str}")
        cmd = ["slop", "-b", border_width_str, "-c", color_str ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        geometry = result.stdout.strip()
        return geometry if geometry else None
    except FileNotFoundError:
        logging.error("`slop` 命令未找到。请确保 slop 已经安装并位于 PATH 中")
        send_desktop_notification("错误：依赖缺失", "`slop` 命令未找到，程序无法启动")
        raise
    except subprocess.CalledProcessError as e:
        logging.warning(f"slop 选择被取消或失败: {e.stderr}")
        return None

def capture_area(x: int, y: int, w: int, h: int, filepath: Path) -> bool:
    """使用 GDK 从根窗口截取指定区域并保存到文件"""
    try:
        root_window = Gdk.get_default_root_window()
        if not root_window:
            logging.error("无法获取 GDK 根窗口")
            return False
        pixbuf = Gdk.pixbuf_get_from_window(root_window, x, y, w, h)
        if not pixbuf:
            logging.error(f"从区域 {w}x{h}+{x}+{y} 抓取 pixbuf 失败")
            return False
        pixbuf.savev(str(filepath), 'png', [], [])
        logging.info(f"成功使用 GDK 截图到: {filepath}")
        return True
    except Exception as e:
        logging.error(f"使用 GDK 截图失败: {e}")
        return False

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

def _find_overlap_pyramid(img_top, img_bottom, max_overlap_search):
    PYRAMID_CUTOFF_THRESHOLD = 50
    if max_overlap_search < PYRAMID_CUTOFF_THRESHOLD:
        return _find_overlap_brute_force(img_top, img_bottom, 1, max_overlap_search)
    scale_factor = (2.0 / max_overlap_search)**0.5
    scale_factor = max(0.04, min(scale_factor, 0.5))
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
        logging.warning(f"搜索的精确范围无效 [{min_fine_search}, {max_fine_search}]，可能匹配失败")
        return estimated_overlap, 0.0
    return _find_overlap_brute_force(img_top, img_bottom, min_fine_search, max_fine_search)

def stitch_images_in_memory(image_paths, session, progress_callback=None):
    if not image_paths:
        return None, 0.0
    try:
        images_pil = [Image.open(p) for p in image_paths]
    except Exception as e:
        logging.error(f"加载图片失败: {e}")
        return None, 0.0
    num_images = len(images_pil)
    if num_images <= 1:
        return images_pil[0].copy() if images_pil else None, 0.0
    images_np = [cv2.cvtColor(np.asarray(img.convert('RGB')), cv2.COLOR_RGB2BGR) for img in images_pil]
    is_grid_mode_session = session.detected_app_class is not None
    is_grid_mode_matching = session.is_matching_enabled and is_grid_mode_session
    is_free_scroll_matching = not is_grid_mode_session and config.ENABLE_FREE_SCROLL_MATCHING
    use_matching = is_grid_mode_matching or is_free_scroll_matching
    total_matching_time = 0.0
    overlaps = [0] * (num_images - 1)
    if not use_matching:
        logging.info("使用简单粘贴模式进行拼接...")
    else:
        match_start_time = time.perf_counter()
        max_overlap_config = (config.GRID_MATCHING_MAX_OVERLAP if is_grid_mode_matching 
                              else config.FREE_SCROLL_MATCHING_MAX_OVERLAP)
        for i in range(num_images - 1):
            img_top_np = images_np[i]
            img_bottom_np = images_np[i+1]
            h1, _, _ = img_top_np.shape
            h2, _, _ = img_bottom_np.shape
            prediction_successful = False
            if session.known_scroll_distances:
                for s_known in session.known_scroll_distances:
                    predicted_overlap = h2 - s_known
                    if 1 <= predicted_overlap < min(h1, h2):
                        _, score = _find_overlap_brute_force(img_top_np, img_bottom_np, predicted_overlap, predicted_overlap)
                        PREDICTION_THRESHOLD = 0.995
                        if score > PREDICTION_THRESHOLD:
                            overlaps[i] = predicted_overlap
                            prediction_successful = True
                            logging.info(f"图片对 {i+1}/{i+2} 预测成功：重叠 {predicted_overlap}px, 相似度 {score:.3f}")
                            break
            if prediction_successful:
                if progress_callback: GLib.idle_add(progress_callback, (i + 1) / (num_images - 1))
                continue
            logging.info(f"图片对 {i+1}/{i+2} 预测失败，执行全范围搜索...")
            effective_search_range = min(max_overlap_config, h1 - 1, h2 - 1)
            overlap_value = 0
            if effective_search_range > 0:
                found_overlap, best_match_score = _find_overlap_pyramid(
                    img_top_np, img_bottom_np, effective_search_range
                )
                QUALITY_THRESHOLD = 0.95
                if best_match_score >= QUALITY_THRESHOLD:
                    overlap_value = found_overlap
                    logging.info(f"图片对 {i+1}/{i+2} 搜索匹配成功：重叠 {overlap_value}px, 相似度 {best_match_score:.3f}")
                else:
                    logging.warning(f"图片对 {i+1}/{i+2} 搜索匹配失败 (最高相似度 {best_match_score:.3f} < {QUALITY_THRESHOLD})")
            overlaps[i] = overlap_value
            if overlap_value > 0:
                s_new = h2 - overlap_value
                if s_new > 0 and s_new not in session.known_scroll_distances:
                    session.known_scroll_distances.append(s_new)
                    logging.info(f"学习到新滚动距离: {s_new}px，可用于下次预测")
            if progress_callback: GLib.idle_add(progress_callback, (i + 1) / (num_images - 1))
        total_matching_time = time.perf_counter() - match_start_time
        logging.info(f"模板匹配总耗时: {total_matching_time:.3f} 秒")
    final_width = images_pil[0].width
    final_height = sum(img.height for img in images_pil) - sum(overlaps)
    logging.info(f"计算完成，最终尺寸: {final_width}x{final_height}")
    stitched_image = Image.new('RGBA', (final_width, final_height))
    y_offset = 0
    for i, img in enumerate(images_pil):
        stitched_image.paste(img, (0, y_offset))
        if i < num_images - 1:
            y_offset += img.height - overlaps[i]
    if session.known_scroll_distances:
        logging.info(f"本次会话学习到的滚动距离列表: {sorted(session.known_scroll_distances)}")
    for img in images_pil:
        img.close()
    return stitched_image, total_matching_time

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

def activate_window_with_xdotool(xid):
    """使用 xdotool 激活一个窗口"""
    if not xid:
        logging.warning("无法激活窗口：XID 不可用")
        return False
    try:
        subprocess.run(
            ["xdotool", "windowactivate", "--sync", str(xid)],
            check=True, capture_output=True, timeout=0.5
        )
        logging.info(f"已使用 xdotool 成功激活窗口 XID {xid}")
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logging.error(f"使用 xdotool 激活窗口 {xid} 失败: {e}")
        return False

class CaptureSession:
    """管理一次滚动截图会话的数据和状态"""
    def __init__(self, geometry_str: str):
        self.captures = []
        self.is_horizontally_locked: bool = False
        self.geometry: dict = self._parse_geometry(geometry_str)
        self.total_height: int = 0
        self.image_width: int = 0
        self.detected_app_class: str = None
        self.is_matching_enabled: bool = False
        self.known_scroll_distances = []

    def _parse_geometry(self, geometry_str: str):
        """从 "WxH+X+Y" 格式的字符串中解析出几何信息"""
        parts = geometry_str.strip().split('+')
        dims, x_str, y_str = parts[0], parts[1], parts[2]
        w_str, h_str = dims.split('x')
        return {'x': int(x_str), 'y': int(y_str), 'w': int(w_str), 'h': int(h_str)}

    def add_capture(self, filepath: str):
        """添加一张截图并更新状态"""
        self.captures.append(filepath)
        try:
            with Image.open(filepath) as img:
                w, h = img.size
                if not self.is_horizontally_locked:
                    self.image_width = w
                self.total_height += h
        except Exception as e:
            logging.error(f"无法读取图片 {filepath} 的尺寸: {e}")
        if not self.is_horizontally_locked:
            self.is_horizontally_locked = True
            logging.info("首次截图完成，窗口水平位置和宽度已被锁定")

    def pop_last_capture(self):
        """移除最后一张截图并更新状态"""
        if not self.captures:
            return None
        last_capture_path = self.captures.pop()
        try:
            with Image.open(last_capture_path) as img:
                _, h = img.size
                self.total_height -= h
        except Exception as e:
             logging.error(f"撤销时无法读取图片 {last_capture_path} 的尺寸: {e}")
        if not self.captures and self.is_horizontally_locked:
            self.is_horizontally_locked = False
            self.image_width = 0
            self.total_height = 0
            logging.info("所有截图均已删除，已解锁窗口水平调整功能")
        return last_capture_path

    @property
    def capture_count(self) -> int:
        return len(self.captures)

    def update_geometry(self, new_geometry):
        """更新捕获区域的几何信息，并确保所有值为整数"""
        self.geometry = {key: int(value) for key, value in new_geometry.items()}

    def cleanup(self):
        """清理临时文件和目录"""
        if config.TMP_DIR.exists():
            try:
                shutil.rmtree(config.TMP_DIR)
            except OSError as e:
                logging.error(f"清理临时目录失败: {e}")

class GridModeController:
    def __init__(self, config: Config, session: CaptureSession, view: 'CaptureOverlay'):
        self.config = config
        self.session = session
        self.view = view
        self.is_active = False
        self.grid_unit = 0
        try:
            self.x_display = display.Display()
        except Exception as e:
            self.x_display = None
            logging.error(f"无法连接到 X Display，整格模式功能将不可用: {e}")

    def _get_window_at_coords(self, d: display.Display, x: int, y: int):
        """在不移动鼠标的情况下，获取指定屏幕坐标下的窗口ID和WM_CLASS"""
        if not d:
            return None, None
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
                    geom = win_obj.get_geometry()
                    translated = win_obj.translate_coords(root, 0, 0)
                    abs_x, abs_y = -translated.x, -translated.y
                    win_w, win_h = geom.width, geom.height
                    if not (abs_x <= x < abs_x + win_w and abs_y <= y < abs_y + win_h):
                        continue
                    wm_class = win_obj.get_wm_class()
                    if wm_class and 'Scroll_stitch.py' not in wm_class[1]:
                        app_class = wm_class[1].lower()
                        logging.info(
                            f"定位成功! ID={win_obj.id}, Class={app_class}, "
                            f"AbsGeom=({abs_x},{abs_y},{win_w},{win_h}), Point=({x},{y})"
                        )
                        return win_obj.id, app_class
                except Exception as e:
                    logging.error(f"错误： {e}")
        except Exception as e:
            logging.error(f"使用 python-xlib 查找窗口时发生严重错误: {e}")
        return None, None

    def _get_app_class_at_center(self):
        win_x, win_y = self.view.get_position()
        shot_x = win_x + self.view.left_panel_w + config.BORDER_WIDTH
        shot_y = win_y + config.BORDER_WIDTH
        center_x = int(shot_x + self.session.geometry['w'] / 2)
        center_y = int(shot_y + self.session.geometry['h'] / 2)
        _, app_class = self._get_window_at_coords(self.x_display, center_x, center_y)
        if app_class:
            logging.info(f"检测到底层应用: {app_class}")
        return app_class

    def toggle(self):
        """切换整格模式的开关"""
        if self.is_active:
            self.is_active = False
            self.grid_unit = 0
            self.session.detected_app_class = None
            self.session.is_matching_enabled = False
            self.view.side_panel.button_panel.set_scroll_buttons_visible(config.ENABLE_FREE_SCROLL)
            self.view.queue_draw()
            logging.info("整格模式已关闭")
            send_desktop_notification("整格模式已关闭", "边框拖动已恢复自由模式")
            return
        # 尝试开启整格模式
        app_class = self._get_app_class_at_center()
        if not app_class:
            send_desktop_notification("模式切换失败", "无法检测到底层应用程序")
            return
        grid_unit_from_config, matching_enabled = config.get_scroll_unit(app_class)
        if grid_unit_from_config > 0:
            self.is_active = True
            self.grid_unit = grid_unit_from_config
            self.session.detected_app_class = app_class
            self.session.is_matching_enabled = matching_enabled
            self.view.side_panel.button_panel.set_scroll_buttons_visible(True)
            match_status = "启用" if matching_enabled else "禁用"
            logging.info(f"为应用 '{app_class}' 启用整格模式，单位: {self.grid_unit}px, 模板匹配: {match_status}")
            send_desktop_notification("整格模式已启用", f"应用: {app_class}\n滚动单位: {self.grid_unit}px\n误差修正: {match_status}")
            self._snap_current_height()
        else:
            logging.warning(f"应用 '{app_class}' 未在配置中找到滚动单位，无法启用整格模式")
            send_desktop_notification("模式切换失败", f"'{app_class}' 的滚动单位未配置")

    def _snap_current_height(self):
        """将当前选区的高度对齐到最近的整格单位"""
        if not self.is_active or self.grid_unit == 0:
            return
        geo = self.session.geometry.copy()
        current_h = geo['h']
        # 计算最接近的整数倍
        snapped_h = max(self.grid_unit, round(current_h / self.grid_unit) * self.grid_unit)
        if geo['h'] != snapped_h:
            geo['h'] = snapped_h
            self.session.update_geometry(geo)
            self.view.update_layout()
            logging.info(f"高度已自动对齐到 {snapped_h}px")

    def start_calibration(self):
        """启动交互式配置流程"""
        if self.is_active:
            send_desktop_notification("操作无效", "请先按 Shift 键退出整格模式再进行配置")
            return
        app_class = self._get_app_class_at_center()
        if not app_class:
            send_desktop_notification("配置失败", "无法检测到底层应用程序")
            return
        logging.info(f"为应用 '{app_class}' 启动交互式配置...")
        num_ticks = self.view.show_scroll_config_dialog()
        if num_ticks > 0:
            region_height = self.session.geometry['h']
            pixels_per_tick = region_height / num_ticks
            rounded_unit = round(pixels_per_tick)
            logging.info(f"计算结果: 区域高度={region_height}px, 格数={num_ticks}, 每格像素≈{pixels_per_tick:.2f}, 取整为 {rounded_unit}px")
            if config.save_scroll_unit(app_class, rounded_unit):
                send_desktop_notification("配置成功", f"已为 '{app_class}' 保存滚动单位: {rounded_unit}px")
            else:
                send_desktop_notification("配置失败", "写入配置文件时发生错误，请查看日志")

class ScrollManager:
    def __init__(self, config: Config, session: CaptureSession, view: 'CaptureOverlay'):
        self.config = config
        self.session = session
        self.view = view
        self.is_fine_scrolling = False

    def scroll_smooth(self, scroll_distance):
        win_x, win_y = self.view.get_position()
        _, win_h = self.view.get_size()
        center_x = win_x + self.view.left_panel_w + self.config.BORDER_WIDTH + self.session.geometry['w'] / 2
        center_y = win_y + self.config.BORDER_WIDTH + (win_h - 2 * self.config.BORDER_WIDTH) / 2
        if self.config.SCROLL_METHOD == 'invisible_cursor' and self.view.invisible_scroller.is_ready:
            scroller = self.view.invisible_scroller
            try:
                scroller.move(int(center_x), int(center_y))
                time.sleep(0.05)
                logging.info(f"触发高级滚动，距离: {scroll_distance}")
                scroller.scroll(scroll_distance)
            except Exception as e:
                logging.error(f"隐形光标滚动时发生错误: {e}")
            finally:
                time.sleep(0.05)
                scroller.park()
        elif self.view.touchpad_controller:
            logging.info("使用'移动用户光标'模式执行滚动...")
            origin_pos = mouse_controller.position
            scroll_exec_pos = (int(center_x), int(center_y))
            time.sleep(0.08)
            mouse_controller.position = scroll_exec_pos
            time.sleep(0.05) # 等待鼠标位置生效
            logging.info(f"触发高级滚动，距离: {scroll_distance}")
            self.view.touchpad_controller.scroll(scroll_distance)
            current_pos_after_scroll = mouse_controller.position
            tolerance = self.config.MOUSE_MOVE_TOLERANCE 
            user_intervened = (
                abs(current_pos_after_scroll[0] - scroll_exec_pos[0]) > tolerance or
                abs(current_pos_after_scroll[1] - scroll_exec_pos[1]) > tolerance
            )
            if not user_intervened:
                logging.info("用户未移动鼠标，恢复原始鼠标位置")
                mouse_controller.position = origin_pos
            else:
                logging.info("检测到用户在滚动期间手动移动鼠标，放弃恢复原始鼠标位置")

    def scroll_discrete(self, ticks):
        if ticks == 0:
            return
        win_x, win_y = self.view.get_position()
        shot_x = win_x + self.view.left_panel_w + self.config.BORDER_WIDTH
        shot_y = win_y + self.config.BORDER_WIDTH
        center_x = int(shot_x + self.session.geometry['w'] / 2)
        center_y = int(shot_y + self.session.geometry['h'] / 2)
        if self.config.SCROLL_METHOD == 'invisible_cursor' and self.view.invisible_scroller:
            logging.info(f"使用隐形光标执行离散滚动: {ticks} 格")
            scroller = self.view.invisible_scroller
            try:
                scroller.move(center_x, center_y)
                time.sleep(0.05)
                scroller.discrete_scroll(ticks)
            finally:
                time.sleep(0.05)
                scroller.park()
        else:
            logging.info(f"使用用户光标执行离散滚动: {ticks} 格")
            original_pos = mouse_controller.position
            mouse_controller.position = (center_x, center_y)
            time.sleep(0.05)
            mouse_controller.scroll(0, ticks)
            time.sleep(0.05)
            mouse_controller.position = original_pos

    def handle_slider_press(self, event):
        event_x_root, event_y_root = event.get_root_coords()
        win_x_root, win_y_root = self.view.get_position()
        window_relative_x = event_x_root - win_x_root
        window_relative_y = event_y_root - win_y_root
        resize_edge = self.view.get_cursor_edge(window_relative_x, window_relative_y)
        if resize_edge:
            return False
        self.is_fine_scrolling = True
        return True

    def handle_slider_motion(self, widget, event):
        if self.is_fine_scrolling:
            slider_height = widget.get_allocated_height()
            mouse_y_in_slider = max(0, min(slider_height, event.y))
            fraction = mouse_y_in_slider / slider_height if slider_height > 0 else 0
            continuous_value = self.config.SLIDER_MAX - (fraction * (self.config.SLIDER_MAX - self.config.SLIDER_MIN))
            self.view.slider_panel.adjustment.set_value(continuous_value)
            return True
        return False

    def handle_slider_release(self, widget, event):
        if self.is_fine_scrolling:
            final_value = self.view.slider_panel.adjustment.get_value()
            scroll_distance = -final_value * self.config.SLIDER_SENSITIVITY
            if abs(scroll_distance) > 1:
                GLib.timeout_add(20, self.scroll_smooth, scroll_distance)
            self.is_fine_scrolling = False
            GLib.idle_add(self.view.slider_panel.adjustment.set_value, 0)
            return True
        return False


class ActionController:
    """处理所有用户操作和业务逻辑"""
    def __init__(self, session: CaptureSession, view: 'CaptureOverlay', config: Config):
        self.session = session
        self.view = view
        self.config = config
        self.final_notification = None
        # 将交互状态从视图移至控制器
        self.is_dragging = False
        self.resize_edge = None
        self.drag_start_geometry = {}
        self.drag_start_x_root = 0
        self.drag_start_y_root = 0
        self.scroll_manager = ScrollManager(self.config, self.session, self.view)
        self.grid_mode_controller = GridModeController(self.config, self.session, self.view)

    def _do_scroll_by_region(self, num_ticks: int, direction_sign: int):
        if num_ticks == 0:
            return
        original_pos = mouse_controller.position
        win_x, win_y = self.view.get_position()
        shot_x = win_x + self.view.left_panel_w + config.BORDER_WIDTH
        shot_y = win_y + config.BORDER_WIDTH
        center_x = int(shot_x + self.session.geometry['w'] / 2)
        center_y = int(shot_y + self.session.geometry['h'] / 2)
        mouse_controller.position = (center_x, center_y)
        time.sleep(0.05)
        logging.info(f"使用 pynput 滚动 {num_ticks} 格，方向: {direction_sign}")
        mouse_controller.scroll(0, num_ticks * direction_sign)
        time.sleep(0.05)
        mouse_controller.position = original_pos

    def handle_movement_action(self, direction: str):
        """根据配置文件处理前进/后退动作 (滚动, 截图, 删除). """
        if not self.grid_mode_controller.is_active and not config.ENABLE_FREE_SCROLL:
            send_desktop_notification("操作无效", "请先按 Shift 键启用整格模式，或在配置中开启非整格模式滚动按钮")
            logging.warning("尝试在不允许的模式下执行移动操作，已取消")
            return
        if self.grid_mode_controller.is_active:
            if self.grid_mode_controller.grid_unit <= 0:
                logging.error("滚动单位无效，无法执行操作")
                return
            action_str = config.FORWARD_ACTION if direction == 'down' else config.BACKWARD_ACTION
            actions = action_str.lower().replace(" ", "").split('_')
            def do_scroll_action():
                region_height = self.session.geometry['h']
                num_ticks = round(region_height / self.grid_mode_controller.grid_unit)
                direction_sign = 1 if direction == 'up' else -1
                total_ticks = num_ticks * direction_sign
                self.scroll_manager.scroll_discrete(total_ticks)
            action_map = {
                'scroll': do_scroll_action,
                'capture': self.take_capture,
                'delete': self.delete_last_capture
            }
            action_queue = [action_map[act] for act in actions if act in action_map]
            if not action_queue:
                logging.warning(f"为方向 '{direction}' 配置了无效的动作: '{action_str}'")
                return
            def execute_next_in_queue(index=0):
                if index >= len(action_queue):
                    return False
                action_func = action_queue[index]
                is_scroll_action = action_func == do_scroll_action
                action_func()
                delay = 250 if is_scroll_action and index + 1 < len(action_queue) else 20
                GLib.timeout_add(delay, execute_next_in_queue, index + 1)
                return False
            execute_next_in_queue(0)
        else:
            scroll_distance = config.FREE_SCROLL_DISTANCE_PX
            final_distance = -scroll_distance if direction == 'down' else scroll_distance
            win_x, win_y = self.view.get_position()
            _, win_h = self.view.get_size()
            GLib.timeout_add(20, self.scroll_manager.scroll_smooth, final_distance)

    def handle_slider_press(self, event):
        return self.scroll_manager.handle_slider_press(event)

    def handle_slider_motion(self, widget, event):
        return self.scroll_manager.handle_slider_motion(widget, event)

    def handle_slider_release(self, widget, event):
        return self.scroll_manager.handle_slider_release(widget, event)

    def take_capture(self, widget=None):
        """执行截图的核心逻辑"""
        grabbed_seat = None
        try:
            win_x, win_y = self.view.get_position()
            shot_x = win_x + self.view.left_panel_w + config.BORDER_WIDTH
            shot_y = win_y + config.BORDER_WIDTH
            shot_w = self.session.geometry['w']
            shot_h = self.session.geometry['h']
            grabbed_seat = self._hide_cursor_if_needed(shot_x, shot_y, shot_w, shot_h)
            if shot_w <= 0 or shot_h <= 0:
                logging.warning(f"捕获区域过小，跳过截图。尺寸: {shot_w}x{shot_h}")
                send_desktop_notification("截图跳过", "选区太小，无法截图", "dialog-warning")
                return
            filepath = config.TMP_DIR / f"{self.session.capture_count:02d}_capture.png"
            if capture_area(shot_x, shot_y, shot_w, shot_h, filepath):
                self.session.add_capture(str(filepath))
            self.view.update_ui()
            logging.info(f"已捕获截图: {filepath}")
            play_sound(config.CAPTURE_SOUND)
        except Exception as e:
            logging.error(f"执行截图失败: {e}")
            send_desktop_notification("截图失败", f"无法执行截图命令: {e}", "dialog-warning")
        finally:
            if grabbed_seat:
                display = self.view.get_display()
                grabbed_seat.ungrab()
                display.flush()
                logging.info("截图操作完成，已释放指针抓取，恢复默认光标")

    def _hide_cursor_if_needed(self, x, y, w, h):
        """
        如果光标在截图区域内且配置为隐藏，则执行指针抓取来全局隐藏光标
        返回抓取成功的 seat 对象，如果失败或无需操作则返回 None
        """
        if config.CAPTURE_WITH_CURSOR:
            return None
        mouse_pos = mouse_controller.position
        mouse_is_inside = (x <= mouse_pos[0] < x + w) and (y <= mouse_pos[1] < y + h)
        if mouse_is_inside:
            logging.info("配置为隐藏鼠标，且检测到鼠标在截图区域内。执行指针抓取")
            display = self.view.get_display()
            seat = display.get_default_seat()
            status = seat.grab(
                self.view.get_window(),
                Gdk.SeatCapabilities.POINTER,
                False,
                self.view.cursors['blank'],
                None, None, None
            )
            if status == Gdk.GrabStatus.SUCCESS:
                logging.info("指针抓取成功，光标已全局隐藏")
                display.flush()
                return seat # 返回 seat 对象，用于后续的释放操作
            else:
                logging.warning(f"指针抓取失败，状态: {status}。光标可能不会被隐藏")
                return None
        else:
            logging.info("配置为隐藏鼠标，但鼠标在区域外，无需操作")
            return None

    def delete_last_capture(self, widget=None):
        """执行删除最后一张截图的逻辑"""
        if not self.session.capture_count:
            return
        try:
            last_capture_path = self.session.pop_last_capture()
            if last_capture_path and os.path.exists(last_capture_path):
                os.remove(last_capture_path)
                logging.info(f"已成功删除截图: {last_capture_path}")
                play_sound(config.UNDO_SOUND)
            self.view.update_ui() # 通知视图更新
        except Exception as e:
            logging.error(f"删除最后一张截图时出错: {e}")
            send_desktop_notification("删除失败", f"无法删除截图: {e}", "dialog-error")

    def finalize_and_quit(self, widget=None):
        """执行完成拼接并退出的逻辑"""
        if not self.session.capture_count:
            logging.warning("未进行任何截图。正在退出")
            self.quit_and_cleanup()
            return
        if hotkey_listener and hotkey_listener.is_alive():
            hotkey_listener.stop()
        processing_window, progress_bar = self.view._create_processing_window()
        self.view.hide()
        thread = threading.Thread(
            target=self._do_finalize_in_background,
            args=(processing_window, progress_bar)
        )
        thread.daemon = True
        thread.start()

    def _ensure_cleanup(self):
        if self.final_notification is not None:
            logging.warning("通知关闭回调超时，强制执行清理")
            self.final_notification = None
            self._perform_cleanup()
        return GLib.SOURCE_REMOVE

    def _do_finalize_in_background(self, processing_window, progress_bar):
        """在后台线程中执行拼接和保存"""
        finalize_start_time = time.perf_counter()
        label_widget = None
        try:
            main_vbox = processing_window.get_child()
            top_hbox = main_vbox.get_children()[0]
            label_widget = top_hbox.get_children()[1]
        except Exception as e:
            logging.warning(f"无法找到处理窗口中的Label控件: {e}")
        def update_progress(fraction):
            GLib.idle_add(progress_bar.set_fraction, fraction)
            return GLib.SOURCE_REMOVE
        def update_label_text(text):
            if label_widget:
                GLib.idle_add(label_widget.set_text, text)
            return GLib.SOURCE_REMOVE
        def _schedule_clipboard_task(path_to_copy):
            copy_to_clipboard(path_to_copy)
            return GLib.SOURCE_REMOVE
        try:
            now = datetime.now()
            timestamp_str = now.strftime(config.FILENAME_TIMESTAMP_FORMAT)
            base_filename = config.FILENAME_TEMPLATE.replace('{timestamp}', timestamp_str)
            file_extension = 'jpg' if config.SAVE_FORMAT == 'JPEG' else 'png'
            final_filename = f"{base_filename}.{file_extension}"
            output_file = config.SAVE_DIRECTORY / final_filename
            output_file.parent.mkdir(parents=True, exist_ok=True)
            stitch_start_time = time.perf_counter()
            stitched_image, total_matching_time = stitch_images_in_memory(self.session.captures, self.session, progress_callback=update_progress)
            stitch_duration = time.perf_counter() - stitch_start_time
            logging.info(f"图片拼接总耗时: {stitch_duration:.3f} 秒")
            if total_matching_time > 0:
                logging.info(f"模板匹配总耗时: {total_matching_time:.3f} 秒")
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
                        action_path=output_file,
                        controller=self,
                        width=self.session.image_width, 
                        height=self.session.total_height
                    )
                )
                GLib.timeout_add_seconds(12, self._ensure_cleanup)
        except Exception as e:
            logging.error(f"最终处理时发生错误: {e}")
            GLib.idle_add(
                send_desktop_notification, "长截图制作失败", f"发生错误: {e}", "dialog-error"
            )
            GLib.idle_add(self._perform_cleanup)
        finally:
            GLib.idle_add(processing_window.destroy)

    def quit_and_cleanup(self, widget=None):
        """处理带确认的退出逻辑"""
        if self.session.capture_count == 0:
            self._perform_cleanup()
            return
        response = self.view.show_quit_confirmation_dialog()
        if response == Gtk.ResponseType.YES:
            logging.info("用户确认放弃截图")
            self._perform_cleanup()
        else:
            logging.info("用户取消了放弃操作")

    def _perform_cleanup(self):
        """执行最终的清理工作"""
        logging.info("正在执行清理和退出操作")
        if hotkey_listener and hotkey_listener.is_alive():
            hotkey_listener.stop()
        if self.view.touchpad_controller:
            self.view.touchpad_controller.close()
        if self.view.invisible_scroller:
            cleanup_thread = threading.Thread(target=self.view.invisible_scroller.cleanup)
            cleanup_thread.start()
            logging.info("InvisibleCursorScroller.cleanup() 正在后台线程中执行")
        global config_window_instance
        if config_window_instance:
            logging.info("检测到配置窗口仍然打开，正在强制保存所有更改...")
            config_window_instance._save_all_configs()
        self.session.cleanup()
        Gtk.main_quit()

    def handle_key_press(self, event):
        """处理来自视图的按键事件"""
        if self.view.is_dialog_open:
            return False
        keyval = event.keyval
        state = event.state & config.GTK_MODIFIER_MASK
        def is_match(hotkey_config):
            return keyval in hotkey_config['gtk_keys'] and state == hotkey_config['gtk_mask']
        if is_match(config.HOTKEY_CAPTURE):
            self.take_capture()
            return True
        elif is_match(config.HOTKEY_FINALIZE):
            self.finalize_and_quit()
            return True
        elif is_match(config.HOTKEY_CANCEL):
            self.quit_and_cleanup()
            return True
        elif is_match(config.HOTKEY_UNDO):
            self.delete_last_capture()
            return True
        elif is_match(config.HOTKEY_SCROLL_UP):
            self.handle_movement_action('up')
            return True
        elif is_match(config.HOTKEY_SCROLL_DOWN):
            self.handle_movement_action('down')
            return True
        elif is_match(config.HOTKEY_CONFIGURE_SCROLL_UNIT):
            self.grid_mode_controller.start_calibration()
            return True
        elif is_match(config.HOTKEY_TOGGLE_GRID_MODE):
            self.grid_mode_controller.toggle()
            return True
        return False

    def handle_button_press(self, event):
        """处理来自视图的鼠标按下事件"""
        self.resize_edge = self.view.get_cursor_edge(event.x, event.y)
        if self.resize_edge:
            self.is_dragging = True
            self.drag_start_x_root, self.drag_start_y_root = event.get_root_coords()
            self.drag_start_geometry = self.session.geometry.copy()

    def handle_button_release(self, event):
        """处理来自视图的鼠标释放事件"""
        self.is_dragging = False
        self.resize_edge = None

    def handle_motion(self, event):
        """处理来自视图的鼠标移动事件（仅在拖拽时）"""
        if not self.is_dragging or not self.resize_edge:
            return
        x_root, y_root = event.get_root_coords()
        delta_x = x_root - self.drag_start_x_root
        delta_y = y_root - self.drag_start_y_root
        new_geo = self.drag_start_geometry.copy()
        if self.grid_mode_controller.is_active:
            if 'top' in self.resize_edge:
                new_geo['y'] = self.drag_start_geometry['y'] + delta_y
            elif 'bottom' in self.resize_edge:
                units_dragged = int(delta_y / self.grid_mode_controller.grid_unit)
                snapped_h = self.drag_start_geometry['h'] + (units_dragged * self.grid_mode_controller.grid_unit)
                new_geo['h'] = max(self.grid_mode_controller.grid_unit, snapped_h)
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
        min_h = self.grid_mode_controller.grid_unit if self.grid_mode_controller.is_active else 2 * config.HANDLE_HEIGHT
        min_w = 2 * config.HANDLE_HEIGHT
        if new_geo['h'] < min_h:
            if 'top' in self.resize_edge: new_geo['y'] -= (min_h - new_geo['h'])
            new_geo['h'] = min_h
        if new_geo['w'] < min_w:
            if 'left' in self.resize_edge: new_geo['x'] -= (min_w - new_geo['w'])
            new_geo['w'] = min_w
        self.session.update_geometry(new_geo)
        self.view.update_layout()

class ButtonPanel(Gtk.Box):
    __gsignals__ = {
        'capture-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'undo-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'finalize-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'cancel-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'scroll-up-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'scroll-down-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=config.BUTTON_SPACING)
        self.btn_scroll_up = Gtk.Button(label="后退")
        self.btn_scroll_down = Gtk.Button(label="前进")
        self.btn_scroll_up.connect("clicked", lambda w: self.emit('scroll-up-clicked'))
        self.btn_scroll_down.connect("clicked", lambda w: self.emit('scroll-down-clicked'))
        btn_capture = Gtk.Button(label="截图")
        btn_capture.connect("clicked", lambda w: self.emit('capture-clicked'))
        self.btn_undo = Gtk.Button(label="撤销")
        self.btn_undo.connect("clicked", lambda w: self.emit('undo-clicked'))
        btn_finalize = Gtk.Button(label="完成")
        btn_finalize.connect("clicked", lambda w: self.emit('finalize-clicked'))
        btn_cancel = Gtk.Button(label="取消")
        btn_cancel.connect("clicked", lambda w: self.emit('cancel-clicked'))
        all_buttons = [
            self.btn_scroll_up, self.btn_scroll_down,
            btn_capture, self.btn_undo, btn_finalize, btn_cancel
        ]
        for btn in all_buttons:
            btn.set_can_focus(False)
            btn.show()
        if config.ENABLE_SCROLL_BUTTONS:
            initial_visible = config.ENABLE_FREE_SCROLL
            self.btn_scroll_up.set_visible(initial_visible)
            self.btn_scroll_down.set_visible(initial_visible)
        else:
             self.btn_scroll_up.set_no_show_all(True)
             self.btn_scroll_down.set_no_show_all(True)
             self.btn_scroll_up.hide()
             self.btn_scroll_down.hide()
        self.btn_undo.set_sensitive(False)
        self.pack_start(self.btn_scroll_up, True, True, 0)
        self.pack_start(self.btn_scroll_down, True, True, 0)
        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        self.pack_start(separator, False, False, 0)
        self.pack_start(btn_capture, True, True, 0)
        self.pack_start(self.btn_undo, True, True, 0)
        self.pack_start(btn_finalize, True, True, 0)
        self.pack_start(btn_cancel, True, True, 0)

    def set_scroll_buttons_visible(self, visible: bool):
        if not config.ENABLE_SCROLL_BUTTONS:
            return
        self.btn_scroll_up.set_visible(visible)
        self.btn_scroll_down.set_visible(visible)
        children = self.get_children()
        if len(children) > 2 and isinstance(children[2], Gtk.Separator):
            children[2].set_visible(visible)

    def set_undo_sensitive(self, sensitive: bool):
        self.btn_undo.set_sensitive(sensitive)

class SliderPanel(Gtk.Scale):
    def __init__(self):
        if config.SLIDER_MARKS_PER_SIDE > 0:
            page_increment_value = config.SLIDER_MAX / config.SLIDER_MARKS_PER_SIDE
        else:
            page_increment_value = config.SLIDER_MAX
        self.adjustment = Gtk.Adjustment(
            value=0, 
            lower=config.SLIDER_MIN, 
            upper=config.SLIDER_MAX, 
            step_increment=10, 
            page_increment=page_increment_value, 
            page_size=0
        )
        super().__init__(orientation=Gtk.Orientation.VERTICAL, adjustment=self.adjustment)
        self.set_inverted(True)
        self.set_draw_value(False)
        self.set_can_focus(False)
        self._add_marks()
        self._apply_css()
        self.show()

    def _add_marks(self):
        self.add_mark(0, Gtk.PositionType.LEFT, config.STR_SLIDER_MARK_MIDDLE)
        self.add_mark(config.SLIDER_MAX, Gtk.PositionType.LEFT, config.STR_SLIDER_MARK_TOP)
        self.add_mark(config.SLIDER_MIN, Gtk.PositionType.LEFT, config.STR_SLIDER_MARK_BOTTOM)
        if config.SLIDER_MARKS_PER_SIDE > 1:
            for i in range(1, config.SLIDER_MARKS_PER_SIDE):
                self.add_mark((config.SLIDER_MAX / config.SLIDER_MARKS_PER_SIDE) * i, Gtk.PositionType.LEFT, None)
                self.add_mark((config.SLIDER_MIN / config.SLIDER_MARKS_PER_SIDE) * i, Gtk.PositionType.LEFT, None)

    def _apply_css(self):
        css_provider = Gtk.CssProvider()
        css_string = config.SLIDER_PANEL_CSS
        css_provider.load_from_data(css_string.encode('utf-8'))
        style_context = self.get_style_context()
        style_context.add_provider(css_provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)

class InfoPanel(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.set_halign(Gtk.Align.CENTER)
        self.get_style_context().add_class("info-panel")
        self.label_count = Gtk.Label()
        self.label_dimensions = Gtk.Label()
        self.label_count.set_name("label_count")
        self.label_dimensions.set_name("label_dimensions")
        for label in [self.label_count, self.label_dimensions]:
            label.set_can_focus(False)
            label.get_style_context().add_class("info-label")
            label.set_no_show_all(True)
            label.set_line_wrap(True)
            label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            label.set_justify(Gtk.Justification.CENTER)
            label.set_xalign(0.5)
            self.pack_start(label, False, False, 0)
        self.update_info(0, 0, 0)
        self._apply_css()

    def _apply_css(self):
        css_provider = Gtk.CssProvider()
        css_string = config.INFO_PANEL_CSS
        css_provider.load_from_data(css_string.encode('utf-8'))
        style_context = self.get_style_context()
        style_context.add_provider(css_provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_USER
        )

    def update_info(self, count: int, width: int, height: int):
        if config.SHOW_CAPTURE_COUNT:
            self.label_count.set_text(config.STR_CAPTURE_COUNT_FORMAT.format(count=count))
            self.label_count.show()
        else:
            self.label_count.hide()
        if config.SHOW_TOTAL_DIMENSIONS:
            if count > 0:
                dim_text = f"{width}\nx\n{height}"
            else:
                dim_text = "宽\nx\n高"
            self.label_dimensions.set_text(dim_text)
            self.label_dimensions.show()
        else:
            self.label_dimensions.hide()

class SidePanel(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=config.BUTTON_SPACING)
        self.info_panel = InfoPanel()
        self.button_panel = ButtonPanel()
        self.info_panel.set_size_request(config.SIDE_PANEL_WIDTH, -1)
        self.pack_start(self.info_panel, False, False, 0)
        self.pack_start(self.button_panel, True, True, 0)
        self.info_panel.show()
        self.button_panel.show()
        _, self._info_natural_h = self.info_panel.get_preferred_height()
        logging.info(f"缓存的 InfoPanel 自然高度: {self._info_natural_h}")
        _, self._button_natural_h_normal = self.button_panel.get_preferred_height()
        logging.info(f"缓存的 ButtonPanel 普通模式自然高度: {self._button_natural_h_normal}")
        self.button_panel.set_scroll_buttons_visible(True)
        _, self._button_natural_h_grid = self.button_panel.get_preferred_height()
        logging.info(f"缓存的 ButtonPanel 整格模式自然高度: {self._button_natural_h_grid}")
        self.button_panel.set_scroll_buttons_visible(config.ENABLE_FREE_SCROLL)

    def update_visibility_by_height(self, available_height: int, is_grid_mode: bool):
        should_show_info_base = config.SHOW_CAPTURE_COUNT or config.SHOW_TOTAL_DIMENSIONS
        should_show_buttons_base = config.ENABLE_BUTTONS
        if not should_show_info_base and not should_show_buttons_base:
            self.info_panel.hide()
            self.button_panel.hide()
            return
        required_h_for_info = self._info_natural_h if should_show_info_base else 0
        if should_show_buttons_base:
            scroll_buttons_are_visible = is_grid_mode or config.ENABLE_FREE_SCROLL
            required_h_for_buttons = self._button_natural_h_grid if scroll_buttons_are_visible else self._button_natural_h_normal
        else:
            required_h_for_buttons = 0
        required_spacing = self.get_spacing() if (should_show_info_base and should_show_buttons_base) else 0
        threshold_for_all_enabled = required_h_for_info + required_h_for_buttons + required_spacing
        threshold_for_info_only = required_h_for_info
        can_show_all_enabled = available_height >= threshold_for_all_enabled
        can_show_info_panel = available_height >= threshold_for_info_only
        if should_show_buttons_base and can_show_all_enabled:
            self.button_panel.show()
        else:
            self.button_panel.hide()
        if should_show_info_base and can_show_info_panel:
            self.info_panel.show()
        else:
            self.info_panel.hide()

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

class ConfigWindow(Gtk.Window):
    """完整的配置窗口，提供所有设置项的图形化编辑界面"""
    def __init__(self, config_obj):
        super().__init__(title="拼长图配置")
        self.config = config_obj
        self.show_advanced = False
        self.input_has_focus = False
        self.managed_settings = [
            ('Output', 'save_directory'), ('Output', 'save_format'),
            ('Output', 'jpeg_quality'), ('Output', 'filename_template'),
            ('Output', 'filename_timestamp_format'),
            ('Interface.Components', 'enable_buttons'), ('Interface.Components', 'enable_scroll_buttons'),
            ('Interface.Components', 'enable_free_scroll'), ('Interface.Components', 'enable_slider'),
            ('Interface.Components', 'show_capture_count'), ('Interface.Components', 'show_total_dimensions'),
            ('Interface.Components', 'show_instruction_notification'),
            ('Behavior', 'enable_free_scroll_matching'), ('Behavior', 'capture_with_cursor'), ('Behavior', 'scroll_method'),
            ('Behavior', 'forward_action'), ('Behavior', 'backward_action'),
            ('Interface.Theme', 'border_color'), ('Interface.Theme', 'matching_indicator_color'),
            ('Interface.Theme', 'slider_marks_per_side'),
            ('Interface.Layout', 'border_width'),
            ('Interface.Layout', 'handle_height'), ('Interface.Layout', 'slider_panel_width'),
            ('Interface.Layout', 'side_panel_width'), ('Interface.Layout', 'button_spacing'),
            ('Interface.Layout', 'processing_dialog_width'), ('Interface.Layout', 'processing_dialog_height'),
            ('Interface.Layout', 'processing_dialog_spacing'), ('Interface.Layout', 'processing_dialog_border_width'),
            ('Interface.Theme', 'slider_panel_css'), ('Interface.Theme', 'processing_dialog_css'),
            ('Interface.Theme', 'info_panel_css'),
            ('System', 'copy_to_clipboard_on_finish'), ('System', 'notification_click_action'),
            ('System', 'large_image_opener'), ('System', 'sound_theme'),
            ('System', 'capture_sound'), ('System', 'undo_sound'), ('System', 'finalize_sound'),
            ('Performance', 'grid_matching_max_overlap'), ('Performance', 'free_scroll_matching_max_overlap'), ('Performance', 'slider_sensitivity'), ('Performance', 'mouse_move_tolerance'),
            ('Performance', 'free_scroll_distance_px'), ('Performance', 'max_viewer_dimension'),
            ('System', 'log_file'), ('System', 'temp_directory_base'),
        ]
        self.sound_data = self._discover_sound_themes()
        self.set_default_size(800, 600)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_type_hint(Gdk.WindowTypeHint.NORMAL)
        self.set_icon_name("preferences-system")
        self.xid = None
        self.capturing_hotkey_button = None
        self.connect("key-press-event", self._on_config_window_key_press)
        self.connect("key-release-event", self._on_config_window_key_release)
        self.connect("destroy", self._on_destroy)
        self.connect("realize", self._on_realize)
        self.connect("delete-event", self._on_delete_event)
        self.log_queue = log_queue
        self.log_text_buffer = None
        self.log_timer_id = None
        self.all_log_records = []
        self.log_tags = {}
        self.filter_checkboxes = {}
        self.log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        self._setup_ui()
        self._create_log_tags()
        self._load_config_values()
        self.show_all()
        self._setup_default_parser()
        self._update_advanced_visibility()
        self.log_timer_id = GLib.timeout_add(150, self._check_log_queue)

    def _on_realize(self, widget):
        try:
            self.xid = self.get_window().get_xid()
        except Exception as e:
            logging.error(f"ConfigWindow: 获取 XID 失败: {e}")

    def _on_delete_event(self, widget, event):
        """窗口关闭时保存所有配置"""
        self._save_all_configs()
        return False

    def _on_destroy(self, widget):
        """窗口销毁时的清理操作"""
        if self.log_timer_id:
            GLib.source_remove(self.log_timer_id)
            self.log_timer_id = None
            logging.info("配置窗口的日志更新定时器已成功移除")

    def _on_input_focus_in(self, widget, event):
        self.input_has_focus = True
        logging.info(f"输入控件 {type(widget).__name__} 获得焦点，全局热键暂停")
        return False

    def _on_input_focus_out(self, widget, event):
        self.input_has_focus = False
        logging.info(f"输入控件 {type(widget).__name__} 失去焦点，全局热键恢复")
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
        key_name_lower = Gdk.keyval_name(keyval).lower()
        is_modifier_only_release = key_name_lower in ('shift_l', 'shift_r', 'control_l', 'control_r', 'alt_l', 'alt_r')
        if is_modifier_only_release:
             if 'shift' in key_name_lower and '<ctrl>' not in mods and '<alt>' not in mods: return '<shift>'
             if 'control' in key_name_lower and '<shift>' not in mods and '<alt>' not in mods: return '<ctrl>'
             if 'alt' in key_name_lower and '<shift>' not in mods and '<ctrl>' not in mods: return '<alt>'
        rev_map = {v: k for k, v in self.config._key_map_gtk_special.items()}
        main_key_str = ""
        if keyval in rev_map:
            main_key_str = rev_map[keyval]
        else:
            codepoint = Gdk.keyval_to_unicode(keyval)
            if codepoint != 0:
                char = chr(codepoint)
                if char.isprintable():
                    main_key_str = char.lower()
            if not main_key_str:
                if not is_modifier_only_release:
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

    def _on_config_window_key_press(self, widget, event):
        if self.capturing_hotkey_button:
            return True 
        return False

    def _on_config_window_key_release(self, widget, event):
        if not self.capturing_hotkey_button:
            return False
        hotkey_str = self._key_event_to_string(event)
        current_key = self.capturing_hotkey_button.get_name()
        original_label = self.capturing_hotkey_button.original_label
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
            dialog = Gtk.MessageDialog(
                transient_for=self,
                modal=True,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text="快捷键冲突"
            )
            dialog.format_secondary_text(
                f"快捷键 '{hotkey_str}' 已被分配给 '{conflicting_key_desc}'\n请设置一个不同的快捷键"
            )
            dialog.run()
            dialog.destroy()
            self.capturing_hotkey_button.set_label(original_label)
        else:
            self.capturing_hotkey_button.set_label(hotkey_str)
        self.capturing_hotkey_button = None
        return True

    def _setup_ui(self):
        """设置主界面布局"""
        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(main_vbox)
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
        dialog = Gtk.FileChooserDialog(
            title="请选择保存目录",
            parent=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK
        )
        current_folder = self.save_dir_entry.get_text()
        if current_folder and Path(current_folder).is_dir():
            dialog.set_current_folder(current_folder)
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            folder_path = dialog.get_filename()
            self.save_dir_entry.set_text(folder_path)
        dialog.destroy()

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
            ("scroll_up", "后退"), ("scroll_down", "前进"),
            ("configure_scroll_unit", "配置滚动单位"), ("toggle_grid_mode", "切换整格模式"),
            ("open_config_editor", "打开/激活配置窗口"), ("toggle_hotkeys_enabled", "启用/禁用全局热键"), ("dialog_confirm", "退出对话框确认"),
            ("dialog_cancel", "退出对话框取消")
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
            ('Interface.Components', 'enable_scroll_buttons'),
            ('Interface.Components', 'enable_free_scroll'),
            ('Interface.Components', 'enable_slider'),
            ('Interface.Components', 'show_capture_count'),
            ('Interface.Components', 'show_total_dimensions'),
            ('Interface.Components', 'show_instruction_notification'),
            ('Behavior', 'capture_with_cursor'),
            ('Behavior', 'scroll_method'),
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
            ("enable_scroll_buttons", "启用“前进/后退”按钮", "控制是否显示“前进”和“后退”按钮\n禁用后，仍能通过快捷键或滑块进行滚动"),
            ("enable_free_scroll", "自由模式启用滚动功能", "控制在<b>自由模式</b>下，“前进/后退”按钮及其快捷键是否可用"),
            ("enable_slider", "启用左侧滑块条", "是否在截图区域左侧显示一个用于平滑滚动的拖动条"),
            ("show_capture_count", "显示已截图数量", "是否在侧边栏信息面板中显示当前已截取的图片数量"),
            ("show_total_dimensions", "显示最终图片总尺寸", "是否在侧边栏信息面板中显示拼接后图片的总宽度和总高度"),
            ("enable_free_scroll_matching", "自由模式启用滚动误差修正", "在<b>自由模式</b>下，使用模板匹配来修正滚动误差，此功能会增加拼接处理时间\n启用后，请确保每次滚动有重叠部分，否则修正无效"),
            ("show_instruction_notification", "启动时显示操作说明", "每次启动截图会话时，是否弹出一个包含基本操作指南的通知")
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
        self.scroll_method_combo.append("invisible_cursor", "使用隐形光标")
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
            ('Interface.Theme', 'slider_marks_per_side'),
            ('Interface.Layout', 'handle_height'),
            ('Interface.Layout', 'slider_panel_width'),
            ('Interface.Layout', 'side_panel_width'),
            ('Interface.Layout', 'button_spacing'),
            ('Interface.Layout', 'processing_dialog_width'),
            ('Interface.Layout', 'processing_dialog_height'),
            ('Interface.Layout', 'processing_dialog_spacing'),
            ('Interface.Layout', 'processing_dialog_border_width'),
            ('Interface.Theme', 'slider_panel_css'),
            ('Interface.Theme', 'processing_dialog_css'),
            ('Interface.Theme', 'info_panel_css'),
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
        self.border_color_button = Gtk.ColorButton()
        grid1.attach(label, 0, 0, 1, 1)
        grid1.attach(self.border_color_button, 1, 0, 1, 1)
        # 指示器颜色
        label_ind = Gtk.Label(label="匹配指示器颜色:", xalign=0)
        label_ind.set_tooltip_markup("误差修正功能启用时，在边框上标记区域的颜色")
        self.indicator_color_button = Gtk.ColorButton()
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
        # 滑块刻度数量
        label = Gtk.Label(label="滑块刻度数量:", xalign=0)
        self.slider_marks_spin = Gtk.SpinButton()
        self._connect_focus_handlers(self.slider_marks_spin)
        self.slider_marks_spin.connect("scroll-event", lambda widget, event: True)
        self.slider_marks_spin.set_range(0, 20)
        self.slider_marks_spin.set_increments(1, 2)
        self.slider_marks_spin.set_halign(Gtk.Align.START)
        grid1.attach(label, 0, 3, 1, 1)
        grid1.attach(self.slider_marks_spin, 1, 3, 1, 1)
        # 布局微调
        frame2 = Gtk.Frame(label="布局微调（像素）")
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
            ("handle_height", "拖动手柄高度", 3, 50, "在截图选区上下边缘，可用于拖动调整高度的区域大小"),
            ("slider_panel_width", "滑块面板宽度", 40, 150, "左侧滑块面板的宽度"),
            ("side_panel_width", "侧边栏宽度", 80, 200, "功能按钮和信息面板的总宽度"),
            ("button_spacing", "按钮间距", 0, 20, "侧边栏中各个按钮之间的垂直间距"),
            ("processing_dialog_width", "处理中对话框宽度", 100, 400, "完成截图后弹出的对话框的宽度"),
            ("processing_dialog_height", "处理中对话框高度", 50, 200, "完成截图后弹出的对话框的宽度"),
            ("processing_dialog_spacing", "处理中对话框间距", 5, 30, "处理中对话框内部元素（图标、文字、进度条）的间距"),
            ("processing_dialog_border_width", "处理中对话框边距", 5, 50, "处理中对话框内容区域距离窗口边缘的距离")
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
            ("slider_panel_css", "滑块样式"),
            ("processing_dialog_css", "处理中对话框样式"), 
            ("info_panel_css", "信息面板样式")
        ]
        for key, desc in css_configs:
            label = Gtk.Label(label=f"{desc}:", xalign=0)
            label.set_tooltip_markup("在此处输入自定义 CSS 代码以调整组件外观")
            scrolled_css = Gtk.ScrolledWindow()
            scrolled_css.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scrolled_css.set_size_request(-1, 250)
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
            ('Performance', 'slider_sensitivity'),
            ('Performance', 'mouse_move_tolerance'),
            ('Performance', 'free_scroll_distance_px'),
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
        # 滑块灵敏度
        label = Gtk.Label(label="滑块灵敏度:", xalign=0)
        label.set_tooltip_markup("数值越大，拖动左侧滑块时滚动的距离越远")
        self.sensitivity_spin = Gtk.SpinButton()
        self.sensitivity_spin.set_tooltip_markup(label.get_tooltip_markup())
        self._connect_focus_handlers(self.sensitivity_spin)
        self.sensitivity_spin.set_range(0.1, 20.0)
        self.sensitivity_spin.set_increments(0.1, 1.0)
        self.sensitivity_spin.set_digits(1)
        self.sensitivity_spin.set_halign(Gtk.Align.START)
        grid3.attach(label, 0, 0, 1, 1)
        grid3.attach(self.sensitivity_spin, 1, 0, 1, 1)
        performance_configs = [
            ("grid_matching_max_overlap", "整格模式误差修正范围", 10, 20, "<b>整格模式</b>下的误差修正设置最大搜索范围"),
            ("free_scroll_matching_max_overlap", "自由模式误差修正范围", 20, 300, "<b>自由模式</b>下的误差修正设置最大搜索范围\n值越大，处理用时越长"),
            ("mouse_move_tolerance", "鼠标容差", 0, 50, "在使用“移动用户光标”方式滚动后，若用户鼠标的移动距离超过此像素值，程序将不会把光标移回原位"),
            ("free_scroll_distance_px", "自由滚动步长", 10, 500, "在<b>自由模式</b>下，“前进”/“后退”滚动的相对距离"),
            ("max_viewer_dimension", "图片尺寸阈值", -1, 131071, "最终图片长或宽超过此值时，会使用上面的“大尺寸图片打开命令”\n设为 <b>-1</b> 禁用此功能，总是用系统默认方式打开图片\n设为 <b>0</b> 总是用自定义命令打开图片")
        ]
        self.performance_spins = {}
        for i, (key, desc, min_val, max_val, tooltip) in enumerate(performance_configs):
            label = Gtk.Label(label=f"{desc}:", xalign=0)
            label.set_tooltip_markup(tooltip)
            spin = Gtk.SpinButton()
            spin.set_tooltip_markup(tooltip)
            self._connect_focus_handlers(spin)
            spin.connect("scroll-event", lambda widget, event: True)
            spin.set_range(min_val, max_val)
            spin.set_increments(1, 10)
            spin.set_halign(Gtk.Align.START)
            grid3.attach(label, 0, i + 1, 1, 1)
            grid3.attach(spin, 1, i + 1, 1, 1)
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
        info_label = Gtk.Label(label="用于手动添加或调整应用的滚动像素单位，应用类名需小写")
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
            ("slider_mark_middle", "滑块标记 (中)"),
            ("slider_mark_top", "滑块标记 (上)"),
            ("slider_mark_bottom", "滑块标记 (下)")
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
            'slider_marks_per_side': self.slider_marks_spin,
            'slider_sensitivity': self.sensitivity_spin,
            'copy_to_clipboard_on_finish': self.clipboard_checkbox,
            'notification_click_action': self.notification_combo,
            'large_image_opener': self.large_opener_entry,
            'capture_with_cursor': self.cursor_checkbox,
            'scroll_method': self.scroll_method_combo,
            'forward_action': self.forward_combo,
            'backward_action': self.backward_combo,
            'copy_to_clipboard_on_finish': self.clipboard_checkbox,
            'notification_click_action': self.notification_combo,
            'large_image_opener': self.large_opener_entry,
            'capture_with_cursor': self.cursor_checkbox,
            'scroll_method': self.scroll_method_combo,
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
        elif isinstance(widget, Gtk.ColorButton):
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
            if key == 'slider_sensitivity':
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
        elif isinstance(widget, Gtk.ColorButton):
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

class CaptureOverlay(Gtk.Window):
    def __init__(self, geometry_str, config: Config):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.session = CaptureSession(geometry_str)
        self.controller = ActionController(self.session, self, config)
        self.touchpad_controller = None
        self.invisible_scroller = None
        self.screen_rect = self._get_current_monitor_geometry()
        if config.ENABLE_SLIDER:
            try:
                if config.SCROLL_METHOD == 'invisible_cursor':
                    self.invisible_scroller = InvisibleCursorScroller(
                        self.screen_rect.width, self.screen_rect.height
                    )
                    setup_thread = threading.Thread(target=self.invisible_scroller.setup, daemon=True)
                    setup_thread.start()
                    logging.info("InvisibleCursorScroller.setup() 正在后台线程中执行")
                else:
                    self.touchpad_controller = VirtualTouchpadController()
            except Exception as err:
                logging.error(f"创建虚拟滚动设备失败: {err}")
                send_desktop_notification(
                    "设备错误", f"无法创建虚拟设备: {err}，滑块微调功能将使用备用模式或不可用", level="critical"
                )
                self.touchpad_controller = None
                self.invisible_scroller = None
        self.window_xid = None # 用于存储窗口的 X11 ID
        self.is_dialog_open = False
        self.show_slider = True
        self.show_side_panel = True
        self.panel_on_left = False
        self._setup_window_properties()
        self.fixed_container = Gtk.Fixed()
        self.add(self.fixed_container)
        self.fixed_container.show()
        self.create_slider_panel()
        self.create_side_panel()
        self._slider_min_h, _ = self.slider_panel.get_preferred_height()
        logging.info(f"缓存的 SliderPanel 最小高度: {self._slider_min_h}")
        self.slider_panel.set_size_request(config.SLIDER_PANEL_WIDTH, self.session.geometry['h']) 
        self.update_layout()
        self._initialize_cursors()
        self._connect_events()
        logging.info(f"GTK 覆盖层已创建，捕获区域几何信息: {self.session.geometry}")
        if config.SHOW_INSTRUCTION_NOTIFICATION:
            GLib.idle_add(self._show_instruction_dialog)

    def _initialize_cursors(self):
        """一次性创建所有需要的光标并缓存"""
        display = self.get_display()
        cursor_names = [
            'default', 'n-resize', 's-resize', 'w-resize', 'e-resize',
            'nw-resize', 'se-resize', 'ne-resize', 'sw-resize'
        ]
        self.cursors = {
            name: Gdk.Cursor.new_from_name(display, name) for name in cursor_names
        }
        surface = cairo.ImageSurface(cairo.Format.ARGB32, 1, 1)
        self.cursors['blank'] = Gdk.Cursor.new_from_surface(display, surface, 0, 0)

    def _get_current_monitor_geometry(self):
        """获取当前指针所在显示器的几何信息"""
        display = Gdk.Display.get_default()
        seat = display.get_default_seat()
        pointer = seat.get_pointer()
        if pointer:
            _screen, x, y = pointer.get_position()
            monitor = display.get_monitor_at_point(x, y)
            return monitor.get_geometry()
        return None

    def _setup_window_properties(self):
        """设置窗口的基本属性"""
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_app_paintable(True)
        visual = self.get_screen().get_rgba_visual()
        if visual and self.get_screen().is_composited():
            self.set_visual(visual)

    def _connect_events(self):
        """连接所有Gtk信号和事件"""
        self.connect("realize", self.on_realize)
        self.connect("draw", self.on_draw)
        self.connect("size-allocate", self.on_size_allocate)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK |
                        Gdk.EventMask.BUTTON_RELEASE_MASK |
                        Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect("button-press-event", self.on_button_press)
        self.connect("button-release-event", self.on_button_release)
        self.connect("motion-notify-event", self.on_motion_notify)
        self.connect("key-press-event", self.on_key_press_event)

    def on_key_press_event(self, widget, event):
        return self.controller.handle_key_press(event)

    def on_realize(self, widget):
        try:
            self.window_xid = widget.get_window().get_xid()
        except Exception as e:
            logging.error(f"获取 xid 失败: {e}")

    def update_ui(self):
        """根据会话状态刷新UI元素"""
        if self.show_side_panel:
            self.side_panel.button_panel.set_undo_sensitive(self.session.capture_count > 0)
            self.side_panel.info_panel.update_info(
                count=self.session.capture_count,
                width=self.session.image_width,
                height=self.session.total_height
            )
        self.queue_draw()

    def show_quit_confirmation_dialog(self):
        """显示退出确认对话框并返回用户的响应"""
        self.is_dialog_open = True
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=config.DIALOG_QUIT_TITLE
        )
        yes_label = config.DIALOG_QUIT_BTN_YES.format(key=config.str_dialog_confirm.upper())
        no_label = config.DIALOG_QUIT_BTN_NO.format(key=config.str_dialog_cancel.upper())
        button_no = dialog.get_widget_for_response(Gtk.ResponseType.NO)
        button_no.set_label(no_label)
        button_yes = dialog.get_widget_for_response(Gtk.ResponseType.YES)
        button_yes.set_label(yes_label)
        message = config.DIALOG_QUIT_MESSAGE.format(count=self.session.capture_count)
        dialog.format_secondary_text(message)
        dialog.connect("key-press-event", self.on_dialog_key_press)
        response = dialog.run()
        self.is_dialog_open = False
        dialog.destroy()
        return response

    def _show_instruction_dialog(self):
        """显示一个包含操作指南的对话框"""
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.INFO,
            text="欢迎使用拼长图",
        )
        dialog.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        dialog.add_button("好的", Gtk.ResponseType.OK)
        dialog.add_button("不再显示", Gtk.ResponseType.CLOSE)
        instruction_lines = ["拼长图操作指南："]
        if config.ENABLE_SLIDER:
            instruction_lines.append("左侧滑块条：按住并拖动滑块，松开滚动界面")
        hotkeys = [
            f"截图：{config.str_capture.upper()}",
            f"完成：{config.str_finalize.upper()}",
            f"取消：{config.str_cancel.upper()}",
            f"撤销：{config.str_undo.upper()}"
        ]
        instruction_lines.append("，".join(hotkeys))
        instruction_lines.append(f"切换整格/自由模式：{config.str_toggle_grid_mode.upper()}，（自由模式下）配置滚动单位：{config.str_configure_scroll_unit.upper()}")
        instruction_lines.append(f"前进：{config.str_scroll_down.upper()}，后退：{config.str_scroll_up.upper()}")
        instruction_lines.append(f"打开/激活配置窗口：{config.str_open_config_editor.upper()}，开启/禁用全局热键：{config.str_toggle_hotkeys_enabled.upper()}")
        instructions = "\n".join(instruction_lines)
        dialog.format_secondary_text(instructions)
        response = dialog.run()
        if response == Gtk.ResponseType.CLOSE:
            logging.info("用户选择 '不再显示'。正在更新配置文件...")
            config.save_setting('Interface.Components', 'show_instruction_notification', 'false')
        dialog.destroy()
        return False

    def show_scroll_config_dialog(self):
        """显示一个对话框，让用户输入滚动格数"""
        dialog = Gtk.Dialog(
            title="辅助配置滚动单位",
            transient_for=self,
            modal=True
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK
        )
        content_area = dialog.get_content_area()
        content_area.set_spacing(10)
        info_label = Gtk.Label(
            label="请将界面恰好滚动一个截图区域的高度，\n然后输入所需的鼠标滚轮“格”数"
        )
        info_label.set_justify(Gtk.Justification.CENTER)
        entry = Gtk.Entry()
        entry.set_placeholder_text("例如: 10")
        # 提示系统这个输入框用于输入数字
        entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
        content_area.pack_start(info_label, True, True, 5)
        content_area.pack_start(entry, True, True, 5)
        dialog.show_all()
        response = dialog.run()
        ticks = 0
        if response == Gtk.ResponseType.OK:
            try:
                value = int(entry.get_text())
                if value > 0:
                    ticks = value
            except ValueError:
                pass
        dialog.destroy()
        return ticks

    def create_slider_panel(self):
        self.slider_panel = SliderPanel()
        self.slider_panel.connect("button-press-event", self.on_slider_press)
        self.slider_panel.connect("motion-notify-event", self.on_slider_motion)
        self.slider_panel.connect("button-release-event", self.on_slider_release)
        self.fixed_container.put(self.slider_panel, 0, config.BORDER_WIDTH)


    def on_slider_press(self, widget, event):
        return self.controller.handle_slider_press(event)

    def on_slider_motion(self, widget, event):
        return self.controller.handle_slider_motion(widget, event)

    def on_slider_release(self, widget, event):
        return self.controller.handle_slider_release(widget, event)

    def create_side_panel(self):
        self.side_panel = SidePanel()
        button_panel = self.side_panel.button_panel
        button_panel.connect("scroll-up-clicked", lambda w: self.controller.handle_movement_action('up'))
        button_panel.connect("scroll-down-clicked", lambda w: self.controller.handle_movement_action('down'))
        button_panel.connect("capture-clicked", self.controller.take_capture)
        button_panel.connect("undo-clicked", self.controller.delete_last_capture)
        button_panel.connect("finalize-clicked", self.controller.finalize_and_quit)
        button_panel.connect("cancel-clicked", self.controller.quit_and_cleanup)
        self.fixed_container.put(self.side_panel, 0, 0)

    def on_dialog_key_press(self, widget, event):
        """处理确认对话框的按键事件"""
        keyval = event.keyval
        state = event.state & config.GTK_MODIFIER_MASK
        def is_match(hotkey_config):
            return keyval in hotkey_config['gtk_keys'] and state == hotkey_config['gtk_mask']
        if is_match(config.HOTKEY_DIALOG_CONFIRM):
            widget.response(Gtk.ResponseType.YES)
            return True
        elif is_match(config.HOTKEY_DIALOG_CANCEL):
            widget.response(Gtk.ResponseType.NO)
            return True
        return False

    def _create_processing_window(self):
        """创建一个显示“正在处理”且带有进度条的模态窗口"""
        win = Gtk.Window(type=Gtk.WindowType.POPUP)
        win.set_transient_for(self)
        win.set_modal(True)
        win.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        win.set_decorated(False)
        win.set_default_size(config.PROCESSING_DIALOG_WIDTH, config.PROCESSING_DIALOG_HEIGHT)
        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=config.PROCESSING_DIALOG_SPACING // 2)
        main_vbox.get_style_context().add_class("background")
        css_provider = Gtk.CssProvider()
        css = config.PROCESSING_DIALOG_CSS
        css_provider.load_from_data(css.encode('utf-8'))
        main_vbox.get_style_context().add_provider(css_provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
        win.add(main_vbox)
        top_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=config.PROCESSING_DIALOG_SPACING)
        spinner = Gtk.Spinner()
        spinner.start()
        label = Gtk.Label(label=config.STR_PROCESSING_TEXT)
        top_hbox.pack_start(spinner, True, True, 0)
        top_hbox.pack_start(label, True, True, 0)
        progress_bar = Gtk.ProgressBar()
        progress_bar.set_fraction(0.0)
        main_vbox.set_border_width(config.PROCESSING_DIALOG_BORDER_WIDTH)
        main_vbox.pack_start(top_hbox, True, True, 0)
        main_vbox.pack_start(progress_bar, True, True, 5)
        win_x, win_y = self.get_position()
        # 计算捕获区域的中心点
        capture_center_x = win_x + self.left_panel_w + config.BORDER_WIDTH + self.session.geometry['w'] // 2
        capture_center_y = win_y + config.BORDER_WIDTH + self.session.geometry['h'] // 2
        # 将处理窗口居中显示在捕获区域
        processing_win_w = config.PROCESSING_DIALOG_WIDTH
        processing_win_h = config.PROCESSING_DIALOG_HEIGHT
        processing_x = capture_center_x - processing_win_w // 2
        processing_y = capture_center_y - processing_win_h // 2
        win.move(processing_x, processing_y)
        win.show_all()
        return win, progress_bar

    def on_draw(self, widget, cr):
        cr.set_source_rgba(0, 0, 0, 0)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        main_r, main_g, main_b, main_a = config.BORDER_COLOR
        ind_r, ind_g, ind_b, ind_a = config.MATCHING_INDICATOR_COLOR
        border_width = config.BORDER_WIDTH
        cr.set_line_width(border_width)
        win_w, win_h = self.get_size()
        capture_h = win_h - 2 * border_width
        capture_w = self.session.geometry['w']
        rect_x = self.left_panel_w + border_width / 2
        rect_y = border_width / 2
        rect_w = capture_w + border_width
        rect_h = capture_h + border_width
        is_grid_mode_matching = self.session.is_matching_enabled and self.session.detected_app_class is not None
        is_free_scroll_matching = not is_grid_mode_matching and config.ENABLE_FREE_SCROLL_MATCHING
        draw_indicator = use_matching = is_grid_mode_matching or is_free_scroll_matching
        if not draw_indicator:
            cr.set_source_rgba(main_r, main_g, main_b, main_a)
            cr.rectangle(rect_x, rect_y, rect_w, rect_h)
            cr.stroke()
            return
        if is_grid_mode_matching:
            overlap_height = config.GRID_MATCHING_MAX_OVERLAP
        else:
            overlap_height = config.FREE_SCROLL_MATCHING_MAX_OVERLAP
        overlap_height = min(overlap_height, capture_h / 2)
        cr.set_source_rgba(main_r, main_g, main_b, main_a)
        cr.move_to(rect_x - border_width/2, rect_y)
        cr.line_to(rect_x + rect_w + border_width/2, rect_y)
        cr.stroke()
        cr.move_to(rect_x - border_width/2, rect_y + rect_h)
        cr.line_to(rect_x + rect_w + border_width/2, rect_y + rect_h)
        cr.stroke()
        y_vertical_start = border_width
        y_vertical_end = win_h - border_width
        y_top_end = y_vertical_start + overlap_height
        y_bottom_start = y_vertical_end - overlap_height
        if y_top_end > y_bottom_start:
            y_top_end = y_bottom_start = y_vertical_start + (y_vertical_end - y_vertical_start) / 2
        left_x = rect_x
        cr.set_source_rgba(ind_r, ind_g, ind_b, ind_a)
        cr.move_to(left_x, rect_y)
        cr.line_to(left_x, y_top_end)
        cr.stroke()
        cr.set_source_rgba(main_r, main_g, main_b, main_a)
        cr.move_to(left_x, y_top_end)
        cr.line_to(left_x, y_bottom_start)
        cr.stroke()
        cr.set_source_rgba(ind_r, ind_g, ind_b, ind_a)
        cr.move_to(left_x, y_bottom_start)
        cr.line_to(left_x, rect_y + rect_h)
        cr.stroke()
        right_x = rect_x + rect_w
        cr.set_source_rgba(ind_r, ind_g, ind_b, ind_a)
        cr.move_to(right_x, rect_y)
        cr.line_to(right_x, y_top_end)
        cr.stroke()
        cr.set_source_rgba(main_r, main_g, main_b, main_a)
        cr.move_to(right_x, y_top_end)
        cr.line_to(right_x, y_bottom_start)
        cr.stroke()
        cr.set_source_rgba(ind_r, ind_g, ind_b, ind_a)
        cr.move_to(right_x, y_bottom_start)
        cr.line_to(right_x, rect_y + rect_h)
        cr.stroke()

    def on_size_allocate(self, widget, allocation):
        win_w, win_h = self.get_size()
        final_input_region = cairo.Region()
        # 1. 计算左侧面板区域
        left_panel_w = 0
        if self.show_slider:
            slider_region = cairo.Region(cairo.RectangleInt(0, 0, config.SLIDER_PANEL_WIDTH, win_h))
            final_input_region.union(slider_region)
            left_panel_w += config.SLIDER_PANEL_WIDTH
        if self.panel_on_left:
            btn_region = cairo.Region(cairo.RectangleInt(left_panel_w, 0, config.SIDE_PANEL_WIDTH, win_h))
            final_input_region.union(btn_region)
            left_panel_w += config.SIDE_PANEL_WIDTH
        # 2. 计算边框区域
        border_area_x_start = left_panel_w
        border_area_width = self.session.geometry['w'] + 2 * config.BORDER_WIDTH
        border_full_region = cairo.Region(
            cairo.RectangleInt(border_area_x_start, 0, border_area_width, win_h)
        )
        inner_height = win_h - 2 * config.BORDER_WIDTH
        inner_transparent_region = cairo.Region(
            cairo.RectangleInt(border_area_x_start + config.BORDER_WIDTH, config.BORDER_WIDTH, self.session.geometry['w'], inner_height)
        )
        border_full_region.subtract(inner_transparent_region)
        final_input_region.union(border_full_region)
        # 3. 计算右侧面板区域 (如果按钮在右边)
        if not self.panel_on_left:
            btn_x_start = border_area_x_start + border_area_width
            btn_region = cairo.Region(cairo.RectangleInt(btn_x_start, 0, config.SIDE_PANEL_WIDTH, win_h))
            final_input_region.union(btn_region)
        self.input_shape_combine_region(final_input_region)

    def get_cursor_edge(self, x, y):
        win_w, win_h = self.get_size()
        handle_size = config.HANDLE_HEIGHT 
        on_top = 0 <= y < handle_size
        on_bottom = win_h - handle_size < y <= win_h
        edge_y = ''
        if on_top: edge_y = 'top'
        elif on_bottom: edge_y = 'bottom'
        edge_x = ''
        if not self.session.is_horizontally_locked:
            # 动态计算边框的起始 x
            border_x_start = self.left_panel_w
            border_x_end = border_x_start + self.session.geometry['w'] + 2 * config.BORDER_WIDTH
            on_left = border_x_start <= x < border_x_start + handle_size
            on_right = border_x_end - handle_size < x <= border_x_end
            if on_left: edge_x = 'left'
            elif on_right: edge_x = 'right'
        edge = edge_y + ('-' + edge_x if edge_x and edge_y else edge_x)
        return edge if edge else None

    def on_button_press(self, widget, event):
        return self.controller.handle_button_press(event)

    def on_button_release(self, widget, event):
        return self.controller.handle_button_release(event)

    @property
    def left_panel_w(self):
        """ 根据当前状态计算左侧所有面板的总宽度 """
        width = 0
        if self.show_slider:
            width += config.SLIDER_PANEL_WIDTH
        if self.show_side_panel and self.panel_on_left:
            width += config.SIDE_PANEL_WIDTH
        return width

    def update_layout(self):
        """根据屏幕和选区位置，动态计算并应用窗口布局和几何属性"""
        screen_w = self.screen_rect.width
        # 1. 决策：整个侧边面板是否显示
        should_show_info = config.SHOW_CAPTURE_COUNT or config.SHOW_TOTAL_DIMENSIONS
        self.show_side_panel = config.ENABLE_BUTTONS or should_show_info
        # 2. 侧边面板位置决策
        if self.show_side_panel:
            side_panel_width = config.SIDE_PANEL_WIDTH
            has_space_right = (self.session.geometry['x'] + self.session.geometry['w'] + config.BORDER_WIDTH + side_panel_width) <= screen_w
            has_space_left = (self.session.geometry['x'] - config.BORDER_WIDTH - side_panel_width) >= 0
            if has_space_right:
                self.panel_on_left = False
            elif has_space_left:
                self.panel_on_left = True
            else:
                self.show_side_panel = False # 两边都没空间，强制隐藏
        else:
            self.panel_on_left = False
        # 3. 滑块决策
        if config.ENABLE_SLIDER:
            has_horizontal_space = (self.session.geometry['x'] - config.BORDER_WIDTH - config.SLIDER_PANEL_WIDTH) >= 0
            has_vertical_space = self.session.geometry['h'] >= self._slider_min_h
            self.show_slider = has_horizontal_space and has_vertical_space and not self.panel_on_left
        else:
            self.show_slider = False
        # 4. 根据决策计算几何属性
        left_total_w = 0
        if self.show_slider:
            left_total_w += config.SLIDER_PANEL_WIDTH
        if self.show_side_panel and self.panel_on_left:
            left_total_w += config.SIDE_PANEL_WIDTH
        right_total_w = 0
        if self.show_side_panel and not self.panel_on_left:
            right_total_w = config.SIDE_PANEL_WIDTH
        win_x = self.session.geometry['x'] - left_total_w - config.BORDER_WIDTH
        win_y = self.session.geometry['y'] - config.BORDER_WIDTH
        win_w = left_total_w + self.session.geometry['w'] + 2 * config.BORDER_WIDTH + right_total_w
        win_h = self.session.geometry['h'] + 2 * config.BORDER_WIDTH
        self.move(win_x, win_y)
        self.resize(win_w, win_h)
        # 5. 更新子组件的可见性和位置
        self.slider_panel.set_visible(self.show_slider)
        if self.show_side_panel:
            capture_height = self.session.geometry['h']
            self.side_panel.update_visibility_by_height(capture_height, self.controller.grid_mode_controller.is_active)
            self.side_panel.show()
            slider_w = config.SLIDER_PANEL_WIDTH if self.show_slider else 0
            if self.panel_on_left:
                panel_x = slider_w
            else:
                panel_x = left_total_w + self.session.geometry['w'] + 2 * config.BORDER_WIDTH
            panel_y = config.BORDER_WIDTH
            self.fixed_container.move(self.side_panel, panel_x, panel_y)
        else:
            self.side_panel.hide()
        self.fixed_container.move(self.slider_panel, 0, config.BORDER_WIDTH)
        slider_new_height = self.session.geometry['h']
        self.slider_panel.set_size_request(config.SLIDER_PANEL_WIDTH, max(0, slider_new_height))

    def on_motion_notify(self, widget, event):
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

def toggle_config_window():
    """创建或显示配置窗口，确保只有一个实例存在"""
    global config_window_instance
    def task():
        global config_window_instance
        # 如果窗口已存在且可见，则将其带到前台
        if config_window_instance:
            activate_window_with_xdotool(config_window_instance.xid)
            return
        # 如果窗口不存在，则创建一个新的
        config_window_instance = ConfigWindow(config)
        def on_window_destroy(widget):
            global config_window_instance
            config_window_instance = None
            logging.info("配置窗口已关闭")
        config_window_instance.connect("destroy", on_window_destroy)
        logging.info("配置窗口已创建")
    GLib.idle_add(task)

def toggle_hotkeys_globally():
    """切换全局热键的启用状态并发送桌面通知"""
    global are_hotkeys_enabled
    are_hotkeys_enabled = not are_hotkeys_enabled
    state_str = "启用" if are_hotkeys_enabled else "禁用"
    title = "全局热键状态"
    message = f"截图会话的全局热键当前已{state_str}"
    GLib.idle_add(send_desktop_notification, title, message)
    logging.info(f"全局热键状态已切换为: {state_str}")

def setup_hotkey_listener(overlay):
    global hotkey_listener
    def is_overlay_focused(overlay_widget):
        """检查截图覆盖层窗口当前是否拥有焦点"""
        if not overlay_widget.window_xid:
            return False
        try:
            result = subprocess.run(
                ['xdotool', 'getactivewindow'],
                capture_output=True, text=True, check=True, timeout=0.2
            )
            focused_xid = int(result.stdout.strip())
            is_focused = (focused_xid == overlay_widget.window_xid)
            if is_focused:
                logging.debug(f"焦点检查: 成功，焦点在覆盖层窗口 (XID: {focused_xid})")
            return is_focused
        except (FileNotFoundError, subprocess.CalledProcessError, ValueError, subprocess.TimeoutExpired) as e:
            logging.warning(f"检查窗口焦点失败: {e}。将假定覆盖层未获得焦点")
            return False

    def activate_and_send(hotkey_config):
        def task():
            if not overlay.window_xid: return
            try:
                main_key_str = hotkey_config.get('main_key_str')
                if not main_key_str:
                    logging.error("无法发送按键：在快捷键配置中未找到 'main_key_str'")
                    return
                key_to_press = None
                if main_key_str in config._key_map_pynput_special:
                    key_to_press = config._key_map_pynput_special[main_key_str]
                elif len(main_key_str) == 1:
                    key_to_press = main_key_str
                if not key_to_press:
                    logging.error(f"无法为 '{main_key_str}' 确定要模拟的 pynput 按键对象")
                    return
                if not activate_window_with_xdotool(overlay.window_xid):
                    return
                time.sleep(0.05)
                modifiers_to_press = []
                if hotkey_config['gtk_mask'] & Gdk.ModifierType.CONTROL_MASK: modifiers_to_press.append(keyboard.Key.ctrl)
                if hotkey_config['gtk_mask'] & Gdk.ModifierType.SHIFT_MASK: modifiers_to_press.append(keyboard.Key.shift)
                if hotkey_config['gtk_mask'] & Gdk.ModifierType.MOD1_MASK: modifiers_to_press.append(keyboard.Key.alt)
                for mod in modifiers_to_press: keyboard_controller.press(mod)
                keyboard_controller.press(key_to_press)
                keyboard_controller.release(key_to_press)
                for mod in reversed(modifiers_to_press): keyboard_controller.release(mod)
            except Exception as e:
                logging.error(f"xdotool 执行 'activate and send key' 失败: {e}")
        threading.Thread(target=task, daemon=True).start()

    def create_hotkey_callback(action_func, requires_activation=False, hotkey_config=None):
        def callback():
            if not are_hotkeys_enabled:
                return
            if overlay.is_dialog_open:
                return
            if is_overlay_focused(overlay):
                return
            if config_window_instance is not None:
                if config_window_instance.capturing_hotkey_button is not None:
                    logging.info("配置窗口正在设置快捷键，全局热键已临时禁用")
                    return
                if config_window_instance.input_has_focus:
                    logging.info("配置窗口输入框具有焦点，全局热键已临时禁用")
                    return
            if requires_activation:
                activate_and_send(hotkey_config)
            else:
                GLib.idle_add(action_func)
        return callback
    hotkey_map = {
        config.HOTKEY_CAPTURE['pynput_str']: create_hotkey_callback(overlay.controller.take_capture),
        config.HOTKEY_FINALIZE['pynput_str']: create_hotkey_callback(overlay.controller.finalize_and_quit),
        config.HOTKEY_UNDO['pynput_str']: create_hotkey_callback(overlay.controller.delete_last_capture),
        config.HOTKEY_SCROLL_UP['pynput_str']: create_hotkey_callback(lambda: overlay.controller.handle_movement_action('up')),
        config.HOTKEY_SCROLL_DOWN['pynput_str']: create_hotkey_callback(lambda: overlay.controller.handle_movement_action('down')),
        config.HOTKEY_TOGGLE_GRID_MODE['pynput_str']: create_hotkey_callback(overlay.controller.grid_mode_controller.toggle),
        config.HOTKEY_CANCEL['pynput_str']: create_hotkey_callback(None, requires_activation=True, hotkey_config=config.HOTKEY_CANCEL),
        config.HOTKEY_CONFIGURE_SCROLL_UNIT['pynput_str']: create_hotkey_callback(None, requires_activation=True, hotkey_config=config.HOTKEY_CONFIGURE_SCROLL_UNIT),
        config.HOTKEY_OPEN_CONFIG_EDITOR['pynput_str']: toggle_config_window,
        config.HOTKEY_TOGGLE_HOTKEYS_ENABLED['pynput_str']: toggle_hotkeys_globally,
    }
    valid_hotkey_map = {k: v for k, v in hotkey_map.items() if k}
    try:
        hotkey_listener = keyboard.GlobalHotKeys(valid_hotkey_map)
        listener_thread = threading.Thread(target=hotkey_listener.start, daemon=True)
        listener_thread.start()
        logging.info("全局组合键热键监听器已启动")
    except Exception as e:
        logging.error(f"启动 GlobalHotKeys 失败: {e}")

def cleanup_stale_temp_dirs(config):
    """在启动时清理由已退出的旧进程留下的临时目录"""
    try:
        raw_template = config.parser.get('Paths', 'temp_directory_base', fallback='/tmp/scroll_stitch_{pid}')
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
    critical_deps = {
        'slop': '用于启动时选择截图区域的核心工具'
    }
    optional_deps = {
        'xdotool': '用于在窗口无焦点时激活窗口',
        'paplay': '用于播放截图、撤销和完成时的音效',
        'xdg-open': '用于在截图完成后从通知中打开文件或目录',
        'xinput': '用于“隐形光标”滚动模式，提供无干扰的滚动体验'
    }
    missing_critical = []
    missing_optional = []
    for dep, reason in critical_deps.items():
        if not shutil.which(dep):
            missing_critical.append(f"关键依赖 '{dep}' 缺失: {reason}")
    for dep, reason in optional_deps.items():
        if not shutil.which(dep):
            missing_optional.append(f"可选依赖 '{dep}' 缺失: {reason}")
    if missing_critical:
        logging.error("错误：缺少关键依赖项，程序无法启动")
        for item in missing_critical:
            logging.error(item)
        logging.error("\n请确保已安装上述程序，并将其路径添加至系统的 PATH 环境变量中")
        Notify.Notification.new("错误：依赖缺失", "\n".join(missing_critical), "dialog-error").show()
        sys.exit(1)
    if missing_optional:
        logging.warning("警告：检测到缺少可选依赖项，部分功能可能无法使用或表现异常")
        for item in missing_optional:
            logging.warning(item)
        logging.warning("\n建议安装以获得完整体验")

def main():
    parser = argparse.ArgumentParser(description="一个手动滚动截图并拼接的工具")
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
    Notify.init("scroll_stitch")
    check_dependencies()
    try:
        X11 = ctypes.cdll.LoadLibrary('libX11.so.6')
        X11.XInitThreads()
        logging.info("已调用 XInitThreads() 以确保多线程安全")
    except Exception as e:
        logging.warning(f"无法调用 XInitThreads(): {e}。应用可能不稳定")
    display = Gdk.Display.get_default()
    if display is None or "wayland" in display.get_name().lower():
        msg = "需要 X11 会话\n"
        logging.error(msg)
        send_desktop_notification("环境错误", msg)
        sys.exit(1)
    logging.info("等待用户使用 slop 选择初始区域...")
    try:
        geometry_str = select_area()
        if not geometry_str:
            sys.exit()
    except Exception as e:
        logging.error(f"slop 选择失败或被取消。错误: {e}")
        sys.exit()
    config.TMP_DIR.mkdir(parents=True, exist_ok=True)
    overlay = CaptureOverlay(geometry_str, config)
    overlay.connect("destroy", Gtk.main_quit)
    overlay.show()
    setup_hotkey_listener(overlay)
    Gtk.main()

if __name__ == "__main__":
    main()
