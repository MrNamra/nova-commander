"""
Transfer Options Dialog for WinSCP-Linux.
Matches WinSCP Transfer Settings / Copy Parameters (CopyParams.dfm).
Configures transfer mode (Binary / Text), file masks, speed limits, and timestamp preservation.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class TransferOptionsDialog(QDialog):
    """
    WinSCP-style Transfer Settings / Options Dialog.
    """

    options_saved = pyqtSignal(dict)

    def __init__(self, current_options: Optional[dict] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.options = current_options or {}

        self.setWindowTitle("Transfer Options")
        self.resize(500, 300)

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        group = QGroupBox("Transfer Parameters")
        form = QFormLayout(group)

        self.cmb_mode = QComboBox()
        self.cmb_mode.addItems(["Binary", "Text / ASCII", "Automatic (by file extension)"])
        form.addRow("Transfer Mode:", self.cmb_mode)

        self.chk_preserve_time = QCheckBox("Preserve original file modification timestamps")
        self.chk_preserve_time.setChecked(self.options.get("preserve_timestamps", True))
        form.addRow("", self.chk_preserve_time)

        self.txt_exclude_masks = QLineEdit(self.options.get("exclude_masks", "*.tmp; *.bak; .git/"))
        self.txt_exclude_masks.setPlaceholderText("e.g. *.tmp; *.bak; .git/")
        form.addRow("Exclude File Masks:", self.txt_exclude_masks)

        self.spin_speed_limit = QSpinBox()
        self.spin_speed_limit.setRange(0, 1000000)
        self.spin_speed_limit.setValue(self.options.get("speed_limit_kb", 0))
        self.spin_speed_limit.setSuffix(" KB/s (0 = unlimited)")
        form.addRow("Speed Limit:", self.spin_speed_limit)

        layout.addWidget(group)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _on_accept(self):
        opts = {
            "mode": self.cmb_mode.currentText(),
            "preserve_timestamps": self.chk_preserve_time.isChecked(),
            "exclude_masks": self.txt_exclude_masks.text().strip(),
            "speed_limit_kb": self.spin_speed_limit.value(),
        }
        self.options_saved.emit(opts)
        self.accept()
