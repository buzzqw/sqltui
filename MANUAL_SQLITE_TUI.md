# User Manual: SQLite TUI (`sqlite-tui.py`)

> **Modern, lightweight, zero-dependency SQLite terminal client.**  
> Inspired by *lazygit* and *sqlit*, built for speed, visual clarity, and high productivity over local and remote SSH sessions.

---

## Table of Contents

1. [Overview & Philosophy](#1-overview--philosophy)
2. [Requirements & Quick Start](#2-requirements--quick-start)
3. [Interface Architecture & Layout](#3-interface-architecture--layout)
4. [Global Shortcuts](#4-global-shortcuts)
5. [Panel Guide](#5-panel-guide)
   - [Panel 1: Schema & Objects](#panel-1-schema--objects)
   - [Panel 2: Columns & Structure](#panel-2-columns--structure)
   - [Panel 3: Advanced SQL Editor](#panel-3-advanced-sql-editor)
   - [Panel 4: Data & Results Grid](#panel-4-data--results-grid)
6. [Relational Foreign Key Navigation](#6-relational-foreign-key-navigation)
7. [Automatic Undo SQL System](#7-automatic-undo-sql-system)
8. [Command Menu (F10) & Advanced Features](#8-command-menu-f10--advanced-features)
9. [Importing, Exporting & CLI Scripting](#9-importing-exporting--cli-scripting)
10. [Themes & Customization](#10-themes--customization)
11. [Troubleshooting & FAQ](#11-troubleshooting--faq)

---

## 1. Overview & Philosophy

`sqlite_tui.py` is an interactive visual database explorer and SQL manager for the command line.

* **Zero External Dependencies:** Built solely with the **Python 3.8+ Standard Library** (`curses`, `sqlite3`, `csv`, `json`). No external packages or `pip install` required.
* **Lazygit & sqlit Ergonomics:** 4 responsive panels reachable with keys `1`..`4`, Unicode rounded borders, illuminated active panel frame, and single-key zoom/fullscreen toggle (`z`).
* **Bidirectional Foreign Key Navigation:** Jump through relational structures effortlessly (`f` forward to parent tables, `F` reverse to children records, `b` to navigate back).
* **Data Safety via Automatic Undo SQL:** Any database modification (cell edits, row insertions, row deletions, table drops, CSV imports, or UPDATE/DELETE queries) automatically records an inverse SQL restoration script in `<database>_undo.sql`. Restore your data anytime with `u`.
* **Full Terminal & Mouse Support:** Seamless over SSH and local terminal emulators with native mouse interaction (click to focus, double-click to open/edit, scroll wheel).
* **CLI Scripting & Demo Mode:** Query output engine (`-e` with `--format table/md/json/csv/tsv/sql`) and instant mock relational database (`--demo` / `--mock`).

---

## 2. Requirements & Quick Start

### Requirements
* Operating System: Linux / macOS / BSD
* Python: version 3.8 or higher
* Terminal with color support (`xterm-256color` recommended)

### Launch Commands

```bash
# Open an existing SQLite database
./sqlite-tui.py my_database.db

# Launch with interactive relational demo e-commerce database (sqlit mock style)
./sqlite-tui.py --demo

# Open in read-only mode (prevents accidental mutations)
./sqlite-tui.py my_database.db --readonly
# Or shorthand:
./sqlite-tui.py my_database.db -r

# Launch an in-memory database (:memory:) for testing or scratchpad
./sqlite-tui.py

# Execute SQL query non-interactively and output formatted table to stdout
./sqlite-tui.py my_database.db -e "SELECT * FROM users LIMIT 10" --format table

# Export query result directly to Markdown or JSON from the command line
./sqlite-tui.py my_database.db -e "SELECT * FROM orders" --format md
./sqlite-tui.py my_database.db -e "SELECT * FROM orders" --format json
```

---

## 3. Interface Architecture & Layout

The screen is structured in 4 main quadrants:

```
╭─ [1] Objects (4) ───────────────────────╮╭─ [3] SQL Editor ─────────────────────────────╮
│ ● users (1,450)                         ││ SELECT name, email FROM users WHERE id > 10; │
│ ● orders (8,230)                        ││                                              │
│ ○ active_users (view)                   ││                                              │
│ ⚡ idx_users_email (index)               ││                                              │
╰─────────────────────────────────────────╯╰──────────────────────────────────────────────╯
╭─ [2] Columns (orders) ──────────────────╮╭─ [4] orders · customer_id (INTEGER) [➔ customers.id (f)] ╮
│ id              INTEGER  🔑 [PK] [NN]   ││ id   customer_id    order_date            total_amount   │
│ customer_id     INTEGER  ➔ [-> cust.id] ││ 1    1              2026-10-18 10:30:00   3878.90        │
│ order_date      TEXT                    ││ 2    2              2026-10-19 14:15:00   136.99         │
│ total_amount    REAL                    ││ 3    1              2026-10-20 09:00:00   1299.00        │
╰─────────────────────────────────────────╯╰──────────────────────────────────────────────────────────╯
 [1]Objects [2]Cols [3]SQL [4]Data │ f:FK Jump │ b:Back │ Tab:Cycle │ z:Zoom │ T:Theme │ u:Undo │ ?:Help
```

* **Unicode Rounded Borders:** The active panel is highlighted with bright accent borders according to your chosen theme.
* **Dynamic Header Badges:** Displays active table, current column name, declared type, primary key marker (`🔑PK`), and foreign key target (`➔ table.col (f)`).
* **Bottom Context Bar (Row H-2):** Dynamically displays relevant shortcuts for whichever panel currently holds focus.
* **Status & Notification Bar (Row H-1):** Reports success messages, transaction states, row counts, and detailed error messages in real time.

---

## 4. Global Shortcuts

These hotkeys are available at all times from any panel:

| Key | Action |
| :--- | :--- |
| `1` | Jump directly to **Panel 1 (Objects & Schema)** |
| `2` | Jump directly to **Panel 2 (Columns & Table Info)** |
| `3` | Jump directly to **Panel 3 (SQL Editor)** |
| `4` | Jump directly to **Panel 4 (Data / Query Results)** |
| `Tab` | Cycle focus clockwise to next panel |
| `Shift + Tab` | Cycle focus counter-clockwise to previous panel |
| `z` | **Toggle Zoom:** Maximize active panel to full screen or restore grid layout |
| `T` | Open **Visual Theme** selector |
| `u` | Trigger **Undo SQL** (revert the latest database mutation) |
| `F1` or `?` | Display interactive Help screen |
| `F10` | Open **Command Menu** (import, export, transactions, DDL, VACUUM) |
| `Ctrl + Q` | Exit the application |
| `Mouse` | Click to focus panel or row, double-click to open/edit, wheel to scroll |

---

## 5. Panel Guide

### Panel 1: Schema & Objects
Lists all database entities with visual indicator icons and live row counts:
* `●` **Table** (with exact row count, e.g. `users (1,450)`)
* `○` **View** (e.g. `active_users (view)`)
* `⚡` **Index**
* `⚙` **Trigger**

#### Hotkeys in Panel 1:
* `↑` / `↓` or `j` / `k`: Select entity.
* `Enter` / `Space`: Open table or view in **Panel 4 (Data Grid)** and load its schema in Panel 2.
* `q`: Generate `SELECT * FROM <table>;` into SQL Editor and move focus to editor.
* `s`: View full DDL definition (`CREATE TABLE ...`, indexes, foreign key relationships).
* `/`: Real-time fuzzy filter for database objects (`Esc` to clear).
* `d` or `Del`: Drop entity (`DROP TABLE/VIEW/INDEX`).  
  *(Generates an inverse Undo SQL script automatically).*
* `r`: Refresh schema and update row counts.

---

### Panel 2: Columns & Structure
Inspects schema details for the currently active table or view:
* Column name and declared SQLite storage type (`INTEGER`, `TEXT`, `REAL`, `BLOB`, `NUMERIC`).
* `🔑 [PK]` badge: Primary key column.
* `[NN]` badge: Not Null constraint.
* `[-> table.col]` badge: Foreign key constraint with target table and column.

#### Hotkeys in Panel 2:
* `↑` / `↓` or `j` / `k`: Select column.
* `f`: **Jump to Foreign Key Table**. Directly opens the referenced table in Panel 4.
* `b` or `Backspace`: Navigate back to previous table.
* `i` or `Enter`: **Statistical Analysis**.  
  Opens a summary popup containing:
  * Total rows, non-null values count, and NULL percentage.
  * Cardinality (number of distinct values).
  * Min, Max, and Mean (for numeric columns).
  * **Top-6 Frequency Distribution** visual histogram rendered with Unicode bar characters (`████░░`).
* `q`: Insert `SELECT <column> FROM <table>;` into SQL Editor.

---

### Panel 3: Advanced SQL Editor
A multi-line text editor with SQL syntax highlighting and query runner:

* **Syntax Highlighting:** Real-time colors for keywords, strings, comments, and numeric literals.
* **Intelligent Autocomplete (`F8`):** Contextual suggestions for SQL keywords, SQLite functions, table names, and column names.
* **SQL Formatter (`Ctrl + P`):** Re-indents query clauses and uppercases keywords.

#### Hotkeys in SQL Editor:
* `F5`: Execute the **entire SQL script**.
* `F9`: Execute **only the statement under the cursor** (delimited by `;`).
* `F8`: Trigger autocomplete popup.
* `Ctrl + P`: Format and beautify SQL query.
* `Ctrl + Z`: Undo text edit in editor.
* `Ctrl + R`: Redo text edit in editor.
* `Ctrl + K`: Cut line.
* `Ctrl + Y`: Paste line.
* `Ctrl + W`: Delete word backward.
* `Ctrl + U`: Delete line backward to start.
* `Tab`: Indent 4 spaces.

---

### Panel 4: Data & Results Grid
Displays table records or the output of custom SQL queries.

* **Dynamic Columns:** Auto-sized widths based on actual terminal width and cell length.
* **Header Indicators:** Shows `🔑` for Primary Keys and `➔` for Foreign Keys.

#### Hotkeys in Data Grid:
* `Arrows` / `h j k l`: Cell navigation.
* `f`: **Follow Foreign Key (Outgoing)**. Jump directly to the parent row referenced by the active cell.
* `F`: **Find Referencing Records (Incoming)**. Jump to child rows pointing to the current record.
* `b` or `Backspace`: **Navigate Back**. Returns to previous table and cursor position in history.
* `g` / `G`: Jump to first / last row of page.
* `PgUp` / `PgDn`: Previous / next page.
* `p`: Jump to a specific page number.
* `e` or `Enter`: **Edit Cell In-Place**.  
  Parses real types (`NULL`, integer, float, string) and records an Undo SQL statement.
* `Space`: **Inspect Cell**.  
  Full-screen view of cell content:
  * Auto-formats and indents **JSON** payloads.
  * Hex dump with two columns (hex + ASCII) for binary **BLOBs**.
* `v`: **Vertical Record Viewer** (inspect all fields of the active row in a vertical layout).
* `n`: **Insert New Row**. Form dialog prompt for each column.
* `d` or `Del`: **Delete Row** (with automatic Undo SQL compensation).
* `o`: **Sort by Column** (cycles: Ascending `▲` -> Descending `▼` -> Reset).
* `/`: **Search / Filter**:
  * In Table mode: prompts for a SQL `WHERE` clause (e.g. `age > 25 AND active = 1`).
  * In Query Results mode: in-memory string search across cells (`n`/`N` for next/previous match).
* `i`: Statistical analysis of the active column.
* `y`: Copy cell content to system clipboard via OSC 52.
* `x`: Export current view (CSV, TSV, JSON, SQL INSERT, Markdown).
* `u`: Trigger Undo SQL for the latest modification.

---

## 6. Relational Foreign Key Navigation

Navigating relational data in `sqlite_tui` works just like following hyperlinks on the web:

### 1. Forward FK Navigation (`f`)
When standing on a cell that references another table (e.g. `customer_id = 42` in `orders`):
1. Press `f`.
2. `sqlite_tui` automatically switches to the `customers` table and applies `WHERE id = 42`.
3. The panel header updates with `[b: Back (1)]`.

### 2. Reverse FK Navigation (`F`)
When standing on a record in a parent table (e.g. customer `Marco Rossi` with `id = 1`):
1. Press `F`.
2. `sqlite_tui` scans the database schema for incoming foreign keys.
3. If multiple child tables reference this customer (e.g. `orders`, `addresses`, `reviews`), a selection modal lets you choose which table to open.
4. `sqlite_tui` opens the chosen child table filtered for that customer's records.

### 3. Navigation History Back Stack (`b` or `Backspace`)
* Press `b` or `Backspace` at any point to step back through your navigation history.
* Preserves previous table, `WHERE` filter, sort criteria, pagination, and cursor position.

---

## 7. Automatic Undo SQL System

`sqlite_tui` automatically protects your data against accidental mistakes or data loss.

### How it works
Every mutating operation generates the **exact inverse SQL compensation command**:

| Operation Performed | Generated Undo SQL |
| :--- | :--- |
| **Cell Edit** (`e`) | `UPDATE table SET col = 'old_val' WHERE pk = id;` |
| **Insert Row** (`n`) | `DELETE FROM table WHERE pk = new_id;` |
| **Delete Row** (`d`) | `INSERT INTO table (rowid, col1, col2) VALUES (...);` |
| **Drop Table** (`d` in Panel 1) | `CREATE TABLE ...; INSERT INTO table VALUES (...);` (schema + all rows) |
| **CSV Import** | `DELETE FROM table WHERE rowid > max_before;` or `DROP TABLE` |
| **SQL UPDATE / DELETE** | Restores pre-mutation state of affected records |

### Log File Location
* File-backed databases: saved in `<database_path>_undo.sql`.
* In-memory databases (`:memory:`): saved in `~/.local/state/sqlite_tui/memory_undo.sql`.

### Applying Restoration
1. Press `u` at any time.
2. A confirmation prompt displays the action to be reverted (e.g. `Restore «Delete row from users» using Undo SQL?`).
3. Press `y` to apply. The database immediately reverts and the grid reloads.
4. To view the complete history of recorded undo statements, open `F10` -> `View restore SQL file (Undo Log)…`.

---

## 8. Command Menu (F10) & Advanced Features

Pressing `F10` opens the command palette:

1. **Execute all (F5)** / **Execute current statement (F9)**
2. **Format SQL query in editor (Ctrl+P)**
3. **Follow foreign key from current cell (f)**
4. **Find referencing records (incoming FK) (F)**
5. **Navigate back in history (b)**
6. **Undo last change (Undo SQL)…** (`u`)
7. **View restore SQL file (Undo Log)…**
8. **Switch visual theme (T)…**
9. **Open another database…**
10. **Load interactive demo database…**
11. **Query history…**
12. **Template: create table**
13. **Export table or results (CSV/JSON/SQL/MD/TSV)…**
14. **Import CSV/TSV into current table…**
15. **Dump database to SQL file…**
16. **Backup database…** (using SQLite Online Backup API)
17. **Attach another database (ATTACH)…**
18. **Transaction Controls:** `BEGIN`, `COMMIT`, `ROLLBACK`
19. **Foreign Keys Toggle:** Enable / Disable runtime FK enforcement
20. **Database Info:** Pages, sizes, encoding, journal mode, versions
21. **Integrity Check:** Runs `PRAGMA integrity_check`
22. **Maintenance:** `VACUUM`, `ANALYZE`, `PRAGMA optimize`
23. **Help & Quit**

---

## 9. Importing, Exporting & CLI Scripting

### Interactive Export (`x` in Panel 4 or via `F10`)
Export tables or query results into 5 formats:
* **CSV:** RFC 4180 standard comma-separated values.
* **TSV:** Tab-separated values (ideal for pasting into spreadsheet tools).
* **JSON:** Formatted array of JSON records.
* **SQL:** Standalone script containing `INSERT INTO ...` statements.
* **Markdown:** GitHub-compatible pipe table (`| Col1 | Col2 |`).

### Non-Interactive CLI Query Runner (`-e`)
Run queries directly from bash scripts and automation pipelines:
```bash
./sqlite-tui.py prod.db -e "SELECT name, price FROM products" --format table
./sqlite-tui.py prod.db -e "SELECT name, price FROM products" --format md
./sqlite-tui.py prod.db -e "SELECT name, price FROM products" --format json > products.json
./sqlite-tui.py prod.db -e "SELECT name, price FROM products" --format csv > products.csv
```

### Importing CSV/TSV (`F10` -> Import CSV)
* **Auto-Delimiter Detection:** Automatically identifies commas (`,`), semicolons (`;`), tabs (`\t`), or pipes (`|`).
* **Auto-Create Table:** If target table does not exist, automatically creates it based on CSV headers.

---

## 10. Themes & Customization

Press `T` to select from 6 built-in color schemes:

1. **Tokyo Night:** Modern cyan and dark blue aesthetic (default).
2. **Nord:** Soft arctic pastel colors.
3. **Gruvbox:** Warm retro earthy tones.
4. **Rosé Pine:** Minimalist pine and rose palette.
5. **Monokai:** High-contrast vibrant hues.
6. **Classic Terminal:** Standard retro green-on-black terminal.

Settings persist automatically in:
`~/.local/state/sqlite_tui/config.json`

---

## 11. Troubleshooting & FAQ

### 1. Panel borders show strange characters
Ensure your terminal emulator and environment variable support `UTF-8`:
```bash
echo $LANG
# Should output en_US.UTF-8 (or UTF-8 equivalent)
```

### 2. How to try the application immediately without a database?
Run with the `--demo` flag:
```bash
./sqlite-tui.py --demo
```
This launches a complete relational e-commerce database in memory with customers, orders, items, products, categories, views, and JSON attributes.

### 3. How to open in read-only mode?
Use the `-r` or `--readonly` flag:
```bash
./sqlite-tui.py production.db --readonly
```
All mutations (cell edits, row insertions, drops, writes) will be prevented at the SQLite connection level.

---

*Built with Python Standard Library and curses.*
