#!/usr/bin/env python3
import sys
import ctypes
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
from pathlib import Path
from datetime import datetime
import time
import math
import bisect
from PIL import Image
import cv2
import numpy as np
import configparser
import argparse
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('Notify', '0.7')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Gdk, GLib, GObject, Notify, Pango, PangoCairo, GdkPixbuf
import cairo
from Xlib import display, X, protocol, XK
from Xlib.ext import xtest
try:
    from evdev import UInput, ecodes as e, AbsInfo
    EVDEV_AVAILABLE = True
except ImportError:
    EVDEV_AVAILABLE = False
# 全局实例
hotkey_listener = None
config_window_instance = None
are_hotkeys_enabled = True
log_queue = None
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
        self.SHOW_INSTRUCTION_NOTIFICATION = self.parser.getboolean('Interface.Components', 'show_instruction_notification', fallback=True)
        # Interface.Layout
        self.BORDER_WIDTH = self.parser.getint('Interface.Layout', 'border_width', fallback=5)
        self.HANDLE_HEIGHT = self.parser.getint('Interface.Layout', 'handle_height', fallback=10)
        self.BUTTON_PANEL_WIDTH = self.parser.getint('Interface.Layout', 'button_panel_width', fallback=100)
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
        self.AUTO_SCROLL_TICKS_PER_STEP = self.parser.getint('Performance', 'auto_scroll_ticks_per_step', fallback=2)
        self.MIN_SCROLL_PER_TICK = self.parser.getint('Performance', 'min_scroll_per_tick', fallback=30)
        self.MAX_SCROLL_PER_TICK = self.parser.getint('Performance', 'max_scroll_per_tick', fallback=170)
        self.MOUSE_MOVE_TOLERANCE = self.parser.getint('Performance', 'mouse_move_tolerance', fallback=5)
        self.MAX_VIEWER_DIMENSION = self.parser.getint('Performance', 'max_viewer_dimension', fallback=32767)
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
        self.str_preview_zoom_in = self.parser.get('Hotkeys', 'preview_zoom_in', fallback='<ctrl>+equal')
        self.str_preview_zoom_out = self.parser.get('Hotkeys', 'preview_zoom_out', fallback='<ctrl>+minus')
        self.str_toggle_hotkeys_enabled = self.parser.get('Hotkeys', 'toggle_hotkeys_enabled', fallback='f4')
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
        self.HOTKEY_OPEN_CONFIG_EDITOR = self._parse_hotkey_string(self.str_open_config_editor)
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
show_instruction_notification = true

[Interface.Layout]
border_width = 5
handle_height = 10
button_panel_width = 100
side_panel_width = 100
button_spacing = 5
processing_dialog_width = 200
processing_dialog_height = 90
processing_dialog_spacing = 15
processing_dialog_border_width = 20

[Interface.Theme]
border_color = 0.73, 0.25, 0.25, 1.00
matching_indicator_color = 0.60, 0.76, 0.95, 1.00
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
max_scroll_per_tick = 170
min_scroll_per_tick = 30
mouse_move_tolerance = 5
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
    def __init__(self, screen_w, screen_h, config: Config):
        self.config = config
        self.screen_w = screen_w
        self.screen_h = screen_h
        self.master_id = None
        self.ui_mouse = None
        self.unique_name = "scroll-stitch-cursor"
        self.park_position = (self.screen_w - 1, self.screen_h - 1)
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
        self.move(*self.park_position)
        logging.info(f"隐形光标已停放至 {self.park_position}")

    def _create_virtual_devices(self):
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

    def move(self, x, y):
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
        capabilities = {
            e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT],
            e.EV_REL: [e.REL_WHEEL],
        }
        # UInput 的初始化
        self.ui_device = UInput(capabilities, name='scroll_stitch-wheel-mouse', version=0x1)
        logging.info("EvdevWheelScroller 初始化成功，虚拟滚轮鼠标已创建")

    def scroll_discrete(self, num_clicks):
        """模拟鼠标滚轮进行离散滚动"""
        if num_clicks == 0:
            return
        value = -1 if num_clicks < 0 else 1
        for _ in range(abs(num_clicks)):
            self.ui_device.write(e.EV_REL, e.REL_WHEEL, value)
            self.ui_device.syn()
            time.sleep(0.01)

    def close(self):
        if self.ui_device:
            self.ui_device.close()
            logging.info("虚拟滚轮鼠标已关闭")

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
                    "open_action",
                    action_label,
                    on_action_clicked,
                    target_path
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
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, check=True)
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
    scale_factor = max(0.08, min(scale_factor, 0.5))
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

def stitch_images_in_memory_from_model(entries: list, image_width: int, total_height: int, progress_callback=None):
    if not entries:
        return None
    num_images = len(entries)
    logging.info(f"开始从 {num_images} 个条目拼接图像，最终尺寸: {image_width}x{total_height}")
    try:
        stitched_image = Image.new('RGBA', (image_width, total_height))
        y_offset = 0
        for i, entry in enumerate(entries):
            filepath = entry['filepath']
            height = entry['height']
            overlap_with_next = entry['overlap']
            logging.info(f"粘贴 {Path(filepath).name} 到 Y={int(round(y_offset))}")
            try:
                img_pil = Image.open(filepath)
                if img_pil.width != image_width:
                    logging.warning(f"图片 {filepath} 宽度 {img_pil.width} 与预期 {image_width} 不符，可能导致错位")
                stitched_image.paste(img_pil, (0, int(round(y_offset))))
                img_pil.close()
            except Exception as e_load:
                logging.error(f"加载或粘贴图片失败 {filepath}: {e_load}")
            if i < num_images - 1:
                y_offset += height - overlap_with_next
            if progress_callback:
                GLib.idle_add(progress_callback, (i + 1) / num_images)
        logging.info("图像拼接完成")
        return stitched_image
    except Exception as e:
        logging.exception(f"拼接图像时发生严重错误: {e}")
        return None

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

def get_active_window_xid():
    disp = None
    try:
        disp = display.Display()
        root = disp.screen().root
        active_window_atom = disp.intern_atom('_NET_ACTIVE_WINDOW')
        prop = root.get_full_property(active_window_atom, X.AnyPropertyType)
        if prop and prop.value:
            active_xid = prop.value[0]
            logging.info(f"通过 python-xlib 获取到活动窗口 XID: {active_xid}")
            return active_xid
        else:
            logging.warning("无法通过 _NET_ACTIVE_WINDOW 获取活动窗口")
            return None
    except Exception as e:
        logging.error(f"使用 python-xlib 获取活动窗口时出错: {e}")
        return None
    finally:
        if disp:
            disp.close()

def activate_window_with_xlib(xid):
    if not xid:
        logging.warning("无法激活窗口：XID 不可用")
    disp = None
    try:
        disp = display.Display()
        root = disp.screen().root
        window_obj = disp.create_resource_object('window', xid)
        if not window_obj:
            logging.error(f"无法为 XID {xid} 创建资源对象，窗口可能不存在。")
        active_window_atom = disp.intern_atom('_NET_ACTIVE_WINDOW')
        event = protocol.event.ClientMessage(
            window=window_obj,
            client_type=active_window_atom,
            data=(32, [2, X.CurrentTime, xid, 0, 0])
        )
        mask = (X.SubstructureRedirectMask | X.SubstructureNotifyMask)
        root.send_event(event, event_mask=mask)
        disp.sync()
        logging.info(f"已通过 python-xlib 成功请求激活窗口 XID {xid}")
    except Exception as e:
        logging.error(f"使用 python-xlib 激活窗口 {xid} 失败: {e}")
    finally:
        if disp:
            disp.close()
    return False

def create_feedback_dialog(parent_window, text, show_progress_bar=False, position=None):
    win = Gtk.Window(type=Gtk.WindowType.POPUP)
    win.set_transient_for(parent_window)
    win.set_modal(True)
    win.set_decorated(False)
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
    label = Gtk.Label(label=text)
    top_hbox.pack_start(spinner, True, True, 0)
    top_hbox.pack_start(label, True, True, 0)
    main_vbox.set_border_width(config.PROCESSING_DIALOG_BORDER_WIDTH)
    main_vbox.pack_start(top_hbox, True, True, 0)
    progress_bar = None
    if show_progress_bar:
        progress_bar = Gtk.ProgressBar()
        progress_bar.set_fraction(0.0)
        main_vbox.pack_start(progress_bar, True, True, 5)
    win.set_default_size(config.PROCESSING_DIALOG_WIDTH, config.PROCESSING_DIALOG_HEIGHT)
    if position:
        win.move(position[0], position[1])
    else:
        win.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
    return win, progress_bar

class StitchModel(GObject.Object):
    """管理拼接数据的模型，支持异步更新和信号通知"""
    __gsignals__ = {
        'model-updated': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
    def __init__(self):
        super().__init__()
        self.entries = []
        self.image_width = 0
        self.total_virtual_height = 0
        self.y_positions = []
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

    def add_entry(self, filepath: str, width: int, height: int, overlap_with_previous: int):
        logging.info(f"主线程: 收到添加请求: {filepath}, h={height}, overlap={overlap_with_previous}")
        if not self.entries:
            self.image_width = width
            self.total_virtual_height = height
            self.y_positions = [0]
            self.entries.append({'filepath': filepath, 'height': height, 'overlap': 0})
            logging.info(f"添加首张截图，当前总高: {self.total_virtual_height}")
        else:
            self.entries[-1]['overlap'] = overlap_with_previous
            last_entry = self.entries[-1]
            last_y_pos = self.y_positions[-1]
            new_y_pos = last_y_pos + last_entry['height'] - overlap_with_previous
            self.y_positions.append(new_y_pos)
            self.entries.append({'filepath': filepath, 'height': height, 'overlap': 0})
            self.total_virtual_height = new_y_pos + height
            logging.info(f"添加新截图，上一张重叠: {overlap_with_previous}px, 当前总高: {self.total_virtual_height}")
        self.emit('model-updated')

    def pop_entry(self):
        if not self.entries:
            return
        logging.info("主线程: 收到移除最后一个条目的请求")
        popped_entry = self.entries.pop()
        self.y_positions.pop()
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
            last_y_pos = self.y_positions[-1] if self.y_positions else 0
            self.total_virtual_height = last_y_pos + last_entry['height']
            logging.info(f"移除截图后，当前总高: {self.total_virtual_height}")
        else:
            self.image_width = 0
            self.total_virtual_height = 0
            logging.info("所有截图已移除")
        self.emit('model-updated')

class CaptureSession:
    """管理一次滚动截图会话的数据和状态"""
    def __init__(self, geometry_str: str):
        self.is_horizontally_locked: bool = False
        self.geometry: dict = self._parse_geometry(geometry_str)
        self.detected_app_class: str = None
        self.is_matching_enabled: bool = False
        self.known_scroll_distances = []

    def _parse_geometry(self, geometry_str: str):
        """从 "WxH+X+Y" 格式的字符串中解析出几何信息"""
        parts = geometry_str.strip().split('+')
        dims, x_str, y_str = parts[0], parts[1], parts[2]
        w_str, h_str = dims.split('x')
        return {'x': int(x_str), 'y': int(y_str), 'w': int(w_str), 'h': int(h_str)}

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
        if self.view.controller.is_auto_scrolling:
            logging.warning("自动滚动模式下忽略切换整格模式请求")
            return
        if self.is_active:
            self.is_active = False
            self.grid_unit = 0
            self.session.detected_app_class = None
            self.session.is_matching_enabled = False
            self.view.button_panel.set_grid_action_buttons_visible(False)
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
            self.view.button_panel.set_grid_action_buttons_visible(True)
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
        """启动自动滚动单位校准流程"""
        if self.view.controller.is_auto_scrolling:
            logging.warning("自动滚动模式下忽略配置滚动单位请求")
            return
        if self.is_active:
            send_desktop_notification("操作无效", "请先按 Shift 键退出整格模式再进行配置")
            return
        app_class = self._get_app_class_at_center()
        if not app_class:
            send_desktop_notification("配置失败", "无法检测到底层应用程序")
            return
        logging.info(f"为应用 '{app_class}' 启动自动校准...")
        screen_rect = self.view.screen_rect
        dialog_w = config.PROCESSING_DIALOG_WIDTH
        dialog_h = config.PROCESSING_DIALOG_HEIGHT
        padding = 20
        dialog_y = screen_rect.y + padding
        dialog_x = screen_rect.x + padding
        dialog_text = f"正在为 {app_class} 自动校准...\n请勿操作"
        dialog, _ = create_feedback_dialog(
            parent_window=self.view,
            text=dialog_text,
            show_progress_bar=False,
            position=(dialog_x, dialog_y)
        )
        self.calibration_state = {
            "app_class": app_class,
            "step": 0,
            "num_samples": 4,
            "measured_units": [],
            "dialog": dialog
        }
        self.calibration_state["dialog"].show_all()
        GLib.idle_add(self._run_calibration_step)

    def _run_calibration_step(self):
        state = self.calibration_state
        step = state["step"]
        h = self.session.geometry['h']
        w = self.session.geometry['w']
        win_x, win_y = self.view.get_position()
        shot_x = win_x + self.view.left_panel_w + config.BORDER_WIDTH
        shot_y = win_y + config.BORDER_WIDTH
        ticks_to_scroll = max(1, int(h / 150))
        if step == 0:
            logging.info(f"校准参数: 截图区高度={h}px, 每次滚动格数={ticks_to_scroll}, 采样次数={state['num_samples']}")
            state['ticks_to_scroll'] = ticks_to_scroll
            state["filepath_before"] = config.TMP_DIR / "cal_before.png"
            if not capture_area(shot_x, shot_y, w, h, state["filepath_before"]):
                self._finalize_calibration(success=False)
                return False
            self.view.controller.scroll_manager.scroll_discrete(-ticks_to_scroll)
            state["step"] += 1
            GLib.timeout_add(400, self._run_calibration_step)
            return False
        if 0 < step <= state["num_samples"]:
            filepath_after = config.TMP_DIR / "cal_after.png"
            if not capture_area(shot_x, shot_y, w, h, filepath_after):
                logging.warning(f"第 {step} 次采样截图失败，跳过")
            else:
                img_top = cv2.imread(str(state["filepath_before"]))
                img_bottom = cv2.imread(str(filepath_after))
                if img_top is not None and img_bottom is not None:
                    max_search_overlap = h - (ticks_to_scroll * self.config.MIN_SCROLL_PER_TICK)
                    found_overlap, score = _find_overlap_pyramid(img_top, img_bottom, max_search_overlap)
                    if score > 0.95:
                        scroll_dist_px = h - found_overlap
                        unit = scroll_dist_px / state['ticks_to_scroll']
                        if unit < self.config.MIN_SCROLL_PER_TICK:
                            logging.warning(f"检测到滚动距离过小({unit:.2f}px/格)，已到达页面末端。提前中止采样")
                            self._finalize_calibration(success=True)
                            return False
                        state["measured_units"].append(unit)
                        logging.info(f"采样 {step}: 成功，单位距离 ≈ {unit:.2f}px/格，相似度 {score:.3f}")
                    else:
                        logging.warning(f"采样 {step}: 匹配失败（相似度 {score:.3f}）")
                        bottom_check_height = ticks_to_scroll * self.config.MIN_SCROLL_PER_TICK
                        if ActionController._check_if_bottom_reached(img_top, img_bottom, bottom_check_height):
                            logging.warning(f"校准：检测到底部（匹配失败后底部仍一致），提前中止采样")
                            self._finalize_calibration(success=True)
                            return False
            if os.path.exists(state["filepath_before"]):
                os.remove(state["filepath_before"])
            if os.path.exists(filepath_after):
                os.rename(filepath_after, state["filepath_before"])
            if step < state["num_samples"]:
                self.view.controller.scroll_manager.scroll_discrete(-state['ticks_to_scroll'])
                state["step"] += 1
                GLib.timeout_add(400, self._run_calibration_step)
            else:
                self._finalize_calibration(success=True)
            return False
    
    def _finalize_calibration(self, success):
        """分析数据、通过聚类剔除离群值、保存结果并清理"""
        state = self.calibration_state
        state["dialog"].destroy()
        if os.path.exists(state.get("filepath_before", "")):
            os.remove(state["filepath_before"])
        MIN_VALID_SAMPLES = 2
        if not success or not state["measured_units"] or len(state["measured_units"]) < MIN_VALID_SAMPLES:
            msg = f"为 '{state['app_class']}' 校准失败\n有效采样数据不足，请在内容更丰富的区域操作或确保界面有足够的滚动空间"
            send_desktop_notification("配置失败", msg)
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
            send_desktop_notification("配置失败", msg)
            logging.error(msg.replace('\n', ' '))
            return
        final_avg_unit = round(np.mean(largest_cluster))
        final_std_dev = np.std(largest_cluster)
        matching_enabled = final_std_dev >= 0.05
        logging.info(f"最终分析: 平均单位={final_avg_unit}, 标准差={final_std_dev:.3f}, 决策:开启误差修正={matching_enabled}")
        if config.save_scroll_unit(state["app_class"], final_avg_unit, matching_enabled):
            status_str = "启用" if matching_enabled else "禁用"
            msg = f"已为 '{state['app_class']}' 保存滚动单位: {final_avg_unit}px\n误差修正已<b>{status_str}</b>"
            send_desktop_notification("配置成功", msg)
        else:
            send_desktop_notification("配置失败", "写入配置文件时发生错误")

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

    def _get_pointer_position(self):
        """使用 GDK 获取当前鼠标指针位置"""
        try:
            _, x, y = self.gdk_pointer.get_position()
            return (x, y)
        except Exception as e:
            logging.error(f"GDK 获取鼠标位置失败: {e}")
            return (0, 0)

    def _set_pointer_position(self, x, y):
        """使用 GDK 设置鼠标指针位置"""
        try:
            self.gdk_pointer.warp(self.gdk_screen, x, y)
            self.gdk_display.flush()
            time.sleep(0.01)
        except Exception as e:
            logging.error(f"GDK warp 失败: {e}")

    def scroll_discrete(self, ticks, is_auto_scroll=False):
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
            original_pos = self._get_pointer_position()
            self._set_pointer_position(center_x, center_y)
            time.sleep(0.05)
            try:
                if self.view.evdev_wheel_scroller:
                    logging.info(f"使用 Evdev 执行离散滚动: {ticks} 格")
                    try:
                        self.view.evdev_wheel_scroller.scroll_discrete(ticks)
                    except Exception as e:
                        logging.error(f"使用 Evdev 模拟滚动失败: {e}")
                else:
                    logging.warning("Evdev 模块不可用，回退到 XTest 滚动")
                    try:
                        disp = display.Display()
                        button_code = 4 if ticks > 0 else 5
                        num_clicks = abs(ticks)
                        for i in range(num_clicks):
                            xtest.fake_input(disp, X.ButtonPress, button_code)
                            disp.sync()
                            time.sleep(0.005)
                            xtest.fake_input(disp, X.ButtonRelease, button_code)
                            disp.sync()
                            if i < num_clicks - 1:
                                time.sleep(0.015)
                        disp.close()
                    except Exception as e:
                        logging.error(f"使用 XTest 模拟滚动失败: {e}")
                        try: disp.close()
                        except: pass
            except Exception as e:
                logging.error(f"模拟滚动失败: {e}")
            hide_and_auto = is_auto_scroll and not self.config.CAPTURE_WITH_CURSOR
            if not hide_and_auto:
               time.sleep(0.05)
               current_pos_after_scroll = self._get_pointer_position()
               tolerance = self.config.MOUSE_MOVE_TOLERANCE
               user_intervened_during_scroll = (
                    abs(current_pos_after_scroll[0] - center_x) > tolerance or
                    abs(current_pos_after_scroll[1] - center_y) > tolerance
               )
               if not user_intervened_during_scroll:
                   self._set_pointer_position(*original_pos)
            else:
                logging.info("自动模式隐藏光标：滚动完成，保持光标在中心")
                time.sleep(0.05)

class ActionController:
    """处理所有用户操作和业务逻辑"""
    def __init__(self, session: CaptureSession, view: 'CaptureOverlay', config: Config):
        self.session = session
        self.view = view
        self.config = config
        self.final_notification = None
        self.is_dragging = False
        self.is_processing_movement = False
        self.is_auto_scrolling = False
        self.auto_scroll_timer_id = None
        self.is_first_auto_capture = False
        self.auto_scroll_original_cursor_pos = None
        self.last_auto_scroll_cursor_pos = None
        self.auto_scroll_needs_capture = False
        self.auto_mode_context = None
        self.SCROLL_TIME_MS = 200
        self.CAPTURE_DELAY_MS = 150
        self.AUTO_SCROLL_INTERVAL_MS = 300
        self.AUTO_CAPTURE_DELAY_MS = 50
        self.resize_edge = None
        self.drag_start_geometry = {}
        self.drag_start_x_root = 0
        self.drag_start_y_root = 0
        self.scroll_manager = ScrollManager(self.config, self.session, self.view)
        self.grid_mode_controller = GridModeController(self.config, self.session, self.view)
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
        can_undo = self.stitch_model.capture_count > 0 and not self.is_auto_scrolling
        if self.view.show_side_panel:
            self.view.side_panel.info_panel.update_info(
                count=self.stitch_model.capture_count,
                width=self.stitch_model.image_width,
                height=self.stitch_model.total_virtual_height
            )
            self.view.button_panel.set_undo_sensitive(can_undo)
        self._check_horizontal_lock_state()

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
                        logging.info(f"主线程: 学习到新滚动距离: {s_new}px")
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
                            for s_known in known_scroll_distances:
                                potential_overlap = h_new - s_known
                                if 1 <= potential_overlap < min(h_top, h_new):
                                    _, score = _find_overlap_brute_force(img_top_np, img_new_np, potential_overlap, potential_overlap)
                                    PREDICTION_THRESHOLD = 0.995
                                    if score > PREDICTION_THRESHOLD:
                                        predicted_overlap = potential_overlap
                                        logging.info(f"StitchWorker: 预测成功 overlap={predicted_overlap}, score={score:.3f}")
                                        break
                        if predicted_overlap != -1:
                            overlap = predicted_overlap
                        else:
                            if should_perform_matching:
                                search_range = min(max_overlap_to_use, h_top - 1, h_new - 1)
                                if search_range > 0:
                                    logging.info(f"StitchWorker: 预测失败，执行全范围搜索 (max={search_range}px)...")
                                    found_overlap, score = _find_overlap_pyramid(img_top_np, img_new_np, search_range)
                                    QUALITY_THRESHOLD = 0.95
                                    bottom_check_height = config.MIN_SCROLL_PER_TICK
                                    if score >= QUALITY_THRESHOLD and found_overlap >= config.MIN_SCROLL_PER_TICK:
                                        overlap = found_overlap
                                        logging.info(f"StitchWorker: 计算重叠成功: {overlap}px, score={score:.3f}")
                                        s_new = h_new - overlap
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
                                        logging.warning(f"StitchWorker: 计算重叠失败 (score {score:.3f} < {QUALITY_THRESHOLD})")
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

    def handle_movement_action(self, direction: str):
        """根据配置文件处理前进/后退动作 (滚动, 截图, 删除). """
        if self.is_processing_movement:
            logging.warning("正在处理上一个移动动作，忽略新的请求")
            return
        if not self.grid_mode_controller.is_active:
            logging.warning("非整格模式下，前进/后退动作无效")
            send_desktop_notification("操作无效", "前进/后退操作仅在整格模式下可用")
            return
        self.is_processing_movement = True
        if self.grid_mode_controller.grid_unit <= 0:
            logging.error("整格模式滚动单位无效，无法执行操作")
            self.is_processing_movement = False
            return
        action_str = config.FORWARD_ACTION if direction == 'down' else config.BACKWARD_ACTION
        actions = action_str.lower().replace(" ", "").split('_')
        def do_scroll_action(callback):
            region_height = self.session.geometry['h']
            num_ticks = round(region_height / self.grid_mode_controller.grid_unit)
            direction_sign = 1 if direction == 'up' else -1
            total_ticks = num_ticks * direction_sign
            self.scroll_manager.scroll_discrete(total_ticks)
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
        grabbed_seat = None
        filepath = None
        self.auto_mode_context = None
        if not auto_mode and self.is_auto_scrolling:
            logging.warning("自动滚动模式下忽略手动截图请求")
            return
        try:
            win_x, win_y = self.view.get_position()
            shot_x = win_x + self.view.left_panel_w + config.BORDER_WIDTH
            shot_y = win_y + config.BORDER_WIDTH
            shot_w = self.session.geometry['w']
            shot_h = self.session.geometry['h']
            if auto_mode:
                if self.is_first_auto_capture:
                    logging.info("自动模式：截取首次完整高度")
                    cap_x, cap_y, cap_w, cap_h = shot_x, shot_y, shot_w, shot_h
                    self.auto_mode_context = {'initial_full': True}
                    self.is_first_auto_capture = False
                else:
                    ticks_to_scroll = self.config.AUTO_SCROLL_TICKS_PER_STEP
                    capture_height_per_tick = self.config.MAX_SCROLL_PER_TICK
                    total_capture_height = capture_height_per_tick * ticks_to_scroll
                    cap_h = min(total_capture_height, shot_h)
                    cap_y = shot_y + shot_h - cap_h
                    cap_x, cap_w = shot_x, shot_w
                    self.auto_mode_context = {'initial_full': False}
            else:
                cap_x, cap_y, cap_w, cap_h = shot_x, shot_y, shot_w, shot_h
                self.auto_mode_context = None

            grabbed_seat = self._hide_cursor_if_needed(cap_x, cap_y, cap_w, cap_h)
            if cap_w <= 0 or cap_h <= 0:
                logging.warning(f"捕获区域过小，跳过截图。尺寸: {cap_w}x{cap_h}")
                send_desktop_notification("截图跳过", "选区太小，无法截图", "dialog-warning")
                return
            filepath = config.TMP_DIR / f"{self.stitch_model.capture_count:02d}_capture.png"
            if capture_area(cap_x, cap_y, cap_w, cap_h, filepath):
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
            send_desktop_notification("截图失败", f"无法执行截图命令: {e}", "dialog-warning")
            filepath = None
        finally:
            if grabbed_seat:
                display = self.view.get_display()
                grabbed_seat.ungrab()
                display.flush()
                logging.info("截图操作完成，已释放指针抓取，恢复默认光标")

    def _hide_cursor_if_needed(self, x, y, w, h):
        """如果光标在截图区域内且配置为隐藏，则执行指针抓取来全局隐藏光标"""
        if config.CAPTURE_WITH_CURSOR:
            return None
        mouse_pos = self.scroll_manager._get_pointer_position()
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
                return seat
            else:
                logging.warning(f"指针抓取失败，状态: {status}。光标可能不会被隐藏")
                return None
        else:
            logging.info("配置为隐藏鼠标，但鼠标在区域外，无需操作")
            return None

    def delete_last_capture(self, widget=None):
        logging.info("请求删除最后一张截图...")
        if self.is_auto_scrolling:
            logging.warning("自动滚动模式下忽略撤销请求")
            return
        play_sound(config.UNDO_SOUND)
        task = {'type': 'POP'}
        self.task_queue.put(task)

    def start_auto_scroll(self, widget=None):
        if self.is_auto_scrolling:
            logging.warning("自动滚动已在运行中")
            return
        self.is_auto_scrolling = True
        self.last_auto_scroll_cursor_pos = None
        self.auto_scroll_original_cursor_pos = None
        if not self.config.CAPTURE_WITH_CURSOR and self.config.SCROLL_METHOD == 'move_user_cursor':
            self.auto_scroll_original_cursor_pos = self.scroll_manager._get_pointer_position()
            logging.info(f"自动模式隐藏光标：记录原始光标位置 {self.auto_scroll_original_cursor_pos}")
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
        send_desktop_notification("自动模式已启动", f"按 {config.str_auto_scroll_stop.upper()} 或移动鼠标停止", level="low")
        btn_panel = self.view.button_panel
        btn_panel.btn_capture.set_sensitive(False)
        btn_panel.btn_undo.set_sensitive(False)
        btn_panel.btn_auto_start.set_sensitive(False)

    def stop_auto_scroll(self, reason_message=None):
        if not self.is_auto_scrolling:
            return
        logging.info("正在停止自动滚动...")
        self.is_auto_scrolling = False
        if self.auto_scroll_timer_id:
            GLib.source_remove(self.auto_scroll_timer_id)
            self.auto_scroll_timer_id = None
            logging.info("自动滚动定时器已移除")
            if self.auto_scroll_needs_capture:
                logging.info("自动模式：停止时捕获最后一个滚动帧")
                self.take_capture(auto_mode=True)
                self.needs_final_auto_capture = False
                self.auto_scroll_needs_capture = False
        self._release_movement_lock()
        if self.auto_scroll_original_cursor_pos:
            logging.info(f"自动模式结束：恢复原始光标位置到 {self.auto_scroll_original_cursor_pos}")
            self.scroll_manager._set_pointer_position(*self.auto_scroll_original_cursor_pos)
            self.auto_scroll_original_cursor_pos = None
        if reason_message:
            send_desktop_notification("自动滚动已停止", reason_message, level="normal")
        else:
            send_desktop_notification("自动模式已停止", "用户手动停止", level="normal")
        btn_panel = self.view.button_panel
        btn_panel.btn_capture.set_sensitive(True)
        btn_panel.set_undo_sensitive(self.stitch_model.capture_count > 0)
        btn_panel.btn_grid_forward.set_sensitive(True)
        btn_panel.btn_grid_backward.set_sensitive(True)
        btn_panel.btn_auto_start.set_sensitive(True)

    def _auto_scroll_step(self):
        if self.last_auto_scroll_cursor_pos:
            current_pos = self.scroll_manager._get_pointer_position()
            dx = current_pos[0] - self.last_auto_scroll_cursor_pos[0]
            dy = current_pos[1] - self.last_auto_scroll_cursor_pos[1]
            distance_moved = math.sqrt(dx*dx + dy*dy)
            if distance_moved > self.config.MOUSE_MOVE_TOLERANCE:
                logging.info(f"自动模式：检测到用户鼠标移动 {distance_moved:.1f}px (超过阈值 {self.config.MOUSE_MOVE_TOLERANCE}px)，停止滚动。")
                self.stop_auto_scroll(reason_message="检测到鼠标移动")
                return False
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
        self.scroll_manager.scroll_discrete(-ticks_to_scroll, is_auto_scroll=True)
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
        if self.config.SCROLL_METHOD == 'move_user_cursor':
            self.last_auto_scroll_cursor_pos = self.scroll_manager._get_pointer_position()
        self.is_processing_movement = False
        self.auto_scroll_needs_capture = False
        self.auto_scroll_timer_id = GLib.timeout_add(
            self.AUTO_CAPTURE_DELAY_MS, 
            self._auto_scroll_step
        )
        return False

    def finalize_and_quit(self, widget=None):
        """执行完成拼接并退出的逻辑"""
        if self.is_auto_scrolling:
            self.stop_auto_scroll()
        if self.stitch_model.capture_count == 0:
            logging.warning("未进行任何截图。正在退出")
            self.quit_and_cleanup()
            return
        if hotkey_listener and hotkey_listener.is_alive():
            hotkey_listener.stop()
        logging.info("请求停止 StitchWorker 并等待...")
        self.task_queue.put({'type': 'EXIT'})
        self.stitch_worker.join(timeout=2.0)
        self.stitch_worker_running = False
        self._check_result_queue()
        logging.info("StitchWorker 已停止且结果队列已清空.")
        processing_window, progress_bar = self.view._create_processing_window()
        self.view.hide()
        if self.view.preview_window:
             logging.info("检测到预览窗口仍然打开，正在销毁它...")
             GLib.idle_add(self.view.preview_window.destroy)
             self.view.preview_window = None
        entries_snapshot = list(self.stitch_model.entries)
        image_width_snapshot = self.stitch_model.image_width
        total_height_snapshot = self.stitch_model.total_virtual_height
        thread = threading.Thread(
            target=self._perform_final_stitch_and_save,
            args=(processing_window, progress_bar, entries_snapshot, image_width_snapshot, total_height_snapshot),
            daemon=True
        )
        thread.start()

    def _ensure_cleanup(self):
        if self.final_notification is not None:
            logging.warning("通知关闭回调超时，强制执行清理")
            self.final_notification = None
            self._perform_cleanup()
        return GLib.SOURCE_REMOVE

    def _perform_final_stitch_and_save(self, processing_window, progress_bar, entries, image_width, total_height):
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
            if not entries:
                logging.warning("Finalize BG: 传入的截图列表为空，退出处理")
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
                 entries=entries,
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
                        action_path=output_file,
                        controller=self,
                        width=image_width,
                        height=total_height,
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
        if self.is_auto_scrolling:
            self.stop_auto_scroll()
        if self.stitch_model.capture_count == 0:
            logging.info("没有截图，直接退出")
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
        global config_window_instance
        if config_window_instance:
            logging.info("检测到配置窗口仍然打开，正在销毁它...")
            config_window_instance.destroy()
            config_window_instance = None
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
        elif is_match(config.HOTKEY_GRID_BACKWARD):
            self.handle_movement_action('up')
            return True
        elif is_match(config.HOTKEY_GRID_FORWARD):
            self.handle_movement_action('down')
            return True
        elif is_match(config.HOTKEY_AUTO_SCROLL_START):
            self.start_auto_scroll()
            return True
        elif is_match(config.HOTKEY_AUTO_SCROLL_STOP):
            self.stop_auto_scroll()
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
        'grid-backward-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'grid-forward-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'auto-scroll-start-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'auto-scroll-stop-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=config.BUTTON_SPACING)
        # 整格模式按钮
        self.btn_grid_forward = Gtk.Button(label="前进")
        self.btn_grid_backward = Gtk.Button(label="后退")
        self.btn_grid_forward.connect("clicked", lambda w: self.emit('grid-forward-clicked'))
        self.btn_grid_backward.connect("clicked", lambda w: self.emit('grid-backward-clicked'))
        # 自动滚动按钮
        self.btn_auto_start = Gtk.Button(label="开始")
        self.btn_auto_stop = Gtk.Button(label="停止")
        self.btn_auto_start.connect("clicked", lambda w: self.emit('auto-scroll-start-clicked'))
        self.btn_auto_stop.connect("clicked", lambda w: self.emit('auto-scroll-stop-clicked'))
        # 主操作按钮
        self.btn_capture = Gtk.Button(label="截图")
        self.btn_capture.connect("clicked", lambda w: self.emit('capture-clicked'))
        self.btn_undo = Gtk.Button(label="撤销")
        self.btn_undo.connect("clicked", lambda w: self.emit('undo-clicked'))
        self.btn_finalize = Gtk.Button(label="完成")
        self.btn_finalize.connect("clicked", lambda w: self.emit('finalize-clicked'))
        self.btn_cancel = Gtk.Button(label="取消")
        self.btn_cancel.connect("clicked", lambda w: self.emit('cancel-clicked'))
        all_buttons = [
            self.btn_grid_backward, self.btn_grid_forward,
            self.btn_auto_start, self.btn_auto_stop,
            self.btn_capture, self.btn_undo, self.btn_finalize, self.btn_cancel
        ]
        for btn in all_buttons:
            btn.set_can_focus(False)
            btn.show()
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
        logging.info(f"缓存的 ButtonPanel 普通模式自然高度: {self._button_natural_h_normal}")
        self.btn_grid_backward.set_visible(config.ENABLE_GRID_ACTION_BUTTONS)
        self.btn_grid_forward.set_visible(config.ENABLE_GRID_ACTION_BUTTONS)
        self.separator_grid_auto.set_visible(config.ENABLE_GRID_ACTION_BUTTONS)
        self.btn_auto_start.set_visible(False)
        self.btn_auto_stop.set_visible(False)
        self.separator_auto_main.set_visible(False)
        _, self._button_natural_h_grid = self.get_preferred_height()
        logging.info(f"缓存的 ButtonPanel 整格模式自然高度: {self._button_natural_h_grid}")
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
            self.hide()
            return
        required_h = self._button_natural_h_grid if is_grid_mode else self._button_natural_h_normal
        if available_height >= required_h:
            self.show()
        else:
            self.hide()

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
        self.info_panel.set_size_request(config.SIDE_PANEL_WIDTH, -1)
        self.pack_start(self.info_panel, False, False, 0)
        self.info_panel.show()
        _, self._info_natural_h = self.info_panel.get_preferred_height()
        logging.info(f"缓存的 InfoPanel 自然高度: {self._info_natural_h}")

    def update_visibility_by_height(self, available_height: int, is_grid_mode: bool):
        should_show_info_base = config.ENABLE_SIDE_PANEL and (config.SHOW_CAPTURE_COUNT or config.SHOW_TOTAL_DIMENSIONS)
        if not should_show_info_base:
            self.info_panel.hide()
            return
        required_h_for_info = self._info_natural_h if should_show_info_base else 0
        threshold_for_info_only = required_h_for_info
        can_show_info_panel = available_height >= threshold_for_info_only
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
            ('Interface.Components', 'enable_buttons'),
            ('Interface.Components', 'enable_grid_action_buttons'), ('Interface.Components', 'enable_auto_scroll_buttons'),
            ('Interface.Components', 'enable_side_panel'),
            ('Interface.Components', 'show_preview_on_start'),
            ('Interface.Components', 'show_capture_count'), ('Interface.Components', 'show_total_dimensions'),
            ('Interface.Components', 'show_instruction_notification'),
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
            ('System', 'copy_to_clipboard_on_finish'), ('System', 'notification_click_action'),
            ('System', 'large_image_opener'), ('System', 'sound_theme'),
            ('System', 'capture_sound'), ('System', 'undo_sound'), ('System', 'finalize_sound'),
            ('Performance', 'grid_matching_max_overlap'), ('Performance', 'free_scroll_matching_max_overlap'), ('Performance', 'mouse_move_tolerance'),
            ('Performance', 'auto_scroll_ticks_per_step'), ('Performance', 'max_scroll_per_tick'), ('Performance', 'min_scroll_per_tick'),
            ('Performance', 'max_viewer_dimension'), ('Performance', 'preview_drag_sensitivity'),
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
        self.connect("map-event", self._on_map_event)
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

    def _on_map_event(self, widget, event):
        logging.info("ConfigWindow map-event 触发，正在请求激活")
        if self.xid:
            GLib.idle_add(activate_window_with_xlib, self.xid)
        else:
            logging.warning("_on_map_event: self.xid 尚未设置，无法激活")
        return False

    def _on_delete_event(self, widget, event):
        """窗口关闭时保存所有配置"""
        self._save_all_configs()
        global hotkey_listener
        if hotkey_listener and are_hotkeys_enabled:
            hotkey_listener.set_normal_keys_grabbed(True)
            logging.info("配置窗口关闭，全局热键已恢复")
        return False

    def _on_destroy(self, widget):
        """窗口销毁时的清理操作"""
        if self.log_timer_id:
            GLib.source_remove(self.log_timer_id)
            self.log_timer_id = None
            logging.info("配置窗口的日志更新定时器已成功移除")
        self._save_all_configs()

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
        rev_map = {v: k for k, v in self.config._key_map_gtk_special.items()}
        print(keyval)
        main_key_str = ""
        if keyval in rev_map:
            main_key_str = rev_map[keyval]
        else:
            codepoint = Gdk.keyval_to_unicode(keyval)
            print(codepoint)
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
        global hotkey_listener
        if hotkey_listener:
            hotkey_listener.set_normal_keys_grabbed(False)
            logging.info("开始捕获快捷键，全局热键暂停")

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
        if hotkey_listener and not self.input_has_focus and are_hotkeys_enabled:
            hotkey_listener.set_normal_keys_grabbed(True)
            logging.info("快捷键捕获结束，全局热键恢复")
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
            ("grid_backward", "整格后退"), ("grid_forward", "整格前进"),
            ("auto_scroll_start", "开始自动滚动"), ("auto_scroll_stop", "停止自动滚动"),
            ("configure_scroll_unit", "配置滚动单位"), ("toggle_grid_mode", "切换整格模式"),
            ("open_config_editor", "激活/隐藏配置窗口"), ("toggle_hotkeys_enabled", "启用/禁用全局热键"), 
            ("preview_zoom_in", "预览窗口放大"), ("preview_zoom_out", "预览窗口缩小"),
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
            ('Interface.Components', 'show_instruction_notification'),
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
            ("show_preview_on_start", "启动时显示预览窗口", "控制是否在截图会话开始时自动打开预览窗口"),
            ("show_capture_count", "显示已截图数量", "是否在侧边栏信息面板中显示当前已截取的图片数量"),
            ("show_total_dimensions", "显示最终图片总尺寸", "是否在侧边栏信息面板中显示拼接后图片的总宽度和总高度"),
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
            ("button_panel_width", "按钮面板宽度", 80, 200, "右侧按钮面板的宽度"),
            ("side_panel_width", "侧边栏宽度", 80, 200, "功能面板和信息面板的总宽度"),
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
            ("processing_dialog_css", "处理中对话框样式"), 
            ("info_panel_css", "信息面板样式")
        ]
        for key, desc in css_configs:
            label = Gtk.Label(label=f"{desc}:", xalign=0)
            label.set_tooltip_markup("在此处输入自定义 CSS 代码以调整组件外观")
            scrolled_css = Gtk.ScrolledWindow()
            scrolled_css.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scrolled_css.set_size_request(-1, 170)
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
            ('Performance', 'mouse_move_tolerance'),
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
            ("auto_scroll_ticks_per_step", "自动滚动步长（格数）", 1, 8, "自动模式下，每一步滚动几格\n值越大滚动越快"),
            ("max_scroll_per_tick", "自动截图高度（每格）", 120, 240, "自动模式下，对应滚动一格的截图高度 (px)\n总截图高度 = 此值 * 滚动格数"),
            ("min_scroll_per_tick", "最小滚动像素", 1, 60, "用于匹配和校准的最小滚动阈值 (px)"),
            ("max_viewer_dimension", "图片尺寸阈值", -1, 131071, "最终图片长或宽超过此值时，会使用上面的“大尺寸图片打开命令”\n设为 <b>-1</b> 禁用此功能，总是用系统默认方式打开图片\n设为 <b>0</b> 总是用自定义命令打开图片"),
            ("preview_drag_sensitivity", "预览拖动灵敏度", 0.5, 10.0, "预览窗口中按住左键拖动图像的速度倍数")
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
            if isinstance(min_val, float) or isinstance(max_val, float): # 处理浮点数 SpinButton
                spin.set_digits(1)
                spin.set_increments(0.1, 1.0)
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

class PreviewWindow(Gtk.Window):
    """显示截图预览的滚动窗口"""
    ZOOM_FACTOR = 1.26  # 缩放系数
    MIN_ZOOM = 0.25     # 最小缩放比例
    MAX_ZOOM = 4.0      # 最大缩放比例
    def __init__(self, model: StitchModel, config: Config, parent_overlay: 'CaptureOverlay'):
        super().__init__(title="长图预览")
        self.model = model
        self.parent_overlay = parent_overlay
        self.config = config
        self.set_transient_for(parent_overlay)
        self.set_destroy_with_parent(True)
        self.set_default_size(500, 800)
        self.set_position(Gtk.WindowPosition.NONE)
        self.zoom_level = 1.0
        self.manual_zoom_active = False
        self.effective_scale_factor = 1.0
        self.drawing_area_width = 1
        self.drawing_area_height = 1
        self.center_vertically = False
        self.was_at_bottom = True
        self.display_total_height = 0
        self.last_viewport_width = -1
        self.is_dragging = False
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.drag_start_hadj_value = 0
        self.drag_start_vadj_value = 0
        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(main_vbox)
        # 创建 ScrolledWindow 和 DrawingArea
        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        main_vbox.pack_start(self.scrolled_window, True, True, 0)
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
        main_vbox.pack_end(button_hbox, False, False, 0)
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
        self.connect("destroy", self._on_preview_window_destroy)
        v_adj = self.scrolled_window.get_vadjustment()
        if v_adj:
            v_adj.connect("value-changed", self.on_scroll_changed)
            v_adj.connect("changed", self._update_button_sensitivity)
        self.drawing_area.connect("draw", self.on_draw)
        self.drawing_area.connect("button-press-event", self._on_drawing_area_button_press)
        self.drawing_area.connect("motion-notify-event", self._on_drawing_area_motion_notify)
        self.drawing_area.connect("button-release-event", self._on_drawing_area_button_release)
        self.scrolled_window.connect("size-allocate", self.on_viewport_resized)
        self.connect("key-press-event", self._on_key_press)
        self._setup_cursors()
        self.on_model_updated(self.model)
        self._update_button_sensitivity()
        main_vbox.show_all()
        logging.info("预览窗口已初始化")

    def _on_preview_window_destroy(self, widget):
        """预览窗口自身销毁时的处理"""
        if self.model and self.model_update_handler_id:
            try:
                self.model.disconnect(self.model_update_handler_id)
                self.model_update_handler_id = None
            except Exception as e:
                logging.warning(f"{e}")

    def _setup_cursors(self):
        """获取并存储拖动所需的光标"""
        display = Gdk.Display.get_default()
        self.cursor_default = None
        self.cursor_grab = Gdk.Cursor.new_from_name(display, "grab")
        self.cursor_grabbing = Gdk.Cursor.new_from_name(display, "grabbing")

    def _on_key_press(self, widget, event):
        """处理预览窗口的按键事件，用于缩放"""
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

    def _zoom_in(self, button=None):
        current_base_zoom = self.zoom_level if self.manual_zoom_active else self.effective_scale_factor
        new_zoom = current_base_zoom * self.ZOOM_FACTOR
        if new_zoom > self.MAX_ZOOM:
            new_zoom = self.MAX_ZOOM
        if abs(new_zoom - current_base_zoom) > 1e-5:
            self.zoom_level = new_zoom
            self.manual_zoom_active = True
            self._update_drawing_area_size()
            self._update_button_sensitivity()
            self._update_zoom_label()

    def _zoom_out(self, button=None):
        current_base_zoom = self.zoom_level if self.manual_zoom_active else self.effective_scale_factor
        new_zoom = current_base_zoom / self.ZOOM_FACTOR
        if new_zoom < self.MIN_ZOOM:
            new_zoom = self.MIN_ZOOM
        if abs(new_zoom - current_base_zoom) > 1e-5:
            self.zoom_level = new_zoom
            self.manual_zoom_active = True
            self._update_drawing_area_size()
            self._update_button_sensitivity()
            self._update_zoom_label()

    def _reset_zoom(self, button=None):
        if abs(self.effective_scale_factor - 1.0) > 1e-5 or not self.manual_zoom_active:
            self.zoom_level = 1.0
            self.manual_zoom_active = True
            self._update_drawing_area_size()
            self._update_button_sensitivity()

    def _update_zoom_label(self):
        self.zoom_label.set_text(f"{self.effective_scale_factor * 100:.0f}%")
        return GLib.SOURCE_REMOVE

    def on_viewport_resized(self, widget, allocation):
        if allocation.width != self.last_viewport_width:
            self.last_viewport_width = allocation.width
            self._update_drawing_area_size(scroll_if_needed=False)

    def on_model_updated(self, model_instance):
        logging.info("预览窗口收到模型更新信号，准备更新尺寸并重绘")
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

    def _scroll_to_top(self, button):
        v_adj = self.scrolled_window.get_vadjustment()
        v_adj.set_value(v_adj.get_lower())

    def _scroll_to_bottom(self, button):
        v_adj = self.scrolled_window.get_vadjustment()
        target_value = v_adj.get_upper() - v_adj.get_page_size()
        v_adj.set_value(max(v_adj.get_lower(), target_value))

    def _update_button_sensitivity(self, adjustment=None):
        v_adj = self.scrolled_window.get_vadjustment()
        can_scroll = v_adj.get_upper() > v_adj.get_page_size() + 1
        self.btn_scroll_top.set_sensitive(can_scroll)
        self.btn_scroll_bottom.set_sensitive(can_scroll)
        self.btn_zoom_in.set_sensitive(self.zoom_level < self.MAX_ZOOM)
        self.btn_zoom_out.set_sensitive(self.zoom_level > self.MIN_ZOOM)
        self.btn_zoom_reset.set_sensitive(abs(self.effective_scale_factor - 1.0) > 1e-5)

    def _update_drawing_area_size(self, scroll_if_needed=False):
        """根据模型数据、缩放级别和视口大小计算绘制区域尺寸和缩放因子"""
        image_width = self.model.image_width
        virtual_height = self.model.total_virtual_height
        if image_width <= 0 or virtual_height <= 0:
            viewport_width = self.scrolled_window.get_allocated_width()
            viewport_height = self.scrolled_window.get_allocated_height()
            if viewport_width <= 0:
                 viewport_width, viewport_height = self.get_default_size()
            self.drawing_area_width = max(1, viewport_width)
            self.drawing_area_height = max(1, viewport_height)
            self.effective_scale_factor = 1.0
            self.center_vertically = True
            self.display_total_height = self.drawing_area_height
        else:
            viewport_width = self.scrolled_window.get_allocated_width()
            viewport_height = self.scrolled_window.get_allocated_height()
            if viewport_width <= 0:
                viewport_width, _ = self.get_default_size()
            auto_scale_factor = 1.0
            if image_width > viewport_width and viewport_width > 0:
                auto_scale_factor = viewport_width / image_width
            if self.manual_zoom_active:
                self.effective_scale_factor = self.zoom_level
            else:
                self.effective_scale_factor = auto_scale_factor
            self.drawing_area_width = math.ceil(image_width * self.effective_scale_factor)
            self.drawing_area_height = math.ceil(virtual_height * self.effective_scale_factor)
            self.center_vertically = self.drawing_area_height < viewport_height
            self.display_total_height = self.drawing_area_height
        GLib.idle_add(self._update_zoom_label)
        self.drawing_area.set_size_request(self.drawing_area_width, self.drawing_area_height)
        self.drawing_area.queue_draw()
        GLib.idle_add(self._update_button_sensitivity)
        if scroll_if_needed and self.drawing_area_height > 0 and not self.is_dragging:
            GLib.idle_add(self._scroll_to_bottom_if_needed)

    def _scroll_to_bottom_if_needed(self):
        """检查并滚动到 Adjustment 的底部"""
        v_adj = self.scrolled_window.get_vadjustment()
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

    def on_draw(self, widget, cr):
         """绘制 DrawingArea 的内容"""
         widget_width = widget.get_allocated_width()
         widget_height = widget.get_allocated_height()
         cr.set_source_rgb(0.1, 0.1, 0.1)
         cr.paint()
         if not self.model.entries:
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
         scale_factor = self.effective_scale_factor
         draw_area_w = self.drawing_area_width
         draw_area_h = self.drawing_area_height
         draw_x_offset = (widget_width - draw_area_w) / 2 if widget_width > draw_area_w else 0
         initial_y_offset = (widget_height - draw_area_h) / 2 if self.center_vertically else 0
         initial_y_offset = max(0, initial_y_offset)
         clip_x1, visible_y1_widget, clip_x2, visible_y2_widget = cr.clip_extents()
         visible_y1_draw = visible_y1_widget - initial_y_offset
         visible_y2_draw = visible_y2_widget - initial_y_offset
         scaled_y_positions = [y * scale_factor for y in self.model.y_positions]
         first_index = max(0, bisect.bisect_right(scaled_y_positions, visible_y1_draw) - 1)
         drawn_count = 0
         for i in range(first_index, len(self.model.entries)):
             entry = self.model.entries[i]
             filepath = entry.get('filepath')
             original_h = entry.get('height', 0)
             scaled_y_pos = scaled_y_positions[i]
             scaled_h = original_h * scale_factor
             if scaled_y_pos >= visible_y2_draw:
                 break
             if scaled_y_pos + scaled_h <= visible_y1_draw:
                 continue
             pixbuf = self.model._get_cached_pixbuf(filepath)
             if not pixbuf:
                 logging.error(f"无法为 {Path(filepath).name} 获取 Pixbuf")
                 continue
             try:
                 cr.save()
                 cr.translate(draw_x_offset, scaled_y_pos + initial_y_offset)
                 cr.scale(scale_factor, scale_factor)
                 Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
                 cr.paint()
                 cr.restore()
                 drawn_count += 1
             except Exception as e:
                 logging.error(f"绘制 Pixbuf {Path(filepath).name} 时出错: {e}")
                 try: cr.restore()
                 except cairo.Error: pass

    def _on_drawing_area_button_press(self, widget, event):
        if event.button == 1:
            hadj = self.scrolled_window.get_hadjustment()
            vadj = self.scrolled_window.get_vadjustment()
            can_scroll_h = hadj and hadj.get_upper() > hadj.get_page_size()
            can_scroll_v = vadj and vadj.get_upper() > vadj.get_page_size()
            if can_scroll_h or can_scroll_v:
                self.is_dragging = True
                self.drag_start_x = event.x_root
                self.drag_start_y = event.y_root
                self.drag_start_hadj_value = hadj.get_value() if hadj else 0
                self.drag_start_vadj_value = vadj.get_value() if vadj else 0
                self.get_window().set_cursor(self.cursor_grab)
                return True
        return False

    def _on_drawing_area_motion_notify(self, widget, event):
        if self.is_dragging:
            hadj = self.scrolled_window.get_hadjustment()
            vadj = self.scrolled_window.get_vadjustment()
            current_hadj_before = hadj.get_value() if hadj else 0
            current_vadj_before = vadj.get_value() if vadj else 0
            drag_sensitivity = self.config.PREVIEW_DRAG_SENSITIVITY
            delta_x = event.x_root - self.drag_start_x
            delta_y = event.y_root - self.drag_start_y
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
            self.get_window().set_cursor(self.cursor_grabbing)
            return True
        return False

    def _on_drawing_area_button_release(self, widget, event):
        if event.button == 1 and self.is_dragging:
            self.is_dragging = False
            self.get_window().set_cursor(self.cursor_default)
            return True
        return False

class CaptureOverlay(Gtk.Window):
    def __init__(self, geometry_str, config: Config):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.session = CaptureSession(geometry_str)
        self.controller = ActionController(self.session, self, config)
        self.preview_window = None
        self.stitch_model = self.controller.stitch_model
        self.stitch_model.connect('model-updated', self.on_model_updated_ui)
        self.evdev_wheel_scroller = None
        self.invisible_scroller = None
        self.screen_rect = self._get_current_monitor_geometry()
        try:
            if EVDEV_AVAILABLE:
                if config.SCROLL_METHOD == 'invisible_cursor':
                    self.invisible_scroller = InvisibleCursorScroller(
                        self.screen_rect.width, self.screen_rect.height, config
                    )
                    self.invisible_scroller.setup()
                    logging.info("InvisibleCursorScroller.setup() 正在后台线程中执行")
                else:
                    self.evdev_wheel_scroller = EvdevWheelScroller()
            else:
                send_desktop_notification("endev 未导入", f"基于 evdev 的滚动功能将不可用")
                self.evdev_wheel_scroller = None
                self.invisible_scroller = None
        except Exception as err:
            logging.error(f"创建虚拟滚动设备失败: {err}")
            send_desktop_notification(
                "设备错误", f"无法创建虚拟设备: {err}，基于 evdev 的滚动功能将不可用", level="critical"
            )
            self.evdev_wheel_scroller = None
            self.invisible_scroller = None
        self.window_xid = None # 用于存储窗口的 X11 ID
        self.is_dialog_open = False
        self.show_side_panel = True
        self.show_button_panel = True
        self.side_panel_on_left = True
        self._setup_window_properties()
        self.fixed_container = Gtk.Fixed()
        self.add(self.fixed_container)
        self.fixed_container.show()
        self.create_panels()
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
            GLib.idle_add(activate_window_with_xlib, self.window_xid)
            if config.SHOW_PREVIEW_ON_START and self.preview_window is None:
                logging.info("正在创建预览窗口...")
                self.preview_window = PreviewWindow(self.controller.stitch_model, config, self)
                self.preview_window.connect("destroy", self._on_preview_destroyed)
                GLib.idle_add(self._position_and_show_preview)
            elif not config.SHOW_PREVIEW_ON_START:
                logging.info("配置项 'show_preview_on_start' 为 false，启动时不创建预览窗口。")
        except Exception as e:
            logging.error(f"获取 xid 失败: {e}")

    def on_model_updated_ui(self, model_instance):
        """模型更新时刷新界面元素 (连接到 StitchModel 的信号)"""
        can_undo = model_instance.capture_count > 0 and not self.controller.is_auto_scrolling
        if self.show_side_panel:
            self.side_panel.info_panel.update_info(
                count=model_instance.capture_count,
                width=model_instance.image_width,
                height=model_instance.total_virtual_height
            )
        if self.show_button_panel:
            self.button_panel.set_undo_sensitive(can_undo)
        self.queue_draw()

    def _position_and_show_preview(self):
        """计算预览窗口的位置并显示它"""
        if self.preview_window and not self.preview_window.get_visible():
            try:
                parent_x, parent_y = self.get_position()
                parent_w, parent_h = self.get_size()
                preview_def_w, preview_def_h = self.preview_window.get_default_size()
                min_req, _ = self.preview_window.get_preferred_size()
                min_preview_w = min_req.width
                screen_x = self.screen_rect.x
                screen_y = self.screen_rect.y
                screen_w = self.screen_rect.width
                screen_h = self.screen_rect.height
                spacing = 20
                space_right = (screen_x + screen_w) - (parent_x + parent_w + spacing)
                space_left = (parent_x - spacing) - screen_x
                can_place_right = space_right >= min_preview_w
                can_place_left = space_left >= min_preview_w
                place_left = False
                available_space = 0
                if can_place_right and can_place_left:
                    if space_left > space_right:
                        place_left = True
                        available_space = space_left
                    else:
                        place_left = False
                        available_space = space_right
                elif can_place_right:
                    place_left = False
                    available_space = space_right
                elif can_place_left:
                    place_left = True
                    available_space = space_left
                else:
                    place_left = True
                    available_space = space_left
                preview_w = max(min_preview_w, min(preview_def_w, available_space))
                if place_left:
                    preview_x = parent_x - spacing - preview_w
                else:
                    preview_x = parent_x + parent_w + spacing
                preview_y = parent_y
                preview_h = preview_def_h
                screen_bottom = screen_y + screen_h
                bottom_edge = preview_y + preview_h
                if bottom_edge > screen_bottom:
                    overflow = bottom_edge - screen_bottom
                    preview_y -= overflow
                    preview_y = max(screen_y, preview_y)
                    preview_h = screen_bottom - preview_y
                logging.info(f"定位预览窗口到 ({preview_x}, {preview_y}) 尺寸 ({preview_w} x {preview_h})")
                self.preview_window.resize(preview_w, preview_h)
                self.preview_window.move(preview_x, preview_y)
                self.preview_window.show_all()
                logging.info("预览窗口已显示")
            except Exception as e:
                logging.error(f"定位和显示预览窗口时出错: {e}")
                if self.preview_window and not self.preview_window.get_visible():
                    try:
                        parent_x, parent_y = self.get_position()
                        parent_w, _ = self.get_size()
                        self.preview_window.move(parent_x + parent_w + spacing, parent_y)
                        self.preview_window.show_all()
                    except Exception as fallback_e:
                         logging.error(f"尝试后备显示预览窗口失败: {fallback_e}")
        return False

    def _on_preview_destroyed(self, widget):
        """预览窗口关闭时的回调"""
        logging.info("预览窗口已被销毁")
        if self.preview_window == widget:
            self.preview_window = None

    def show_quit_confirmation_dialog(self):
        """显示退出确认对话框并返回用户的响应"""
        GLib.idle_add(activate_window_with_xlib, self.window_xid)
        self.is_dialog_open = True
        global hotkey_listener
        if hotkey_listener:
            logging.info("打开退出对话框，暂停普通全局热键")
            hotkey_listener.set_normal_keys_grabbed(False)
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
        message = config.DIALOG_QUIT_MESSAGE.format(count=self.stitch_model.capture_count)
        dialog.format_secondary_text(message)
        dialog.connect("key-press-event", self.on_dialog_key_press)
        response = dialog.run()
        self.is_dialog_open = False
        dialog.destroy()
        if hotkey_listener and are_hotkeys_enabled:
            logging.info("关闭退出对话框，恢复普通全局热键")
            hotkey_listener.set_normal_keys_grabbed(True)
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
        hotkeys = [
            f"截图：{config.str_capture.upper()}",
            f"完成：{config.str_finalize.upper()}",
            f"取消：{config.str_cancel.upper()}",
            f"撤销：{config.str_undo.upper()}"
        ]
        instruction_lines.append("，".join(hotkeys))
        instruction_lines.append(f"切换整格/自由模式：{config.str_toggle_grid_mode.upper()}，（自由模式下）配置滚动单位：{config.str_configure_scroll_unit.upper()}")
        instruction_lines.append(f"前进：{config.str_grid_forward.upper()}，后退：{config.str_grid_backward.upper()}")
        instruction_lines.append(f"打开/激活配置窗口：{config.str_open_config_editor.upper()}，开启/禁用全局热键：{config.str_toggle_hotkeys_enabled.upper()}")
        instructions = "\n".join(instruction_lines)
        dialog.format_secondary_text(instructions)
        response = dialog.run()
        if response == Gtk.ResponseType.CLOSE:
            logging.info("用户选择 '不再显示'。正在更新配置文件...")
            config.save_setting('Interface.Components', 'show_instruction_notification', 'false')
        dialog.destroy()
        return False

    def create_panels(self):
        self.side_panel = SidePanel()
        self.fixed_container.put(self.side_panel, 0, 0)
        self.button_panel = ButtonPanel()
        self.button_panel.connect("grid-backward-clicked", lambda w: self.controller.handle_movement_action('up'))
        self.button_panel.connect("grid-forward-clicked", lambda w: self.controller.handle_movement_action('down'))
        self.button_panel.connect("auto-scroll-start-clicked", self.controller.start_auto_scroll)
        self.button_panel.connect("auto-scroll-stop-clicked", self.controller.stop_auto_scroll)
        self.button_panel.connect("capture-clicked", self.controller.take_capture)
        self.button_panel.connect("undo-clicked", self.controller.delete_last_capture)
        self.button_panel.connect("finalize-clicked", self.controller.finalize_and_quit)
        self.button_panel.connect("cancel-clicked", self.controller.quit_and_cleanup)
        self.fixed_container.put(self.button_panel, 0, 0)

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
        win, progress_bar = create_feedback_dialog(
            parent_window=self,
            text=config.STR_PROCESSING_TEXT,
            show_progress_bar=True
        )
        win_x, win_y = self.get_position()
        capture_center_x = win_x + self.left_panel_w + config.BORDER_WIDTH + self.session.geometry['w'] // 2
        capture_center_y = win_y + config.BORDER_WIDTH + self.session.geometry['h'] // 2
        processing_win_w, _ = win.get_size()
        processing_win_h = config.PROCESSING_DIALOG_HEIGHT
        win.move(capture_center_x - processing_win_w // 2, capture_center_y - processing_win_h // 2)
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
        if self.show_side_panel and self.side_panel_on_left:
            left_panel_w = config.SIDE_PANEL_WIDTH
            left_region = cairo.Region(cairo.RectangleInt(0, 0, left_panel_w, win_h))
            final_input_region.union(left_region)
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
        # 3. 计算右侧面板区域
        right_panel_w = 0
        btn_x_start = border_area_x_start + border_area_width
        if self.show_button_panel:
            right_panel_w = config.BUTTON_PANEL_WIDTH
        elif self.show_side_panel and not self.side_panel_on_left:
            right_panel_w = config.SIDE_PANEL_WIDTH
        if right_panel_w > 0:
            right_region = cairo.Region(cairo.RectangleInt(btn_x_start, 0, right_panel_w, win_h))
            final_input_region.union(right_region)
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
        if self.show_side_panel and self.side_panel_on_left:
            return config.SIDE_PANEL_WIDTH
        return 0

    def update_layout(self):
        """根据屏幕和选区位置，动态计算并应用窗口布局和几何属性"""
        screen_w = self.screen_rect.width
        side_panel_needed_w = config.SIDE_PANEL_WIDTH
        button_panel_needed_w = config.BUTTON_PANEL_WIDTH
        should_show_info = config.SHOW_CAPTURE_COUNT or config.SHOW_TOTAL_DIMENSIONS
        should_show_side_panel_base = config.ENABLE_SIDE_PANEL and should_show_info
        should_show_side_panel_base = should_show_info
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
        win_x = self.session.geometry['x'] - left_total_w - config.BORDER_WIDTH
        win_y = self.session.geometry['y'] - config.BORDER_WIDTH
        win_w = left_total_w + self.session.geometry['w'] + 2 * config.BORDER_WIDTH + right_total_w
        win_h = self.session.geometry['h'] + 2 * config.BORDER_WIDTH
        self.move(win_x, win_y)
        self.resize(win_w, win_h)
        # 更新子组件的可见性和位置
        capture_height = self.session.geometry['h']
        if self.show_side_panel:
            capture_height = self.session.geometry['h']
            self.side_panel.update_visibility_by_height(capture_height, self.controller.grid_mode_controller.is_active)
            self.side_panel.show()
            if self.side_panel_on_left:
                panel_x = 0
            else:
                panel_x = left_total_w + self.session.geometry['w'] + 2 * config.BORDER_WIDTH
            panel_y = config.BORDER_WIDTH
            self.fixed_container.move(self.side_panel, panel_x, panel_y)
        else:
            self.side_panel.hide()
        if self.show_button_panel:
            self.button_panel.update_visibility_by_height(capture_height, self.controller.grid_mode_controller.is_active)
            panel_x = left_total_w + self.session.geometry['w'] + 2 * config.BORDER_WIDTH
            panel_y = config.BORDER_WIDTH
            self.fixed_container.move(self.button_panel, panel_x, panel_y)
        else:
            self.button_panel.hide()

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

class XlibHotkeyInterceptor(threading.Thread):
    """使用 Xlib (XGrabKey) 在后台线程中拦截全局热键，并支持动态启用/禁用"""
    def __init__(self, overlay, handlers, keymap_tuples):
        super().__init__(daemon=True)
        self.overlay = overlay
        self.handlers = handlers
        self.keymap_tuples = keymap_tuples
        self.running = False
        self.disp = None
        self.root = None
        self.lock = threading.Lock()
        self.debug_key_map = {}
        for kc, mod, _, name in keymap_tuples:
            key_id = (kc, mod)
            if key_id not in self.debug_key_map:
                 self.debug_key_map[key_id] = name

    def set_normal_keys_grabbed(self, grab_state: bool):
        with self.lock:
            if not self.running:
                logging.warning("set_normal_keys_grabbed: 监听器未运行，跳过。")
                return
            if not are_hotkeys_enabled:
                logging.info(f"set_normal_keys_grabbed({grab_state}): 全局热键已禁用，跳过。")
                return
        normal_keys = [k for k in self.keymap_tuples if not k[2]]
        if normal_keys:
            logging.info(f"XlibHotkeyInterceptor: 正在将普通热键的抓取状态设置为 {grab_state}")
            threading.Thread(
                target=self._grab_ungrab_keys, 
                args=(grab_state, normal_keys), 
                daemon=True
            ).start()

    def _grab_ungrab_keys(self, grab: bool, keys_to_process):
        with self.lock:
            if not self.disp or not self.root:
                logging.warning(f"Grab/Ungrab 失败：线程 display 未准备好")
                return
            action_name = "GrabKey" if grab else "UngrabKey"
            logging.info(f"XlibHotkeyInterceptor: 正在执行 {action_name} All...")
            for (keycode, modifier_mask, _, *_) in keys_to_process:
                masks_to_process = [
                    modifier_mask,
                    modifier_mask | X.Mod2Mask,
                    modifier_mask | X.LockMask,
                    modifier_mask | X.Mod2Mask | X.LockMask
                ]
                for mask in masks_to_process:
                    try:
                        if grab:
                            self.root.grab_key(keycode, mask, False, X.GrabModeAsync, X.GrabModeAsync)
                        else:
                            self.root.ungrab_key(keycode, mask, self.root)
                    except Exception as e:
                        if "BadAccess" not in str(e) and "BadValue" not in str(e):
                             logging.warning(f"{action_name} 失败 (kc={keycode}, m={mask}): {e}")
            try:
                self.disp.flush()
            except Exception as e:
                 logging.warning(f"disp.flush() during {action_name} failed: {e}")
        logging.info(f"XlibHotkeyInterceptor: 完成 {action_name} All")

    def run(self):
        """线程主循环，监听 X events"""
        try:
            self.disp = display.Display()
            self.root = self.disp.screen().root
            self.running = True
        except Exception as e:
            logging.error(f"XlibHotkeyInterceptor 线程初始化 Display 失败: {e}")
            self.running = False
            return
        toggle_keys = [k for k in self.keymap_tuples if k[2]]
        normal_keys_to_grab = [k for k in self.keymap_tuples if not k[2] and are_hotkeys_enabled]
        self._grab_ungrab_keys(True, toggle_keys + normal_keys_to_grab)
        logging.info("Xlib 热键拦截线程已启动")
        while self.running:
            try:
                event = self.disp.next_event()
                if event.type == X.KeyPress and self.running:
                    keycode = event.detail
                    clean_state = event.state & (X.ShiftMask | X.ControlMask | X.Mod1Mask | X.Mod4Mask)
                    key_id = (keycode, clean_state)
                    key_name = self.debug_key_map.get(key_id, "UnknownKey")
                    log_key_str = f"key='{key_name}' (kc={keycode})"
                    if key_id in self.handlers:
                        is_toggle_key = False
                        for kc, mod, is_toggle, _ in self.keymap_tuples:
                            if kc == keycode and mod == clean_state:
                                is_toggle_key = is_toggle
                                break
                        if is_toggle_key:
                            logging.info(f"Xlib 拦截到切换键 ({log_key_str}, state={clean_state})")
                            callback = self.handlers[key_id]
                            GLib.idle_add(callback)
                            continue
                        if are_hotkeys_enabled:
                            logging.info(f"Xlib 拦截到热键 ({log_key_str}, state={clean_state}) 并执行回调")
                            callback = self.handlers[key_id]
                            GLib.idle_add(callback)
            except Exception as e:
                if self.running:
                    logging.error(f"Xlib 事件循环错误: {e}")
                    time.sleep(0.1)
        logging.info("Xlib 热键拦截线程正在停止...")
        all_keys = self.keymap_tuples
        self._grab_ungrab_keys(False, all_keys)
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

def toggle_config_window():
    """创建或显示配置窗口，确保只有一个实例存在"""
    global config_window_instance
    def on_window_destroy(widget):
        global config_window_instance
        config_window_instance = None
        logging.info("配置窗口已销毁，实例已清除")

    def task():
        global config_window_instance
        if config_window_instance is None:
            logging.info("配置窗口不存在，正在创建...")
            config_window_instance = ConfigWindow(config)
            config_window_instance.connect("destroy", on_window_destroy)
            logging.info("配置窗口已创建并显示")
        else:
            win_xid = config_window_instance.xid
            if not config_window_instance.is_visible():
                logging.info("配置窗口已隐藏，正在显示并激活...")
                config_window_instance.show()
                GLib.idle_add(activate_window_with_xlib, win_xid)
            else:
                active_xid = get_active_window_xid()
                if win_xid == active_xid:
                    logging.info("配置窗口已激活，正在隐藏...")
                    config_window_instance.hide()
                    global hotkey_listener
                    if hotkey_listener and are_hotkeys_enabled:
                        hotkey_listener.set_normal_keys_grabbed(True)
                        logging.info("配置窗口隐藏，全局热键已恢复")
                else:
                    logging.info("配置窗口可见但未激活，正在激活...")
                    GLib.idle_add(activate_window_with_xlib, win_xid)
    GLib.idle_add(task)

def toggle_hotkeys_globally():
    """切换全局热键的启用状态，应用 grab/ungrab 并发送桌面通知"""
    global are_hotkeys_enabled
    are_hotkeys_enabled = not are_hotkeys_enabled
    state_str = "启用" if are_hotkeys_enabled else "禁用"
    if hotkey_listener:
        normal_keys = [k for k in hotkey_listener.keymap_tuples if not k[2]]
        if not normal_keys:
            logging.info("没有需要切换状态的普通热键。")
        else:
            should_grab = are_hotkeys_enabled
            if should_grab and config_window_instance:
                if config_window_instance.capturing_hotkey_button or config_window_instance.input_has_focus:
                    logging.info("全局热键已设为启用，但配置窗口处于输入状态，暂不抓取普通热键")
                    should_grab = False
            logging.info(f"正在调用 _grab_ungrab_keys({should_grab}) for normal keys")
            threading.Thread(
                target=hotkey_listener._grab_ungrab_keys, 
                args=(should_grab, normal_keys), 
                daemon=True
            ).start()
    else:
        logging.warning("hotkey_listener 未初始化或类型错误，无法应用 grab/ungrab 状态")
    title = "全局热键状态"
    message = f"截图会话的全局热键当前已{state_str}"
    GLib.idle_add(send_desktop_notification, title, message)
    logging.info(f"全局热键状态已切换为: {state_str}")

def setup_hotkey_listener(overlay):
    global hotkey_listener
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
    hotkey_key_callback_list = [
        ('capture', overlay.controller.take_capture),
        ('finalize', overlay.controller.finalize_and_quit),
        ('undo', overlay.controller.delete_last_capture),
        ('cancel', overlay.controller.quit_and_cleanup),
        ('grid_backward', lambda: overlay.controller.handle_movement_action('up')),
        ('grid_forward', lambda: overlay.controller.handle_movement_action('down')),
        ('auto_scroll_start', overlay.controller.start_auto_scroll),
        ('auto_scroll_stop', overlay.controller.stop_auto_scroll),
        ('toggle_grid_mode', overlay.controller.grid_mode_controller.toggle),
        ('configure_scroll_unit', overlay.controller.grid_mode_controller.start_calibration),
        ('open_config_editor', toggle_config_window),
        ('toggle_hotkeys_enabled', toggle_hotkeys_globally),
    ]
    handlers_map = {}
    keymap_tuples = []
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
            debug_key_name = Gdk.keyval_name(keyval)
            if key_id in handlers_map:
                if handlers_map[key_id] != callback:
                    logging.warning(f"热键组合 (kc={keycode}, mask={x_mask}, name={debug_key_name}) 已存在，将被覆盖。请检查配置。键名: {key_name}")
                else:
                    pass
            handlers_map[key_id] = callback
            keymap_tuples.append((keycode, x_mask, is_toggle_key_flag, debug_key_name))
    if not any(k[2] for k in keymap_tuples):
         logging.warning("配置中未找到有效的切换键 (如 toggle_hotkeys_enabled)，热键启用/禁用功能可能无法通过键盘触发。")
    try:
        hotkey_listener = XlibHotkeyInterceptor(overlay, handlers_map, keymap_tuples)
        hotkey_listener.start()
    except Exception as e:
        logging.error(f"启动 XlibHotkeyInterceptor 线程失败: {e}")

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
    parser = argparse.ArgumentParser(description="一个辅助滚动截图并拼接的工具")
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
