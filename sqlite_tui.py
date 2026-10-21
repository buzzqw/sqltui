#!/usr/bin/env python3
"""sqlite_tui: terminal SQLite explorer and editor (designed for SSH).

Inspired by lazygit.
Zero external dependencies: uses only Python standard library (curses and sqlite3).

Usage:
    python3 sqlite_tui.py [database.db] [--readonly]
    ./sqlite_tui.py [database.db] [--readonly]

Inside the application:
    1..4     jump directly between panels
    Tab      cycle focus across panels · z toggle zoom/maximize
    T        switch theme (Tokyo Night, Nord, Gruvbox, Rosé Pine, Monokai, Classic)
    u        undo last change (applies automatically generated Undo SQL)
    i        statistical analysis & distribution of the current column
    Ctrl+O   open database or SQL file · Ctrl+S save SQL script
    F2       simple one-panel mode / 4-panel layout · ? / F1 help
"""

from __future__ import annotations

import argparse
import base64
import csv
import curses
import functools
import io
import json
import locale
import os
import pathlib
import re
import sqlite3
import sys
import textwrap
import time
import unicodedata
from typing import Any, NamedTuple, Optional

APP = "sqlite_tui"
MAX_ROWS = 5000                  # Maximum rows fetched per ad-hoc query
MAX_COL_WIDTH = 40
CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".local", "state", APP, "config.json")
HISTORY_FILE = os.path.join(os.path.expanduser("~"), ".local", "state", APP, "history.json")
HISTORY_MAX = 300
USER_OBJECTS = "name NOT LIKE 'sqlite\\_%' ESCAPE '\\'"
DEFAULT_SQL = "-- F5 executes all · F9 executes statement under cursor · F8 autocomplete\nSELECT name, type FROM sqlite_master;\n"
SQLITE_EXTENSIONS = (".db", ".sqlite", ".sqlite3", ".db3", ".s3db", ".sqlite2")
SQL_EXTENSIONS = (".sql",)
OPEN_EXTENSIONS = SQLITE_EXTENSIONS + SQL_EXTENSIONS

KEYWORDS = set("""
ADD ALL ALTER ANALYZE AND AS ASC ATTACH AUTOINCREMENT BEGIN BETWEEN BLOB BY CASE CAST CHECK COLLATE COLUMN COMMIT CONFLICT
CONSTRAINT CREATE CROSS CURRENT CURRENT_DATE CURRENT_TIME CURRENT_TIMESTAMP DATABASE DEFAULT DEFERRABLE DEFERRED DELETE DESC DETACH
DISTINCT DO DROP EACH ELSE END ESCAPE EXCEPT EXCLUDE EXCLUSIVE EXISTS EXPLAIN FAIL FILTER FIRST FOLLOWING FOR FOREIGN
FROM FULL GENERATED GLOB GROUP GROUPS HAVING IF IGNORE IMMEDIATE IN INDEX INDEXED INITIALLY INNER INSERT INSTEAD
INTEGER INTERSECT INTO IS ISNULL JOIN KEY LAST LEFT LIKE LIMIT MATCH MATERIALIZED NATURAL NO NOT NOTHING NOTNULL NULL
NULLS NUMERIC OF OFFSET ON ONLY OPEN OR ORDER OTHERS OUTER OVER PARTITION PLAN PRAGMA PRECEDING PRIMARY QUERY RAISE
RANGE REAL RECURSIVE REFERENCES REGEXP REINDEX RELEASE RENAME REPLACE RESTRICT RETURNING ROLLBACK ROW ROWID ROWS
SAVEPOINT SELECT SET STORED TABLE TEMP TEMPORARY THEN TIES TO TRANSACTION TRIGGER UNBOUNDED UNION UNIQUE UPDATE
USING VACUUM VALUES VIEW VIRTUAL WHEN WHERE WINDOW WITH WITHOUT
ABS AVG CHANGES CHAR COALESCE COUNT DATE DATETIME GLOB GROUP_CONCAT HEX IFNULL INSTR JSON JSON_ARRAY JSON_EXTRACT
JSON_OBJECT JSON_QUOTE JSON_TYPE JSON_VALID JULIANDAY LAG LAST_VALUE LEAD LENGTH LOWER LTRIM MAX MIN NULLIF
PRINTF RANDOM RANDOMBLOB RANK ROUND ROW_NUMBER RTRIM SOUNDEX STRFTIME SUBSTR SUBSTRING SUM TIME TOTAL TOTAL_CHANGES
TRIM TYPEOF UNICODE UNHEX UPPER ZEROBLOB
""".split())
KEYWORD_LIST = sorted(KEYWORDS)

TOKEN_RE = re.compile(
    r"(--[^\n]*)"              # 1 line comment
    r"|(/\*[\s\S]*?\*/)"       # 2 block comment
    r"|('(?:[^']|'')*'?)"      # 3 string
    r'|("(?:[^"]|"")*"?)'      # 4 quoted identifier
    r"|(\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)"  # 5 number
    r"|([A-Za-z_]\w*)"         # 6 word / identifier
)

KEY_ENTER = {10, 13, curses.KEY_ENTER}
KEY_BACK = {8, 127, curses.KEY_BACKSPACE}
KEY_ESC = 27
KEY_TAB = 9
MOUSE_B1 = (getattr(curses, "BUTTON1_CLICKED", 0) |
            getattr(curses, "BUTTON1_DOUBLE_CLICKED", 0) |
            getattr(curses, "BUTTON1_PRESSED", 0))
MOUSE_DBL = getattr(curses, "BUTTON1_DOUBLE_CLICKED", 0)
MOUSE_WHEEL_UP = (getattr(curses, "BUTTON4_PRESSED", 0) |
                  getattr(curses, "BUTTON4_CLICKED", 0))
MOUSE_WHEEL_DOWN = (getattr(curses, "BUTTON5_PRESSED", 0) |
                    getattr(curses, "BUTTON5_CLICKED", 0))
CTRL_A, CTRL_C, CTRL_D, CTRL_E, CTRL_F, CTRL_K, CTRL_O, CTRL_P, CTRL_Q, CTRL_R, CTRL_S, CTRL_U, CTRL_W, CTRL_Y, CTRL_Z = (
    1, 3, 4, 5, 6, 11, 15, 16, 17, 18, 19, 21, 23, 25, 26
)

A: dict[str, int] = {}           # Drawing attributes (populated in init_ui)

THEMES = {
    "tokyo-night": {
        "name": "Tokyo Night",
        "panel_active": curses.COLOR_CYAN,
        "panel_dim": curses.COLOR_BLUE,
        "bar_fg": curses.COLOR_BLACK,
        "bar_bg": curses.COLOR_WHITE,
        "kw": curses.COLOR_YELLOW,
        "str": curses.COLOR_GREEN,
        "num": curses.COLOR_MAGENTA,
        "comment": curses.COLOR_BLUE,
        "err": curses.COLOR_RED,
        "ok": curses.COLOR_GREEN,
        "warn": curses.COLOR_YELLOW,
        "hdr": curses.COLOR_CYAN,
        "null": curses.COLOR_BLUE,
        "accent": curses.COLOR_CYAN,
    },
    "nord": {
        "name": "Nord",
        "panel_active": curses.COLOR_CYAN,
        "panel_dim": curses.COLOR_WHITE,
        "bar_fg": curses.COLOR_BLACK,
        "bar_bg": curses.COLOR_CYAN,
        "kw": curses.COLOR_CYAN,
        "str": curses.COLOR_GREEN,
        "num": curses.COLOR_MAGENTA,
        "comment": curses.COLOR_WHITE,
        "err": curses.COLOR_RED,
        "ok": curses.COLOR_GREEN,
        "warn": curses.COLOR_YELLOW,
        "hdr": curses.COLOR_WHITE,
        "null": curses.COLOR_CYAN,
        "accent": curses.COLOR_CYAN,
    },
    "gruvbox": {
        "name": "Gruvbox",
        "panel_active": curses.COLOR_YELLOW,
        "panel_dim": curses.COLOR_WHITE,
        "bar_fg": curses.COLOR_BLACK,
        "bar_bg": curses.COLOR_YELLOW,
        "kw": curses.COLOR_RED,
        "str": curses.COLOR_GREEN,
        "num": curses.COLOR_MAGENTA,
        "comment": curses.COLOR_WHITE,
        "err": curses.COLOR_RED,
        "ok": curses.COLOR_GREEN,
        "warn": curses.COLOR_YELLOW,
        "hdr": curses.COLOR_YELLOW,
        "null": curses.COLOR_WHITE,
        "accent": curses.COLOR_YELLOW,
    },
    "rose-pine": {
        "name": "Rosé Pine",
        "panel_active": curses.COLOR_MAGENTA,
        "panel_dim": curses.COLOR_CYAN,
        "bar_fg": curses.COLOR_BLACK,
        "bar_bg": curses.COLOR_MAGENTA,
        "kw": curses.COLOR_YELLOW,
        "str": curses.COLOR_GREEN,
        "num": curses.COLOR_CYAN,
        "comment": curses.COLOR_WHITE,
        "err": curses.COLOR_RED,
        "ok": curses.COLOR_GREEN,
        "warn": curses.COLOR_YELLOW,
        "hdr": curses.COLOR_MAGENTA,
        "null": curses.COLOR_CYAN,
        "accent": curses.COLOR_MAGENTA,
    },
    "monokai": {
        "name": "Monokai",
        "panel_active": curses.COLOR_GREEN,
        "panel_dim": curses.COLOR_WHITE,
        "bar_fg": curses.COLOR_BLACK,
        "bar_bg": curses.COLOR_GREEN,
        "kw": curses.COLOR_RED,
        "str": curses.COLOR_YELLOW,
        "num": curses.COLOR_MAGENTA,
        "comment": curses.COLOR_WHITE,
        "err": curses.COLOR_RED,
        "ok": curses.COLOR_GREEN,
        "warn": curses.COLOR_YELLOW,
        "hdr": curses.COLOR_CYAN,
        "null": curses.COLOR_BLUE,
        "accent": curses.COLOR_GREEN,
    },
    "classic": {
        "name": "Classic Terminal",
        "panel_active": curses.COLOR_WHITE,
        "panel_dim": curses.COLOR_WHITE,
        "bar_fg": curses.COLOR_BLACK,
        "bar_bg": curses.COLOR_WHITE,
        "kw": curses.COLOR_YELLOW,
        "str": curses.COLOR_GREEN,
        "num": curses.COLOR_MAGENTA,
        "comment": curses.COLOR_BLUE,
        "err": curses.COLOR_RED,
        "ok": curses.COLOR_GREEN,
        "warn": curses.COLOR_YELLOW,
        "hdr": curses.COLOR_CYAN,
        "null": curses.COLOR_BLUE,
        "accent": curses.COLOR_WHITE,
    }
}

HELP_LINES = [
    "# Global Shortcuts (lazygit style)",
    "  1           jump to Panel [1] Schema & Objects",
    "  2           jump to Panel [2] Columns & Table Info",
    "  3           jump to Panel [3] SQL Editor",
    "  4           jump to Panel [4] Data & Results Grid",
    "  Tab         cycle panels forward (Shift+Tab backward · F6 forward)",
    "  z           toggle maximize / restore active panel (Zoom)",
    "  T           switch theme (Tokyo Night, Nord, Gruvbox, Rosé Pine, Monokai, Classic)",
    "  m / F10     command menu (open, export, import, transactions, VACUUM...)",
    "  Ctrl+O      browse & open DB or SQL file (Enter open · p path · n new)",
    "  Ctrl+S      save the SQL editor content to a .sql file",
    "  F2          toggle simple mode (one panel) / 4-panel layout",
    "  Ctrl+Q      quit application",
    "  Mouse       click tabs/menu items, double-click to open/edit, wheel to scroll",
    "  Note        inside the SQL editor z/T/?/m type text; use F1/F6/F10 for actions",
    "",
    "# Panel [1]: Schema & Objects",
    "  arrows / j k    move          Enter   open table in data viewer",
    "  q               generate SELECT * query in SQL editor",
    "  s               view detailed DDL structure",
    "  d / Del         drop object (DROP) [generates Undo SQL script]",
    "  /               filter objects in real time        Esc  clear filter",
    "  r               refresh schema and row counts",
    "",
    "# Panel [2]: Columns & Structure",
    "  arrows / j k    select column",
    "  i / Enter       full statistical analysis (min, max, mean, cardinality, distribution)",
    "  f               jump to referenced table for selected Foreign Key column",
    "  b / Backspace   navigate back to previous table in history",
    "  q               insert SELECT for selected column into SQL editor",
    "",
    "# Panel [3]: SQL Editor",
    "  F5              execute entire script",
    "  F9              execute statement under cursor",
    "  F8              autocomplete keywords, functions, tables, and columns",
    "  Ctrl+S          save the editor content to a .sql file",
    "  Ctrl+P          format / prettify SQL query with uppercase keywords",
    "  Ctrl+Z          undo                          Ctrl+R   redo",
    "  Ctrl+K          cut line                      Ctrl+Y   paste line",
    "  Ctrl+W          delete word backward          Ctrl+U   delete to start of line",
    "  Tab             indent 4 spaces (Shift+Tab / F6 cycle panels)",
    "",
    "# Panel [4]: Data & Results Grid",
    "  arrows / hjkl   move                          g / G    go to top / bottom",
    "  f               follow Foreign Key in active cell to target table",
    "  F               find referencing rows (incoming FK) from other tables",
    "  b / Backspace   navigate back to previous table/view in history",
    "  e / Enter       edit cell [generates automatic Undo SQL]",
    "  Space           inspect full cell (formatted JSON view & hex viewer for BLOBs)",
    "  v               view formatted full record",
    "  y               copy cell value to clipboard (OSC 52)",
    "  n               insert new row [generates automatic Undo SQL]",
    "  d / Del         delete row [generates automatic Undo SQL]",
    "  u               undo data change (runs automatically generated restore SQL)",
    "  i               statistical analysis of current column",
    "  o               sort by column (ascending / descending / clear sort)",
    "  /               search in query results (SQL) / WHERE filter (Data)",
    "  Esc             clear the active filter or result search",
    "  [ / ]           previous / next search match",
    "  x               export to CSV, JSON, SQL INSERT, Markdown Table, or TSV",
    "  PgUp / PgDn     change page                   p        jump to page number",
    "",
    "# Relational FK Navigation & CLI Features",
    "  f / F           forward (outgoing) & reverse (incoming) foreign key relationship navigation",
    "  b / Backspace   back stack keeps full history of table, filter, and cursor position",
    "  --demo / --mock launches rich interactive relational e-commerce demo database",
    "  -e 'SQL'        run query non-interactively from CLI and format as table, csv, json, md, tsv",
    "",
    "# Automatic Undo SQL System",
    "  Every database mutation (cell edit, row insert, row delete, DROP, CSV import,",
    "  or manual UPDATE/DELETE queries) automatically generates the exact inverse SQL",
    "  script to restore your data to its prior state.",
    "  Saved in: <database>_undo.sql (or ~/.local/state/sqlite_tui/memory_undo.sql).",
    "  Press 'u' to preview and apply the restoration immediately.",
]


# ---------------------------------------------------------------------------
# Configuration & text display utilities
# ---------------------------------------------------------------------------

def load_config() -> dict:
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"theme": "tokyo-night"}


def save_config(cfg: dict) -> None:
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


@functools.lru_cache(maxsize=512)
def char_width(c: str) -> int:
    return 2 if unicodedata.east_asian_width(c) in ("F", "W") else 1


def str_width(s: str) -> int:
    """Returns actual display width in terminal columns (ultra-fast ASCII path)."""
    if s.isascii():
        return len(s)
    return sum(char_width(c) for c in s)


def fit_str(s: str, max_w: int) -> str:
    """Clips the string so its visual width is <= max_w, appending '~' if clipped."""
    if max_w <= 0:
        return ""
    if s.isascii():
        if len(s) <= max_w:
            return s
        return s[:max_w - 1] + "~"
    if str_width(s) <= max_w:
        return s
    cur_w = 0
    chars = []
    target = max_w - 1
    for c in s:
        w = char_width(c)
        if cur_w + w > target:
            chars.append("~")
            break
        chars.append(c)
        cur_w += w
    return "".join(chars)


def pad_str(s: str, target_w: int, align: str = "left") -> str:
    """Pads string with spaces according to terminal display width."""
    w = len(s) if s.isascii() else str_width(s)
    if w >= target_w:
        return s
    pad = " " * (target_w - w)
    return s + pad if align == "left" else pad + s


def wrap_text(text: str, width: int) -> list[str]:
    """Word-wraps text to the given display width (used by modal dialogs)."""
    if width <= 0:
        return [text]
    return textwrap.wrap(text, width=width, break_long_words=True, break_on_hyphens=False) or [""]


def quote_ident(name: str) -> str:
    return f'"{name.replace(chr(34), chr(34) * 2)}"' if '"' in name else f'"{name}"'


def sql_literal(v: Any) -> str:
    """Converts any Python value into a valid SQLite SQL literal."""
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, bytes):
        return f"X'{v.hex()}'"
    s = str(v)
    return "'" + s.replace("'", "''") + "'" if "'" in s else f"'{s}'"


def split_statements(sql: str) -> list[tuple[int, int]]:
    """Returns (start, end) span of each statement separated by ';' outside strings, comments, and triggers."""
    spans = []
    start = 0
    i = 0
    n = len(sql)
    in_trigger = False
    while i < n:
        c = sql[i]
        if c in "'\"`":
            j = i + 1
            while j < n:
                if sql[j] == c:
                    if j + 1 < n and sql[j + 1] == c:
                        j += 2
                        continue
                    break
                j += 1
            i = min(n, j + 1)
        elif c == "[":
            j = sql.find("]", i)
            i = n if j < 0 else j + 1
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c.isalpha() or c == "_":
            m = re.match(r"[A-Za-z_]\w*", sql[i:])
            if m:
                word = m.group(0).upper()
                if word == "TRIGGER":
                    in_trigger = True
                elif word == "END" and in_trigger:
                    in_trigger = False
                i += len(m.group(0))
            else:
                i += 1
        elif c == ";":
            if not in_trigger:
                spans.append((start, i))
                i += 1
                start = i
            else:
                i += 1
        else:
            i += 1
    spans.append((start, n))
    return [(a, b) for a, b in spans if sql[a:b].strip()]


def statement_at(sql: str, offset: int) -> Optional[tuple[int, int]]:
    spans = split_statements(sql)
    if not spans:
        return None
    for a, b in spans:
        if a <= offset <= b:
            return a, b
    before = [s for s in spans if s[1] < offset]
    return before[-1] if before else spans[0]


def fmt_value(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, bytes):
        return f"<blob {len(v)} B>"
    s = str(v)
    if "\r" in s or "\n" in s or "\t" in s:
        return s.replace("\r", "").replace("\n", "\\n").replace("\t", "\\t")
    return s


def short_path(path: str) -> str:
    """Abbreviates the home directory with ~ for compact display."""
    home = os.path.expanduser("~")
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def preview_len(v: Any, max_len: int = MAX_COL_WIDTH) -> int:
    """Efficiently estimates visual width capped at max_len."""
    if v is None:
        return 4  # "NULL"
    if isinstance(v, bytes):
        return min(max_len, 12 + len(str(len(v))))
    if isinstance(v, (int, float)):
        return min(max_len, len(str(v)))
    s = str(v)
    if s.isascii():
        return min(max_len, len(s))
    w = 0
    for ch in s:
        w += 2 if (ch in "\r\n\t" or unicodedata.east_asian_width(ch) in ("F", "W")) else 1
        if w >= max_len:
            return max_len
    return w


INT_RE = re.compile(r"[+-]?\d+$")
FLOAT_RE = re.compile(r"[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")


def parse_input(text: str, decl_type: str) -> Any:
    """Converts user input text to appropriate type. 'NULL' = None."""
    if text == "NULL":
        return None
    if text == "''" or text == '""':
        return ""
    if any(t in decl_type.upper() for t in ("CHAR", "CLOB", "TEXT", "BLOB")):
        return text
    if INT_RE.match(text):
        return int(text)
    if FLOAT_RE.match(text):
        return float(text)
    return text


def export_value(v: Any) -> Any:
    if isinstance(v, bytes):
        return base64.b64encode(v).decode("ascii")
    return v


def export_rows(path: str, cols: list[str], rows, table_name: str = "export") -> int:
    """Writes rows to CSV, TSV, JSON, SQL, or Markdown Table. Returns number of rows exported."""
    n = 0
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    lower = path.lower()
    if lower.endswith(".json"):
        with open(path, "w", encoding="utf-8") as f:
            f.write("[\n")
            for r in rows:
                obj = dict(zip(cols, (export_value(v) for v in r)))
                f.write((",\n" if n else "") + "  " + json.dumps(obj, ensure_ascii=False))
                n += 1
            f.write("\n]\n")
    elif lower.endswith(".sql"):
        with open(path, "w", encoding="utf-8") as f:
            col_list = ", ".join(quote_ident(c) for c in cols)
            for r in rows:
                vals = [sql_literal(v) for v in r]
                f.write(f"INSERT INTO {quote_ident(table_name)} ({col_list}) VALUES ({', '.join(vals)});\n")
                n += 1
    elif lower.endswith(".md"):
        with open(path, "w", encoding="utf-8") as f:
            f.write("| " + " | ".join(cols) + " |\n")
            f.write("| " + " | ".join("---" for _ in cols) + " |\n")
            for r in rows:
                line_vals = [fmt_value(v).replace("|", "\\|") for v in r]
                f.write("| " + " | ".join(line_vals) + " |\n")
                n += 1
    elif lower.endswith(".tsv"):
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, delimiter="\t")
            w.writerow(cols)
            for r in rows:
                w.writerow([export_value(v) for v in r])
                n += 1
    else:
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for r in rows:
                w.writerow([export_value(v) for v in r])
                n += 1
    return n


def format_cli_table(cols: list[str], rows: list[tuple]) -> str:
    """Renders tabular data with clean Unicode borders for terminal output."""
    if not cols:
        return "(empty)"
    widths = [str_width(c) for c in cols]
    for r in rows:
        for i, v in enumerate(r):
            s = fmt_value(v)
            if str_width(s) > widths[i]:
                widths[i] = str_width(s)
    widths = [min(max(w, 4), 60) for w in widths]
    top_b = "┌─" + "─┬─".join("─" * w for w in widths) + "─┐"
    mid_b = "├─" + "─┼─".join("─" * w for w in widths) + "─┤"
    bot_b = "└─" + "─┴─".join("─" * w for w in widths) + "─┘"
    hdr = "│ " + " │ ".join(pad_str(fit_str(cols[i], widths[i]), widths[i]) for i in range(len(cols))) + " │"
    lines = [top_b, hdr, mid_b]
    for r in rows:
        row_str = "│ " + " │ ".join(pad_str(fit_str(fmt_value(r[i]), widths[i]), widths[i]) for i in range(len(cols))) + " │"
        lines.append(row_str)
    lines.append(bot_b)
    return "\n".join(lines)


def format_query_output(cols: list[str], rows: list[tuple], fmt: str = "table", table_name: str = "results") -> str:
    """Formats query output for non-interactive CLI scripting."""
    fmt = fmt.lower()
    if fmt == "json":
        data = [dict(zip(cols, (export_value(v) for v in r))) for r in rows]
        return json.dumps(data, indent=2, ensure_ascii=False)
    if fmt == "csv":
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(cols)
        for r in rows:
            w.writerow([export_value(v) for v in r])
        return out.getvalue().rstrip()
    if fmt == "tsv":
        out = io.StringIO()
        w = csv.writer(out, delimiter="\t")
        w.writerow(cols)
        for r in rows:
            w.writerow([export_value(v) for v in r])
        return out.getvalue().rstrip()
    if fmt == "sql":
        lines = []
        col_list = ", ".join(quote_ident(c) for c in cols)
        for r in rows:
            vals = [sql_literal(v) for v in r]
            lines.append(f"INSERT INTO {quote_ident(table_name)} ({col_list}) VALUES ({', '.join(vals)});\n")
        return "".join(lines).rstrip()
    if fmt == "md":
        lines = [
            "| " + " | ".join(cols) + " |",
            "| " + " | ".join("---" for _ in cols) + " |"
        ]
        for r in rows:
            lines.append("| " + " | ".join(fmt_value(v).replace("|", "\\|") for v in r) + " |")
        return "\n".join(lines)
    return format_cli_table(cols, rows)


def format_sql(sql: str) -> str:
    """Prettifies and formats SQL queries with capitalized keywords and standard indentation."""
    if not sql.strip():
        return sql
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    formatted_stmts = []
    major_clauses = [
        "SELECT", "FROM", "WHERE", "GROUP BY", "HAVING", "ORDER BY",
        "LIMIT", "OFFSET", "INSERT INTO", "VALUES", "UPDATE", "SET",
        "DELETE FROM", "CREATE TABLE", "CREATE INDEX", "CREATE VIEW",
        "LEFT JOIN", "RIGHT JOIN", "INNER JOIN", "CROSS JOIN", "JOIN", "ON",
        "UNION ALL", "UNION", "EXCEPT", "INTERSECT"
    ]
    major_clauses.sort(key=len, reverse=True)
    pattern = r"\b(" + "|".join(re.escape(k) for k in major_clauses) + r")\b"

    for stmt in statements:
        clean = " ".join(stmt.split())
        res = re.sub(pattern, lambda m: "\n" + m.group(1).upper() + " ", clean, flags=re.IGNORECASE)
        lines = [line.strip() for line in res.splitlines() if line.strip()]
        indented = []
        for line in lines:
            upper = line.upper()
            if any(upper.startswith(k) for k in ("FROM", "WHERE", "GROUP BY", "HAVING", "ORDER BY", "LIMIT", "SET", "VALUES")):
                indented.append("  " + line)
            elif any(upper.startswith(k) for k in ("LEFT JOIN", "RIGHT JOIN", "INNER JOIN", "JOIN", "ON")):
                indented.append("    " + line)
            else:
                indented.append(line)
        formatted_stmts.append("\n".join(indented) + ";")
    return "\n\n".join(formatted_stmts) + "\n"


def osc52_copy(text: str) -> bool:
    """Copies to local clipboard via OSC 52 terminal escape sequence (works over SSH)."""
    data = base64.b64encode(text.encode("utf-8")).decode("ascii")
    try:
        sys.stdout.write(f"\x1b]52;c;{data}\x07")
        sys.stdout.flush()
        return True
    except OSError:
        return False


def load_history() -> list[str]:
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return [s for s in data if isinstance(s, str)][:HISTORY_MAX]
    except (OSError, ValueError):
        return []


def save_history(items: list[str]) -> None:
    try:
        os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def read_key(scr) -> Any:
    """Returns str for printable characters, int for control/function keys."""
    try:
        k = scr.get_wch()
    except curses.error:
        return None
    if isinstance(k, str):
        o = ord(k)
        return k if o >= 32 and o != 127 else o
    return k


def put(scr, y: int, x: int, text: str, attr: int = 0, w: Optional[int] = None) -> None:
    """Writes text clipped to available width without boundary errors."""
    H, W = scr.getmaxyx()
    if y < 0 or y >= H or x < 0 or x >= W:
        return
    avail = W - x if w is None else min(w, W - x)
    if avail <= 0:
        return
    text = fit_str(text, avail)
    try:
        scr.addstr(y, x, text, attr)
    except curses.error:
        pass


def draw_box(scr, y: int, x: int, h: int, w: int, title: str = "", active: bool = False,
             count: Optional[int] = None, badge: str = "") -> None:
    """Draws a lazygit-style rounded border frame with title and focus status."""
    if h < 2 or w < 4:
        return
    border_attr = A["panel_active"] if active else A["panel_dim"]
    title_attr = A["title_active"] if active else A["title_dim"]

    # Compose title with optional count/badge, dropping decorations that do not fit.
    base = f" {title} " if title else ""
    count_part = f"({count})" if count is not None else ""
    badge_part = f"[{badge}] " if badge else ""

    def compose(include_count: bool, include_badge: bool) -> str:
        if include_count and count_part:
            text = f" {title} {count_part} "
        else:
            text = base
        if include_badge:
            text += badge_part
        return text

    title_str = compose(count is not None, bool(badge_part))
    if str_width(title_str) > w - 6:
        title_str = compose(count is not None, False)
    if str_width(title_str) > w - 6:
        title_str = compose(False, False)
    if str_width(title_str) > w - 6:
        title_str = fit_str(title_str, max(1, w - 6))
    t_len = str_width(title_str)
    top_left = "╭"
    top_right = "╮"
    bot_left = "╰"
    bot_right = "╯"
    horiz = "─"
    vert = "│"

    if t_len + 4 <= w:
        prefix = "──"
        suffix_len = w - 2 - 2 - t_len
        suffix = horiz * max(0, suffix_len)
        put(scr, y, x, f"{top_left}{prefix}", border_attr)
        put(scr, y, x + 3, title_str, title_attr)
        put(scr, y, x + 3 + t_len, f"{suffix}{top_right}", border_attr)
    else:
        put(scr, y, x, f"{top_left}{horiz * (w - 2)}{top_right}", border_attr)

    for row in range(1, h - 1):
        try:
            scr.addstr(y + row, x, vert, border_attr)
            scr.addstr(y + row, x + w - 1, vert, border_attr)
        except curses.error:
            pass

    put(scr, y + h - 1, x, f"{bot_left}{horiz * (w - 2)}{bot_right}", border_attr)


def draw_segments(scr, y: int, x: int, w: int, segs: list[tuple[str, int]], left: int) -> None:
    pos = 0
    for text, attr in segs:
        end = pos + len(text)
        a = max(pos, left)
        b = min(end, left + w)
        if a < b:
            put(scr, y, x + (a - left), text[a - pos:b - pos], attr, b - a)
        pos = end


_TOKEN_CACHE: dict[tuple[str, int], list[tuple[str, int]]] = {}


def tokenize(line: str) -> list[tuple[str, int]]:
    if not line:
        return []
    theme_attr = A.get("kw", 0)
    cache_key = (line, theme_attr)
    cached = _TOKEN_CACHE.get(cache_key)
    if cached is not None:
        return cached

    segs = []
    pos = 0
    for m in TOKEN_RE.finditer(line):
        if m.start() > pos:
            segs.append((line[pos:m.start()], 0))
        kind = m.lastindex
        text = m.group(0)
        if kind == 6 and text.upper() in KEYWORDS:
            attr = A["kw"]
        elif kind in (3, 4):
            attr = A["str"]
        elif kind in (1, 2):
            attr = A["comment"]
        elif kind == 5:
            attr = A["num"]
        else:
            attr = 0
        segs.append((text, attr))
        pos = m.end()
    if pos < len(line):
        segs.append((line[pos:], 0))

    if len(_TOKEN_CACHE) > 1024:
        _TOKEN_CACHE.clear()
    _TOKEN_CACHE[cache_key] = segs
    return segs


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

class Column(NamedTuple):
    name: str
    type: str
    notnull: int
    default: Any
    pk: int


class KeyInfo(NamedTuple):
    extra: bool                        # True if query prepends rowid
    keys: list[tuple[str, int]]        # (column, index in raw row)

    @property
    def editable(self) -> bool:
        return bool(self.keys)


class Result(NamedTuple):
    cols: list[str]
    rows: list[tuple]
    message: str
    is_query: bool
    truncated: bool
    elapsed: float


class Db:
    def __init__(self, path: str, readonly: bool = False):
        self.path = path
        self.readonly = readonly
        self._keys: dict[str, KeyInfo] = {}
        self._cols: dict[str, list[Column]] = {}
        self._fks: dict[str, list[tuple]] = {}
        if readonly and path != ":memory:":
            uri = pathlib.Path(path).resolve().as_uri() + "?mode=ro"
            self.conn = sqlite3.connect(uri, uri=True, isolation_level=None)
        else:
            self.conn = sqlite3.connect(path, isolation_level=None)
        self.conn.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self.conn.close()

    def forget(self) -> None:
        self._keys.clear()
        self._cols.clear()
        self._fks.clear()

    def schema(self) -> list[tuple]:
        return self.conn.execute(
            f"SELECT type, name, tbl_name, sql FROM sqlite_master WHERE {USER_OBJECTS} "
            "ORDER BY name COLLATE NOCASE").fetchall()

    def foreign_keys(self, table: str) -> list[tuple]:
        if table not in self._fks:
            try:
                self._fks[table] = self.conn.execute(f"PRAGMA foreign_key_list({quote_ident(table)})").fetchall()
            except sqlite3.Error:
                self._fks[table] = []
        return self._fks[table]

    def columns(self, table: str) -> list[Column]:
        if table not in self._cols:
            rows = self.conn.execute(f"PRAGMA table_info({quote_ident(table)})").fetchall()
            self._cols[table] = [Column(r[1], r[2], r[3], r[4], r[5]) for r in rows]
        return self._cols[table]

    def _has_rowid(self, table: str) -> bool:
        try:
            self.conn.execute(f"SELECT rowid FROM {quote_ident(table)} LIMIT 0").close()
            return True
        except sqlite3.Error:
            return False

    def key_info(self, table: str) -> KeyInfo:
        if table not in self._keys:
            if self._has_rowid(table):
                info = KeyInfo(True, [("rowid", 0)])
            else:
                cols = self.columns(table)
                pks = sorted((c.pk, i, c.name) for i, c in enumerate(cols) if c.pk)
                info = KeyInfo(False, [(name, i) for _, i, name in pks])
            self._keys[table] = info
        return self._keys[table]

    @staticmethod
    def table_sql(table: str, key: KeyInfo, where: str = "", order: Optional[str] = None,
                  desc: bool = False, limit: Optional[int] = None, offset: int = 0) -> str:
        sql = f"SELECT {'rowid, ' if key.extra else ''}* FROM {quote_ident(table)}"
        if where.strip():
            sql += f" WHERE ({where})"
        if order:
            sql += f" ORDER BY {quote_ident(order)}{' DESC' if desc else ''}"
        if limit is not None:
            sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
        return sql

    def query_table(self, table: str, key: KeyInfo, where: str, order: Optional[str], desc: bool,
                    limit: int, offset: int) -> tuple[list[str], list[tuple]]:
        cur = self.conn.execute(self.table_sql(table, key, where, order, desc, limit, offset))
        cols = [d[0] for d in cur.description]
        return cols, cur.fetchall()

    def count(self, table: str, where: str = "") -> int:
        sql = f"SELECT COUNT(*) FROM {quote_ident(table)}"
        if where.strip():
            sql += f" WHERE ({where})"
        return self.conn.execute(sql).fetchone()[0]

    def update_cell(self, table: str, column: str, value: Any, key: KeyInfo, keyvals: tuple) -> int:
        cond = " AND ".join(f"{quote_ident(k)} IS ?" for k, _ in key.keys)
        sql = f"UPDATE {quote_ident(table)} SET {quote_ident(column)} = ? WHERE {cond}"
        return self.conn.execute(sql, [value, *keyvals]).rowcount

    def delete_row(self, table: str, key: KeyInfo, keyvals: tuple) -> int:
        cond = " AND ".join(f"{quote_ident(k)} IS ?" for k, _ in key.keys)
        return self.conn.execute(f"DELETE FROM {quote_ident(table)} WHERE {cond}", list(keyvals)).rowcount

    def insert_row(self, table: str, values: dict[str, Any]) -> int:
        if values:
            names = ", ".join(quote_ident(n) for n in values)
            marks = ", ".join("?" * len(values))
            sql = f"INSERT INTO {quote_ident(table)} ({names}) VALUES ({marks})"
            return self.conn.execute(sql, list(values.values())).rowcount
        return self.conn.execute(f"INSERT INTO {quote_ident(table)} DEFAULT VALUES").rowcount

    def table_undo_inserts(self, table: str, where: str = "", limit: Optional[int] = None) -> list[str]:
        schema_cols = [c.name for c in self.columns(table)]
        if not schema_cols:
            return []
        has_rowid = self._has_rowid(table)
        has_int_pk = any(c.pk for c in self.columns(table) if "INT" in c.type.upper())
        include_rowid = has_rowid and not has_int_pk and ("rowid" not in [c.lower() for c in schema_cols])
        cols_to_insert = (["rowid"] + schema_cols) if include_rowid else schema_cols
        col_str = ", ".join(quote_ident(c) for c in cols_to_insert)
        query = f"SELECT {'rowid, ' if include_rowid else ''}* FROM {quote_ident(table)}"
        if where.strip():
            query += f" WHERE ({where})"
        if limit is not None:
            query += f" LIMIT {int(limit)}"
        try:
            cur = self.conn.execute(query)
            stmts = []
            for r in cur:
                val_str = ", ".join(sql_literal(v) for v in r)
                stmts.append(f"INSERT INTO {quote_ident(table)} ({col_str}) VALUES ({val_str});")
            cur.close()
            return stmts
        except sqlite3.Error:
            return []

    def run(self, sql: str, params: tuple = ()) -> Result:
        t0 = time.perf_counter()
        cur = self.conn.execute(sql, params)
        try:
            if cur.description:
                cols = [d[0] for d in cur.description]
                rows = cur.fetchmany(MAX_ROWS + 1)
                truncated = len(rows) > MAX_ROWS
                rows = rows[:MAX_ROWS]
                return Result(cols, rows, f"{len(rows)} rows", True, truncated, time.perf_counter() - t0)
            msg = f"{cur.rowcount} row(s) affected" if cur.rowcount >= 0 else "OK"
            return Result([], [], msg, False, False, time.perf_counter() - t0)
        finally:
            cur.close()


def table_lines(db: Db, kind: str, name: str) -> list[str]:
    lines = [f"# {kind} {name}"]
    if kind == "table":
        lines.append(f"Rows: {db.count(name):,}")
    lines += ["", "# Columns",
              f"{'#':>3}  {'name':<24} {'type':<14} {'null':<5} {'pk':<3} default"]
    for i, c in enumerate(db.columns(name)):
        default = "" if c.default is None else c.default
        lines.append(f"{i:>3}  {c.name:<24} {(c.type or '-'):<14} "
                     f"{'no' if c.notnull else 'yes':<5} {c.pk or '':<3} {default}")
    if kind == "table":
        q = quote_ident(name)
        lines += ["", "# Indexes"]
        idx = db.conn.execute(f"PRAGMA index_list({q})").fetchall()
        if not idx:
            lines.append("(none)")
        for _seq, iname, uniq, origin, _partial in idx:
            cols = [r[2] for r in db.conn.execute(f"PRAGMA index_info({quote_ident(iname)})")]
            flag = "UNIQUE  " if uniq else ""
            lines.append(f"{iname}  ({', '.join(str(c) for c in cols)})  {flag}origin={origin}")
        lines += ["", "# Outgoing Foreign Keys"]
        fks = db.conn.execute(f"PRAGMA foreign_key_list({q})").fetchall()
        if not fks:
            lines.append("(none)")
        for fk in fks:
            lines.append(f"{fk[3]} -> {fk[2]}({fk[4] or '?'})  ON UPDATE {fk[5]}  ON DELETE {fk[6]}")
        lines += ["", "# Referenced By"]
        found = False
        for (other,) in db.conn.execute(f"SELECT name FROM sqlite_master WHERE type = 'table' AND {USER_OBJECTS}"):
            for fk in db.conn.execute(f"PRAGMA foreign_key_list({quote_ident(other)})"):
                if str(fk[2]).lower() == name.lower():
                    lines.append(f"{other}.{fk[3]} -> {name}({fk[4] or '?'})")
                    found = True
        if not found:
            lines.append("(no references)")
    row = db.conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (name,)).fetchone()
    lines += ["", "# SQL"] + ((row[0] or "").splitlines() if row else [])
    return lines


def describe(db: Db, kind: str, name: str) -> list[str]:
    if kind in ("table", "view"):
        return table_lines(db, kind, name)
    row = db.conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (name,)).fetchone()
    body = (row[0] or "(no SQL)").splitlines() if row else ["(not found)"]
    return [f"# {kind} {name}", ""] + body


def db_info(db: Db) -> list[str]:
    c = db.conn

    def pragma(name: str) -> Any:
        return c.execute(f"PRAGMA {name}").fetchone()[0]

    page_count = pragma("page_count") or 0
    page_size = pragma("page_size") or 0
    calc_size = page_count * page_size
    size = os.path.getsize(db.path) if db.path != ":memory:" and os.path.exists(db.path) else 0
    size_str = f"{size:,} bytes" if size > 0 else f"~{calc_size:,} bytes (in-memory)"

    counts = dict(c.execute("SELECT type, COUNT(*) FROM sqlite_master GROUP BY type").fetchall())
    lines = [
        "# Database Information",
        f"Path: {db.path}",
        f"Mode: {'read-only' if db.readonly else 'read-write'}",
        f"Size: {size_str}",
        f"Pages: {page_count} x {page_size} bytes",
        f"Free pages: {pragma('freelist_count')}",
        f"Encoding: {pragma('encoding')}",
        f"Journal mode: {pragma('journal_mode')}",
        f"user_version: {pragma('user_version')}",
        f"Foreign keys: {'ON' if pragma('foreign_keys') else 'OFF'}",
        f"SQLite version: {sqlite3.sqlite_version}",
        "",
        "# Objects",
    ]
    lines += [f"{k}: {v}" for k, v in sorted(counts.items())] or ["(empty)"]
    return lines


def populate_demo_db(db: Db) -> None:
    """Populates an interactive, highly relational e-commerce demo database."""
    c = db.conn
    c.execute("PRAGMA foreign_keys = ON")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        description TEXT
    );

    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
        sku TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        price REAL NOT NULL,
        stock INTEGER DEFAULT 0,
        specs_json TEXT
    );

    CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        city TEXT,
        country TEXT DEFAULT 'Italy',
        registered_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
        order_date TEXT DEFAULT CURRENT_TIMESTAMP,
        status TEXT CHECK(status IN ('pending', 'processing', 'completed', 'cancelled')) DEFAULT 'pending',
        total_amount REAL DEFAULT 0.0,
        notes TEXT
    );

    CREATE TABLE IF NOT EXISTS order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
        product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
        quantity INTEGER NOT NULL CHECK(quantity > 0),
        unit_price REAL NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_products_cat ON products(category_id);
    CREATE INDEX IF NOT EXISTS idx_orders_cust ON orders(customer_id);
    CREATE INDEX IF NOT EXISTS idx_items_order ON order_items(order_id);
    CREATE INDEX IF NOT EXISTS idx_items_prod ON order_items(product_id);

    DROP VIEW IF EXISTS v_customer_orders;
    CREATE VIEW v_customer_orders AS
    SELECT 
        o.id AS order_id,
        c.name AS customer_name,
        c.city,
        o.order_date,
        o.status,
        o.total_amount,
        COUNT(oi.id) AS item_count
    FROM orders o
    JOIN customers c ON o.customer_id = c.id
    LEFT JOIN order_items oi ON o.id = oi.order_id
    GROUP BY o.id;

    DROP TRIGGER IF EXISTS trg_calc_order_total;
    CREATE TRIGGER trg_calc_order_total
    AFTER INSERT ON order_items
    BEGIN
        UPDATE orders 
        SET total_amount = (
            SELECT COALESCE(SUM(quantity * unit_price), 0.0)
            FROM order_items
            WHERE order_id = NEW.order_id
        )
        WHERE id = NEW.order_id;
    END;
    """)

    has_cats = c.execute("SELECT 1 FROM categories LIMIT 1").fetchone()
    if not has_cats:
        cats = [
            ("Electronics", "Computers, audio, displays and smart accessories"),
            ("Books", "Technical programming guides, sci-fi and literature"),
            ("Home & Kitchen", "Modern smart home gear, espresso machines and accessories"),
            ("Sports & Fitness", "Cycling equipment, hiking gear and running shoes"),
            ("Gaming", "Mechanical keyboards, monitors and gaming controllers"),
        ]
        c.executemany("INSERT INTO categories (name, description) VALUES (?, ?)", cats)

        prods = [
            (1, "ELEC-MBP-16", "MacBook Pro M3 Max", 3499.00, 14, json.dumps({"ram_gb": 36, "storage_tb": 1, "cpu_cores": 14, "color": "Space Black"})),
            (1, "ELEC-WH-1000", "Sony WH-1000XM5 ANC", 379.90, 45, json.dumps({"anc": True, "battery_hours": 30, "codec": "LDAC", "bluetooth": "5.2"})),
            (1, "ELEC-DELL-4K", "Dell UltraSharp 32 4K", 899.00, 22, json.dumps({"resolution": "3840x2160", "panel": "IPS Black", "usb_c_pd_w": 90})),
            (2, "BOOK-DDIA", "Designing Data-Intensive Apps", 48.50, 80, json.dumps({"author": "Martin Kleppmann", "pages": 616, "publisher": "OReilly"})),
            (2, "BOOK-RUST", "The Rust Programming Language", 39.99, 65, json.dumps({"author": "Steve Klabnik", "edition": "2nd", "pages": 560})),
            (3, "HOME-BREV", "Breville Barista Touch Impress", 1299.00, 8, json.dumps({"auto_milking": True, "touchscreen": True, "heatup_sec": 3})),
            (4, "SPRT-GARMIN", "Garmin Forerunner 965", 599.99, 19, json.dumps({"display": "AMOLED", "gps": "Multi-band", "battery_days": 23})),
            (5, "GAME-KEYB", "Keychron Q1 Pro Wireless", 199.00, 35, json.dumps({"layout": "75%", "switches": "Gateron Jupiter Red", "hot_swap": True})),
        ]
        c.executemany("INSERT INTO products (category_id, sku, name, price, stock, specs_json) VALUES (?, ?, ?, ?, ?, ?)", prods)

        custs = [
            ("Marco Rossi", "m.rossi@example.it", "Milano", "Italy"),
            ("Giulia Bianchi", "g.bianchi@example.it", "Roma", "Italy"),
            ("Luca Moretti", "luca.moretti@example.com", "Torino", "Italy"),
            ("Elena Ferrari", "elena.f@example.org", "Bologna", "Italy"),
            ("Matteo Conti", "matteo.c@example.it", "Firenze", "Italy"),
        ]
        c.executemany("INSERT INTO customers (name, email, city, country) VALUES (?, ?, ?, ?)", custs)

        orders = [
            (1, "2026-10-18 10:30:00", "completed", 0.0, "Express priority shipping"),
            (2, "2026-10-19 14:15:00", "processing", 0.0, "Leave at front desk"),
            (3, "2026-10-20 09:00:00", "completed", 0.0, None),
            (4, "2026-10-20 16:45:00", "pending", 0.0, "Invoice requested"),
            (5, "2026-10-21 07:10:00", "completed", 0.0, None),
        ]
        c.executemany("INSERT INTO orders (customer_id, order_date, status, total_amount, notes) VALUES (?, ?, ?, ?, ?)", orders)

        items = [
            (1, 1, 1, 3499.00),
            (1, 2, 1, 379.90),
            (2, 4, 2, 48.50),
            (2, 5, 1, 39.99),
            (3, 6, 1, 1299.00),
            (4, 7, 1, 599.99),
            (5, 8, 2, 199.00),
        ]
        c.executemany("INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES (?, ?, ?, ?)", items)


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

class Editor:
    def __init__(self):
        self.lines = [""]
        self.cy = self.cx = self.top = self.left = 0
        self.h = 5
        self.kill = ""
        self.cursor: Optional[tuple[int, int]] = None
        self.undo_stack: list[tuple[list[str], int, int]] = []
        self.redo_stack: list[tuple[list[str], int, int]] = []
        self._in_typing = False

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def save_undo(self) -> None:
        state = (list(self.lines), self.cy, self.cx)
        if not self.undo_stack or self.undo_stack[-1][0] != state[0]:
            self.undo_stack.append(state)
            if len(self.undo_stack) > 150:
                self.undo_stack.pop(0)
            self.redo_stack.clear()

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        self.redo_stack.append((list(self.lines), self.cy, self.cx))
        lines, cy, cx = self.undo_stack.pop()
        self.lines = lines
        self.cy = max(0, min(cy, len(self.lines) - 1))
        self.cx = max(0, min(cx, len(self.lines[self.cy])))
        return True

    def redo(self) -> bool:
        if not self.redo_stack:
            return False
        self.undo_stack.append((list(self.lines), self.cy, self.cx))
        lines, cy, cx = self.redo_stack.pop()
        self.lines = lines
        self.cy = max(0, min(cy, len(self.lines) - 1))
        self.cx = max(0, min(cx, len(self.lines[self.cy])))
        return True

    def set_text(self, text: str) -> None:
        self.lines = text.split("\n") or [""]
        self.cy = len(self.lines) - 1
        self.cx = len(self.lines[-1])
        self.top = self.left = 0
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._in_typing = False

    def offset(self) -> int:
        return sum(len(line) + 1 for line in self.lines[:self.cy]) + self.cx

    def insert_text(self, text: str) -> None:
        line = self.lines[self.cy]
        head, tail = line[:self.cx], line[self.cx:]
        parts = text.split("\n")
        if len(parts) == 1:
            self.lines[self.cy] = head + text + tail
            self.cx += len(text)
            return
        self.lines[self.cy] = head + parts[0]
        self.lines[self.cy + 1:self.cy + 1] = parts[1:-1] + [parts[-1] + tail]
        self.cy += len(parts) - 1
        self.cx = len(parts[-1])

    def newline(self) -> None:
        indent = re.match(r"\s*", self.lines[self.cy]).group(0)
        self.insert_text("\n" + indent)

    def backspace(self) -> None:
        if self.cx > 0:
            line = self.lines[self.cy]
            self.lines[self.cy] = line[:self.cx - 1] + line[self.cx:]
            self.cx -= 1
        elif self.cy > 0:
            prev = self.lines[self.cy - 1]
            self.cx = len(prev)
            self.lines[self.cy - 1] = prev + self.lines.pop(self.cy)
            self.cy -= 1

    def delete(self) -> None:
        line = self.lines[self.cy]
        if self.cx < len(line):
            self.lines[self.cy] = line[:self.cx] + line[self.cx + 1:]
        elif self.cy < len(self.lines) - 1:
            self.lines[self.cy] += self.lines.pop(self.cy + 1)

    def kill_line(self) -> None:
        self.kill = self.lines[self.cy]
        if len(self.lines) > 1:
            self.lines.pop(self.cy)
            self.cy = min(self.cy, len(self.lines) - 1)
        else:
            self.lines[0] = ""
        self.cx = min(self.cx, len(self.lines[self.cy]))

    def delete_word_backward(self) -> None:
        line = self.lines[self.cy]
        if self.cx == 0:
            if self.cy > 0:
                self.backspace()
            return
        i = self.cx
        while i > 0 and line[i - 1].isspace():
            i -= 1
        while i > 0 and not line[i - 1].isspace():
            i -= 1
        del_count = self.cx - i
        for _ in range(del_count):
            self.backspace()

    def kill_to_start(self) -> None:
        line = self.lines[self.cy]
        self.lines[self.cy] = line[self.cx:]
        self.cx = 0

    def replace_before(self, n: int, text: str) -> None:
        self.save_undo()
        for _ in range(n):
            self.backspace()
        self.insert_text(text)

    def handle_key(self, key: Any) -> bool:
        line = self.lines[self.cy]
        if key == curses.KEY_LEFT:
            self._in_typing = False
            if self.cx > 0:
                self.cx -= 1
            elif self.cy > 0:
                self.cy -= 1
                self.cx = len(self.lines[self.cy])
        elif key == curses.KEY_RIGHT:
            self._in_typing = False
            if self.cx < len(line):
                self.cx += 1
            elif self.cy < len(self.lines) - 1:
                self.cy += 1
                self.cx = 0
        elif key in (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_PPAGE, curses.KEY_NPAGE):
            self._in_typing = False
            step = {curses.KEY_UP: -1, curses.KEY_DOWN: 1,
                    curses.KEY_PPAGE: -self.h, curses.KEY_NPAGE: self.h}[key]
            self.cy = max(0, min(len(self.lines) - 1, self.cy + step))
            self.cx = min(self.cx, len(self.lines[self.cy]))
        elif key in (curses.KEY_HOME, CTRL_A):
            self._in_typing = False
            self.cx = 0
        elif key in (curses.KEY_END, CTRL_E):
            self._in_typing = False
            self.cx = len(line)
        elif key == CTRL_Z:
            self._in_typing = False
            self.undo()
        elif key == CTRL_R:
            self._in_typing = False
            self.redo()
        elif key in KEY_BACK:
            self._in_typing = False
            self.save_undo()
            self.backspace()
        elif key in (curses.KEY_DC, CTRL_D):
            self._in_typing = False
            self.save_undo()
            self.delete()
        elif key == CTRL_W:
            self._in_typing = False
            self.save_undo()
            self.delete_word_backward()
        elif key == CTRL_U:
            self._in_typing = False
            self.save_undo()
            self.kill_to_start()
        elif key in KEY_ENTER:
            self._in_typing = False
            self.save_undo()
            self.newline()
        elif key == KEY_TAB:
            self._in_typing = False
            self.save_undo()
            self.insert_text("    ")
        elif key == CTRL_K:
            self._in_typing = False
            self.save_undo()
            self.kill_line()
        elif key == CTRL_Y:
            self._in_typing = False
            self.save_undo()
            self.insert_text(self.kill)
        elif isinstance(key, str):
            if not self._in_typing:
                self.save_undo()
                self._in_typing = True
            self.insert_text(key)
        else:
            return False
        return True

    def draw(self, scr, y: int, x: int, h: int, w: int, focused: bool) -> None:
        self.h = h
        num_w = len(str(max(1, len(self.lines))))
        gutter = num_w + 2                       # marker + line number + space
        tw = max(1, w - gutter)
        if self.cy < self.top:
            self.top = self.cy
        if self.cy >= self.top + h:
            self.top = self.cy - h + 1
        if self.cx < self.left:
            self.left = self.cx
        if self.cx >= self.left + tw:
            self.left = self.cx - tw + 1
        for row in range(h):
            i = self.top + row
            if i >= len(self.lines):
                break
            current = i == self.cy
            num_attr = A["tab_on"] if (current and focused) else (A["hdr"] if current else A["dim"])
            put(scr, y + row, x, "▸" if current else " ", num_attr, 1)
            put(scr, y + row, x + 1, f"{i + 1:>{num_w}}", num_attr, num_w)
            put(scr, y + row, x + 1 + num_w, " ", num_attr, 1)
            draw_segments(scr, y + row, x + gutter, tw, tokenize(self.lines[i]), self.left)
        self.cursor = (y + self.cy - self.top, x + gutter + self.cx - self.left) if focused else None


class ColumnList:
    """Panel [2]: Inspector for columns, types, primary and foreign keys."""
    def __init__(self):
        self.table = ""
        self.cols: list[Column] = []
        self.fks: list[tuple] = []
        self.cy = self.top = 0
        self.h = 5

    def set_table(self, db: Db, table: str) -> None:
        if self.table == table and self.cols:
            return
        self.table = table
        self.cy = self.top = 0
        try:
            self.cols = db.columns(table)
            self.fks = db.foreign_keys(table)
        except sqlite3.Error:
            self.cols = []
            self.fks = []

    def selected(self) -> Optional[Column]:
        if 0 <= self.cy < len(self.cols):
            return self.cols[self.cy]
        return None

    def handle_key(self, key: Any) -> bool:
        if key in (curses.KEY_UP, "k"):
            self.cy = max(0, self.cy - 1)
        elif key in (curses.KEY_DOWN, "j"):
            self.cy = min(max(0, len(self.cols) - 1), self.cy + 1)
        elif key in ("g", curses.KEY_HOME):
            self.cy = 0
        elif key in ("G", curses.KEY_END):
            self.cy = max(0, len(self.cols) - 1)
        else:
            return False
        return True

    def draw(self, scr, y: int, x: int, h: int, w: int, focused: bool) -> None:
        self.h = h
        if not self.cols:
            put(scr, y, x, "  (select a table in panel [1])", A["dim"], w)
            return
        if self.cy < self.top:
            self.top = self.cy
        if self.cy >= self.top + h:
            self.top = self.cy - h + 1
        fk_map = {fk[3]: f"{fk[2]}.{fk[4] or 'id'}" for fk in self.fks}
        name_w = max(6, min(max(str_width(c.name) for c in self.cols), max(8, w // 3)))
        type_w = max(6, min(12, w - name_w - 7))
        show_notes = w >= 34
        for r in range(h):
            i = self.top + r
            if i >= len(self.cols):
                break
            c = self.cols[i]
            pk = "PK" if c.pk else "  "
            name = pad_str(fit_str(c.name, name_w), name_w)
            decl = pad_str(fit_str(c.type or "ANY", type_w), type_w)
            line_str = f"{pk} {name} {decl}"
            if show_notes:
                notes = []
                if c.notnull:
                    notes.append("NOT NULL")
                if c.default is not None:
                    notes.append(f"={c.default}")
                if c.name in fk_map:
                    notes.append(f"→ {fk_map[c.name]}")
                if notes:
                    line_str += " " + " · ".join(notes)
            else:
                if c.name in fk_map and str_width(line_str) + 2 <= w:
                    line_str += " →"
                if c.notnull and str_width(line_str) + 3 <= w:
                    line_str += " NN"
            attr = (A["sel"] if focused else A["underline"]) if i == self.cy else 0
            put(scr, y + r, x, pad_str(line_str, w), attr, w)


class Grid:
    def __init__(self):
        self.cols: list[str] = []
        self.col_types: dict[str, str] = {}
        self.pks: set[str] = set()
        self.fks: list[tuple] = []
        self.rows: list[tuple] = []
        self._orig_rows: list[tuple] = []
        self.cy = self.cx = self.top = self.left = 0
        self.widths: list[int] = []
        self.view_h = 10
        self.sort: tuple[Optional[str], bool] = (None, False)
        self.search_query = ""
        self.search_matches: list[tuple[int, int]] = []
        self.search_idx = -1
        self.table_mode = False
        self.row_offset = 0

    def set_data(self, cols: list[str], rows: list[tuple], keep: bool = False,
                 col_types: Optional[dict[str, str]] = None, pks: Optional[set[str]] = None,
                 fks: Optional[list[tuple]] = None, table_mode: bool = False) -> None:
        self.cols, self.rows = cols, rows
        self._orig_rows = list(rows)
        self.col_types = col_types or {}
        self.pks = pks or set()
        self.fks = fks or []
        self.table_mode = table_mode
        if not keep:
            self.cy = self.top = self.left = 0
        self.cy = max(0, min(self.cy, len(rows) - 1)) if rows else 0
        self.cx = max(0, min(self.cx, len(cols) - 1)) if cols else 0
        self.widths = []
        for i, c in enumerate(cols):
            extra = 4
            if c in self.pks:
                extra += 3
            if self.fk_for_col(c):
                extra += 3
            w = max(str_width(c) + extra, 6)
            for r in rows[:300]:
                if w >= MAX_COL_WIDTH:
                    break
                w = max(w, preview_len(r[i], MAX_COL_WIDTH))
            self.widths.append(min(w, MAX_COL_WIDTH))
        if self.search_query:
            self.search(self.search_query)

    def fk_for_col(self, col_name: str) -> Optional[tuple]:
        for fk in self.fks:
            if fk[3].lower() == col_name.lower():
                return fk
        return None

    def current(self) -> Optional[tuple]:
        return self.rows[self.cy] if self.rows else None

    def _span(self, a: int, b: int) -> int:
        return sum(self.widths[i] + 1 for i in range(a, b + 1))

    def sort_by_col(self, col_idx: int) -> None:
        """Sorts current results in memory by selected column."""
        if not self.cols or not self.rows or col_idx < 0 or col_idx >= len(self.cols):
            return
        col_name = self.cols[col_idx]
        cur_col, cur_desc = self.sort
        if cur_col != col_name:
            new_desc = False
        elif not cur_desc:
            new_desc = True
        else:
            self.sort = (None, False)
            self.rows = list(self._orig_rows)
            return
        self.sort = (col_name, new_desc)

        def sort_key(r):
            v = r[col_idx]
            if v is None:
                return (0, "")
            if isinstance(v, (int, float)):
                return (1, v)
            return (2, str(v).lower())

        self.rows.sort(key=sort_key, reverse=new_desc)

    def search(self, query: str) -> int:
        self.search_query = query.lower()
        self.search_matches = []
        if not query:
            self.search_idx = -1
            return 0
        for r_idx, row in enumerate(self.rows):
            for c_idx, val in enumerate(row):
                if self.search_query in str(val).lower():
                    self.search_matches.append((r_idx, c_idx))
        if self.search_matches:
            self.search_idx = 0
            self.cy, self.cx = self.search_matches[0]
        else:
            self.search_idx = -1
        return len(self.search_matches)

    def next_match(self, step: int = 1) -> bool:
        if not self.search_matches:
            return False
        self.search_idx = (self.search_idx + step) % len(self.search_matches)
        self.cy, self.cx = self.search_matches[self.search_idx]
        return True

    def handle_key(self, key: Any) -> Optional[str]:
        if key in (curses.KEY_UP, "k"):
            if self.cy > 0:
                self.cy -= 1
            else:
                return "up"
        elif key in (curses.KEY_DOWN, "j"):
            if self.cy < len(self.rows) - 1:
                self.cy += 1
            else:
                return "down"
        elif key in (curses.KEY_LEFT, "h"):
            self.cx = max(0, self.cx - 1)
        elif key in (curses.KEY_RIGHT, "l"):
            self.cx = min(len(self.cols) - 1, self.cx + 1)
        elif key == curses.KEY_HOME:
            self.cx = 0
        elif key == curses.KEY_END:
            self.cx = max(0, len(self.cols) - 1)
        elif key in ("g",):
            self.cy = 0
        elif key in ("G",):
            self.cy = max(0, len(self.rows) - 1)
        elif key == curses.KEY_PPAGE:
            self.cy = max(0, self.cy - self.view_h)
        elif key == curses.KEY_NPAGE:
            self.cy = max(0, min(len(self.rows) - 1, self.cy + self.view_h))
        else:
            return None
        return "ok"

    def draw(self, scr, y: int, x: int, h: int, w: int, focused: bool) -> None:
        self.view_h = max(1, h - 1)
        if not self.cols:
            put(scr, y, x, "  (no results — run a query with F5 or open a table with Enter)", A["dim"], w)
            return
        if self.cy < self.top:
            self.top = self.cy
        if self.cy >= self.top + self.view_h:
            self.top = self.cy - self.view_h + 1

        # Row-number gutter keeps the grid easy to scan and to talk about.
        last_num = self.row_offset + max(1, len(self.rows))
        num_w = max(2, len(str(last_num)))
        gutter = num_w + 2
        avail = max(1, w - gutter)

        if self.left > self.cx:
            self.left = self.cx
        while self.left < self.cx and self._span(self.left, self.cx) > avail:
            self.left += 1

        sort_col, sort_desc = self.sort
        put(scr, y, x, " " + pad_str("#", num_w, "right") + " ", A["dim"], gutter)
        col = x + gutter
        last_col = len(self.cols) - 1
        for i in range(self.left, len(self.cols)):
            if col >= x + w:
                break
            cw = min(self.widths[i], x + w - col)
            raw_name = self.cols[i]
            t = self.col_types.get(raw_name, "").upper()
            if any(k in t for k in ("INT", "NUM")):
                glyph = "#"
            elif any(k in t for k in ("REAL", "DOUB", "FLOA")):
                glyph = "$"
            elif "BLOB" in t:
                glyph = "B"
            else:
                glyph = "T"
            badges = []
            if raw_name in self.pks:
                badges.append("PK")
            if self.fk_for_col(raw_name):
                badges.append("FK")
            name = raw_name + ((" " + " ".join(badges)) if badges else "") + f" {glyph}"
            if raw_name == sort_col:
                name += " ▼" if sort_desc else " ▲"
            put(scr, y, col, pad_str(fit_str(name, cw), cw), A["hdr"], cw)
            col += self.widths[i]
            if i < last_col and col < x + w:
                put(scr, y, col, "│", A["dim"], 1)
            col += 1

        if not self.rows:
            hint = ("  (empty table — press 'n' to insert a row)"
                    if self.table_mode else "  (no rows returned)")
            put(scr, y + 1, x, hint, A["dim"], w)
            return

        for r in range(self.view_h):
            ri = self.top + r
            if ri >= len(self.rows):
                break
            row = self.rows[ri]
            sel_row = ri == self.cy
            row_attr = A["sel"] if focused else A["underline"]
            if sel_row:
                put(scr, y + 1 + r, x, " " * w, row_attr, w)
            put(scr, y + 1 + r, x, " " + pad_str(f"{self.row_offset + ri + 1:,}", num_w, "right") + " ",
                row_attr if sel_row else A["dim"], gutter)
            col = x + gutter
            for i in range(self.left, len(self.cols)):
                if col >= x + w:
                    break
                cw = min(self.widths[i], x + w - col)
                v = row[i]
                padded = pad_str(fit_str(fmt_value(v), cw), cw)
                is_match = bool(self.search_query and self.search_query in str(v).lower())
                if sel_row:
                    attr = (A["tab_on"] | curses.A_BOLD) if (focused and i == self.cx) else row_attr
                else:
                    attr = A["null"] if v is None else 0
                    if is_match:
                        attr = A["warn"]
                put(scr, y + 1 + r, col, padded, attr, cw)
                col += self.widths[i]
                if i < last_col and col < x + w:
                    put(scr, y + 1 + r, col, "│", row_attr if sel_row else A["dim"], 1)
                col += 1


class TextView:
    def __init__(self):
        self.lines: list[str] = []
        self.top = 0
        self.h = 10

    def set(self, lines: list[str]) -> None:
        self.lines = list(lines)
        self.top = 0

    def handle_key(self, key: Any) -> bool:
        n = len(self.lines)
        last = max(0, n - self.h)
        if key in (curses.KEY_UP, "k"):
            self.top = max(0, self.top - 1)
        elif key in (curses.KEY_DOWN, "j"):
            self.top = min(last, self.top + 1)
        elif key == curses.KEY_PPAGE:
            self.top = max(0, self.top - self.h)
        elif key == curses.KEY_NPAGE:
            self.top = min(last, self.top + self.h)
        elif key in (curses.KEY_HOME, "g"):
            self.top = 0
        elif key in (curses.KEY_END, "G"):
            self.top = last
        else:
            return False
        return True

    def draw(self, scr, y: int, x: int, h: int, w: int) -> None:
        self.h = max(1, h)
        for r in range(h):
            i = self.top + r
            if i >= len(self.lines):
                break
            line = self.lines[i]
            if line.startswith("# "):
                put(scr, y + r, x, line[2:], A["hdr"], w)
            else:
                put(scr, y + r, x, line, 0, w)


class Sidebar:
    """Panel [1]: SQLite schema explorer with row counts and live filter."""
    def __init__(self):
        self.groups: list[tuple[str, str, list[str]]] = []
        self.table_counts: dict[str, Optional[int]] = {}
        self.cy = self.top = 0
        self.h = 10
        self.filter = ""
        self._cached_rows: Optional[list[tuple[str, str]]] = None

    def invalidate(self) -> None:
        self._cached_rows = None

    def rows(self) -> list[tuple[str, str]]:
        if self._cached_rows is not None:
            return self._cached_rows
        out = []
        f = self.filter.lower()
        for title, kind, names in self.groups:
            items = [n for n in names if f in n.lower()] if f else names
            if not items:
                continue
            if f:
                out.append(("hdr", f"{title} ({len(items)}/{len(names)})"))
            else:
                out.append(("hdr", f"{title} ({len(items)})"))
            out.extend((kind, n) for n in items)
        self._cached_rows = out
        return out

    def count(self) -> int:
        return sum(1 for row in self.rows() if row[0] != "hdr")

    def move(self, delta: int, rows: list[tuple[str, str]]) -> None:
        if not rows:
            return
        i = max(0, min(len(rows) - 1, self.cy + delta))
        step = 1 if delta >= 0 else -1
        j = i
        while 0 <= j < len(rows) and rows[j][0] == "hdr":
            j += step
        if 0 <= j < len(rows):
            self.cy = j
            return
        j = i
        while 0 <= j < len(rows) and rows[j][0] == "hdr":
            j -= step
        if 0 <= j < len(rows):
            self.cy = j

    def selected(self) -> Optional[tuple[str, str]]:
        rows = self.rows()
        if 0 <= self.cy < len(rows) and rows[self.cy][0] != "hdr":
            return rows[self.cy]
        return None

    def draw(self, scr, y: int, x: int, h: int, w: int, focused: bool) -> None:
        self.h = h
        rows = self.rows()
        self.cy = max(0, min(self.cy, len(rows) - 1)) if rows else 0
        if rows and rows[self.cy][0] == "hdr":
            self.move(1, rows)
        if self.cy < self.top:
            self.top = self.cy
        if self.cy >= self.top + h:
            self.top = self.cy - h + 1
        if not rows:
            put(scr, y, x, "  (no objects — press 'r' to refresh)", A["dim"], w)
            return
        for r in range(h):
            i = self.top + r
            if i >= len(rows):
                break
            kind, name = rows[i]
            if kind == "hdr":
                put(scr, y + r, x, pad_str(f"▼ {name}", w), A["hdr"], w)
                continue
            cnt = self.table_counts.get(name)
            glyph = "●" if kind == "table" else ("○" if kind == "view" else ("⚡" if kind == "index" else "⚙"))
            left = f"  {glyph} {name}"
            right = f"{cnt:,} " if cnt is not None else ""
            attr = (A["sel"] if focused else A["underline"]) if i == self.cy else 0
            if right and str_width(left) + str_width(right) + 2 <= w:
                put(scr, y + r, x, pad_str(left, w - str_width(right)), attr, w - str_width(right))
                put(scr, y + r, x + w - str_width(right), right, attr, str_width(right))
            else:
                put(scr, y + r, x, pad_str(left, w), attr, w)


# ---------------------------------------------------------------------------
# Colors and Theme Management
# ---------------------------------------------------------------------------

def apply_theme(theme_key: str) -> None:
    theme = THEMES.get(theme_key, THEMES["tokyo-night"])
    if not curses.has_colors():
        return
    bg = -1
    specs = [
        ("title_active", curses.COLOR_BLACK, theme["panel_active"]),
        ("title_dim", theme["panel_dim"], bg),
        ("panel_active", theme["panel_active"], bg),
        ("panel_dim", theme["panel_dim"], bg),
        ("bar", theme["bar_fg"], theme["bar_bg"]),
        ("tab_on", curses.COLOR_BLACK, theme["accent"]),
        ("prompt", curses.COLOR_BLACK, theme["accent"]),
        ("kw", theme["kw"], bg),
        ("str", theme["str"], bg),
        ("num", theme["num"], bg),
        ("comment", theme["comment"], bg),
        ("err", theme["err"], bg),
        ("ok", theme["ok"], bg),
        ("warn", theme["warn"], bg),
        ("hdr", theme["hdr"], bg),
        ("null", theme["null"], bg),
    ]
    for i, (name, fg, back) in enumerate(specs, 1):
        try:
            curses.init_pair(i, fg, back)
            extra = curses.A_BOLD if name in ("title_active", "panel_active", "bar", "tab_on", "kw", "err", "warn", "hdr") else 0
            A[name] = curses.color_pair(i) | extra
        except curses.error:
            pass


def init_ui(scr) -> None:
    try:
        curses.raw()
    except curses.error:
        pass
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    scr.keypad(True)
    if hasattr(curses, "set_escdelay"):
        curses.set_escdelay(25)

    try:
        curses.mousemask(curses.BUTTON1_CLICKED | curses.BUTTON1_DOUBLE_CLICKED |
                         curses.BUTTON4_PRESSED | getattr(curses, "BUTTON5_PRESSED", 0x200000))
    except curses.error:
        pass

    A.update(dim=curses.A_DIM, underline=curses.A_UNDERLINE, sel=curses.A_REVERSE,
             cur=curses.A_REVERSE | curses.A_BOLD, title_active=curses.A_REVERSE | curses.A_BOLD,
             title_dim=curses.A_BOLD, panel_active=curses.A_BOLD, panel_dim=curses.A_DIM,
             bar=curses.A_REVERSE, tab_on=curses.A_REVERSE | curses.A_BOLD, prompt=curses.A_REVERSE,
             kw=curses.A_BOLD, str=curses.A_UNDERLINE, num=0, comment=curses.A_DIM,
             err=curses.A_BOLD, ok=0, warn=curses.A_BOLD, hdr=curses.A_BOLD, null=curses.A_DIM, bold=curses.A_BOLD)

    if curses.has_colors():
        curses.start_color()
        try:
            curses.use_default_colors()
        except curses.error:
            pass
        cfg = load_config()
        apply_theme(cfg.get("theme", "tokyo-night"))


# ---------------------------------------------------------------------------
# Main Application
# ---------------------------------------------------------------------------

class App:
    def __init__(self, scr, db: Db):
        self.scr = scr
        self.db = db
        self.cfg = load_config()
        self.theme_key = self.cfg.get("theme", "tokyo-night")
        self.focus_panel_id = 1       # 1: Objects, 2: Columns, 3: Editor, 4: Results/Data
        self.zoomed = False           # Maximize active panel (lazygit style)
        self.simple = bool(self.cfg.get("simple_mode", True))
        self.last_sql_path = ""
        self.tab_hits: list[tuple[int, int, int]] = []

        self.side = Sidebar()
        self.col_list = ColumnList()
        self.editor = Editor()
        self.grid = Grid()
        self.text = TextView()

        self.msg = ""
        self.msg_err = False
        self.result_title = ""
        self.table: Optional[str] = None
        self.table_cols: list[Column] = []
        self.key = KeyInfo(False, [])
        self.keyvals: list[tuple] = []
        self.where = ""
        self.order: Optional[str] = None
        self.desc = False
        self.page = 0
        self.total = 0
        self._cached_total_table: Optional[str] = None
        self._cached_total_where: Optional[str] = None
        self.col_names: set[str] = set()
        self.table_names: list[str] = []
        self.history = load_history()
        self.undo_stack: list[dict[str, Any]] = []
        self.nav_stack: list[dict[str, Any]] = []
        self.undo_file_path = self.get_undo_file_path()

        self.editor.set_text(DEFAULT_SQL)
        self.refresh_schema()
        if "orders" in self.table_names:
            self.open_table("orders")
        elif self.table_names:
            self.open_table(self.table_names[0])
        self.set_msg("Simple mode · Ctrl+O open DB/SQL · F2 layout" if self.simple
                     else "4-panel mode · Ctrl+O open DB/SQL · F2 simple")

    def get_undo_file_path(self) -> str:
        if self.db.path != ":memory:":
            base = os.path.splitext(self.db.path)[0]
            return f"{base}_undo.sql"
        state_dir = os.path.join(os.path.expanduser("~"), ".local", "state", APP)
        os.makedirs(state_dir, exist_ok=True)
        return os.path.join(state_dir, "memory_undo.sql")

    def record_undo(self, desc: str, undo_sql: str, table: Optional[str] = None) -> None:
        if not undo_sql.strip():
            return
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        record = {"time": now, "desc": desc, "sql": undo_sql.strip(), "table": table}
        self.undo_stack.append(record)
        if len(self.undo_stack) > 100:
            self.undo_stack.pop(0)
        try:
            d = os.path.dirname(self.undo_file_path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(self.undo_file_path, "a", encoding="utf-8") as f:
                header = (
                    f"\n-- ==========================================================================\n"
                    f"-- [{now}] {desc}\n"
                    f"-- ==========================================================================\n"
                )
                f.write(header + undo_sql.strip() + "\n")
        except OSError:
            pass

    def apply_undo(self) -> None:
        if not self.undo_stack:
            self.set_msg("No undo actions available in this session", True)
            return
        last = self.undo_stack[-1]
        desc = last["desc"]
        sql = last["sql"]
        if not self.confirm(f"Restore «{desc}» using Undo SQL?"):
            return
        try:
            self.db.conn.executescript(sql)
            self.undo_stack.pop()
            self.refresh_schema()
            if self.table:
                self.load_data(keep=True, recount=True)
            self.set_msg(f"Restored successfully: {desc}")
        except sqlite3.Error as e:
            self.set_msg(f"Error restoring: {e}", True)

    def view_undo_log(self) -> None:
        if not os.path.exists(self.undo_file_path):
            self.set_msg("Undo log file is currently empty")
            return
        try:
            with open(self.undo_file_path, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
            self.show_text("Undo SQL Restore History", lines)
        except OSError as e:
            self.set_msg(f"Error reading undo file: {e}", True)

    def capture_sql_undo(self, sql: str) -> Optional[tuple[str, str]]:
        trimmed = sql.strip().rstrip(";")
        m_upd = re.match(r"^\s*UPDATE\s+([`\"\[]?\w+[`\"\]]?)\s+SET\s+.*?(?:\s+WHERE\s+(.*))?$",
                         trimmed, re.IGNORECASE | re.DOTALL)
        if m_upd:
            raw_tbl = m_upd.group(1).strip("`\"[]")
            where = m_upd.group(2) or "1=1"
            try:
                cur = self.db.conn.execute(f"SELECT rowid, * FROM {quote_ident(raw_tbl)} WHERE {where} LIMIT 500")
                cols = [d[0] for d in cur.description][1:]
                rows = cur.fetchall()
                if rows:
                    undo_stmts = []
                    for r in rows:
                        rowid = r[0]
                        set_items = ", ".join(f"{quote_ident(c)} = {sql_literal(v)}" for c, v in zip(cols, r[1:]))
                        undo_stmts.append(f"UPDATE {quote_ident(raw_tbl)} SET {set_items} WHERE rowid = {rowid};")
                    return f"SQL UPDATE on «{raw_tbl}» ({len(rows)} rows)", "\n".join(undo_stmts)
            except sqlite3.Error:
                pass

        m_del = re.match(r"^\s*DELETE\s+FROM\s+([`\"\[]?\w+[`\"\]]?)(?:\s+WHERE\s+(.*))?$",
                         trimmed, re.IGNORECASE | re.DOTALL)
        if m_del:
            raw_tbl = m_del.group(1).strip("`\"[]")
            where = m_del.group(2) or "1=1"
            try:
                undo_stmts = self.db.table_undo_inserts(raw_tbl, where=where, limit=500)
                if undo_stmts:
                    return f"SQL DELETE on «{raw_tbl}» ({len(undo_stmts)} rows)", "\n".join(undo_stmts)
            except sqlite3.Error:
                pass

        m_drop = re.match(r"^\s*DROP\s+TABLE\s+([`\"\[]?\w+[`\"\]]?)$", trimmed, re.IGNORECASE)
        if m_drop:
            raw_tbl = m_drop.group(1).strip("`\"[]")
            try:
                row = self.db.conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                                           (raw_tbl,)).fetchone()
                if row and row[0]:
                    undo_stmts = [row[0] + ";"]
                    undo_stmts.extend(self.db.table_undo_inserts(raw_tbl))
                    return f"SQL DROP TABLE «{raw_tbl}»", "\n".join(undo_stmts)
            except sqlite3.Error:
                pass

        return None

    # ----- main loop -------------------------------------------------------

    def run(self) -> None:
        while True:
            self.draw()
            curses.doupdate()
            key = read_key(self.scr)
            if key is None or key == curses.KEY_RESIZE or key == CTRL_C:
                continue
            if key == curses.KEY_MOUSE:
                try:
                    _, mx, my, _, bstate = curses.getmouse()
                    self.handle_mouse(mx, my, bstate)
                except curses.error:
                    pass
                continue
            try:
                if self.dispatch(key):
                    return
            except sqlite3.Error as e:
                self.set_msg(f"SQLite Error: {e}", True)
            except (OSError, ValueError, StopIteration) as e:
                self.set_msg(f"Error: {e}", True)

    def set_msg(self, text: str, err: bool = False) -> None:
        self.msg, self.msg_err = text, err

    def focus_panel(self, pid: int) -> None:
        self.focus_panel_id = max(1, min(4, pid))

    def cycle_panel(self, delta: int) -> None:
        self.focus_panel_id = ((self.focus_panel_id - 1 + delta) % 4) + 1

    def handle_mouse(self, mx: int, my: int, bstate: int) -> None:
        H, W = self.scr.getmaxyx()
        if my == 0:
            for x0, x1, pid in self.tab_hits:
                if x0 <= mx < x1:
                    if pid == 0:
                        self.show_text("Shortcuts & Panels Help", HELP_LINES)
                    else:
                        self.focus_panel(pid)
                    return
            return
        y0, h_avail = 1, H - 3
        sw = min(36, max(22, W // 4))
        h_left1 = h_avail * 55 // 100
        h_right1 = max(5, h_avail * 38 // 100)

        wheel_up = bstate & (curses.BUTTON4_PRESSED | getattr(curses, "BUTTON4_CLICKED", 0x20000))
        wheel_down = bstate & (getattr(curses, "BUTTON5_PRESSED", 0x200000) | getattr(curses, "BUTTON5_CLICKED", 0x400000))

        if self.simple:
            if my < 2 or my >= H - 2:
                return
            if wheel_up or wheel_down:
                step = -1 if wheel_up else 1
                if self.focus_panel_id == 1:
                    self.side.move(step * 3, self.side.rows())
                elif self.focus_panel_id == 2:
                    self.col_list.handle_key(curses.KEY_DOWN if step > 0 else curses.KEY_UP)
                elif self.focus_panel_id == 3:
                    self.editor.handle_key(curses.KEY_DOWN if step > 0 else curses.KEY_UP)
                else:
                    self.grid.handle_key(curses.KEY_DOWN if step > 0 else curses.KEY_UP)
                return
            row_idx = my - 2
            if self.focus_panel_id == 1:
                rows = self.side.rows()
                i = self.side.top + row_idx
                if 0 <= i < len(rows):
                    self.side.cy = i
                    sel = self.side.selected()
                    if sel and sel[0] in ("table", "view"):
                        self.col_list.set_table(self.db, sel[1])
                        if bstate & curses.BUTTON1_DOUBLE_CLICKED:
                            self.open_table(sel[1])
                            self.focus_panel(4)
            elif self.focus_panel_id == 2:
                i = self.col_list.top + row_idx
                if 0 <= i < len(self.col_list.cols):
                    self.col_list.cy = i
                    if bstate & curses.BUTTON1_DOUBLE_CLICKED:
                        self.show_column_stats()
            elif self.focus_panel_id == 3:
                self.editor.cy = max(0, min(len(self.editor.lines) - 1, self.editor.top + row_idx))
            else:
                i = self.grid.top + row_idx
                if 0 <= i < len(self.grid.rows):
                    self.grid.cy = i
                    if bstate & curses.BUTTON1_DOUBLE_CLICKED:
                        self.edit_cell()
            return

        if wheel_up or wheel_down:
            step = -1 if wheel_up else 1
            if mx < sw:
                if my < y0 + h_left1:
                    self.side.move(step * 3, self.side.rows())
                else:
                    self.col_list.handle_key(curses.KEY_DOWN if step > 0 else curses.KEY_UP)
            else:
                if my < y0 + h_right1:
                    self.editor.handle_key(curses.KEY_DOWN if step > 0 else curses.KEY_UP)
                else:
                    self.grid.handle_key(curses.KEY_DOWN if step > 0 else curses.KEY_UP)
            return

        if my < y0 or my >= y0 + h_avail:
            return

        if mx < sw:
            if my < y0 + h_left1:
                self.focus_panel(1)
                row_idx = self.side.top + (my - y0 - 1)
                rows = self.side.rows()
                if 0 <= row_idx < len(rows):
                    self.side.cy = row_idx
                    sel = self.side.selected()
                    if sel and sel[0] in ("table", "view"):
                        self.col_list.set_table(self.db, sel[1])
                        if bstate & curses.BUTTON1_DOUBLE_CLICKED:
                            self.open_table(sel[1])
            else:
                self.focus_panel(2)
                row_idx = self.col_list.top + (my - (y0 + h_left1) - 1)
                if 0 <= row_idx < len(self.col_list.cols):
                    self.col_list.cy = row_idx
                    if bstate & curses.BUTTON1_DOUBLE_CLICKED:
                        self.show_column_stats()
        else:
            if my < y0 + h_right1:
                self.focus_panel(3)
                self.editor.cy = max(0, min(len(self.editor.lines) - 1, self.editor.top + (my - y0 - 1)))
            else:
                self.focus_panel(4)
                gy = y0 + h_right1 + 1
                row_idx = self.grid.top + (my - gy - 1)
                if 0 <= row_idx < len(self.grid.rows):
                    self.grid.cy = row_idx
                if bstate & curses.BUTTON1_DOUBLE_CLICKED:
                    self.edit_cell()

    def dispatch(self, key: Any) -> bool:
        if key == curses.KEY_F1 or (key == "?" and self.focus_panel_id != 3):
            self.show_text("Shortcuts & Panels Help", HELP_LINES)
            return False
        if key == curses.KEY_F10:
            return self.menu()
        if key == curses.KEY_F2:
            self.toggle_simple()
            return False
        if key == CTRL_S:
            self.save_sql_file()
            return False
        if key == "m" and self.focus_panel_id != 3:
            return self.menu()
        if key == CTRL_O:
            self.open_db()
            return False
        if key == CTRL_Q:
            return self.quit()
        if key == "T" and self.focus_panel_id != 3:
            self.theme_menu()
            return False
        if key == "z" and self.focus_panel_id != 3:
            self.toggle_zoom()
            return False
        if (key == ord("\t") or key == KEY_TAB) and self.focus_panel_id != 3:
            self.cycle_panel(1)
            return False
        if key == curses.KEY_BTAB:
            self.cycle_panel(-1)
            return False
        if key == curses.KEY_F6:
            self.cycle_panel(1)
            return False
        if key == "u" and self.focus_panel_id != 3:
            self.apply_undo()
            return False

        if self.focus_panel_id != 3:
            if key == "1":
                self.focus_panel(1); return False
            if key == "2":
                self.focus_panel(2); return False
            if key == "3":
                self.focus_panel(3); return False
            if key == "4":
                self.focus_panel(4); return False

        if key == curses.KEY_F5:
            self.run_sql(current=False); return False
        if key == curses.KEY_F9:
            self.run_sql(current=True); return False

        if self.focus_panel_id == 1:
            self.side_key(key)
        elif self.focus_panel_id == 2:
            self.col_key(key)
        elif self.focus_panel_id == 3:
            if key == CTRL_P:
                self.format_editor_sql()
                return False
            self.editor.handle_key(key)
        elif self.focus_panel_id == 4:
            self.grid_key(key)

        return False

    def quit(self) -> bool:
        if self.db.conn.in_transaction:
            if not self.confirm("There is an open transaction. ROLLBACK and exit?"):
                return False
            self.db.conn.execute("ROLLBACK")
        return True

    def toggle_zoom(self) -> None:
        if self.simple:
            self.set_msg("Simple mode already shows one panel · F2 for 4-panel layout")
            return
        self.zoomed = not self.zoomed
        self.set_msg("Panel maximized [ZOOM]" if self.zoomed else "Multi-panel layout restored")

    def toggle_simple(self) -> None:
        self.simple = not self.simple
        self.zoomed = False
        self.cfg["simple_mode"] = self.simple
        save_config(self.cfg)
        if self.simple:
            self.set_msg("Simple mode: one panel at a time · F2 for 4-panel layout")
        else:
            self.set_msg("4-panel layout restored · F2 for simple mode")

    def _panel_title(self, pid: int) -> tuple[str, Optional[int], str]:
        """Builds panel title, count and badge in one place for both layouts."""
        if pid == 1:
            badge = f"/{self.side.filter}" if self.side.filter else ""
            return "[1] Objects", self.side.count(), badge
        if pid == 2:
            W = self.scr.getmaxyx()[1]
            full = self.zoomed or self.simple
            panel_w = W if full else min(36, max(24, W // 4))
            title = "[2] Columns"
            candidate = title + (f" · {self.table}" if self.table else "")
            if self.table and str_width(candidate) + 2 <= panel_w - 6:
                title = candidate
            return title, None, ""
        if pid == 3:
            return "[3] SQL", None, f"Ln {self.editor.cy + 1}, Col {self.editor.cx + 1}"

        g = self.grid
        W = self.scr.getmaxyx()[1]
        full = self.zoomed or self.simple
        panel_w = W if full else W - min(36, max(24, W // 4))
        compact = panel_w < 70
        parts = []
        if self.nav_stack:
            parts.append(f"b:back {len(self.nav_stack)}")
        if self.table:
            title = f"[4] {self.table}"
            if g.cols and 0 <= g.cx < len(g.cols):
                cur_col = g.cols[g.cx]
                col_type = g.col_types.get(cur_col, "")
                title += f" · {cur_col}"
                fk = g.fk_for_col(cur_col)
                if not compact:
                    title += f" ({col_type or 'ANY'})"
                    if cur_col in g.pks:
                        title += " PK"
                    if fk:
                        title += f" → {fk[2]}.{fk[4] or 'id'}"
                elif fk:
                    title += " →"
            if self.pages() > 1:
                parts.insert(0, f"pg {self.page + 1}/{self.pages()}")
            if self.where:
                parts.append("WHERE " + fit_str(self.where, 18))
            if self.order:
                parts.append(f"sort {self.order} {'▼' if self.desc else '▲'}")
            return title, self.total, " · ".join(parts)

        title = "[4] Results"
        if self.result_title:
            title += f" · {self.result_title}"
        return title, len(g.rows), " · ".join(parts)

    # ----- drawing ---------------------------------------------------------

    def draw(self) -> None:
        scr = self.scr
        scr.erase()
        H, W = scr.getmaxyx()
        if H < 14 or W < 50:
            put(scr, 0, 0, "Terminal too small (minimum 50x14)", A["err"])
            scr.noutrefresh()
            return

        self.draw_header(W)
        y0, h_avail = 1, H - 3
        t1, c1, b1 = self._panel_title(1)
        t2, c2, b2 = self._panel_title(2)
        t3, c3, b3 = self._panel_title(3)
        t4, c4, b4 = self._panel_title(4)

        if self.zoomed or self.simple:
            if self.focus_panel_id == 1:
                draw_box(scr, y0, 0, h_avail, W, t1, True, c1, b1)
                self.side.draw(scr, y0 + 1, 1, h_avail - 2, W - 2, True)
            elif self.focus_panel_id == 2:
                draw_box(scr, y0, 0, h_avail, W, t2, True, c2, b2)
                self.col_list.draw(scr, y0 + 1, 1, h_avail - 2, W - 2, True)
            elif self.focus_panel_id == 3:
                draw_box(scr, y0, 0, h_avail, W, t3, True, c3, b3)
                self.editor.draw(scr, y0 + 1, 1, h_avail - 2, W - 2, True)
            else:
                draw_box(scr, y0, 0, h_avail, W, t4, True, c4, b4)
                self.grid.draw(scr, y0 + 1, 1, h_avail - 2, W - 2, True)
        else:
            sw = min(36, max(24, W // 4))
            rw = W - sw
            h_left1 = h_avail * 55 // 100
            h_left2 = h_avail - h_left1
            h_right1 = max(5, h_avail * 38 // 100)
            h_right2 = h_avail - h_right1

            draw_box(scr, y0, 0, h_left1, sw, t1, self.focus_panel_id == 1, c1, b1)
            self.side.draw(scr, y0 + 1, 1, h_left1 - 2, sw - 2, self.focus_panel_id == 1)

            draw_box(scr, y0 + h_left1, 0, h_left2, sw, t2, self.focus_panel_id == 2, c2, b2)
            self.col_list.draw(scr, y0 + h_left1 + 1, 1, h_left2 - 2, sw - 2, self.focus_panel_id == 2)

            draw_box(scr, y0, sw, h_right1, rw, t3, self.focus_panel_id == 3, c3, b3)
            self.editor.draw(scr, y0 + 1, sw + 1, h_right1 - 2, rw - 2, self.focus_panel_id == 3)

            draw_box(scr, y0 + h_right1, sw, h_right2, rw, t4, self.focus_panel_id == 4, c4, b4)
            self.grid.draw(scr, y0 + h_right1 + 1, sw + 1, h_right2 - 2, rw - 2, self.focus_panel_id == 4)

        self.draw_context_bar(H - 2, W)
        self.draw_status(H - 1, W)

        cur = self.editor.cursor if self.focus_panel_id == 3 else None
        try:
            if cur:
                scr.move(*cur)
            curses.curs_set(1 if cur else 0)
        except curses.error:
            pass
        scr.noutrefresh()

    PANEL_TABS = ((1, "Objects", "Obj"), (2, "Columns", "Cols"), (3, "SQL", "SQL"), (4, "Data", "Data"))

    def draw_header(self, W: int) -> None:
        scr = self.scr
        put(scr, 0, 0, " " * W, A["bar"], W)
        compact = W < 78
        brand = " sqlite_tui " if W >= 58 else " sqltui "
        put(scr, 0, 0, brand, A["bar"] | curses.A_BOLD, str_width(brand))
        x = str_width(brand)

        flags = []
        if self.zoomed:
            flags.append("ZOOM")
        if self.db.readonly:
            flags.append("RO")
        if self.db.conn.in_transaction:
            flags.append("TX")
        right = ("".join(f" [{f}]" for f in flags) if flags else "") + " ? Help "
        if x + str_width(right) + 20 > W:
            right = " ? "
        right_w = str_width(right)
        limit = max(x, W - right_w - 1)

        self.tab_hits = []
        for pid, full, short in self.PANEL_TABS:
            label = short if compact else full
            text = f" {pid} {label} "
            tw = str_width(text)
            if x + tw > limit:
                break
            attr = A["tab_on"] if pid == self.focus_panel_id else A["bar"] | curses.A_UNDERLINE
            put(scr, 0, x, text, attr, tw)
            self.tab_hits.append((x, x + tw, pid))
            x += tw

        name = "scratchpad" if self.db.path == ":memory:" else os.path.basename(self.db.path)
        if W >= 110 and x + 4 < limit:
            put(scr, 0, x, "│ " + fit_str(name, max(8, limit - x - 3)), A["bar"], max(0, limit - x))

        rx = W - right_w
        put(scr, 0, rx, right, A["bar"] | curses.A_BOLD, right_w)
        hp = right.find("?")
        if hp >= 0:
            self.tab_hits.append((rx + hp, W, 0))

    def draw_context_bar(self, y: int, W: int) -> None:
        if self.focus_panel_id == 1:
            actions = [("1-4", "Panels"), ("Ctrl+O", "Open"), ("Enter", "Open"), ("q", "SELECT"),
                       ("s", "Schema"), ("m", "Menu"), ("/", "Filter"), ("d", "DROP")]
        elif self.focus_panel_id == 2:
            actions = [("1-4", "Panels"), ("i/Enter", "Stats"), ("f", "FK Table"),
                       ("b", f"Back ({len(self.nav_stack)})") if self.nav_stack else ("", ""),
                       ("q", "SELECT col"), ("m", "Menu")]
            actions = [(k, d) for k, d in actions if k]
        elif self.focus_panel_id == 3:
            actions = [("1-4", "Panels"), ("F5", "Run All"), ("F9", "Run Line"), ("F8", "Complete"),
                       ("Ctrl+S", "Save"), ("Ctrl+P", "Format"), ("Ctrl+Z", "Undo"), ("Ctrl+R", "Redo"), ("m", "Menu")]
        else:
            actions = [("1-4", "Panels"), ("e", "Edit"), ("Space", "Inspect"),
                       ("f", "FK Jump"), ("F", "Ref By"),
                       ("b", f"Back ({len(self.nav_stack)})") if self.nav_stack else ("", ""),
                       ("n", "New"), ("d", "Delete"), ("o", "Sort"), ("/", "Filter"),
                       ("i", "Stats"), ("x", "Export"), ("u", "Undo")]
            actions = [(k, d) for k, d in actions if k]

        if self.simple:
            if self.focus_panel_id == 1:
                actions = [("Enter", "Open"), ("q", "SELECT"), ("/", "Filter"),
                           ("Ctrl+O", "Open DB/SQL"), ("F2", "Layout")]
            elif self.focus_panel_id == 2:
                actions = [("i", "Stats"), ("f", "FK Table"), ("q", "SELECT"),
                           ("Ctrl+O", "Open DB/SQL"), ("F2", "Layout")]
            elif self.focus_panel_id == 3:
                actions = [("F5", "Run"), ("F9", "Statement"), ("F8", "Complete"),
                           ("Ctrl+O", "Open"), ("Ctrl+S", "Save"), ("F2", "Layout")]
            else:
                actions = [("e", "Edit"), ("Space", "Inspect"), ("/", "Filter"),
                           ("o", "Sort"), ("x", "Export"), ("F2", "Layout")]

        cur_x = 0
        help_text = " ? Help "
        help_w = str_width(help_text)
        limit = max(0, W - help_w - 1)
        for key, desc in actions:
            key_badge = f" {key} "
            desc_badge = f" {desc} "
            seg_w = str_width(key_badge) + str_width(desc_badge)
            if cur_x + seg_w > limit:
                break
            put(self.scr, y, cur_x, key_badge, A["tab_on"] | curses.A_UNDERLINE, str_width(key_badge))
            cur_x += str_width(key_badge)
            put(self.scr, y, cur_x, desc_badge, A["bar"], str_width(desc_badge))
            cur_x += str_width(desc_badge)
            if cur_x < limit:
                put(self.scr, y, cur_x, " ", A["bar"], 1)
                cur_x += 1
        put(self.scr, y, W - help_w, help_text, A["bar"] | curses.A_BOLD, help_w)

    def status_info(self) -> str:
        parts = []
        if self.focus_panel_id == 3:
            parts.append(f"Ln {self.editor.cy + 1}, Col {self.editor.cx + 1}")
        elif self.focus_panel_id == 4:
            if self.table:
                parts.append(f"{self.total:,} rows")
                if self.pages() > 1:
                    parts.append(f"pg {self.page + 1}/{self.pages()}")
                if self.where:
                    parts.append("filtered")
                if self.order:
                    parts.append("sorted")
            else:
                parts.append(f"{len(self.grid.rows):,} rows")
        elif self.focus_panel_id == 2:
            if self.col_list.table:
                parts.append(f"{self.col_list.table} · {len(self.col_list.cols)} cols")
        else:
            n = self.side.count()
            parts.append(f"{n} object{'s' if n != 1 else ''}")
        if self.side.filter and self.focus_panel_id == 1:
            parts.append(f"/{self.side.filter}")
        if self.undo_stack:
            parts.append(f"undo {len(self.undo_stack)}")
        if self.db.readonly:
            parts.append("read-only")
        parts.append("scratchpad" if self.db.path == ":memory:" else os.path.basename(self.db.path))
        return " · ".join(parts)

    def draw_status(self, y: int, W: int) -> None:
        info = fit_str(self.status_info(), max(0, W // 2))
        info_w = str_width(info)
        msg_w = max(0, W - info_w - 2)
        if self.msg:
            prefix = " ✖ " if self.msg_err else " ● "
            put(self.scr, y, 0, prefix + self.msg, A["err"] if self.msg_err else A["ok"], msg_w)
        else:
            undo_info = f" · undo available ({len(self.undo_stack)})" if self.undo_stack else ""
            put(self.scr, y, 0, f" ● ready{undo_info}", A["dim"], msg_w)
        if info_w:
            put(self.scr, y, W - info_w, info, A["dim"], info_w)

    # ----- schema and column inspector ------------------------------------

    def refresh_schema(self) -> None:
        groups: dict[str, list[str]] = {"table": [], "view": [], "index": [], "trigger": []}
        try:
            for typ, name, _tbl, _sql in self.db.schema():
                groups.setdefault(typ, []).append(name)
        except sqlite3.Error as e:
            self.set_msg(f"Error reading schema: {e}", True)
            return

        self.table_names = groups["table"] + groups["view"]
        self.side.groups = [
            ("Tables", "table", groups["table"]),
            ("Views", "view", groups["view"]),
            ("Indexes", "index", groups["index"]),
            ("Triggers", "trigger", groups["trigger"]),
        ]
        self.side.invalidate()
        self.side.table_counts = {}
        for t in groups["table"]:
            try:
                self.side.table_counts[t] = self.db.conn.execute(f"SELECT COUNT(*) FROM {quote_ident(t)}").fetchone()[0]
            except sqlite3.Error:
                self.side.table_counts[t] = None

        self.db.forget()
        self.col_names = set()
        for t in self.table_names:
            try:
                self.col_names.update(c.name for c in self.db.columns(t))
            except sqlite3.Error:
                pass

        sel = self.side.selected()
        if sel and sel[0] in ("table", "view"):
            self.col_list.set_table(self.db, sel[1])

    def side_key(self, key: Any) -> None:
        s = self.side
        rows = s.rows()
        prev_cy = s.cy
        if key in (curses.KEY_UP, "k"):
            s.move(-1, rows)
        elif key in (curses.KEY_DOWN, "j"):
            s.move(1, rows)
        elif key == curses.KEY_PPAGE:
            s.move(-s.h, rows)
        elif key == curses.KEY_NPAGE:
            s.move(s.h, rows)
        elif key == curses.KEY_HOME:
            s.cy = 0; s.move(1, rows)
        elif key == curses.KEY_END:
            s.cy = max(0, len(rows) - 1); s.move(-1, rows)
        elif key in KEY_ENTER or key == " ":
            sel = s.selected()
            if sel and sel[0] in ("table", "view"):
                self.open_table(sel[1])
                self.focus_panel(4)
            elif sel:
                self.show_struct(*sel)
        elif key == "q":
            sel = s.selected()
            if sel and sel[0] in ("table", "view"):
                self.editor.set_text(f"SELECT * FROM {quote_ident(sel[1])} LIMIT 100;\n")
                self.focus_panel(3)
                self.set_msg(f"Query generated for «{sel[1]}»")
        elif key == "s":
            sel = s.selected()
            if sel:
                self.show_struct(*sel)
        elif key in (curses.KEY_DC, "d"):
            self.drop_selected()
        elif key == "/":
            f = self.prompt("Filter objects: ", s.filter)
            if f is not None:
                s.filter = f.strip()
                s.invalidate()
                s.cy = 0
        elif key == KEY_ESC:
            s.filter = ""
            s.invalidate()
        elif key == "r":
            self.refresh_schema()
            self.set_msg("Schema and row counts updated")

        if s.cy != prev_cy:
            sel = s.selected()
            if sel and sel[0] in ("table", "view"):
                self.col_list.set_table(self.db, sel[1])

    def col_key(self, key: Any) -> None:
        if key in ("f", ord("f")):
            self.col_follow_fk()
            return
        if key in ("b", ord("b"), *KEY_BACK):
            if self.nav_back():
                return
        if self.col_list.handle_key(key):
            return
        if key in ("i", ord("i"), *KEY_ENTER):
            self.show_column_stats()
        elif key == "q":
            col = self.col_list.selected()
            if col and self.col_list.table:
                self.editor.set_text(f"SELECT {quote_ident(col.name)} FROM {quote_ident(self.col_list.table)} LIMIT 50;\n")
                self.focus_panel(3)

    def show_column_stats(self) -> None:
        tbl = self.table or (self.side.selected()[1] if self.side.selected() else self.col_list.table)
        if not tbl:
            self.set_msg("Select a table first", True)
            return
        if self.focus_panel_id == 4 and self.grid.cols:
            col_name = self.grid.cols[self.grid.cx]
        elif self.col_list.selected():
            col_name = self.col_list.selected().name
        elif self.grid.cols:
            col_name = self.grid.cols[0]
        else:
            self.set_msg("Select a column", True)
            return

        try:
            stats = self.calc_column_stats(tbl, col_name)
        except sqlite3.Error as e:
            self.set_msg(f"Error analyzing column: {e}", True)
            return

        lines = [
            f"# Statistical Analysis: {tbl}.{col_name}",
            f"Declared type: {stats['type'] or 'ANY'}  ·  Total rows: {stats['total']:,}",
            f"Non-null values: {stats['non_null']:,} ({stats['non_null_pct']:.1f}%)",
            f"NULL values: {stats['nulls']:,} ({stats['null_pct']:.1f}%)",
            f"Distinct values (cardinality): {stats['distinct']:,}",
            ""
        ]
        if stats['min'] is not None and stats['max'] is not None:
            lines += [
                "# Numeric Range / Metrics",
                f"Min: {stats['min']}  ·  Max: {stats['max']}",
            ]
            if stats['avg'] is not None:
                lines.append(f"Mean (Average): {stats['avg']:.4f}")
            lines.append("")

        if stats['top']:
            lines += ["# Most Frequent Values Distribution", ""]
            max_cnt = max((c for _, c in stats['top']), default=1)
            bar_len = 22
            for val, cnt in stats['top']:
                pct = (cnt / stats['total'] * 100) if stats['total'] else 0
                val_str = fit_str(fmt_value(val), 18)
                filled = int(round(cnt / max_cnt * bar_len))
                bar = "█" * filled + "░" * (bar_len - filled)
                lines.append(f"  {val_str:<18}  {bar}  {cnt:,} ({pct:.1f}%)")

        self.show_text(f"Statistics: {tbl}.{col_name}", lines)

    def calc_column_stats(self, table: str, col: str) -> dict[str, Any]:
        q_tbl = quote_ident(table)
        q_col = quote_ident(col)
        total, non_null, distinct, min_val, max_val, avg_val = 0, 0, 0, None, None, None
        try:
            r = self.db.conn.execute(
                f"SELECT COUNT(*), COUNT({q_col}), COUNT(DISTINCT {q_col}), "
                f"MIN({q_col}), MAX({q_col}), AVG({q_col}) FROM {q_tbl}"
            ).fetchone()
            total, non_null, distinct, min_val, max_val, avg_val = r
        except sqlite3.Error:
            try:
                r = self.db.conn.execute(
                    f"SELECT COUNT(*), COUNT({q_col}), COUNT(DISTINCT {q_col}), "
                    f"MIN({q_col}), MAX({q_col}) FROM {q_tbl}"
                ).fetchone()
                total, non_null, distinct, min_val, max_val = r
            except sqlite3.Error:
                pass
        nulls = total - non_null

        decl_type = ""
        for c in self.db.columns(table):
            if c.name.lower() == col.lower():
                decl_type = c.type
                break

        top_rows = []
        try:
            top_rows = self.db.conn.execute(
                f"SELECT {q_col}, COUNT(*) as c FROM {q_tbl} WHERE {q_col} IS NOT NULL "
                f"GROUP BY {q_col} ORDER BY c DESC LIMIT 6"
            ).fetchall()
        except sqlite3.Error:
            pass

        return {
            "type": decl_type,
            "total": total,
            "non_null": non_null,
            "non_null_pct": (non_null / total * 100) if total else 0,
            "nulls": nulls,
            "null_pct": (nulls / total * 100) if total else 0,
            "distinct": distinct,
            "min": min_val,
            "max": max_val,
            "avg": avg_val,
            "top": top_rows
        }

    def drop_selected(self) -> None:
        if self.db.readonly:
            self.set_msg("Action blocked: database is in read-only mode", True)
            return
        sel = self.side.selected()
        if not sel:
            return
        kind, name = sel
        if not self.confirm(f"Drop {kind} «{name}» (DROP)?"):
            return

        undo_lines = []
        try:
            row = self.db.conn.execute("SELECT sql FROM sqlite_master WHERE type = ? AND name = ?", (kind, name)).fetchone()
            if row and row[0]:
                undo_lines.append(row[0] + ";")
            if kind == "table":
                undo_lines.extend(self.db.table_undo_inserts(name))
        except sqlite3.Error:
            pass

        undo_sql = "\n".join(undo_lines)
        self.db.conn.execute(f"DROP {kind.upper()} {quote_ident(name)}")
        if undo_sql:
            self.record_undo(f"DROP {kind.upper()} «{name}»", undo_sql)
        if self.table == name:
            self.table = None
            self.grid.set_data([], [])
        self.refresh_schema()
        fname = os.path.basename(self.undo_file_path)
        self.set_msg(f"{kind} «{name}» dropped · Undo SQL saved to {fname}")

    def show_struct(self, kind: str, name: str) -> None:
        self.show_text(f"Structure: {kind} {name}", describe(self.db, kind, name))

    # ----- SQL -------------------------------------------------------------

    def run_sql(self, current: bool) -> None:
        text = self.editor.text
        if current:
            span = statement_at(text, self.editor.offset())
            stmts = [text[span[0]:span[1]]] if span else []
        else:
            stmts = [text[a:b] for a, b in split_statements(text)]
        if not stmts:
            self.set_msg("No statements to execute")
            return
        self.remember(stmts[0] if current else text)
        last_query: Optional[Result] = None
        info = ""
        t0 = time.perf_counter()
        for n, sql in enumerate(stmts, 1):
            undo_info = self.capture_sql_undo(sql)
            try:
                res = self.db.run(sql)
            except sqlite3.Error as e:
                self.set_msg(f"Error in statement {n}: {e}", True)
                self.result_title = "error"
                self.grid.set_data([], [])
                self.refresh_schema()
                return

            if undo_info and not res.is_query and res.message != "0 row(s) affected":
                desc, undo_sql = undo_info
                self.record_undo(desc, undo_sql)

            if res.is_query:
                last_query = res
            else:
                info = res.message
        ms = (time.perf_counter() - t0) * 1000
        if last_query:
            self.table = None
            self.grid.set_data(last_query.cols, last_query.rows)
            self.grid.row_offset = 0
            self.grid.sort = (None, False)
            limited = " (limited)" if last_query.truncated else ""
            self.result_title = f"{len(last_query.rows)} rows{limited} · {ms:.1f} ms"
            self.focus_panel(4)
        else:
            self.grid.set_data([], [])
            self.result_title = f"{info} · {ms:.1f} ms"
        self.set_msg(f"{len(stmts)} statement(s) executed")
        self.refresh_schema()

    def remember(self, sql: str) -> None:
        sql = sql.strip()
        if not sql:
            return
        if sql in self.history:
            self.history.remove(sql)
        self.history.insert(0, sql)
        del self.history[HISTORY_MAX:]
        save_history(self.history)

    def autocomplete(self) -> None:
        ed = self.editor
        before = ed.lines[ed.cy][:ed.cx]
        m = re.search(r"[A-Za-z_]\w*$", before)
        prefix = m.group(0) if m else ""
        up = prefix.upper()
        cands = [k for k in KEYWORD_LIST if k.startswith(up)]
        cands += sorted(c for c in self.table_names + sorted(self.col_names) if c.upper().startswith(up))
        cands = [c for c in dict.fromkeys(cands) if c != prefix]
        if not cands:
            self.set_msg("No suggestions")
            return
        if len(cands) == 1:
            choice = cands[0]
        else:
            i = self.choose(f"Autocomplete «{prefix}»", cands[:300])
            if i is None:
                return
            choice = cands[i]
        if prefix and prefix.islower() and choice.upper() in KEYWORDS:
            choice = choice.lower()
        ed.replace_before(len(prefix), choice)

    # ----- relational FK navigation ---------------------------------------

    def push_nav_state(self) -> None:
        if self.table:
            self.nav_stack.append({
                "table": self.table,
                "where": self.where,
                "order": self.order,
                "desc": self.desc,
                "page": self.page,
                "cx": self.grid.cx,
                "cy": self.grid.cy,
            })
            if len(self.nav_stack) > 50:
                self.nav_stack.pop(0)

    def nav_back(self) -> bool:
        if not self.nav_stack:
            self.set_msg("Navigation history is empty", True)
            return False
        state = self.nav_stack.pop()
        self.open_table(state["table"])
        self.where = state["where"]
        self.order = state["order"]
        self.desc = state["desc"]
        self.page = state["page"]
        self.load_data(recount=True)
        if self.grid.cols:
            self.grid.cx = min(state["cx"], len(self.grid.cols) - 1)
        if self.grid.rows:
            self.grid.cy = min(state["cy"], len(self.grid.rows) - 1)
        self.focus_panel(4)
        rem = f" ({len(self.nav_stack)} left)" if self.nav_stack else ""
        self.set_msg(f"⬅ Returned to «{state['table']}»{rem}")
        return True

    def follow_fk(self) -> None:
        if not self.table or not self.grid.rows or not self.grid.cols:
            self.set_msg("No record or cell selected", True)
            return
        col_name = self.grid.cols[self.grid.cx]
        val = self.grid.rows[self.grid.cy][self.grid.cx]

        # Check outgoing foreign key
        fk = self.grid.fk_for_col(col_name)
        if fk:
            target_tbl = fk[2]
            target_col = fk[4]
            if not target_col:
                target_cols = self.db.columns(target_tbl)
                pks = [c.name for c in target_cols if c.pk]
                target_col = pks[0] if pks else "id"
            if val is None:
                self.set_msg(f"Foreign key in «{col_name}» is NULL", True)
                return
            self.push_nav_state()
            self.open_table(target_tbl)
            self.where = f"{quote_ident(target_col)} = {sql_literal(val)}"
            self.page = 0
            self.load_data(recount=True)
            self.focus_panel(4)
            self.set_msg(f"➔ FK Jump: {target_tbl} WHERE {target_col} = {fmt_value(val)} · (press 'b' to go back)")
            return

        # If current column is not outgoing FK, try incoming FKs (Referenced By)
        self.find_incoming_fks()

    def find_incoming_fks(self) -> None:
        if not self.table or not self.grid.rows or not self.grid.cols:
            self.set_msg("No record selected", True)
            return
        # Find PK value of current row
        pk_col = None
        pk_val = None
        if self.key.keys:
            pk_col = self.key.keys[0][0]
            if pk_col in self.grid.cols:
                pk_val = self.grid.rows[self.grid.cy][self.grid.cols.index(pk_col)]
        if pk_val is None:
            pk_col = self.grid.cols[self.grid.cx]
            pk_val = self.grid.rows[self.grid.cy][self.grid.cx]

        if pk_val is None:
            self.set_msg("Cannot find references for NULL value", True)
            return

        candidates = []
        for tbl_name in self.table_names:
            for fk in self.db.foreign_keys(tbl_name):
                if fk[2].lower() == self.table.lower():
                    ref_to = fk[4] or (self.key.keys[0][0] if self.key.keys else "id")
                    if ref_to.lower() == pk_col.lower():
                        cnt = self.db.conn.execute(
                            f"SELECT COUNT(*) FROM {quote_ident(tbl_name)} WHERE {quote_ident(fk[3])} = ?", (pk_val,)
                        ).fetchone()[0]
                        candidates.append((tbl_name, fk[3], cnt))

        if not candidates:
            self.set_msg(f"No foreign keys reference {self.table}.{pk_col} = {fmt_value(pk_val)}", True)
            return

        if len(candidates) == 1:
            tbl_name, col, cnt = candidates[0]
            self.push_nav_state()
            self.open_table(tbl_name)
            self.where = f"{quote_ident(col)} = {sql_literal(pk_val)}"
            self.page = 0
            self.load_data(recount=True)
            self.focus_panel(4)
            self.set_msg(f"➔ Referenced By: {tbl_name} ({cnt} rows where {col} = {fmt_value(pk_val)}) · Press 'b' to go back")
        else:
            labels = [f"{tbl} (via {col} = {fmt_value(pk_val)}) [{cnt} rows]" for tbl, col, cnt in candidates]
            i = self.choose(f"Referenced By ({self.table}.{pk_col} = {fmt_value(pk_val)})", labels)
            if i is not None:
                tbl_name, col, cnt = candidates[i]
                self.push_nav_state()
                self.open_table(tbl_name)
                self.where = f"{quote_ident(col)} = {sql_literal(pk_val)}"
                self.page = 0
                self.load_data(recount=True)
                self.focus_panel(4)
                self.set_msg(f"➔ Referenced By: {tbl_name} ({cnt} rows) · Press 'b' to go back")

    def col_follow_fk(self) -> None:
        col = self.col_list.selected()
        if not col or not self.col_list.table:
            return
        for fk in self.col_list.fks:
            if fk[3].lower() == col.name.lower():
                self.push_nav_state()
                self.open_table(fk[2])
                self.focus_panel(4)
                self.set_msg(f"➔ Switched to referenced table: «{fk[2]}» (press 'b' to go back)")
                return
        self.set_msg(f"Column «{col.name}» is not a foreign key", True)

    def format_editor_sql(self) -> None:
        formatted = format_sql(self.editor.text)
        if formatted.strip() != self.editor.text.strip():
            self.editor.set_text(formatted)
            self.set_msg("SQL formatted (Ctrl+P)")
        else:
            self.set_msg("SQL already formatted")

    def load_demo_db(self) -> None:
        if not self.confirm("Load interactive relational e-commerce demo database?"):
            return
        populate_demo_db(self.db)
        self.nav_stack.clear()
        self.table = None
        self.refresh_schema()
        self.open_table("orders")
        self.focus_panel(4)
        self.set_msg("Interactive demo database loaded: categories, products, customers, orders, order_items")

    # ----- table data ------------------------------------------------------

    def open_table(self, name: str) -> None:
        try:
            self.table_cols = self.db.columns(name)
            self.key = self.db.key_info(name)
        except sqlite3.Error as e:
            self.set_msg(f"Error: {e}", True)
            return
        self.table = name
        self.where = ""
        self.order = None
        self.desc = False
        self.page = 0
        self.col_list.set_table(self.db, name)
        self.load_data(recount=True)

    def page_size(self) -> int:
        return max(5, self.grid.view_h)

    def pages(self) -> int:
        return max(1, -(-self.total // self.page_size()))

    def load_data(self, keep: bool = False, recount: bool = False) -> bool:
        if not self.table:
            return False
        ps = self.page_size()
        try:
            if recount or self._cached_total_table != self.table or self._cached_total_where != self.where:
                self.total = self.db.count(self.table, self.where)
                self._cached_total_table = self.table
                self._cached_total_where = self.where
            self.page = max(0, min(self.page, self.pages() - 1))
            cols, raw = self.db.query_table(self.table, self.key, self.where, self.order,
                                            self.desc, ps, self.page * ps)
        except sqlite3.Error as e:
            self.set_msg(f"Error: {e}", True)
            return False
        off = 1 if self.key.extra else 0
        col_types = {c.name: c.type for c in self.table_cols}
        pks = {c.name for c in self.table_cols if c.pk}
        fks = self.db.foreign_keys(self.table)
        self.grid.set_data(cols[off:], [r[off:] for r in raw], keep, col_types, pks, fks, table_mode=True)
        self.grid.row_offset = self.page * ps
        self.grid.sort = (self.order, self.desc)
        self.keyvals = [tuple(r[i] for _, i in self.key.keys) for r in raw]
        return True

    def change_page(self, delta: int) -> bool:
        new = self.page + delta
        if new < 0 or new >= self.pages():
            return False
        self.page = new
        self.load_data()
        return True

    def grid_key(self, key: Any) -> None:
        if key in (KEY_ENTER, "e", ord("e")) and self.table:
            self.edit_cell(); return
        if key == " " or key == ord(" "):
            self.view_cell(); return
        if key in ("f", ord("f")) and self.table:
            self.follow_fk(); return
        if key in ("F", ord("F")) and self.table:
            self.find_incoming_fks(); return
        if key in ("b", ord("b"), *KEY_BACK):
            if self.nav_back(): return
        if key in ("n", ord("n")) and self.table:
            self.insert_row(); return
        if key in (curses.KEY_DC, "d", ord("d")) and self.table:
            self.delete_row(); return
        if key in ("u", ord("u")):
            self.apply_undo(); return
        if key in ("i", ord("i")):
            self.show_column_stats(); return
        if key in ("o", ord("o")):
            if self.table:
                self.cycle_sort()
            else:
                self.grid.sort_by_col(self.grid.cx)
            return
        if key == "/":
            if self.table:
                self.set_filter()
            else:
                q = self.prompt("Search in results: ", self.grid.search_query)
                if q is not None:
                    count = self.grid.search(q)
                    self.set_msg(f"{count} match(es)" if count else "No matches")
            return
        if key in ("]", "["):
            if self.grid.next_match(1 if key == "]" else -1):
                idx = self.grid.search_idx + 1
                total = len(self.grid.search_matches)
                self.set_msg(f"Match {idx}/{total}")
            else:
                self.set_msg("No active search", True)
            return
        if key == KEY_ESC:
            if self.grid.search_query:
                self.grid.search_query = ""
                self.grid.search_matches = []
                self.grid.search_idx = -1
                self.set_msg("Search cleared")
            elif self.where:
                self.where = ""
                self.page = 0
                self.load_data(recount=True)
                self.set_msg("Filter cleared")
            return
        if key == "c" and self.table:
            self.where, self.order, self.desc, self.page = "", None, False, 0
            self.load_data(recount=True)
            return
        if key == "v":
            self.record_view(); return
        if key == "y":
            self.copy_cell(); return
        if key == "x":
            self.export_prompt(); return
        if key == "p" and self.table:
            self.goto_page(); return

        if self.table and key == curses.KEY_NPAGE:
            self.change_page(1); return
        if self.table and key == curses.KEY_PPAGE:
            self.change_page(-1); return

        action = self.grid.handle_key(key)
        if action == "down" and self.table and self.change_page(1):
            self.grid.cy = 0
        elif action == "up" and self.table and self.change_page(-1):
            self.grid.cy = max(0, len(self.grid.rows) - 1)

    def column_info(self, name: str) -> Column:
        for c in self.table_cols:
            if c.name.lower() == name.lower():
                return c
        return Column(name, "", 0, None, 0)

    def view_cell(self) -> None:
        row = self.grid.current()
        if not row or not self.grid.cols or self.grid.cx >= len(self.grid.cols):
            return
        col = self.grid.cols[self.grid.cx]
        val = row[self.grid.cx]
        lines = [f"# {col} (Row {self.grid.cy + 1})", ""]
        if val is None:
            lines.append("NULL")
        elif isinstance(val, bytes):
            lines.append(f"Type: BLOB ({len(val)} bytes)")
            lines.append(f"Hex: {val[:256].hex(' ')}{' ...' if len(val) > 256 else ''}")
            try:
                txt = val.decode("utf-8")
                lines += ["", "# Decoded UTF-8 text:", *txt.splitlines()]
            except UnicodeDecodeError:
                pass
        else:
            s = str(val)
            if s.startswith("{") or s.startswith("["):
                try:
                    parsed = json.loads(s)
                    pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
                    lines += ["# Detected JSON format:", *pretty.splitlines()]
                except Exception:
                    lines += s.splitlines()
            else:
                lines += s.splitlines()
        self.show_text(f"Cell: {col}", lines)

    def edit_cell(self) -> None:
        if self.db.readonly:
            self.set_msg("Action blocked: database is in read-only mode", True)
            return
        g = self.grid
        if not g.rows:
            return
        if not self.key.editable:
            self.set_msg("Read-only: table has no rowid or primary key", True)
            return
        name = g.cols[g.cx]
        raw = g.rows[g.cy][g.cx]
        if isinstance(raw, bytes):
            self.set_msg("BLOBs cannot be edited directly here", True)
            return
        initial = "NULL" if raw is None else str(raw)
        decl = self.column_info(name).type
        new = self.prompt(f"{name} [{decl or 'ANY'}] (NULL = null): ", initial)
        if new is None or new == initial:
            return
        value = parse_input(new, decl)

        keyvals = self.keyvals[g.cy]
        where_parts = []
        for (k_name, _), k_val in zip(self.key.keys, keyvals):
            if k_val is None:
                where_parts.append(f"{quote_ident(k_name)} IS NULL")
            else:
                where_parts.append(f"{quote_ident(k_name)} = {sql_literal(k_val)}")
        where_clause = " AND ".join(where_parts)
        undo_sql = f"UPDATE {quote_ident(self.table)} SET {quote_ident(name)} = {sql_literal(raw)} WHERE {where_clause};"

        n = self.db.update_cell(self.table, name, value, self.key, keyvals)
        self.record_undo(f"Edit cell «{name}» in «{self.table}»", undo_sql, self.table)
        fname = os.path.basename(self.undo_file_path)
        self.set_msg(f"{n} row(s) updated · Undo SQL in {fname} (press 'u' to restore)")
        self.load_data(keep=True)

    def insert_row(self) -> None:
        if self.db.readonly:
            self.set_msg("Action blocked: database is in read-only mode", True)
            return
        values: dict[str, Any] = {}
        for c in self.table_cols:
            v = self.prompt(f"{c.name} [{c.type or 'ANY'}] (empty = DEFAULT): ", "")
            if v is None:
                self.set_msg("Insert cancelled")
                return
            if v != "":
                values[c.name] = parse_input(v, c.type)
        try:
            cur = self.db.conn.cursor()
            if values:
                names = ", ".join(quote_ident(n) for n in values)
                marks = ", ".join("?" * len(values))
                sql = f"INSERT INTO {quote_ident(self.table)} ({names}) VALUES ({marks})"
                cur.execute(sql, list(values.values()))
            else:
                cur.execute(f"INSERT INTO {quote_ident(self.table)} DEFAULT VALUES")
            last_id = cur.lastrowid
            cur.close()
        except sqlite3.Error as e:
            self.set_msg(f"Insert failed: {e}", True)
            return

        if self.key.extra and last_id:
            undo_sql = f"DELETE FROM {quote_ident(self.table)} WHERE rowid = {last_id};"
        elif self.key.keys:
            where_parts = []
            for k_name, _ in self.key.keys:
                if k_name in values:
                    where_parts.append(f"{quote_ident(k_name)} = {sql_literal(values[k_name])}")
            where_clause = " AND ".join(where_parts) if where_parts else f"rowid = {last_id}"
            undo_sql = f"DELETE FROM {quote_ident(self.table)} WHERE {where_clause};"
        else:
            undo_sql = f"DELETE FROM {quote_ident(self.table)} WHERE rowid = {last_id};"

        self.record_undo(f"Insert row in «{self.table}»", undo_sql, self.table)
        fname = os.path.basename(self.undo_file_path)
        self.set_msg(f"Row inserted · Undo SQL in {fname} (press 'u' to restore)")
        self.load_data(keep=True, recount=True)

    def delete_row(self) -> None:
        if self.db.readonly:
            self.set_msg("Action blocked: database is in read-only mode", True)
            return
        g = self.grid
        if not g.rows:
            return
        if not self.key.editable:
            self.set_msg("Read-only: table has no rowid or primary key", True)
            return
        if not self.confirm(f"Delete row {g.cy + 1} from «{self.table}»?"):
            return

        keyvals = self.keyvals[g.cy]
        row_vals = g.rows[g.cy]
        cols = list(g.cols)

        has_int_pk = any(c.pk for c in self.db.columns(self.table) if "INT" in c.type.upper())
        include_rowid = self.key.extra and not has_int_pk and ("rowid" not in [c.lower() for c in cols])

        if include_rowid:
            rowid_val = keyvals[0]
            col_list = "rowid, " + ", ".join(quote_ident(c) for c in cols)
            val_list = f"{rowid_val}, " + ", ".join(sql_literal(v) for v in row_vals)
        else:
            col_list = ", ".join(quote_ident(c) for c in cols)
            val_list = ", ".join(sql_literal(v) for v in row_vals)
        undo_sql = f"INSERT INTO {quote_ident(self.table)} ({col_list}) VALUES ({val_list});"

        n = self.db.delete_row(self.table, self.key, keyvals)
        self.record_undo(f"Delete row from «{self.table}»", undo_sql, self.table)
        fname = os.path.basename(self.undo_file_path)
        self.set_msg(f"{n} row(s) deleted · Undo SQL in {fname} (press 'u' to restore)")
        self.load_data(keep=True, recount=True)

    def cycle_sort(self) -> None:
        if not self.grid.cols:
            return
        name = self.grid.cols[self.grid.cx]
        if self.order != name:
            self.order, self.desc = name, False
        elif not self.desc:
            self.desc = True
        else:
            self.order, self.desc = None, False
        self.page = 0
        self.load_data()

    def set_filter(self) -> None:
        v = self.prompt("WHERE clause (empty = no filter): ", self.where)
        if v is None:
            return
        old, self.where = self.where, v.strip()
        self.page = 0
        if not self.load_data(recount=True):
            self.where = old
            self.load_data(recount=True)

    def goto_page(self) -> None:
        v = self.prompt(f"Go to page (1-{self.pages()}): ", str(self.page + 1))
        if v and v.isdigit():
            self.page = max(0, min(int(v) - 1, self.pages() - 1))
            self.load_data()

    def record_view(self) -> None:
        g = self.grid
        row = g.current()
        if row is None:
            return
        lines = [f"# Record {g.cy + 1} of {len(g.rows)}", ""]
        for c, v in zip(g.cols, row):
            lines.append(f"┌─ {c} " + "─" * max(0, 42 - str_width(c)))
            if v is None:
                lines.append("  NULL")
            elif isinstance(v, bytes):
                lines.append(f"  <BLOB {len(v)} bytes>")
                lines.append(f"  hex: {v[:64].hex(' ')}{' ...' if len(v) > 64 else ''}")
                try:
                    txt = v.decode("utf-8")
                    lines += ["  utf-8 text:", *[f"    {l}" for l in txt.splitlines()[:20]]]
                except UnicodeDecodeError:
                    pass
            elif isinstance(v, str) and ("\n" in v or "\r" in v):
                for l in v.splitlines():
                    lines.append(f"  {l}")
            elif isinstance(v, str) and (v.startswith("{") or v.startswith("[")):
                try:
                    parsed = json.loads(v)
                    pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
                    lines += ["  (Detected JSON format):", *[f"    {l}" for l in pretty.splitlines()]]
                except Exception:
                    lines.append(f"  {v}")
            else:
                lines.append(f"  {v}")
            lines.append("")
        self.show_text(f"Record {g.cy + 1}", lines)

    def copy_cell(self) -> None:
        row = self.grid.current()
        if row is None:
            return
        v = row[self.grid.cx]
        text = "" if v is None else (v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v))
        if osc52_copy(text):
            self.set_msg(f"Copied ({len(text)} characters) · requires OSC 52 terminal")
        else:
            self.set_msg("Failed to copy", True)

    def export_prompt(self) -> None:
        in_table = bool(self.table)
        if not in_table and not self.grid.cols:
            self.set_msg("No results to export", True)
            return
        default = f"{self.table}.csv" if in_table else "results.csv"
        path = self.prompt("Export to (.csv, .json, .sql, .md, .tsv): ", default)
        if not path:
            return
        path = os.path.expanduser(path.strip())
        tbl = self.table or "results"
        if in_table:
            cur = self.db.conn.execute(self.db.table_sql(self.table, self.key, self.where, self.order, self.desc))
            cols = [d[0] for d in cur.description]
            off = 1 if self.key.extra else 0
            n = export_rows(path, cols[off:], (r[off:] for r in cur), table_name=tbl)
        else:
            n = export_rows(path, self.grid.cols, self.grid.rows, table_name=tbl)
        self.set_msg(f"Exported {n} rows to {path}")

    def import_csv(self) -> None:
        if self.db.readonly:
            self.set_msg("Action blocked: database is in read-only mode", True)
            return
        path = self.prompt("Import CSV/TSV file (with header): ", "")
        if not path:
            return
        full_path = os.path.expanduser(path.strip())
        if not os.path.isfile(full_path):
            self.set_msg(f"File not found: {path}", True)
            return
        table = self.table or self.prompt("Destination table: ", "")
        if not table:
            return
        table = table.strip()
        conn = self.db.conn

        try:
            max_rowid_before = conn.execute(f"SELECT COALESCE(MAX(rowid), 0) FROM {quote_ident(table)}").fetchone()[0]
        except sqlite3.Error:
            max_rowid_before = 0

        try:
            with open(full_path, newline="", encoding="utf-8", errors="replace") as f:
                sample = f.read(8192)
                f.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample)
                except Exception:
                    dialect = csv.excel
                reader = csv.reader(f, dialect)
                header = next(reader, None)
                if not header:
                    self.set_msg("CSV file is empty", True)
                    return
                existing = conn.execute("SELECT 1 FROM sqlite_master WHERE type in ('table','view') AND name = ?",
                                        (table,)).fetchone()
                if not existing:
                    if not self.confirm(f"Table «{table}» does not exist. Create automatically?"):
                        return
                    col_defs = ", ".join(f"{quote_ident(h)} TEXT" for h in header)
                    conn.execute(f"CREATE TABLE {quote_ident(table)} ({col_defs})")

                sql = (f"INSERT INTO {quote_ident(table)} ({', '.join(quote_ident(h) for h in header)}) "
                       f"VALUES ({', '.join('?' * len(header))})")
                conn.execute("SAVEPOINT csv_import")
                n = 0
                for row_idx, row in enumerate(reader, start=2):
                    if not row:
                        continue
                    if len(row) < len(header):
                        row = row + [None] * (len(header) - len(row))
                    elif len(row) > len(header):
                        row = row[:len(header)]
                    conn.execute(sql, [v if v != "" else None for v in row])
                    n += 1
                conn.execute("RELEASE csv_import")
        except sqlite3.Error as e:
            try:
                conn.execute("ROLLBACK TO csv_import")
                conn.execute("RELEASE csv_import")
            except sqlite3.Error:
                pass
            self.set_msg(f"Error in CSV row {row_idx if 'row_idx' in locals() else '?'}: {e}", True)
            return
        except OSError as e:
            self.set_msg(f"Read error: {e}", True)
            return

        if not existing:
            undo_sql = f"DROP TABLE {quote_ident(table)};"
        else:
            undo_sql = f"DELETE FROM {quote_ident(table)} WHERE rowid > {max_rowid_before};"
        self.record_undo(f"Import {n} rows into «{table}»", undo_sql, table)

        self.refresh_schema()
        if self.table == table:
            self.load_data(keep=True, recount=True)
        fname = os.path.basename(self.undo_file_path)
        self.set_msg(f"Imported {n} rows into {table} · Undo SQL in {fname} (press 'u' to restore)")

    def theme_menu(self) -> None:
        items = list(THEMES.keys())
        labels = [f"{THEMES[k]['name']}" + (" (active)" if k == self.theme_key else "") for k in items]
        i = self.choose("Select Visual Theme", labels)
        if i is not None:
            self.theme_key = items[i]
            self.cfg["theme"] = self.theme_key
            save_config(self.cfg)
            apply_theme(self.theme_key)
            self.set_msg(f"Theme switched to: {THEMES[self.theme_key]['name']}")

    # ----- prompts, confirmations and menus --------------------------------

    def prompt(self, label: str, initial: str = "") -> Optional[str]:
        buf = list(initial)
        pos = len(buf)
        while True:
            self.draw()
            H, W = self.scr.getmaxyx()
            shown = label
            if str_width(shown) + 24 > W:
                shown = fit_str(label, max(8, W - 24))
            label_w = str_width(shown)
            avail = max(1, W - label_w)
            start = max(0, pos - avail + 1)
            visible = "".join(buf[start:start + avail])
            put(self.scr, H - 1, 0, " " * W, 0, W)
            put(self.scr, H - 1, 0, shown, A["prompt"], label_w)
            put(self.scr, H - 1, label_w, visible, 0, avail)
            try:
                cursor_x = label_w + str_width("".join(buf[start:pos]))
                self.scr.move(H - 1, min(W - 1, cursor_x))
                curses.curs_set(1)
            except curses.error:
                pass
            curses.doupdate()
            key = read_key(self.scr)
            if key is None or key == curses.KEY_RESIZE:
                continue
            if key in KEY_ENTER:
                return "".join(buf)
            if key in (KEY_ESC, CTRL_C):
                return None
            if key in (curses.KEY_LEFT,):
                pos = max(0, pos - 1)
            elif key in (curses.KEY_RIGHT,):
                pos = min(len(buf), pos + 1)
            elif key in (curses.KEY_HOME, CTRL_A):
                pos = 0
            elif key in (curses.KEY_END, CTRL_E):
                pos = len(buf)
            elif key in KEY_BACK:
                if pos > 0:
                    del buf[pos - 1]
                    pos -= 1
            elif key in (curses.KEY_DC, CTRL_D):
                if pos < len(buf):
                    del buf[pos]
            elif key == CTRL_W:
                while pos > 0 and buf[pos - 1].isspace():
                    del buf[pos - 1]
                    pos -= 1
                while pos > 0 and not buf[pos - 1].isspace():
                    del buf[pos - 1]
                    pos -= 1
            elif key == CTRL_K:
                buf = buf[:pos]
            elif key == CTRL_U:
                buf = buf[pos:]
                pos = 0
            elif isinstance(key, str):
                buf.insert(pos, key)
                pos += 1

    def confirm(self, msg: str) -> bool:
        while True:
            self.draw()
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            H, W = self.scr.getmaxyx()
            w = min(W - 4, max(38, min(str_width(msg) + 8, 76)))
            inner = max(10, w - 6)
            lines = wrap_text(msg, inner)
            h = min(H - 2, len(lines) + 5)
            max_lines = max(1, h - 4)
            if len(lines) > max_lines:
                lines = lines[:max_lines]
                lines[-1] = fit_str(lines[-1] + " …", inner)
            win = self.popup("Confirm", h, w)
            for i, line in enumerate(lines):
                put(win, 2 + i, 3, line, A["warn"], inner)
            put(win, h - 2, 3, "[ y ] Yes", A["sel"], 10)
            put(win, h - 2, 15, "[ n ] Cancel", A["dim"], 13)
            win.noutrefresh()
            curses.doupdate()
            key = read_key(self.scr)
            if key is None or key == curses.KEY_RESIZE:
                continue
            if key == curses.KEY_MOUSE:
                event = self._read_mouse()
                if event is None:
                    continue
                mx, my, bstate = event
                if bstate & MOUSE_B1:
                    hit = self._window_hit(win, mx, my)
                    if hit is None:
                        return False
                    wx, wy = hit
                    button_y = win.getmaxyx()[0] - 2
                    if wy == button_y and 3 <= wx < 13:
                        return True
                    if wy == button_y and 15 <= wx < 28:
                        return False
                continue
            return key in ("y", "Y", "s", "S")

    def _read_mouse(self) -> Optional[tuple[int, int, int]]:
        try:
            _id, mx, my, _z, bstate = curses.getmouse()
            return mx, my, bstate
        except curses.error:
            return None

    @staticmethod
    def _window_hit(win, mx: int, my: int) -> Optional[tuple[int, int]]:
        """Returns window-relative (x, y) when the click is inside the popup."""
        try:
            py, px = win.getbegyx()
            wh, ww = win.getmaxyx()
        except curses.error:
            return None
        if px <= mx < px + ww and py <= my < py + wh:
            return mx - px, my - py
        return None

    def popup(self, title: str, h: int, w: int):
        H, W = self.scr.getmaxyx()
        h, w = min(h, H - 2), min(w, W - 2)
        win = curses.newwin(h, w, (H - h) // 2, (W - w) // 2)
        win.keypad(True)
        win.erase()
        draw_box(win, 0, 0, h, w, title, True)
        return win

    def choose(self, title: str, items: list[str]) -> Optional[int]:
        if not items:
            return None
        needle = ""
        idx = top = 0
        filtered = list(enumerate(items))
        while True:
            self.draw()
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            H, W = self.scr.getmaxyx()
            h = min(max(9, len(items) + 5), max(9, H - 2))
            longest = max(max((str_width(lbl) for lbl in items), default=10), str_width(title))
            w = min(max(longest + 8, 44), W - 2)
            inner = max(1, h - 4)
            if filtered:
                idx = max(0, min(idx, len(filtered) - 1))
            else:
                idx = 0
            if idx < top:
                top = idx
            if idx >= top + inner:
                top = idx - inner + 1
            win = self.popup(title if not needle else f"{title} · /{needle}", h, w)
            put(win, 1, 2, "/ " + needle, A["hdr"], w - 4)
            if not filtered:
                put(win, 2, 2, "  (no matches — Backspace to edit filter)", A["dim"], w - 4)
            for row in range(inner):
                i = top + row
                if i >= len(filtered):
                    break
                _orig, label = filtered[i]
                text = pad_str(fit_str(label, w - 4), w - 4)
                put(win, 2 + row, 2, text, A["sel"] if i == idx else 0, w - 4)
            pos = f" {idx + 1}/{len(filtered)} " if filtered else " 0/0 "
            fw = max(0, w - 4 - str_width(pos))
            footer = " ↑↓ move · Enter open · Esc cancel · type to filter "
            if str_width(footer) > fw:
                footer = " ↑↓ · Enter · Esc · filter "
            if str_width(footer) > fw:
                footer = " ↑↓ Enter Esc "
            put(win, h - 2, 2, fit_str(footer, fw), A["dim"], fw)
            put(win, h - 2, w - 2 - str_width(pos), pos, A["dim"], str_width(pos))
            win.noutrefresh()
            curses.doupdate()
            key = read_key(self.scr)
            if key is None or key == curses.KEY_RESIZE:
                continue
            if key == curses.KEY_MOUSE:
                event = self._read_mouse()
                if event is None:
                    continue
                mx, my, bstate = event
                if bstate & MOUSE_WHEEL_UP:
                    idx = max(0, idx - 1)
                elif bstate & MOUSE_WHEEL_DOWN:
                    idx = min(len(filtered) - 1, idx + 1) if filtered else 0
                elif bstate & MOUSE_B1:
                    hit = self._window_hit(win, mx, my)
                    if hit is None:
                        return None
                    _wx, wy = hit
                    row = wy - 2
                    if 0 <= row < inner and top + row < len(filtered):
                        idx = top + row
                        return filtered[idx][0]
                continue
            if key == curses.KEY_UP:
                idx = max(0, idx - 1)
            elif key == curses.KEY_DOWN:
                idx = min(len(filtered) - 1, idx + 1) if filtered else 0
            elif key == curses.KEY_PPAGE:
                idx = max(0, idx - inner)
            elif key == curses.KEY_NPAGE:
                idx = min(len(filtered) - 1, idx + inner) if filtered else 0
            elif key in KEY_BACK:
                if needle:
                    needle = needle[:-1]
                    filtered = [(i, s) for i, s in enumerate(items) if needle.lower() in s.lower()]
                    idx = top = 0
            elif key in KEY_ENTER:
                if filtered:
                    return filtered[idx][0]
            elif key == CTRL_C:
                return None
            elif key == KEY_ESC:
                if needle:
                    needle = ""
                    filtered = list(enumerate(items))
                    idx = top = 0
                else:
                    return None
            elif isinstance(key, str) and key.isprintable():
                needle += key
                filtered = [(i, s) for i, s in enumerate(items) if needle.lower() in s.lower()]
                idx = top = 0

    def show_text(self, title: str, lines: list[str]) -> None:
        view = TextView()
        view.set(lines)
        while True:
            self.draw()
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            H, W = self.scr.getmaxyx()
            h, w = max(6, H - 4), max(30, W - 6)
            win = self.popup(title, h, w)
            view.draw(win, 1, 2, h - 3, w - 4)
            if lines:
                pos = (f" {min(view.top + 1, len(lines))}-"
                       f"{min(view.top + h - 3, len(lines))}/{len(lines)} ")
            else:
                pos = " 0/0 "
            hint = " ↑↓/jk scroll · g/G top/bottom · Esc/q close "
            fw = max(0, w - 4 - str_width(pos))
            put(win, h - 2, 2, fit_str(hint, fw), A["dim"], fw)
            put(win, h - 2, w - 2 - str_width(pos), pos, A["dim"], str_width(pos))
            win.noutrefresh()
            curses.doupdate()
            key = read_key(self.scr)
            if key is None:
                continue
            if key == curses.KEY_MOUSE:
                event = self._read_mouse()
                if event is None:
                    continue
                mx, my, bstate = event
                if self._window_hit(win, mx, my) is None:
                    return
                if bstate & MOUSE_WHEEL_UP:
                    for _ in range(3):
                        view.handle_key(curses.KEY_UP)
                elif bstate & MOUSE_WHEEL_DOWN:
                    for _ in range(3):
                        view.handle_key(curses.KEY_DOWN)
                continue
            if key in (KEY_ESC, "q", "Q", CTRL_C) or key in KEY_ENTER or key == curses.KEY_F1:
                return
            if key == curses.KEY_RESIZE:
                continue
            view.handle_key(key)

    def menu(self) -> bool:
        fk_on = bool(self.db.conn.execute("PRAGMA foreign_keys").fetchone()[0])
        items = [
            ("Execute all (F5)", lambda: self.run_sql(False)),
            ("Execute current statement (F9)", lambda: self.run_sql(True)),
            ("Browse & open database or SQL script… (Ctrl+O)", self.open_db),
            ("Save SQL script… (Ctrl+S)", self.save_sql_file),
            ("Format SQL query in editor (Ctrl+P)", self.format_editor_sql),
            ("Follow foreign key from current cell (f)", self.follow_fk),
            ("Find referencing records (incoming FK) (F)", self.find_incoming_fks),
            ("Navigate back in history (b)", self.nav_back),
            ("Undo last change (Undo SQL)…", self.apply_undo),
            ("View restore SQL file (Undo Log)…", self.view_undo_log),
            ("Switch visual theme (T)…", self.theme_menu),
            ("Toggle zoom on active panel (z)", self.toggle_zoom),
            ("Load interactive demo database…", self.load_demo_db),
            ("Query history…", self.history_menu),
            ("Template: create table", self.template_create),
            ("Export table or results (CSV/JSON/SQL/MD/TSV)…", self.export_prompt),
            ("Import CSV/TSV into current table…", self.import_csv),
            ("Dump database to SQL file…", self.dump),
            ("Backup database…", self.backup),
            ("Attach another database (ATTACH)…", self.attach),
            ("Transaction: BEGIN", self.tx_begin),
            ("Transaction: COMMIT", self.tx_commit),
            ("Transaction: ROLLBACK", self.tx_rollback),
            (f"Foreign keys: {'disable' if fk_on else 'enable'}", self.toggle_fk),
            ("Database info", lambda: self.show_text("Database Info", db_info(self.db))),
            ("Check integrity", self.integrity),
            ("VACUUM", lambda: self.run_maintenance("VACUUM")),
            ("ANALYZE", lambda: self.run_maintenance("ANALYZE")),
            ("PRAGMA optimize", lambda: self.run_maintenance("PRAGMA optimize")),
            ("Refresh schema", self.refresh_schema),
            ("Help (F1 / ?)", lambda: self.show_text("Help", HELP_LINES)),
            ("Quit (Ctrl+Q)", self.quit),
        ]
        i = self.choose("Command Menu", [label for label, _ in items])
        if i is None:
            return False
        return bool(items[i][1]())

    def history_menu(self) -> None:
        if not self.history:
            self.set_msg("History is empty")
            return
        labels = [" ".join(s.split())[:120] for s in self.history]
        i = self.choose("Query History", labels)
        if i is not None:
            self.editor.set_text(self.history[i])
            self.focus_panel(3)

    def template_create(self) -> None:
        self.focus_panel(3)
        self.editor.insert_text("CREATE TABLE table_name (\n"
                                "    id INTEGER PRIMARY KEY,\n"
                                "    name TEXT NOT NULL,\n"
                                "    created_at TEXT DEFAULT CURRENT_TIMESTAMP\n);\n")

    def _resolve_db_path(self, value: str, base: str) -> str:
        """Resolves ~, absolute and relative paths against the browsed directory."""
        raw = value.strip()
        if raw == ":memory:":
            return raw
        path = os.path.expanduser(raw)
        if not os.path.isabs(path):
            path = os.path.join(base, path)
        return os.path.abspath(path)

    def _list_open_dir(self, path: str, all_files: bool) -> tuple[list[tuple[str, bool, str, int]], str]:
        """Lists parent, directories and SQLite candidates for the file browser."""
        dirs: list[tuple[str, bool, str, int]] = []
        files: list[tuple[str, bool, str, int]] = []
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    if not all_files and entry.name.startswith("."):
                        continue
                    try:
                        if entry.is_dir():
                            dirs.append((entry.name, True, entry.path, 0))
                        elif entry.is_file() and (all_files or entry.name.lower().endswith(OPEN_EXTENSIONS)):
                            files.append((entry.name, False, entry.path, entry.stat().st_size))
                    except OSError:
                        continue
        except OSError as exc:
            return [], str(exc)
        dirs.sort(key=lambda item: item[0].lower())
        files.sort(key=lambda item: item[0].lower())
        items: list[tuple[str, bool, str, int]] = []
        parent = os.path.dirname(os.path.normpath(path))
        if parent and os.path.normpath(parent) != os.path.normpath(path):
            items.append(("..", True, parent, 0))
        items.extend(dirs)
        items.extend(files)
        return items, ""

    def pick_open_path(self) -> Optional[str]:
        """Interactive browser for .db/.sqlite files (mouse and keyboard friendly)."""
        start = self.cfg.get("last_db_dir", "")
        if not start or not os.path.isdir(start):
            if self.db.path not in (":memory:", "") and os.path.exists(self.db.path):
                start = os.path.dirname(os.path.abspath(self.db.path))
            else:
                start = os.getcwd()
        current = start
        all_files = False
        idx = top = 0
        while True:
            entries, error = self._list_open_dir(current, all_files)
            self.draw()
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            H, W = self.scr.getmaxyx()
            h = min(max(12, len(entries) + 5), H - 2)
            w = min(max(52, W * 2 // 3), W - 4)
            inner = max(1, h - 4)
            if entries:
                idx = max(0, min(idx, len(entries) - 1))
            else:
                idx = top = 0
            if idx < top:
                top = idx
            if idx >= top + inner:
                top = idx - inner + 1
            title = "Open Database or SQL Script" + (" · all files" if all_files else "")
            win = self.popup(title, h, w)
            put(win, 1, 2, fit_str(short_path(current), w - 4), A["hdr"], w - 4)
            if not entries:
                message = error or "  (empty directory)"
                put(win, 2, 2, fit_str(message, w - 4), A["err"] if error else A["dim"], w - 4)
            for row in range(inner):
                i = top + row
                if i >= len(entries):
                    break
                name, is_dir, _full, size = entries[i]
                label = f"  {name}/" if is_dir else f"  {name}"
                size_text = "" if is_dir else human_size(size) + " "
                text_w = max(1, w - 4 - str_width(size_text))
                line = pad_str(fit_str(label, text_w), text_w) + size_text
                attr = A["sel"] if i == idx else (A["hdr"] if is_dir else 0)
                put(win, 2 + row, 2, line, attr, w - 4)

            pos = f" {idx + 1}/{len(entries)} " if entries else " 0/0 "
            hint = " ↑↓ move · Enter open · ⌫ up · a all · p path · n new · Esc "
            if str_width(hint) + str_width(pos) > w - 4:
                hint = " ↑↓ Enter · ⌫ up · a all · p path · Esc "
            if str_width(hint) + str_width(pos) > w - 4:
                hint = " ↑↓ Enter Esc "
            fw = max(0, w - 4 - str_width(pos))
            put(win, h - 2, 2, fit_str(hint, fw), A["dim"], fw)
            put(win, h - 2, w - 2 - str_width(pos), pos, A["dim"], str_width(pos))
            win.noutrefresh()
            curses.doupdate()
            key = read_key(self.scr)
            if key is None or key == curses.KEY_RESIZE:
                continue

            if key == curses.KEY_MOUSE:
                event = self._read_mouse()
                if event is None:
                    continue
                mx, my, bstate = event
                if bstate & MOUSE_WHEEL_UP:
                    idx = max(0, idx - 3)
                elif bstate & MOUSE_WHEEL_DOWN:
                    idx = min(len(entries) - 1, idx + 3) if entries else 0
                elif bstate & MOUSE_DBL:
                    hit = self._window_hit(win, mx, my)
                    if hit is None:
                        return None
                    _wx, wy = hit
                    row = wy - 2
                    if 0 <= row < inner and top + row < len(entries):
                        idx = top + row
                        _name, is_dir, full, _size = entries[idx]
                        if is_dir:
                            current, idx, top = full, 0, 0
                        else:
                            return full
                elif bstate & MOUSE_B1:
                    hit = self._window_hit(win, mx, my)
                    if hit is None:
                        return None
                    _wx, wy = hit
                    row = wy - 2
                    if 0 <= row < inner and top + row < len(entries):
                        idx = top + row
                continue

            if key in (KEY_ESC, "q", "Q", CTRL_C):
                return None
            if key in (curses.KEY_UP, "k"):
                idx = max(0, idx - 1)
            elif key in (curses.KEY_DOWN, "j"):
                idx = min(len(entries) - 1, idx + 1) if entries else 0
            elif key == curses.KEY_PPAGE:
                idx = max(0, idx - inner)
            elif key == curses.KEY_NPAGE:
                idx = min(len(entries) - 1, idx + inner) if entries else 0
            elif key in (curses.KEY_HOME, "g"):
                idx = 0
            elif key in (curses.KEY_END, "G"):
                idx = max(0, len(entries) - 1)
            elif key in KEY_ENTER or key == curses.KEY_RIGHT:
                if entries:
                    _name, is_dir, full, _size = entries[idx]
                    if is_dir:
                        current, idx, top = full, 0, 0
                    else:
                        return full
            elif key in KEY_BACK or key == curses.KEY_LEFT:
                parent = os.path.dirname(os.path.normpath(current))
                if parent and os.path.normpath(parent) != os.path.normpath(current):
                    current, idx, top = parent, 0, 0
            elif key in ("a", "A"):
                all_files = not all_files
                idx = top = 0
            elif key in ("p", "P"):
                del win
                typed = self.prompt("Path or database: ", current.rstrip(os.sep) + os.sep)
                if not typed:
                    continue
                target = self._resolve_db_path(typed, current)
                if os.path.isdir(target):
                    current, idx, top = target, 0, 0
                else:
                    return target
            elif key in ("n", "N"):
                del win
                typed = self.prompt("New database file: ", current.rstrip(os.sep) + os.sep)
                if typed:
                    return self._resolve_db_path(typed, current)

    def open_db(self) -> None:
        path = self.pick_open_path()
        if not path:
            return
        path = os.path.expanduser(path.strip())
        if path.lower().endswith(SQL_EXTENSIONS):
            self.load_sql_file(os.path.abspath(path))
            return
        self._open_db_path(path)

    def _open_db_path(self, path: str) -> None:
        path = os.path.expanduser(path.strip())
        if path != ":memory:":
            path = os.path.abspath(path)
            if os.path.isdir(path):
                self.set_msg(f"Not a database file: {short_path(path)}", True)
                return
            if not os.path.exists(path):
                if self.db.readonly:
                    self.set_msg("Cannot create a database in read-only mode", True)
                    return
                if not self.confirm(f"Database «{short_path(path)}» does not exist. Create it?"):
                    return
        try:
            new = Db(path, readonly=self.db.readonly)
        except sqlite3.Error as e:
            self.set_msg(f"Could not open database: {e}", True)
            return
        if self.db.conn.in_transaction:
            if not self.confirm("There is an open transaction. ROLLBACK and switch database?"):
                new.close()
                return
            self.db.conn.execute("ROLLBACK")
        self.db.close()
        self.db = new
        self.table = None
        self.table_cols = []
        self.key = KeyInfo(False, [])
        self.keyvals = []
        self.where = ""
        self.order = None
        self.desc = False
        self.page = 0
        self.total = 0
        self.result_title = ""
        self._cached_total_table = None
        self._cached_total_where = None
        self.side.filter = ""
        self.side.cy = self.side.top = 0
        self.side.invalidate()
        self.col_list.table = ""
        self.col_list.cols = []
        self.col_list.fks = []
        self.col_list.cy = self.col_list.top = 0
        self.grid.set_data([], [])
        self.grid.sort = (None, False)
        self.grid.search_query = ""
        self.grid.search_matches = []
        self.grid.search_idx = -1
        self.undo_stack.clear()
        self.undo_file_path = self.get_undo_file_path()
        if path != ":memory:":
            self.cfg["last_db_dir"] = os.path.dirname(path)
            save_config(self.cfg)
        self.refresh_schema()
        self.focus_panel(1)
        self.set_msg(f"Database opened: {short_path(path)}")

    def load_sql_file(self, path: str) -> None:
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError as exc:
            self.set_msg(f"Cannot read SQL file: {exc}", True)
            return
        self.editor.set_text(text)
        self.last_sql_path = path
        self.cfg["last_sql_dir"] = os.path.dirname(os.path.abspath(path))
        self.cfg["last_db_dir"] = os.path.dirname(os.path.abspath(path))
        save_config(self.cfg)
        self.focus_panel(3)
        self.set_msg(f"SQL script loaded: {short_path(path)} · F5 to run")

    def save_sql_file(self) -> None:
        default = self.last_sql_path or os.path.join(self.cfg.get("last_sql_dir", os.getcwd()), "query.sql")
        path = self.prompt("Save SQL script to: ", default)
        if not path:
            return
        path = os.path.expanduser(path.strip())
        if not os.path.isabs(path):
            path = os.path.abspath(path)
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(self.editor.text)
        except OSError as exc:
            self.set_msg(f"Cannot save SQL file: {exc}", True)
            return
        self.last_sql_path = path
        self.cfg["last_sql_dir"] = os.path.dirname(path)
        save_config(self.cfg)
        self.set_msg(f"SQL script saved: {short_path(path)}")

    def dump(self) -> None:
        if self.db.path == ":memory:":
            default = "base.sql"
        else:
            stem = os.path.splitext(os.path.basename(self.db.path))[0] or "base"
            default = f"{stem}.sql"
        path = self.prompt("Dump to SQL file: ", default)
        if not path:
            return
        n = 0
        with open(os.path.expanduser(path.strip()), "w", encoding="utf-8") as f:
            for line in self.db.conn.iterdump():
                f.write(line + "\n")
                n += 1
        self.set_msg(f"Dumped {n} lines to {path}")

    def backup(self) -> None:
        if self.db.path == ":memory:":
            default = "backup.db"
        else:
            default = os.path.splitext(self.db.path)[0] + "_backup.db"
        path = self.prompt("Backup database to: ", default)
        if not path:
            return
        dst = sqlite3.connect(os.path.expanduser(path.strip()))
        try:
            self.db.conn.backup(dst)
        finally:
            dst.close()
        self.set_msg(f"Backup created at {path}")

    def attach(self) -> None:
        if self.db.readonly:
            self.set_msg("Action blocked: database is in read-only mode", True)
            return
        path = self.prompt("Path to attach: ", "")
        if not path:
            return
        alias = self.prompt("Alias (use as alias.table in SQL): ", "other")
        if not alias:
            return
        self.db.conn.execute(f"ATTACH DATABASE ? AS {quote_ident(alias.strip())}", (os.path.expanduser(path.strip()),))
        self.set_msg(f"Attached as «{alias}». Query it with {alias}.table")

    def tx_begin(self) -> None:
        if self.db.readonly:
            self.set_msg("Action blocked: database is in read-only mode", True)
            return
        if self.db.conn.in_transaction:
            self.set_msg("A transaction is already open")
            return
        self.db.conn.execute("BEGIN")
        self.set_msg("Transaction started: use COMMIT or ROLLBACK")

    def tx_commit(self) -> None:
        if not self.db.conn.in_transaction:
            self.set_msg("No transaction is open")
            return
        self.db.conn.execute("COMMIT")
        self.set_msg("Transaction committed")

    def tx_rollback(self) -> None:
        if not self.db.conn.in_transaction:
            self.set_msg("No transaction is open")
            return
        self.db.conn.execute("ROLLBACK")
        self.set_msg("Transaction rolled back")

    def toggle_fk(self) -> None:
        if self.db.conn.in_transaction:
            self.set_msg("Cannot toggle foreign keys inside an active transaction", True)
            return
        on = bool(self.db.conn.execute("PRAGMA foreign_keys").fetchone()[0])
        self.db.conn.execute(f"PRAGMA foreign_keys = {'OFF' if on else 'ON'}")
        self.set_msg(f"Foreign keys: {'OFF' if on else 'ON'}")

    def integrity(self) -> None:
        rows = self.db.conn.execute("PRAGMA integrity_check").fetchall()
        self.show_text("Integrity Check", ["# Integrity Check Results"] + [str(r[0]) for r in rows])

    def run_maintenance(self, sql: str) -> None:
        if self.db.readonly:
            self.set_msg("Action blocked: database is in read-only mode", True)
            return
        res = self.db.run(sql)
        self.set_msg(f"{sql}: {res.message}")


# ---------------------------------------------------------------------------
# Application Entry Point
# ---------------------------------------------------------------------------

def _run(scr, db: Db) -> None:
    init_ui(scr)
    App(scr, db).run()


def main(argv: Optional[list[str]] = None) -> int:
    os.environ.setdefault("ESCDELAY", "25")
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass
    ap = argparse.ArgumentParser(
        prog=APP,
        description="Rich, keyboard-driven terminal SQLite explorer & editor (lazygit style)."
    )
    ap.add_argument("database", nargs="?", default=":memory:",
                    help="SQLite database file (created if non-existent; defaults to in-memory)")
    ap.add_argument("-r", "--readonly", action="store_true", help="open database in read-only mode")
    ap.add_argument("--demo", "--mock", action="store_true",
                    help="launch with interactive relational e-commerce demo database")
    ap.add_argument("-e", "--execute", help="execute SQL query directly and print formatted results without opening TUI")
    ap.add_argument("--format", choices=["table", "csv", "json", "md", "tsv", "sql"], default="table",
                    help="output format for --execute (default: table)")
    args = ap.parse_args(argv)
    try:
        db = Db(args.database, args.readonly)
    except sqlite3.Error as e:
        print(f"Could not open database: {e}", file=sys.stderr)
        return 1
    try:
        if args.demo:
            populate_demo_db(db)

        if args.execute:
            try:
                cur = db.conn.execute(args.execute)
                cols = [d[0] for d in cur.description] if cur.description else []
                rows = cur.fetchall()
                tbl_name = "results"
                m = re.search(r"\bFROM\s+([A-Za-z_0-9]+)", args.execute, re.IGNORECASE)
                if m:
                    tbl_name = m.group(1)
                print(format_query_output(cols, rows, args.format, table_name=tbl_name))
                return 0
            except sqlite3.Error as e:
                print(f"SQL Error: {e}", file=sys.stderr)
                return 1

        curses.wrapper(_run, db)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
