<div align="center">

# 🗄️ SQLTUI (`sqlite-tui`)

**Fast, keyboard-driven terminal SQLite explorer and editor.**  
*Heavily inspired by [lazygit](https://github.com/jesseduffield/lazygit).*

[![Python Version](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-0%20(standard%20library)-brightgreen.svg)]()
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20BSD-lightgrey.svg)]()

</div>

---

## ⚡ Highlights

* **Zero External Dependencies:** Built 100% on the Python 3.8+ Standard Library (`curses`, `sqlite3`, `csv`, `json`). No `pip install` required.
* **Lazygit-Style 4-Panel Ergonomics:** Seamlessly jump between Schema (`1`), Columns (`2`), SQL Editor (`3`), and Data Grid (`4`). Persistent clickable tab bar, full-screen zoom toggle (`z`), and a context-aware shortcut bar.
* **Simple by Default:** Opens in a clean **one-panel-at-a-time** layout. Press `F2` whenever you want the classic 4-panel quadrant view.
* **Open SQL Scripts:** Use `Ctrl+O` to browse and load existing `.sql` files directly into the editor; save them back with `Ctrl+S`.
* **Polished, Discoverable UI:** Row numbers and `PK`/`FK` badges in the data grid, aligned column inspector, contextual status bar, searchable selection menus, and clear modal confirmations.
* **Open Databases On the Fly:** Press `Ctrl+O` to browse directories with the built-in file picker (mouse or keyboard) and switch database without leaving the TUI.
* **Bidirectional Foreign Key Navigation:** Traverse relational data effortlessly. Follow outgoing Foreign Keys (`f`) to parent records, inspect incoming relations (`F`), and jump back anytime (`b` / `Backspace`).
* **Automatic Undo SQL System:** Every data mutation (cell edit, row insert, row delete, table drop, CSV import) automatically generates the exact inverse SQL restoration script (`<database>_undo.sql`). Press `u` to preview and revert.
* **In-Place Cell Editing & Rich Inspector:** Edit cell values with real data type coercion (`e`). Inspect cells with formatted JSON pretty-printing and binary BLOB hex-dump viewer (`Space`).
* **Column Statistical Analysis:** Instant aggregation metrics (min, max, mean, cardinality, null counts) with Unicode frequency distribution bar charts (`i`).
* **SQL Editor with Autocomplete & Prettifier:** Multi-line text editor with real-time syntax highlighting, intelligent auto-completion (`F8`), statement execution (`F9`), script execution (`F5`), and SQL query formatting (`Ctrl+P`).
* **Interactive Demo Mode:** Spin up a rich, relational e-commerce mock database on the fly (`--demo` / `--mock`).
* **CLI Query Runner & Exporter:** Execute SQL queries directly from the command line (`-e`) and print formatted output in Unicode tables, Markdown, JSON, CSV, TSV, or SQL inserts.
* **SSH & Mouse Friendly:** Runs smoothly over remote SSH connections with full mouse support (click to focus, double-click to open/edit, scroll wheel) and OSC 52 clipboard copying (`y`).
* **6 Built-in Visual Themes:** Tokyo Night, Nord, Gruvbox, Rosé Pine, Monokai, and Classic Terminal (`T`).

---

## 📸 Interface Layout

By default SQLTUI opens in **Simple mode**: one panel at a time, with the `1-4` tabs at the top. Press `F2` to switch to the classic 4-panel layout shown below and back.

```
 sqlite_tui  1 Objects  2 Columns  3 SQL  4 Data │ demo.db                        ? Help
╭── [1] Objects (6) ────────────╮╭── [3] SQL [Ln 4, Col 12] ─────────────────────╮
│▼ Tables (5)                   ││ 1 SELECT o.id, c.name, o.total_amount         │
│  ● categories              5  ││ 2   FROM orders o                             │
│  ● products                8  ││ 3     JOIN customers c ON o.customer_id=c.id  │
│  ● customers               5  ││▸4   WHERE o.total_amount > 500;               │
│  ● orders                  5  ││                                               │
│  ● order_items             7  ││                                               │
│▼ Views (1)                    │╰───────────────────────────────────────────────╯
│  ○ v_customer_orders          │╭── [4] orders · customer_id (INTEGER) FK (5) ─╮
│▼ Indexes (4)                  ││  # id PK# │customer_id FK# │order_date T      │
│  ⚡ idx_orders_cust            ││  1 1      │1               │2026-10-18 10:30  │
╰───────────────────────────────╯│  2 2      │2               │2026-10-19 14:15  │
╭── [2] Columns · orders ───────╮│  3 3      │3               │2026-10-20 09:00  │
│PK id          INTEGER         │╰───────────────────────────────────────────────╯
│   customer_id INTEGER → cust… │
│   order_date  TEXT    =CURREN…│
│   status      TEXT    ='pend… │
│   total_amount REAL   =0.0    │
╰───────────────────────────────╯
 1-4 Panels  Enter Open  q SELECT  s Schema  m Menu  / Filter  d DROP      ? Help
 ● ready · undo available (1)          5 rows · pg 1/1 · demo.db
```

---

## 🚀 Quick Start

### 1. Installation

`sqltui` requires no external packages. Just clone and run:

```bash
git clone https://github.com/buzzqw/sqltui.git
cd sqltui
chmod +x sqlite-tui.py
```

Optional: Install system-wide or add to your `$PATH`:

```bash
sudo ln -s "$(pwd)/sqlite-tui.py" /usr/local/bin/sqltui
```

### 2. Usage Examples

```bash
# Launch interactive demo database (test FK navigation and features instantly)
./sqlite-tui.py --demo

# Open an existing SQLite database
./sqlite-tui.py production.db

# Open in read-only mode (prevents accidental writes or drops)
./sqlite-tui.py production.db --readonly

# Open an in-memory scratchpad
./sqlite-tui.py
```

### 3. Open a Database or SQL Script From the UI (`Ctrl+O`)

Press `Ctrl+O` (or `m` / `F10` → **Browse & open database or SQL script…**) to open the built-in file browser:

- **Database files** (`.db`, `.sqlite`, `.sqlite3`, `.db3`, `.s3db`, `.sqlite2`) are opened as a connection
- **SQL scripts** (`.sql`) are loaded into the SQL editor, ready for `F5`
- `↑` / `↓` or `j` / `k` move, `Enter` / `→` opens a folder or file
- `←` / `Backspace` goes to the parent folder, `a` shows all files/hidden entries
- `p` lets you type a path manually, `n` creates a new database file
- Mouse: click to select, **double-click** to open, wheel to scroll
- The last visited directory is remembered in the config file

Save the current editor content with `Ctrl+S` (or `F10` → **Save SQL script…**).

Only `.db`, `.sqlite`, `.sqlite3`, `.db3`, `.s3db`, `.sqlite2` and `.sql` files are shown by default; press `a` to display every file.

### 4. Non-Interactive CLI Query Runner (`-e`)

Run queries and output formatted results directly to `stdout` without entering the TUI:

```bash
# Formatted Unicode ASCII Table (default)
./sqlite-tui.py --demo -e "SELECT * FROM v_customer_orders" --format table

# Markdown Table (ready for GitHub PRs and docs)
./sqlite-tui.py --demo -e "SELECT id, name, price, stock FROM products LIMIT 5" --format md

# Structured JSON Array
./sqlite-tui.py --demo -e "SELECT * FROM customers" --format json > customers.json

# Standard CSV / TSV
./sqlite-tui.py --demo -e "SELECT * FROM orders" --format csv > orders.csv
```

---

## 🔗 Relational Foreign Key Navigation

Like navigating the web with hyperlinks, `sqltui` lets you traverse database relations seamlessly:

* **Follow Foreign Key (`f`):** On any cell pointing to another table (e.g. `customer_id = 1` in `orders`), press `f` to jump straight to `customers WHERE id = 1`.
* **Find Referencing Rows (`F`):** On a parent record (e.g. customer `Marco Rossi`), press `F` to discover and jump to children records across all referencing tables (`orders`, `reviews`, etc.).
* **Back Stack (`b` or `Backspace`):** Return to previous tables and views, preserving your exact filters, sorting, and cursor coordinates.
* **Visual Cues:** FK columns show the `FK` badge in the grid header, the active column's target appears in the Panel 4 title, and the row number gutter keeps your place.

---

## 🛡️ Automatic Undo SQL System

Every mutating action generates an inverse compensation script saved to `<database>_undo.sql` (or `~/.local/state/sqlite_tui/memory_undo.sql`):

| Action Performed | Generated Undo SQL |
| :--- | :--- |
| **Cell Edit** (`e`) | `UPDATE table SET col = 'old_value' WHERE pk = id;` |
| **Insert Row** (`n`) | `DELETE FROM table WHERE pk = new_id;` |
| **Delete Row** (`d`) | `INSERT INTO table (rowid, cols...) VALUES (...);` |
| **Drop Table** (`d` in Panel 1) | `CREATE TABLE ...; INSERT INTO table ...;` (schema + all data) |
| **CSV Import** | `DELETE FROM table WHERE rowid > max_before;` |
| **UPDATE / DELETE Query** | Restores exact state of affected rows |

* Press `u` at any time to preview and restore the previous state immediately.
* Open `m` / `F10` ➔ `View restore SQL file (Undo Log)…` to audit all generated restore scripts.

---

## ⌨️ Keyboard Shortcuts Reference

### Global Shortcuts

| Key | Action |
| :--- | :--- |
| `1` .. `4` | Jump directly to Panel 1 (Objects), 2 (Columns), 3 (SQL), 4 (Data) |
| `Tab` / `Shift+Tab` | Cycle focus across panels; `Shift+Tab` goes backward (`F6` also cycles forward) |
| `F2` | Toggle **Simple mode** (one panel) / classic 4-panel layout |
| `z` | **Toggle Zoom:** Maximize active panel or restore 4-panel layout |
| `T` | Switch visual theme (Tokyo Night, Nord, Gruvbox, Rosé Pine, Monokai, Classic) |
| `u` | **Undo:** Restore last database change via generated Undo SQL |
| `m` / `F10` | Open full **Command Menu** |
| `Ctrl + O` | Browse and open a database **or a `.sql` script** (file picker with mouse support) |
| `Ctrl + S` | Save the SQL editor content to a `.sql` file |
| `?` or `F1` | Interactive shortcut help screen |
| `Ctrl + Q` | Quit application |
| `Mouse` | Click tabs/menu items, double-click to open/edit, wheel scrolls; dialog buttons are clickable |

### Panel [1]: Schema & Objects
| Key | Action |
| :--- | :--- |
| `↑` `↓` or `j` `k` | Navigate entities |
| `Enter` / `Space` | Open table/view in Data Grid and inspect columns |
| `q` | Generate `SELECT * FROM table;` query in SQL editor |
| `s` | View complete DDL schema definition (`CREATE TABLE ...`, indexes, foreign keys) |
| `d` / `Del` | Drop entity (`DROP TABLE/VIEW/INDEX`) [with automatic Undo SQL] |
| `/` | Real-time fuzzy filter for database objects (`Esc` to clear) |
| `r` | Refresh schema and recalculate row counts |

### Panel [2]: Columns & Structure
| Key | Action |
| :--- | :--- |
| `↑` `↓` or `j` `k` | Select column |
| `f` | **Follow Foreign Key:** Open referenced table in Data Grid |
| `b` / `Backspace` | Navigate back to previous table |
| `i` / `Enter` | **Statistical Analysis:** Min, max, mean, cardinality, distribution histogram |
| `q` | Insert `SELECT <column> FROM <table>;` into SQL editor |

### Panel [3]: Advanced SQL Editor
| Key | Action |
| :--- | :--- |
| `F5` | Execute **entire SQL script** |
| `F9` | Execute **statement under cursor** (delimited by `;`) |
| `F8` | **Smart Autocomplete:** Keywords, functions, tables, and columns |
| `Ctrl + P` | **Format SQL:** Clean indentation and uppercase keywords |
| `Ctrl + S` | Save the editor content to a `.sql` file |
| `Ctrl + Z` / `Ctrl + R` | Undo / Redo text editing |
| `Ctrl + K` / `Ctrl + Y` | Cut line / Paste line |
| `Ctrl + W` / `Ctrl + U` | Delete word backward / Delete to start of line |
| `Tab` | Indent 4 spaces (`Shift+Tab` / `F6` still cycle panels) |

### Panel [4]: Data & Results Grid
| Key | Action |
| :--- | :--- |
| `Arrows` / `h j k l` | Move cell selection |
| `f` | **Follow Foreign Key (Outgoing):** Jump to referenced parent record |
| `F` | **Find References (Incoming):** Jump to child rows pointing to current record |
| `b` / `Backspace` | **Back:** Navigate back in history stack |
| `e` / `Enter` | **Edit Cell In-Place:** Type-safe editing [with automatic Undo SQL] |
| `Space` | **Inspect Cell:** Pretty JSON viewer & binary BLOB hex-dump viewer |
| `v` | **Vertical Record View:** Display all fields of the active row vertically |
| `n` | **Insert Row:** Modal prompt for new row values [with Undo SQL] |
| `d` / `Del` | **Delete Row:** Delete active record [with Undo SQL] |
| `o` | **Sort by Column:** Ascending `▲` ➔ Descending `▼` ➔ Reset |
| `/` | **Filter / Search:** SQL `WHERE` clause (Table mode) or text search (Query results) |
| `Esc` | Clear the active filter or result search |
| `[` / `]` | Previous / next search match |
| `c` | Clear filters and sorting |
| `i` | Column statistical analysis and distribution chart |
| `x` | **Export:** Save view to CSV, JSON, Markdown, TSV, or SQL INSERT statements |
| `y` | Copy cell value to system clipboard via OSC 52 |
| `PgUp` / `PgDn` / `p` | Page up / Page down / Jump to page number |

---

## 🎨 Visual Themes

Press `T` to switch between 6 built-in themes:

1. **Tokyo Night:** Modern cyan and deep blue accents (default).
2. **Nord:** Arctic pastel color palette.
3. **Gruvbox:** Warm, earthy retro tones.
4. **Rosé Pine:** Minimalist pine, rose, and gold palette.
5. **Monokai:** High-contrast vibrant neon highlights.
6. **Classic Terminal:** Retro green-on-black terminal aesthetic.

User preferences are persisted automatically in `~/.local/state/sqlite_tui/config.json`.

---

## 🧪 Testing

The repository includes a comprehensive automated test suite verifying CLI behavior, formatting engines, relational FK navigation, undo systems, and database operations:

```bash
python3 test_sqlite_tui.py
```

---

## 📄 Documentation

For the in-depth architectural breakdown, command menu specifications, and troubleshooting tips, see the [User Manual (MANUAL_SQLITE_TUI.md)](MANUAL_SQLITE_TUI.md).

---

## ⚖️ License

Distributed under the **MIT License**. See [LICENSE](LICENSE) for details.
