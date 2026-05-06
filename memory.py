"""
JARVIS Memory & Planning — persistent context, tasks, notes, and smart routing.

Three systems:
1. Memory — facts, preferences, project context JARVIS learns from conversations
2. Tasks — to-do items with priority, due dates, project association
3. Notes — freeform context tied to projects, people, or topics

Everything stored in SQLite. Relevant memories injected into every LLM call
so JARVIS gets smarter over time.
"""

import json
import logging
import re
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger("jarvis.memory")

DB_PATH = Path(__file__).parent / "data" / "jarvis.db"
_FEEDBACK_SYNC_MTIMES: dict[str, float] = {}
_FEEDBACK_DATE_RE = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\s*$")
_FEEDBACK_HEADER_RE = re.compile(r"^###\s+(.+?)\s+[—-]\s+(\d{2}:\d{2}:\d{2})\s*$")
_FEEDBACK_FIELD_RE = re.compile(
    r"^- \*\*(Command|Response|JARVIS said|Should have)\*\*:\s*(.*)$"
)


def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,          -- 'fact', 'preference', 'project', 'person', 'decision'
            content TEXT NOT NULL,
            source TEXT DEFAULT '',      -- what conversation/context it came from
            importance INTEGER DEFAULT 5, -- 1-10, higher = more important
            created_at REAL NOT NULL,
            last_accessed REAL,
            access_count INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            priority TEXT DEFAULT 'medium', -- 'high', 'medium', 'low'
            status TEXT DEFAULT 'open',     -- 'open', 'in_progress', 'done', 'cancelled'
            due_date TEXT,                  -- ISO date string
            due_time TEXT,                  -- HH:MM
            project TEXT DEFAULT '',
            tags TEXT DEFAULT '[]',         -- JSON array
            notes TEXT DEFAULT '',
            created_at REAL NOT NULL,
            completed_at REAL
        );

        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT DEFAULT '',
            content TEXT NOT NULL,
            topic TEXT DEFAULT '',       -- project name, person, or topic
            tags TEXT DEFAULT '[]',      -- JSON array
            created_at REAL NOT NULL,
            updated_at REAL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
            content, type, source,
            content='memories', content_rowid='id'
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS task_fts USING fts5(
            title, description, project, notes,
            content='tasks', content_rowid='id'
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS note_fts USING fts5(
            title, content, topic,
            content='notes', content_rowid='id'
        );
    """)
    conn.close()
    log.info("Memory database initialized")


# ---------------------------------------------------------------------------
# Memories — facts JARVIS learns
# ---------------------------------------------------------------------------

def _insert_memory(
    conn: sqlite3.Connection,
    *,
    content: str,
    mem_type: str = "fact",
    source: str = "",
    importance: int = 5,
    created_at: float | None = None,
) -> int:
    timestamp = time.time() if created_at is None else created_at
    cur = conn.execute(
        "INSERT INTO memories (type, content, source, importance, created_at) VALUES (?, ?, ?, ?, ?)",
        (mem_type, content, source, importance, timestamp)
    )
    mem_id = cur.lastrowid
    conn.execute(
        "INSERT INTO memory_fts (rowid, content, type, source) VALUES (?, ?, ?, ?)",
        (mem_id, content, mem_type, source)
    )
    return mem_id


def remember(
    content: str,
    mem_type: str = "fact",
    source: str = "",
    importance: int = 5,
    created_at: float | None = None,
) -> int:
    """Store a memory. Returns the memory ID."""
    conn = _get_db()
    mem_id = _insert_memory(
        conn,
        content=content,
        mem_type=mem_type,
        source=source,
        importance=importance,
        created_at=created_at,
    )
    conn.commit()
    conn.close()
    log.info(f"Stored memory [{mem_type}]: {content[:60]}")
    return mem_id


def _memory_exists(conn: sqlite3.Connection, content: str, mem_type: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM memories WHERE type = ? AND content = ? LIMIT 1",
        (mem_type, content),
    ).fetchone()
    return row is not None


def _get_feedback_log_paths() -> list[Path]:
    paths: list[Path] = [Path(__file__).parent / "jarvis-feedback-log.md"]

    try:
        from actions import _get_primary_obsidian_vault

        vault = _get_primary_obsidian_vault()
        if vault:
            _, vault_path = vault
            paths.append(Path(vault_path) / "JARVIS" / "jarvis-feedback-log.md")
    except Exception as exc:
        log.debug(f"Feedback log path discovery failed: {exc}")

    unique_paths: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique_paths.append(path)
    return unique_paths


def _parse_feedback_log_entries(text: str) -> list[dict]:
    entries: list[dict] = []
    current_date = ""
    current: dict | None = None

    def flush_current() -> None:
        if not current:
            return

        fields = current.get("fields", {})
        kind = str(current.get("kind", "")).strip()
        command = str(fields.get("Command", "")).strip()
        if not current_date or not kind or not command:
            return

        timestamp = None
        time_str = str(current.get("time", "")).strip()
        if time_str:
            try:
                timestamp = datetime.strptime(
                    f"{current_date} {time_str}", "%Y-%m-%d %H:%M:%S"
                ).timestamp()
            except ValueError:
                timestamp = None

        if kind == "confirm":
            response = str(fields.get("Response", "")).strip()
            if not response:
                return
            entries.append(
                {
                    "entry_id": f"{current_date}|{time_str}|confirm|{command}".lower(),
                    "command": command,
                    "type": "confirmed",
                    "importance": 8,
                    "created_at": timestamp,
                    "content": f"[CONFIRMED CORRECT] User said: '{command}' → JARVIS responded: '{response}'",
                }
            )
            return

        if kind == "correction":
            response = str(fields.get("JARVIS said", "")).strip()
            correction = str(fields.get("Should have", "")).strip()
            if not correction:
                return
            entries.append(
                {
                    "entry_id": f"{current_date}|{time_str}|correction|{command}".lower(),
                    "command": command,
                    "type": "correction",
                    "importance": 9,
                    "created_at": timestamp,
                    "content": (
                        f"[CORRECTION] When user says '{command}', JARVIS said '{response}' — "
                        f"but should have: {correction}"
                    ),
                }
            )

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        date_match = _FEEDBACK_DATE_RE.match(line)
        if date_match:
            flush_current()
            current = None
            current_date = date_match.group(1)
            continue

        header_match = _FEEDBACK_HEADER_RE.match(line)
        if header_match:
            flush_current()
            header_text = header_match.group(1).lower()
            if "confirmed correct" in header_text:
                kind = "confirm"
            elif "correction" in header_text:
                kind = "correction"
            else:
                current = None
                continue

            current = {"kind": kind, "time": header_match.group(2), "fields": {}}
            continue

        field_match = _FEEDBACK_FIELD_RE.match(line)
        if field_match and current:
            current["fields"][field_match.group(1)] = field_match.group(2).strip()

    flush_current()
    return entries


def sync_feedback_logs(force: bool = False) -> dict:
    """
    Import feedback entries from Markdown logs into SQLite memory.

    This turns the Obsidian feedback note into an active source of memory
    instead of a passive archive.
    """
    summary = {
        "scanned_files": 0,
        "skipped_files": 0,
        "parsed_entries": 0,
        "imported_entries": 0,
        "updated_entries": 0,
    }
    conn = _get_db()

    try:
        for path in _get_feedback_log_paths():
            if not path.exists():
                continue

            path_key = str(path.resolve())
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue

            if not force and _FEEDBACK_SYNC_MTIMES.get(path_key) == mtime:
                summary["skipped_files"] += 1
                continue

            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError as exc:
                log.warning(f"Feedback log read failed for {path}: {exc}")
                continue

            entries = _parse_feedback_log_entries(text)
            summary["scanned_files"] += 1
            summary["parsed_entries"] += len(entries)

            for entry in entries:
                content = str(entry.get("content", "")).strip()
                mem_type = str(entry.get("type", "fact")).strip()
                if not content or not mem_type:
                    continue

                source_key = f"feedback_log:{path.name}:{entry.get('entry_id', '')}"
                importance = int(entry.get("importance", 5) or 5)
                created_at = entry.get("created_at")

                if _memory_exists(conn, content, mem_type):
                    continue

                _insert_memory(
                    conn,
                    content=content,
                    mem_type=mem_type,
                    source=source_key,
                    importance=importance,
                    created_at=created_at,
                )
                summary["imported_entries"] += 1

            _FEEDBACK_SYNC_MTIMES[path_key] = mtime

        conn.commit()
    finally:
        conn.close()

    if summary["imported_entries"] or summary["updated_entries"]:
        log.info(
            "Feedback sync imported %s and updated %s entries from Markdown logs",
            summary["imported_entries"],
            summary["updated_entries"],
        )
    return summary


def _sanitize_fts_query(query: str) -> str:
    """Clean a query string for FTS5 — remove special characters that break it."""
    # Remove apostrophes, quotes, and FTS operators
    cleaned = query.replace("'", "").replace('"', "").replace("*", "").replace("-", " ")
    # Take meaningful words only
    words = [w for w in cleaned.split() if len(w) > 2]
    if not words:
        return ""
    # Join with OR for broader matching
    return " OR ".join(words[:5])


def recall(query: str, limit: int = 5) -> list[dict]:
    """Search memories by relevance. Returns most relevant matches."""
    fts_query = _sanitize_fts_query(query)
    if not fts_query:
        return []
    conn = _get_db()
    try:
        results = conn.execute("""
            SELECT m.id, m.type, m.content, m.importance, m.created_at, m.access_count
            FROM memory_fts f
            JOIN memories m ON f.rowid = m.id
            WHERE memory_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (fts_query, limit)).fetchall()
    except Exception:
        results = []

    # Update access counts
    for r in results:
        conn.execute(
            "UPDATE memories SET last_accessed = ?, access_count = access_count + 1 WHERE id = ?",
            (time.time(), r["id"])
        )
    conn.commit()
    conn.close()
    return [dict(r) for r in results]


def get_recent_memories(limit: int = 10) -> list[dict]:
    """Get most recent memories."""
    conn = _get_db()
    results = conn.execute(
        "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in results]


def get_important_memories(limit: int = 10) -> list[dict]:
    """Get highest importance memories."""
    conn = _get_db()
    results = conn.execute(
        "SELECT * FROM memories ORDER BY importance DESC, access_count DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in results]


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

def create_task(title: str, description: str = "", priority: str = "medium",
                due_date: str = "", due_time: str = "", project: str = "",
                tags: list[str] = None) -> int:
    """Create a task. Returns task ID."""
    conn = _get_db()
    cur = conn.execute(
        """INSERT INTO tasks (title, description, priority, due_date, due_time,
           project, tags, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (title, description, priority, due_date, due_time,
         project, json.dumps(tags or []), time.time())
    )
    task_id = cur.lastrowid
    conn.execute(
        "INSERT INTO task_fts (rowid, title, description, project, notes) VALUES (?, ?, ?, ?, ?)",
        (task_id, title, description, project, "")
    )
    conn.commit()
    conn.close()
    log.info(f"Created task [{priority}]: {title}")
    return task_id


def get_open_tasks(project: str = None) -> list[dict]:
    """Get all open/in-progress tasks, optionally filtered by project."""
    conn = _get_db()
    if project:
        results = conn.execute(
            "SELECT * FROM tasks WHERE status IN ('open','in_progress') AND project LIKE ? ORDER BY "
            "CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, due_date",
            (f"%{project}%",)
        ).fetchall()
    else:
        results = conn.execute(
            "SELECT * FROM tasks WHERE status IN ('open','in_progress') ORDER BY "
            "CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, due_date"
        ).fetchall()
    conn.close()
    return [dict(r) for r in results]


def get_tasks_for_date(date_str: str) -> list[dict]:
    """Get tasks due on a specific date (YYYY-MM-DD)."""
    conn = _get_db()
    results = conn.execute(
        "SELECT * FROM tasks WHERE due_date = ? AND status != 'cancelled' ORDER BY "
        "CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, due_time",
        (date_str,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in results]


def complete_task(task_id: int):
    """Mark a task as done."""
    conn = _get_db()
    conn.execute(
        "UPDATE tasks SET status = 'done', completed_at = ? WHERE id = ?",
        (time.time(), task_id)
    )
    conn.commit()
    conn.close()


def search_tasks(query: str, limit: int = 10) -> list[dict]:
    """Search tasks by text."""
    fts_query = _sanitize_fts_query(query)
    if not fts_query:
        return []
    conn = _get_db()
    try:
        results = conn.execute("""
            SELECT t.* FROM task_fts f
            JOIN tasks t ON f.rowid = t.id
            WHERE task_fts MATCH ?
            ORDER BY rank LIMIT ?
        """, (fts_query, limit)).fetchall()
    except Exception:
        results = []
    conn.close()
    return [dict(r) for r in results]


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

def create_note(content: str, title: str = "", topic: str = "", tags: list[str] = None) -> int:
    """Create a note. Returns note ID."""
    conn = _get_db()
    now = time.time()
    cur = conn.execute(
        "INSERT INTO notes (title, content, topic, tags, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (title, content, topic, json.dumps(tags or []), now, now)
    )
    note_id = cur.lastrowid
    conn.execute(
        "INSERT INTO note_fts (rowid, title, content, topic) VALUES (?, ?, ?, ?)",
        (note_id, title, content, topic)
    )
    conn.commit()
    conn.close()
    log.info(f"Created note: {title or content[:40]}")
    return note_id


def search_notes(query: str, limit: int = 10) -> list[dict]:
    """Search notes by text."""
    fts_query = _sanitize_fts_query(query)
    if not fts_query:
        return []
    conn = _get_db()
    try:
        results = conn.execute("""
            SELECT n.* FROM note_fts f
            JOIN notes n ON f.rowid = n.id
            WHERE note_fts MATCH ?
            ORDER BY rank LIMIT ?
        """, (fts_query, limit)).fetchall()
    except Exception:
        results = []
    conn.close()
    return [dict(r) for r in results]


def get_notes_by_topic(topic: str) -> list[dict]:
    """Get all notes for a topic/project."""
    conn = _get_db()
    results = conn.execute(
        "SELECT * FROM notes WHERE topic LIKE ? ORDER BY updated_at DESC",
        (f"%{topic}%",)
    ).fetchall()
    conn.close()
    return [dict(r) for r in results]


# ---------------------------------------------------------------------------
# Context Builder — smart context for LLM calls
# ---------------------------------------------------------------------------

def build_memory_context(user_message: str) -> str:
    """Build relevant context from memories, tasks, and notes for the LLM.

    Searches for relevant memories based on what the user is talking about.
    Fast — runs FTS queries, no heavy computation.
    """
    try:
        sync_feedback_logs()
    except Exception as exc:
        log.debug(f"Feedback sync skipped: {exc}")

    parts = []

    # Always include: open high-priority tasks
    high_tasks = [t for t in get_open_tasks() if t["priority"] == "high"]
    if high_tasks:
        task_lines = [f"  - [{t['priority']}] {t['title']}" +
                      (f" (due {t['due_date']})" if t["due_date"] else "")
                      for t in high_tasks[:5]]
        parts.append("HIGH PRIORITY TASKS:\n" + "\n".join(task_lines))

    # Search memories relevant to what user is saying
    if len(user_message) > 5:
        relevant = recall(user_message, limit=3)
        if relevant:
            mem_lines = [f"  - [{m['type']}] {m['content']}" for m in relevant]
            parts.append("RELEVANT MEMORIES:\n" + "\n".join(mem_lines))

    # Recent important memories (always available)
    important = get_important_memories(limit=3)
    if important:
        imp_lines = [f"  - {m['content']}" for m in important
                     if not any(m["content"] == r["content"] for r in (relevant if 'relevant' in dir() else []))]
        if imp_lines:
            parts.append("KEY FACTS:\n" + "\n".join(imp_lines[:3]))

    return "\n\n".join(parts) if parts else ""


def format_tasks_for_voice(tasks: list[dict]) -> str:
    """Format tasks for voice response."""
    if not tasks:
        return "No tasks on the list, sir."
    count = len(tasks)
    high = [t for t in tasks if t["priority"] == "high"]
    if count == 1:
        t = tasks[0]
        return f"One task: {t['title']}." + (f" Due {t['due_date']}." if t["due_date"] else "")
    result = f"You have {count} open tasks."
    if high:
        result += f" {len(high)} are high priority."
    top = tasks[:3]
    for t in top:
        result += f" {t['title']}."
    if count > 3:
        result += f" And {count - 3} more."
    return result


def format_plan_for_voice(tasks: list[dict], events: list[dict]) -> str:
    """Format a day plan combining tasks and calendar events."""
    if not tasks and not events:
        return "Your day looks clear, sir. No events or tasks scheduled."

    parts = []
    if events:
        parts.append(f"{len(events)} events on the calendar")
    if tasks:
        high = [t for t in tasks if t["priority"] == "high"]
        parts.append(f"{len(tasks)} tasks" + (f", {len(high)} high priority" if high else ""))

    result = f"For tomorrow: {', '.join(parts)}. "

    # List events first
    if events:
        for e in events[:3]:
            result += f"{e.get('start', '')} {e['title']}. "

    # Then high priority tasks
    if tasks:
        for t in [t for t in tasks if t["priority"] == "high"][:2]:
            result += f"Priority: {t['title']}. "

    result += "Shall I adjust anything?"
    return result


# ---------------------------------------------------------------------------
# Memory extraction — learn from conversations
# ---------------------------------------------------------------------------

async def extract_memories(user_text: str, jarvis_response: str, anthropic_client) -> list[str]:
    """After a conversation turn, extract any facts worth remembering.

    Uses Haiku to decide if anything in the exchange is worth storing.
    Returns list of memories stored.
    """
    if not anthropic_client or len(user_text) < 15:
        return []

    try:
        response = await anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=(
                "Extract facts worth remembering from this conversation. "
                "Only extract CONCRETE facts: preferences, decisions, names, dates, plans, goals. "
                "NOT opinions, greetings, or casual chat. "
                "Return JSON array of objects: [{\"type\": \"fact|preference|project|person|decision\", \"content\": \"...\", \"importance\": 1-10}] "
                "Return [] if nothing worth remembering. Be very selective."
            ),
            messages=[{"role": "user", "content": f"User: {user_text}\nJARVIS: {jarvis_response}"}],
        )

        text = response.content[0].text.strip()
        # Parse JSON
        if text.startswith("["):
            items = json.loads(text)
            stored = []
            for item in items:
                if isinstance(item, dict) and "content" in item:
                    remember(
                        content=item["content"],
                        mem_type=item.get("type", "fact"),
                        source=user_text[:50],
                        importance=item.get("importance", 5),
                    )
                    stored.append(item["content"])
            return stored
    except Exception as e:
        log.debug(f"Memory extraction failed: {e}")

    return []


# Initialize on import
init_db()
