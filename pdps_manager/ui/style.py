# -*- coding: utf-8 -*-
"""样式助手：调色、带特效按钮、状态栏配色。"""

from PyQt5.QtWidgets import QPushButton


def _lighten(hex_color: str, factor: float = 1.18) -> str:
    """将 HEX 颜色调亮，用于 hover 状态。"""
    h = hex_color.lstrip('#')
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return "#{:02x}{:02x}{:02x}".format(min(255, int(r * factor)),
                                        min(255, int(g * factor)),
                                        min(255, int(b * factor)))


def _darken(hex_color: str, factor: float = 0.78) -> str:
    """将 HEX 颜色调暗，用于 pressed 状态。"""
    h = hex_color.lstrip('#')
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return "#{:02x}{:02x}{:02x}".format(int(r * factor), int(g * factor), int(b * factor))


def make_btn(text, color, callback=None, width=None, height=None) -> QPushButton:
    """创建带悬停/按下变色特效的按钮。"""
    btn = QPushButton(text)
    hov = _lighten(color)      # 悬停时变亮
    prs = _darken(color)       # 按下时变暗
    btn.setStyleSheet(
        f"QPushButton {{"
        f"  background-color: {color}; color: white;"
        f"  border: none; border-radius: 4px;"
        f"  padding: 5px 12px; font-size: 13px;"
        f"}}"
        f"QPushButton:hover {{"
        f"  background-color: {hov};"
        f"  border-bottom: 2px solid {prs};"
        f"}}"
        f"QPushButton:pressed {{"
        f"  background-color: {prs};"
        f"  border-top: 2px solid {hov};"
        f"  padding-top: 7px;"
        f"}}"
        f"QPushButton:disabled {{"
        f"  background-color: #aab7b8; color: #ecf0f1;"
        f"}}"
    )
    if callback:
        btn.clicked.connect(callback)
    if width:
        btn.setFixedWidth(width)
    if height:
        btn.setFixedHeight(height)
    return btn


def status_style(bg: str, pad: str = "4px 8px", font_size: int = 10) -> str:
    """生成状态栏（白字 + 背景色）样式字符串。"""
    return f"background:{bg};color:white;padding:{pad};font-size:{font_size}px;"
