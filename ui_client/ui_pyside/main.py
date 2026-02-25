import sys
import os
from pathlib import Path

import json
import requests

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QFileDialog,
    QLineEdit,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QMessageBox,
    QComboBox,
    QInputDialog,
    QDialog,
    QHeaderView,
    QAbstractItemView,
)

from backend.db.repo import Repo
from backend.parsers.ebom_parser import parse_ebom
from backend.services.pipeline import process_file, compute_part_name
from backend.services.id_generation import normalize_prefix, format_external_id
from backend.export.odoo_export import export_odoo_csv
from matching_logic.core.normalize import normalize_name, canonical_key



# --- Backend API configuration (FastAPI) ---
def _config_base_dir() -> Path:
    """Directory where config.json lives. Works for both source-run and PyInstaller exe."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    # ui_pyside/main.py -> project root
    return Path(__file__).resolve().parent.parent

def _config_path() -> Path:
    return _config_base_dir() / 'config.json'

def load_api_config() -> dict:
    p = _config_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            return {}
    return {}

def save_api_config(api_url: str, api_key: str) -> None:
    p = _config_path()
    p.write_text(json.dumps({'api_url': api_url, 'api_key': api_key}, indent=2), encoding='utf-8')
MODULE_NAME = "DEFAULT"


COL_PART_NAME = 0
COL_ITEM_TYPE = 1
COL_QTY = 2
COL_PRICE = 3
COL_STANDARD = 4
COL_EXTERNAL_ID = 5
COL_STATUS = 6  # also hosts match-review dropdown for POSSIBLE_MATCH rows
COL_INCLUDE = 7

BASE_COL_COUNT = 8  # core columns; custom columns are appended after this

ADD_CATEGORY_LABEL = "➕ Add new category..."
REMOVE_CATEGORY_LABEL = "🗑 Remove category..."


class App(QWidget):
    """Small demo UI for meeting:

    1) Load EBOM (txt/csv)
    2) Designer enters a regular prefix (PS/MD/...)
    3) Mark rows as STANDARD (uses STD prefix)
    4) Generate sequential IDs (stored in SQLite)
    5) Export CSV with 4 columns:
       External ID, Part Name, Internal Reference, Type of Item
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Creo → Odoo Middleware (Demo)")

        # --- Require backend connection (Option A) ---
        self.api_url = None
        self.api_key = None
        self.api = requests.Session()
        self._ensure_backend_or_exit()
        # Pass API URL and session to Repo so it uses PostgreSQL for matching
        self.repo = Repo(api_url=self.api_url, api_session=self.api)

        self.processed_rows = []  # List[ProcessedRow]
        self._last_ebom_rows = None  # raw EBOMRow list for refresh
        self._categories = self.repo.list_item_categories()

        self._custom_columns: list[str] = []

        # In-memory session state (nothing is persisted until export).
        # prefix -> last_number (seeded from DB on first use)
        self._session_last_numbers: dict[str, int] = {}

        root = QVBoxLayout()

        # Top controls
        top = QHBoxLayout()
        top.addWidget(QLabel("Regular Prefix:"))
        last_pref = self.repo.get_last_prefix(MODULE_NAME) or "PS"
        self.prefix_edit = QLineEdit(last_pref)
        self.prefix_edit.setPlaceholderText("e.g. PS, MD")
        self.prefix_edit.setMaximumWidth(140)
        top.addWidget(self.prefix_edit)

        self.btn_open = QPushButton("1) Open EBOM (txt/csv)")
        self.btn_open.clicked.connect(self.open_file)
        top.addWidget(self.btn_open)

        self.btn_rename = QPushButton("2) Rename")
        self.btn_rename.setToolTip(
            "Prefix Part Name with the Regular Prefix for all NON-standard rows (Standard rows are left unchanged)."
        )
        self.btn_rename.clicked.connect(self.rename_parts)
        self.btn_rename.setEnabled(False)
        top.addWidget(self.btn_rename)

        self.btn_generate = QPushButton("3) Generate IDs")
        self.btn_generate.clicked.connect(self.generate_ids)
        self.btn_generate.setEnabled(False)
        top.addWidget(self.btn_generate)

        self.btn_export = QPushButton("4) Export CSV")
        self.btn_export.clicked.connect(self.export_csv)
        self.btn_export.setEnabled(False)
        top.addWidget(self.btn_export)

        # DB Viewer (edit/delete records in PostgreSQL)
        self.btn_db_viewer = QPushButton("DB Viewer")
        self.btn_db_viewer.setToolTip("View, edit, and permanently delete records stored in the central database.")
        self.btn_db_viewer.clicked.connect(self.open_db_viewer)
        self.btn_db_viewer.setEnabled(True)
        top.addWidget(self.btn_db_viewer)

        # Column controls (+ / -) for adding/removing custom export columns
        top.addSpacing(8)
        top.addWidget(QLabel("Columns:"))
        self.btn_add_col = QPushButton("+")
        self.btn_add_col.setFixedWidth(28)
        self.btn_add_col.clicked.connect(self.add_custom_column)
        top.addWidget(self.btn_add_col)
        self.btn_remove_col = QPushButton("-")
        self.btn_remove_col.setFixedWidth(28)
        self.btn_remove_col.clicked.connect(self.remove_custom_column)
        top.addWidget(self.btn_remove_col)

        # Bulk category apply
        top.addSpacing(12)
        top.addWidget(QLabel("Category:"))
        self.category_combo = QComboBox()
        self._refresh_category_combo(self.category_combo, include_add_new=True)
        self.category_combo.setToolTip("Select a category and apply it to selected rows")
        self.category_combo.setMaximumWidth(220)
        # Make the special actions (Add/Remove) functional immediately when selected
        self.category_combo.currentIndexChanged.connect(self._on_bulk_category_changed)
        top.addWidget(self.category_combo)
        self.btn_apply_category = QPushButton("Apply to selected")
        self.btn_apply_category.clicked.connect(self.apply_category_to_selected)
        self.btn_apply_category.setEnabled(False)
        top.addWidget(self.btn_apply_category)

        # DEV-only: reset counters helper (never show in production builds)
        self._is_dev = os.environ.get("APP_ENV", "development").strip().lower() in {
            "dev",
            "development",
            "local",
            "test",
        }
        if self._is_dev:
            self.btn_reset_counters = QPushButton("Reset ID counters (dev)")
            self.btn_reset_counters.setToolTip(
                "Development/testing only. Deletes all prefix counters so numbering restarts at 001."
            )
            self.btn_reset_counters.clicked.connect(self.reset_counters)
            self.btn_reset_counters.setEnabled(True)
            top.addWidget(self.btn_reset_counters)

        top.addStretch(1)
        root.addLayout(top)

        # Table
        # NOTE: Status column also hosts the match-review dropdown for POSSIBLE_MATCH rows.
        self.table = QTableWidget(0, BASE_COL_COUNT)
        self.table.setHorizontalHeaderLabels(
            [
                "Part Name (UPPERCASE)",
                "Type of Item",
                "Qty",
                "Price",
                "Standard? (STD)",
                "External ID",
                "Status",
                "Include",
            ]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)

        # Nice column sizing
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(COL_PART_NAME, header.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_ITEM_TYPE, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_QTY, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_PRICE, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_STANDARD, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_EXTERNAL_ID, header.ResizeMode.ResizeToContents)
        # Status can contain a dropdown for POSSIBLE_MATCH rows, so give it room.
        header.setSectionResizeMode(COL_STATUS, header.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_INCLUDE, header.ResizeMode.ResizeToContents)

        root.addWidget(self.table)
        self.setLayout(root)


    def _ensure_backend_or_exit(self) -> None:
        """Load API settings (config.json) and verify backend is reachable.
        If not reachable, show error and exit the app (Option A).
        """
        cfg = load_api_config()
        api_url = (cfg.get("api_url") or "http://localhost:8000").rstrip("/")
        api_key = (cfg.get("api_key") or "").strip()

        # Prompt until we have working credentials or user cancels
        while True:
            if not api_key:
                api_key, ok = QInputDialog.getText(
                    self,
                    "Backend API Key Required",
                    "Enter API Key (X-API-Key):",
                    QLineEdit.Password,
                )
                if not ok:
                    QMessageBox.critical(self, "Backend required", "Cannot start without backend connection.")
                    raise SystemExit(1)
                api_key = api_key.strip()

            # Allow URL edit too (in case they are not using localhost)
            api_url_in, ok = QInputDialog.getText(
                self,
                "Backend Server URL",
                "Backend URL:",
                QLineEdit.Normal,
                api_url,
            )
            if not ok:
                QMessageBox.critical(self, "Backend required", "Cannot start without backend connection.")
                raise SystemExit(1)
            api_url = api_url_in.strip().rstrip("/")

            try:
                r = requests.get(f"{api_url}/health", headers={"X-API-Key": api_key}, timeout=5)
                if r.status_code == 200:
                    self.api_url = api_url
                    self.api_key = api_key
                    self.api.headers.update({"X-API-Key": api_key})
                    save_api_config(api_url, api_key)
                    return
                else:
                    QMessageBox.warning(
                        self,
                        "Backend auth failed",
                        f"Server responded with {r.status_code}. Please check API key / URL.",
                    )
                    # loop again (re-enter)
                    api_key = ""
            except Exception as e:
                QMessageBox.warning(
                    self,
                    "Backend unreachable",
                    f"Could not reach backend at {api_url}.\n\nError: {e}",
                )
                # loop again


    def _refresh_category_combo(self, combo: QComboBox, include_add_new: bool = False, current_value: str | None = None):
        """Populate a QComboBox with categories from DB.

        If current_value is provided and not in categories, it's inserted as the first option
        so existing values from parsed EBOM / DB can still be displayed.
        """
        combo.blockSignals(True)
        combo.clear()

        categories = list(self._categories)
        cur = (current_value or "").strip()
        if cur and cur not in categories:
            combo.addItem(cur)
            combo.insertSeparator(combo.count())

        for c in categories:
            combo.addItem(c)

        if include_add_new:
            combo.insertSeparator(combo.count())
            combo.addItem(ADD_CATEGORY_LABEL)
            combo.addItem(REMOVE_CATEGORY_LABEL)

        # Select current if present
        if cur:
            idx = combo.findText(cur)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        combo.blockSignals(False)

    def _prompt_add_category(self) -> str | None:
        name, ok = QInputDialog.getText(
            self,
            "Add new category",
            "Enter a new category name (e.g., Manufactured, Bought Part):",
        )
        if not ok:
            return None
        name = (name or "").strip()
        if not name:
            QMessageBox.warning(self, "Invalid", "Category name cannot be empty.")
            return None
        # Normalize to Title Case for nice display
        name = " ".join(w.capitalize() for w in name.split())
        self.repo.add_item_category(name)
        self._categories = self.repo.list_item_categories()
        return name

    def _prompt_remove_category(self) -> str | None:
        # Allow removing *any* category, including the seeded defaults.
        choices = list(self._categories)
        if not choices:
            QMessageBox.information(self, "Remove category", "No categories to remove.")
            return None
        name, ok = QInputDialog.getItem(
            self,
            "Remove category",
            "Select a category to remove:",
            choices,
            0,
            False,
        )
        if not ok:
            return None
        name = (name or "").strip()
        if not name:
            return None
        confirm = QMessageBox.question(
            self,
            "Confirm removal",
            f"Remove category '{name}'? It will be removed from dropdowns.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return None
        self.repo.remove_item_category(name)
        self._categories = self.repo.list_item_categories()
        return name

    def _apply_category_to_row(self, row_idx: int, category: str) -> None:
        """Set the category value for a row (table + model)."""
        if row_idx < len(self.processed_rows):
            self.processed_rows[row_idx].item_type = (category or "").strip()

        item = self.table.item(row_idx, COL_ITEM_TYPE)
        if item is None:
            item = QTableWidgetItem("")
            item.setFlags(item.flags() ^ Qt.ItemIsEditable)
            self.table.setItem(row_idx, COL_ITEM_TYPE, item)
        item.setText((category or "").strip())

    # NOTE: We intentionally removed the per-row dropdown for "Type of Item".
    # The only way to set categories is via the top "Category" control.

    def apply_category_to_selected(self) -> None:
        """Apply the chosen category to all selected rows."""
        selected_category = self.category_combo.currentText()
        if selected_category == REMOVE_CATEGORY_LABEL:
            removed = self._prompt_remove_category()
            self._refresh_category_combo(self.category_combo, include_add_new=True)
            # Clear removed value from any rows that used it
            if removed:
                for r in range(self.table.rowCount()):
                    if r < len(self.processed_rows) and (self.processed_rows[r].item_type or "") == removed:
                        self._apply_category_to_row(r, "")
            return

        if selected_category == ADD_CATEGORY_LABEL:
            selected_category = self._prompt_add_category() or ""
            self._refresh_category_combo(self.category_combo, include_add_new=True)
            if not selected_category:
                return

        # Determine selected rows
        selected_rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        if not selected_rows:
            QMessageBox.information(self, "No selection", "Select one or more rows first.")
            return

        for row_idx in selected_rows:
            # Skip rows that are not exportable (EXISTING) for consistency
            status = (self.table.item(row_idx, COL_STATUS).text() if self.table.item(row_idx, COL_STATUS) else "")
            if status == "EXISTING":
                continue
            self._apply_category_to_row(row_idx, selected_category)


    def _on_bulk_category_changed(self, _idx: int | None = None) -> None:
        """Handle Add/Remove actions from the top 'Category' dropdown.

        Users expect the dialogs to open immediately when selecting the special menu items.
        """
        txt = (self.category_combo.currentText() or "").strip()

        if txt == ADD_CATEGORY_LABEL:
            new_name = self._prompt_add_category()
            self._refresh_category_combo(self.category_combo, include_add_new=True, current_value=new_name or "")
            return

        if txt == REMOVE_CATEGORY_LABEL:
            removed = self._prompt_remove_category()
            self._refresh_category_combo(self.category_combo, include_add_new=True, current_value="")
            # Clear removed category from rows that used it
            if removed:
                for r in range(self.table.rowCount()):
                    if r < len(self.processed_rows) and (self.processed_rows[r].item_type or "") == removed:
                        self._apply_category_to_row(r, "")
            return

    def _bool_item(self, checked: bool) -> QTableWidgetItem:
        item = QTableWidgetItem("")
        item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        return item


    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select EBOM file", "", "EBOM (*.csv *.txt)"
        )
        if not path:
            return

        try:
            rows = parse_ebom(path)
            self._last_ebom_rows = rows
            self.processed_rows = process_file(self.repo, module=MODULE_NAME, ebom_rows=rows)
            # New file = new in-memory session state.
            self._session_last_numbers = {}
        except Exception as e:
            QMessageBox.critical(self, "Failed to load", str(e))
            return

        self._populate_table()
        self.btn_generate.setEnabled(True)
        self.btn_export.setEnabled(True)
        self.btn_apply_category.setEnabled(True)
        self.btn_rename.setEnabled(True)

    def _populate_table(self):
        self.table.setRowCount(0)
        for r in self.processed_rows:
            row_idx = self.table.rowCount()
            self.table.insertRow(row_idx)

            # Part Name (editable). We'll normalize to UPPERCASE on sync.
            name_item = QTableWidgetItem(r.name)
            self.table.setItem(row_idx, COL_PART_NAME, name_item)

            # "Type of Item" is displayed as text (no per-row dropdown).
            type_item = QTableWidgetItem((r.item_type or "").strip())
            type_item.setFlags(type_item.flags() ^ Qt.ItemIsEditable)
            self.table.setItem(row_idx, COL_ITEM_TYPE, type_item)

            qty_item = QTableWidgetItem(str(r.qty))
            qty_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            qty_item.setFlags(qty_item.flags() ^ Qt.ItemIsEditable)
            self.table.setItem(row_idx, COL_QTY, qty_item)

            # Price (editable)
            price_txt = "" if r.price is None else str(r.price)
            price_item = QTableWidgetItem(price_txt)
            price_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row_idx, COL_PRICE, price_item)

            std_cell = self._bool_item(r.is_standard)
            # If the row already exists in the DB, the designer should not be able to
            # change its classification here (the part is not exportable anyway).
            if r.status == "EXISTING":
                std_cell.setFlags(std_cell.flags() & ~Qt.ItemIsEnabled)
                std_cell.setCheckState(Qt.Unchecked)
            self.table.setItem(row_idx, COL_STANDARD, std_cell)

            ext_item = QTableWidgetItem(r.external_id or "")
            ext_item.setFlags(ext_item.flags() ^ Qt.ItemIsEditable)
            self.table.setItem(row_idx, COL_EXTERNAL_ID, ext_item)

            # Status column (may become a dropdown if POSSIBLE_MATCH)
            self._set_status_widget(row_idx, r)

            inc_cell = self._bool_item(r.included)
            # Policy: EXISTING rows must be fixed (excluded or renamed) before export.
            # To avoid confusion, we disable inclusion for EXISTING rows in the UI.
            if r.status == "EXISTING":
                inc_cell.setCheckState(Qt.Unchecked)
                inc_cell.setFlags(inc_cell.flags() & ~Qt.ItemIsEnabled)
            self.table.setItem(row_idx, COL_INCLUDE, inc_cell)

            

            # Initialize custom columns (editable)
            for c in range(BASE_COL_COUNT, self.table.columnCount()):
                if self.table.item(row_idx, c) is None:
                    self.table.setItem(row_idx, c, QTableWidgetItem(""))
            # (Match review is merged into the Status column)

    def _set_status_widget(self, row_idx: int, r):
        """Populate the Status column.

        For POSSIBLE_MATCH rows we show a dropdown that lets the user:
        - confirm a suggested existing part (uses its existing external_id, row becomes EXISTING)
        - reject the suggestion (treat as NEW so a new ID can be generated)

        For NEW/EXISTING rows we show plain, non-editable text.
        """
        # Clear any existing widget
        self.table.setCellWidget(row_idx, COL_STATUS, None)

        if r.status != "POSSIBLE_MATCH" or not getattr(r, "suggestions", None):
            item = QTableWidgetItem(r.status)
            item.setFlags(item.flags() ^ Qt.ItemIsEditable)
            self.table.setItem(row_idx, COL_STATUS, item)
            return

        combo = QComboBox()
        combo.addItem("POSSIBLE_MATCH — Review… (choose)", None)
        for s in r.suggestions:
            # Show both ID and name for better confidence when reviewing
            name = (s.name or "").strip()
            label_name = f" — {name}" if name else ""
            combo.addItem(f"Confirm: {s.external_id}{label_name} (score {s.score:.2f})", s.external_id)
        combo.addItem("Not a match (new ID)", "REJECT")

        # Restore previous decision if present
        if r.match_decision:
            idx = combo.findData(r.match_decision)
            if idx >= 0:
                combo.setCurrentIndex(idx)

        combo.currentIndexChanged.connect(lambda _i, rr=row_idx, cb=combo: self._on_match_decision_changed(rr, cb))
        self.table.setCellWidget(row_idx, COL_STATUS, combo)

    def _on_match_decision_changed(self, row_idx: int, combo: QComboBox) -> None:
        if row_idx >= len(self.processed_rows):
            return
        r = self.processed_rows[row_idx]
        decision = combo.currentData()
        r.match_decision = decision

        if decision is None:
            # Not reviewed yet
            return

        if decision == "REJECT":
            # Treat as NEW
            r.external_id = None
            r.status = "NEW"
            r.included = True

            # Update table
            self.table.item(row_idx, COL_EXTERNAL_ID).setText("")
            self._set_status_widget(row_idx, r)
            inc = self.table.item(row_idx, COL_INCLUDE)
            if inc:
                inc.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                inc.setCheckState(Qt.Checked)
            return

        # Confirmed match to existing external_id
        ext = str(decision)
        r.external_id = ext
        r.status = "EXISTING"
        r.included = False

        self.table.item(row_idx, COL_EXTERNAL_ID).setText(ext)
        self._set_status_widget(row_idx, r)
        inc = self.table.item(row_idx, COL_INCLUDE)
        if inc:
            inc.setCheckState(Qt.Unchecked)
            inc.setFlags(inc.flags() & ~Qt.ItemIsEnabled)

    def _col_index(self, *candidates: str):
        """Find column index by header text (case-insensitive, contains match).
        Useful when user-added columns shift indices.
        """
        cand_l = [c.lower().strip() for c in candidates if c]
        for i in range(self.table.columnCount()):
            h = self.table.horizontalHeaderItem(i)
            if not h:
                continue
            ht = (h.text() or "").strip().lower()
            for c in cand_l:
                if c and c in ht:
                    return i
        return None

    def _sync_from_table(self):
        """Copy UI state into ProcessedRow objects using header-based column lookup."""
        # Resolve column indices dynamically (fallback to constants)
        idx_name = self._col_index("part name") or COL_PART_NAME
        idx_type = self._col_index("type of item", "type") or COL_ITEM_TYPE
        idx_price = self._col_index("price") or COL_PRICE
        idx_std = self._col_index("standard", "std") or COL_STANDARD
        idx_inc = self._col_index("include") or COL_INCLUDE

        for idx, r in enumerate(self.processed_rows):
            name_item = self.table.item(idx, idx_name)
            type_item = self.table.item(idx, idx_type)
            price_item = self.table.item(idx, idx_price)
            std_item = self.table.item(idx, idx_std)
            inc_item = self.table.item(idx, idx_inc)

            # Keep the designer-edited name, but normalize to our canonical format.
            if name_item:
                r.name = normalize_name(name_item.text())
            r.item_type = (type_item.text() if type_item else "").strip()

            # Price is user-editable (optional)
            ptxt = (price_item.text() if price_item else "").strip()
            if ptxt == "":
                r.price = None
            else:
                try:
                    r.price = float(ptxt.replace(",", "."))
                except Exception:
                    r.price = None

            # Refresh stable key for matching/export payload.
            r.canonical_key = canonical_key(r.name, r.revision)

            # Standard / Include checkboxes
            r.is_standard = bool(std_item and std_item.checkState() == Qt.Checked)
            r.included = bool(inc_item and inc_item.checkState() == Qt.Checked)


    def rename_parts(self):
        """Rename (prefix) all non-standard parts using the designer prefix.

        Requirement:
        - User types prefix like PS
        - Click Rename
        - All NON-standard rows get prefixed with '<PS>_' (unless already prefixed)
        - Standard rows are left unchanged
        """
        self._sync_from_table()
        regular_prefix = self.prefix_edit.text().strip()

        changed = 0
        try:
            for i, r in enumerate(self.processed_rows):
                if r.is_standard:
                    continue
                new_name = compute_part_name(regular_prefix=regular_prefix, row=r, add_prefix=True)
                if new_name != r.name:
                    r.name = new_name
                    item = self.table.item(i, COL_PART_NAME)
                    if item:
                        item.setText(new_name)
                    changed += 1
        except Exception as e:
            QMessageBox.critical(self, "Rename failed", str(e))
            return

        QMessageBox.information(self, "Renamed", f"Renamed {changed} part(s).")

    def reset_counters(self):
        """DEV-only reset of the ID counters."""
        if not getattr(self, "_is_dev", False):
            QMessageBox.warning(self, "Not available", "Reset counters is disabled in production.")
            return
        confirm = QMessageBox.question(
            self,
            "Reset ID counters (dev)",
            "This is for development/testing only.\n\n"
            "It will delete all prefix counters so the next generated IDs restart at 001.\n"
            "Existing parts in the DB will still exist.\n\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            self.repo.reset_module_counters()
            # Also reset the in-memory session counters.
            self._session_last_numbers = {}
        except Exception as e:
            QMessageBox.critical(self, "Reset failed", str(e))
            return
        QMessageBox.information(self, "Reset", "ID counters reset.")

    
    def generate_ids(self):
        """
        Reserve unique External IDs from backend and apply to rows.

        IMPORTANT:
        - Treat External ID values "" (empty) and "NEW" as "needs ID".
        - Use header-based column lookup so custom columns (+/-) do not break logic.
        - Read Include / Standard / External ID directly from the table to avoid
          desync issues between UI and ProcessedRow objects.
        """
        # Resolve indices safely
        idx_name = self._col_index("part name") or COL_PART_NAME
        idx_std = self._col_index("standard", "std") or COL_STANDARD
        idx_ext = self._col_index("external id", "external") or COL_EXTERNAL_ID
        idx_inc = self._col_index("include") or COL_INCLUDE

        regular_prefix_raw = self.prefix_edit.text().strip()
        regular_prefix = normalize_prefix(regular_prefix_raw)

        # Collect unique keys that need IDs (dedupe within EBOM)
        key_to_ext: dict[tuple[str, str], str] = {}
        keys_by_prefix: dict[str, list[tuple[str, str]]] = {}

        row_count = self.table.rowCount()
        for row in range(row_count):
            inc_item = self.table.item(row, idx_inc)
            included = bool(inc_item and inc_item.checkState() == Qt.Checked)
            if not included:
                continue

            ext_item = self.table.item(row, idx_ext)
            ext_txt = (ext_item.text() if ext_item else "").strip()
            if ext_txt != "" and ext_txt.upper() != "NEW":
                # Already has an ID
                continue

            std_item = self.table.item(row, idx_std)
            is_std = bool(std_item and std_item.checkState() == Qt.Checked)
            prefix = "STD" if is_std else regular_prefix

            # Use processed_rows canonical key when possible; otherwise derive from table name
            key_str = ""
            if row < len(self.processed_rows):
                pr = self.processed_rows[row]
                key_str = pr.canonical_key or canonical_key(pr.name, pr.revision)
            if not key_str:
                name_item = self.table.item(row, idx_name)
                nm = normalize_name(name_item.text()) if name_item else ""
                key_str = canonical_key(nm, "")

            key = (key_str, prefix)
            if key in key_to_ext:
                continue

            key_to_ext[key] = ""
            keys_by_prefix.setdefault(prefix, []).append(key)

        if not keys_by_prefix:
            QMessageBox.information(self, "Generate IDs", "No rows need External IDs.")
            return

        # Reserve IDs per prefix from backend
        try:
            for prefix, keys in keys_by_prefix.items():
                resp = self.api.post(
                    f"{self.api_url}/ids/reserve",
                    json={"prefix": prefix, "count": len(keys)},
                    timeout=10,
                )
                if resp.status_code != 200:
                    QMessageBox.critical(
                        self,
                        "Backend error",
                        f"Failed to reserve IDs for prefix '{prefix}'.\nStatus: {resp.status_code}\n{resp.text}",
                    )
                    return
                ids = resp.json().get("ids", [])
                if len(ids) != len(keys):
                    QMessageBox.critical(
                        self,
                        "Backend error",
                        f"Backend returned {len(ids)} IDs, expected {len(keys)} for prefix '{prefix}'.",
                    )
                    return
                for k, ext in zip(keys, ids):
                    key_to_ext[k] = ext
        except Exception as e:
            QMessageBox.critical(self, "Backend unreachable", f"Could not reserve IDs from backend.\n\nError: {e}")
            return

        # Apply IDs back to table + processed_rows
        created = 0
        skipped = 0
        for row in range(row_count):
            inc_item = self.table.item(row, idx_inc)
            included = bool(inc_item and inc_item.checkState() == Qt.Checked)
            if not included:
                skipped += 1
                continue

            ext_item = self.table.item(row, idx_ext)
            ext_txt = (ext_item.text() if ext_item else "").strip()
            if ext_txt != "" and ext_txt.upper() != "NEW":
                skipped += 1
                continue

            std_item = self.table.item(row, idx_std)
            is_std = bool(std_item and std_item.checkState() == Qt.Checked)
            prefix = "STD" if is_std else regular_prefix

            key_str = ""
            if row < len(self.processed_rows):
                pr = self.processed_rows[row]
                key_str = pr.canonical_key or canonical_key(pr.name, pr.revision)
            if not key_str:
                name_item = self.table.item(row, idx_name)
                nm = normalize_name(name_item.text()) if name_item else ""
                key_str = canonical_key(nm, "")

            key = (key_str, prefix)
            new_id = key_to_ext.get(key, "")
            if not ext_item:
                ext_item = QTableWidgetItem("")
                self.table.setItem(row, idx_ext, ext_item)
            ext_item.setText(new_id)
            if row < len(self.processed_rows):
                self.processed_rows[row].external_id = new_id
                # Set status to CREATED after generating ID
                if new_id:
                    self.processed_rows[row].status = "CREATED"
            if new_id:
                created += 1

        QMessageBox.information(self, "Generate IDs", f"Assigned {created} IDs. Skipped {skipped} rows.")

    def add_custom_column(self) -> None:
        """Append a user-defined column to the table and include it in export."""
        default_name = f"Custom Column {len(self._custom_columns) + 1}"
        name, ok = QInputDialog.getText(self, "Add Column", "Column name:", text=default_name)
        if not ok:
            return
        col_name = (name or "").strip() or default_name

        # Ensure uniqueness against existing headers
        existing = [self.table.horizontalHeaderItem(i).text() for i in range(self.table.columnCount()) if self.table.horizontalHeaderItem(i)]
        base = col_name
        n = 2
        while col_name in existing:
            col_name = f"{base} ({n})"
            n += 1

        col_idx = self.table.columnCount()
        self.table.insertColumn(col_idx)
        self.table.setHorizontalHeaderItem(col_idx, QTableWidgetItem(col_name))
        self._custom_columns.append(col_name)

        # Initialize existing rows
        for r in range(self.table.rowCount()):
            self.table.setItem(r, col_idx, QTableWidgetItem(""))

    def remove_custom_column(self) -> None:
        """Remove one of the custom columns (core columns cannot be removed)."""
        if not self._custom_columns:
            QMessageBox.information(self, "No custom columns", "There are no custom columns to remove.")
            return

        col_name, ok = QInputDialog.getItem(
            self,
            "Remove Column",
            "Select a custom column to remove:",
            self._custom_columns,
            editable=False,
        )
        if not ok or not col_name:
            return

        # Find the column index by header text
        col_idx = None
        for i in range(BASE_COL_COUNT, self.table.columnCount()):
            hdr = self.table.horizontalHeaderItem(i)
            if hdr and hdr.text() == col_name:
                col_idx = i
                break
        if col_idx is None:
            # Fallback: remove last custom column
            col_idx = self.table.columnCount() - 1

        self.table.removeColumn(col_idx)
        try:
            self._custom_columns.remove(col_name)
        except ValueError:
            # If indices shifted and the exact name isn't found, rebuild list from headers
            self._custom_columns = [
                self.table.horizontalHeaderItem(i).text()
                for i in range(BASE_COL_COUNT, self.table.columnCount())
                if self.table.horizontalHeaderItem(i)
            ]

    def _cell_export_value(self, row: int, col: int) -> str:
        """Return a string export value from a cell that may contain widgets/checkboxes."""
        w = self.table.cellWidget(row, col)
        if w is not None:
            if isinstance(w, QComboBox):
                # Prefer the selected data if present; otherwise use the visible text
                data = w.currentData()
                if data is None:
                    txt = (w.currentText() or "").strip()
                    # Ignore placeholder prompts
                    return "" if "Review" in txt and "POSSIBLE_MATCH" in txt else txt
                return str(data)
            # Unknown widget -> best-effort
            return ""

        item = self.table.item(row, col)
        if item is None:
            return ""
        # Checkable items (Standard?/Include)
        if item.flags() & Qt.ItemIsUserCheckable:
            return "1" if item.checkState() == Qt.Checked else "0"
        return (item.text() or "").strip()

    def _push_parts_to_backend(self) -> bool:
        """Persist current table rows to backend (PostgreSQL via API)."""
        try:
            username = os.getenv("USERNAME") or os.getenv("USER") or ""
        except Exception:
            username = ""

        # Build payload: fixed fields + JSON data for dynamic/custom columns
        headers = [self.table.horizontalHeaderItem(c).text() for c in range(self.table.columnCount())]
        def col_index(name: str):
            try:
                return headers.index(name)
            except ValueError:
                return None

        idx_ext = col_index("External ID")
        idx_name = col_index("Part Name (UPPERCASE)")
        idx_type = col_index("Type of Item")
        idx_qty = col_index("Qty")
        idx_status = col_index("Status")
        idx_include = col_index("Include")
        idx_std = col_index("Standard? (STD)")

        core_names = {"External ID","Part Name (UPPERCASE)","Type of Item","Qty","Price","Standard? (STD)","Status","Include"}

        parts_payload = []
        for ri in range(self.table.rowCount()):
            # respect Include column if present
            if idx_include is not None:
                it = self.table.item(ri, idx_include)
                if it is not None and it.checkState() != Qt.Checked:
                    continue

            def cell_text(ci):
                item = self.table.item(ri, ci) if ci is not None else None
                return item.text().strip() if item else ""

            external_id = cell_text(idx_ext)
            part_name = cell_text(idx_name)
            item_type = cell_text(idx_type)
            qty_raw = cell_text(idx_qty)
            status = cell_text(idx_status)

            if not external_id or not part_name:
                # skip incomplete rows
                continue

            # qty coercion (backend expects int; we coerce 2.0 -> 2)
            qty = None
            if qty_raw:
                try:
                    qf = float(qty_raw)
                    qty = int(qf) if qf.is_integer() else int(round(qf))
                except Exception:
                    qty = None

            data = {}
            for ci, h in enumerate(headers):
                if h in core_names:
                    continue
                data[h] = cell_text(ci)

            parts_payload.append({
                "external_id": external_id,
                "part_name": part_name,
                "internal_reference": external_id,
                "item_type": item_type or None,
                "qty": qty,
                "status": status or None,
                "data": data,
                "created_by": username or None,
            })

        resp = self.api.post(f"{self.api_url}/parts/bulk_upsert", json=parts_payload, timeout=20)
        if resp.status_code != 200:
            QMessageBox.critical(self, "Backend error", f"Failed to save parts to backend.\nStatus: {resp.status_code}\n{resp.text}")
            return False
        return True


    

    def open_db_viewer(self):
        """
        Open DB Viewer dialog to view/edit/delete records stored in PostgreSQL.
        Save happens only when the user clicks 'Save to DB'.
        Delete is permanent (hard delete).
        """
        if not self.api_url or not self.api_key:
            QMessageBox.critical(self, "Backend not configured", "Backend URL/API key is missing. Configure backend first.")
            return
        dlg = DbViewerDialog(parent=self)
        dlg.exec()

    def export_csv(self):
        self._sync_from_table()
        regular_prefix = self.prefix_edit.text().strip()

        # Require users to resolve possible matches before export.
        unresolved = [r for r in self.processed_rows if r.included and r.status == "POSSIBLE_MATCH" and r.match_decision is None]
        if unresolved:
            QMessageBox.warning(
                self,
                "Review required",
                "There are 'POSSIBLE_MATCH' rows that haven't been reviewed.\n\n"
                "Use the Status dropdown to Confirm or Reject them, then export.",
            )
            return
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Export CSV", "odoo_export.csv", "CSV (*.csv)"
        )
        if not out_path:
            return

        try:
            
            # Build dynamic export columns based on the table headers (incl. custom columns)
            table_headers = [
                (self.table.horizontalHeaderItem(i).text() if self.table.horizontalHeaderItem(i) else f"Column {i+1}")
                for i in range(self.table.columnCount())
            ]

            # Normalize a couple of headers to match the usual export naming
            header_rename = {
                "Part Name (UPPERCASE)": "Part Name",
                "Standard? (STD)": "Standard? (STD)",
            }
            export_headers = [header_rename.get(h, h) for h in table_headers]

            # Ensure Internal Reference is present (Odoo convention: same as External ID)
            if "Internal Reference" not in export_headers:
                if "External ID" in export_headers:
                    export_headers.insert(export_headers.index("External ID") + 1, "Internal Reference")
                else:
                    export_headers.append("Internal Reference")

            # Map export header -> table column index
            export_to_table_col = {}
            for i, th in enumerate(table_headers):
                eh = header_rename.get(th, th)
                export_to_table_col[eh] = i

            row_idx_map = {id(r): i for i, r in enumerate(self.processed_rows)}

            def _build_export_row(r):
                ri = row_idx_map.get(id(r), 0)
                ext = (r.external_id or "").strip()
                d = {}
                for h in export_headers:
                    if h == "Internal Reference":
                        d[h] = ext
                    else:
                        c = export_to_table_col.get(h)
                        d[h] = self._cell_export_value(ri, c) if c is not None else ""
                return d

            if not self._push_parts_to_backend():
                return

            export_odoo_csv(
                self.processed_rows,
                out_path,
                regular_prefix=regular_prefix,
                fieldnames=export_headers,
                row_builder=_build_export_row,
            )

            # -------------------------
            # Data persisted to PostgreSQL via backend API
            # -------------------------
            # The _push_parts_to_backend() call above already saved all parts to the backend.
            # No SQLite commits needed anymore - PostgreSQL is now the single source of truth.
            
            # Remember last used regular prefix for UX (read-only on next import).
            try:
                self.repo.set_last_prefix(MODULE_NAME, normalize_prefix(regular_prefix))
            except Exception:
                # Don't fail export if prefix isn't valid/filled; user can type it next time.
                pass

        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))
            return

        QMessageBox.information(self, "Exported", f"Export written to:\n{out_path}")




class DbViewerDialog(QDialog):
    """
    DB Viewer: fetches parts from backend, allows inline edits, and permanent delete.
    External ID is treated as primary identifier and is read-only.
    """

    FIXED_FIELDS = ["external_id", "part_name", "internal_reference", "item_type", "qty", "status"]

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Database Viewer (Edit / Delete)")
        self.setMinimumSize(1100, 650)

        self.app: App = parent  # type: ignore

        root = QVBoxLayout(self)

        # Controls
        controls = QHBoxLayout()
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.load_parts)
        controls.addWidget(self.btn_refresh)

        self.btn_save = QPushButton("Save to DB")
        self.btn_save.setToolTip("Saves all rows currently shown in the viewer back to the database.")
        self.btn_save.clicked.connect(self.save_to_db)
        controls.addWidget(self.btn_save)

        self.btn_delete = QPushButton("Delete Selected (Permanent)")
        self.btn_delete.setToolTip("Permanently deletes selected rows from the database.")
        self.btn_delete.clicked.connect(self.delete_selected)
        controls.addWidget(self.btn_delete)

        controls.addStretch(1)
        root.addLayout(controls)

        # Table
        self.table = QTableWidget()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        root.addWidget(self.table)

        self._records: list[dict] = []
        self._dynamic_cols: list[str] = []

        self.load_parts()

    def _request_headers_ok(self) -> bool:
        return bool(self.app and self.app.api_url and self.app.api_key)

    def load_parts(self):
        if not self._request_headers_ok():
            QMessageBox.critical(self, "Backend not configured", "Backend URL/API key is missing.")
            return
        try:
            resp = self.app.api.get(f"{self.app.api_url}/parts", timeout=20)
            resp.raise_for_status()
            self._records = resp.json() or []
        except Exception as e:
            QMessageBox.critical(self, "DB Viewer", f"Failed to load parts from backend.\n\n{e}")
            return

        # Build dynamic columns (union of JSON keys)
        dyn = set()
        for r in self._records:
            data = r.get("data") or {}
            if isinstance(data, dict):
                dyn.update(data.keys())
        self._dynamic_cols = sorted(dyn)

        headers = [h.replace('_', ' ').title() for h in (self.FIXED_FIELDS + self._dynamic_cols)]
        self.table.clear()
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(self._records))

        for row_idx, rec in enumerate(self._records):
            # fixed
            for col_idx, field in enumerate(self.FIXED_FIELDS):
                val = rec.get(field, "")
                item = QTableWidgetItem("" if val is None else str(val))
                if field == "external_id":
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row_idx, col_idx, item)

            data = rec.get("data") or {}
            if not isinstance(data, dict):
                data = {}
            for j, key in enumerate(self._dynamic_cols):
                val = data.get(key, "")
                item = QTableWidgetItem("" if val is None else str(val))
                self.table.setItem(row_idx, len(self.FIXED_FIELDS) + j, item)

        self.table.resizeColumnsToContents()

    def _row_to_payload(self, row_idx: int) -> dict:
        # Extract fixed fields
        payload = {}
        for col_idx, field in enumerate(self.FIXED_FIELDS):
            item = self.table.item(row_idx, col_idx)
            txt = item.text().strip() if item else ""
            if field == "qty":
                # qty is int if possible
                if txt == "":
                    payload[field] = None
                else:
                    try:
                        payload[field] = int(float(txt))
                    except Exception:
                        payload[field] = None
            else:
                payload[field] = txt if txt != "" else None

        # Internal reference default
        if not payload.get("internal_reference") and payload.get("external_id"):
            payload["internal_reference"] = payload["external_id"]

        # Extract dynamic fields
        data = {}
        for j, key in enumerate(self._dynamic_cols):
            col = len(self.FIXED_FIELDS) + j
            item = self.table.item(row_idx, col)
            txt = item.text() if item else ""
            if txt is not None and str(txt).strip() != "":
                data[key] = txt
        payload["data"] = data
        return payload

    def save_to_db(self):
        if not self._request_headers_ok():
            QMessageBox.critical(self, "Backend not configured", "Backend URL/API key is missing.")
            return

        parts_payload = []
        for r in range(self.table.rowCount()):
            p = self._row_to_payload(r)
            if not p.get("external_id"):
                continue
            # minimal required for schema
            if not p.get("part_name"):
                p["part_name"] = p["external_id"]
            parts_payload.append(p)

        if not parts_payload:
            QMessageBox.information(self, "Save to DB", "No valid rows to save.")
            return

        try:
            resp = self.app.api.post(f"{self.app.api_url}/parts/bulk_upsert", json=parts_payload, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            QMessageBox.critical(self, "Save to DB", f"Failed to save changes to DB.\n\n{e}")
            return

        QMessageBox.information(self, "Save to DB", "Changes saved successfully.")
        # refresh to show server truth
        self.load_parts()

    def delete_selected(self):
        rows = sorted({idx.row() for idx in self.table.selectionModel().selectedRows()})
        if not rows:
            QMessageBox.information(self, "Delete", "Select at least one row to delete.")
            return

        ext_ids = []
        for r in rows:
            item = self.table.item(r, 0)  # external_id col
            if item and item.text().strip():
                ext_ids.append(item.text().strip())

        if not ext_ids:
            QMessageBox.information(self, "Delete", "No valid External IDs selected.")
            return

        msg = "This will permanently delete the selected records from the database.\n\nProceed?"
        if QMessageBox.question(self, "Confirm Permanent Delete", msg, QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return

        failed = []
        for ext_id in ext_ids:
            try:
                resp = self.app.api.delete(f"{self.app.api_url}/parts/{ext_id}", timeout=20)
                if resp.status_code not in (200, 204):
                    failed.append(ext_id)
            except Exception:
                failed.append(ext_id)

        if failed:
            QMessageBox.warning(self, "Delete", f"Some deletes failed:\n" + "\n".join(failed))
        else:
            QMessageBox.information(self, "Delete", "Selected records deleted.")

        self.load_parts()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = App()
    w.resize(1100, 600)
    w.show()
    sys.exit(app.exec())