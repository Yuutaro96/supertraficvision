"""
database.py
Capa de acceso a datos SQLite para el sistema de aforo vehicular.

Tablas:
- cameras   : cámaras IP configuradas
- lines     : líneas virtuales de conteo por cámara
- counts    : registros históricos de cruces
- settings  : configuración global persistida

El acceso es thread-safe usando un lock y conexiones por operación
(check_same_thread=False), adecuado para el modelo multi-hilo de las cámaras.
"""

import sqlite3
import threading
from datetime import datetime, date
from typing import List, Dict, Optional, Any

import config

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db() -> None:
    """Crea las tablas si no existen."""
    with _lock, _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS cameras (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                url         TEXT NOT NULL,
                active      INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS lines (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                camera_id   INTEGER NOT NULL,
                name        TEXT NOT NULL,
                x1          INTEGER NOT NULL,
                y1          INTEGER NOT NULL,
                x2          INTEGER NOT NULL,
                y2          INTEGER NOT NULL,
                movement    TEXT NOT NULL DEFAULT 'RECTO',
                created_at  TEXT NOT NULL,
                FOREIGN KEY (camera_id) REFERENCES cameras(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS counts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT NOT NULL,
                camera_id   INTEGER NOT NULL,
                line_id     INTEGER NOT NULL,
                class_name  TEXT NOT NULL,
                direction   TEXT NOT NULL,
                count       INTEGER NOT NULL DEFAULT 1
            );

            CREATE INDEX IF NOT EXISTS idx_counts_ts ON counts(timestamp);
            CREATE INDEX IF NOT EXISTS idx_counts_cam ON counts(camera_id);

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        conn.commit()
    _load_settings()


# ---------------------------------------------------------------------------
# Settings persistidos
# ---------------------------------------------------------------------------
def _load_settings() -> None:
    with _lock, _connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    mapping = {r["key"]: r["value"] for r in rows}
    if not mapping:
        return

    def _as_int(key):
        return int(mapping[key]) if key in mapping and mapping[key] not in (None, "None", "") else None

    def _as_float(key):
        return float(mapping[key]) if key in mapping and mapping[key] not in (None, "None", "") else None

    active_classes = None
    if "active_classes" in mapping:
        try:
            import ast
            parsed = ast.literal_eval(mapping["active_classes"])
            if isinstance(parsed, (list, tuple)):
                active_classes = [int(x) for x in parsed]
        except Exception:
            active_classes = None

    config.settings.update(
        confidence=_as_float("confidence"),
        model_size=mapping.get("model_size"),
        target_fps=_as_int("target_fps"),
        device=mapping.get("device"),
        iou=_as_float("iou"),
        display_fps=_as_int("display_fps"),
        stream_quality=_as_int("stream_quality"),
        stream_resolution=mapping.get("stream_resolution"),
        reconnect_delay=_as_int("reconnect_delay"),
        data_retention_days=_as_int("data_retention_days"),
        active_classes=active_classes,
    )


def save_settings(data: Dict[str, Any]) -> None:
    with _lock, _connect() as conn:
        for key, value in data.items():
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )
        conn.commit()


# ---------------------------------------------------------------------------
# Cámaras
# ---------------------------------------------------------------------------
def get_cameras(only_active: bool = False) -> List[Dict]:
    q = "SELECT * FROM cameras"
    if only_active:
        q += " WHERE active = 1"
    q += " ORDER BY id"
    with _lock, _connect() as conn:
        rows = conn.execute(q).fetchall()
    return [dict(r) for r in rows]


def get_camera(camera_id: int) -> Optional[Dict]:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM cameras WHERE id = ?", (camera_id,)).fetchone()
    return dict(row) if row else None


def create_camera(name: str, url: str, active: bool = True) -> Dict:
    with _lock, _connect() as conn:
        cur = conn.execute(
            "INSERT INTO cameras(name, url, active, created_at) VALUES(?, ?, ?, ?)",
            (name, url, 1 if active else 0, datetime.now().isoformat()),
        )
        conn.commit()
        cid = cur.lastrowid
    return get_camera(cid)


def update_camera(camera_id: int, name: str = None, url: str = None,
                  active: bool = None) -> Optional[Dict]:
    cam = get_camera(camera_id)
    if not cam:
        return None
    name = name if name is not None else cam["name"]
    url = url if url is not None else cam["url"]
    active = active if active is not None else bool(cam["active"])
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE cameras SET name=?, url=?, active=? WHERE id=?",
            (name, url, 1 if active else 0, camera_id),
        )
        conn.commit()
    return get_camera(camera_id)


def delete_camera(camera_id: int) -> bool:
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM lines WHERE camera_id = ?", (camera_id,))
        cur = conn.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
        conn.commit()
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Líneas
# ---------------------------------------------------------------------------
def get_lines(camera_id: int) -> List[Dict]:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM lines WHERE camera_id = ? ORDER BY id", (camera_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_line(line_id: int) -> Optional[Dict]:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM lines WHERE id = ?", (line_id,)).fetchone()
    return dict(row) if row else None


def create_line(camera_id: int, name: str, x1: int, y1: int, x2: int, y2: int,
                movement: str = "RECTO") -> Dict:
    with _lock, _connect() as conn:
        cur = conn.execute(
            "INSERT INTO lines(camera_id, name, x1, y1, x2, y2, movement, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (camera_id, name, x1, y1, x2, y2, movement, datetime.now().isoformat()),
        )
        conn.commit()
        lid = cur.lastrowid
    return get_line(lid)


def update_line(line_id: int, **kwargs) -> Optional[Dict]:
    line = get_line(line_id)
    if not line:
        return None
    fields = {}
    for key in ("name", "x1", "y1", "x2", "y2", "movement"):
        fields[key] = kwargs[key] if kwargs.get(key) is not None else line[key]
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE lines SET name=?, x1=?, y1=?, x2=?, y2=?, movement=? WHERE id=?",
            (fields["name"], fields["x1"], fields["y1"], fields["x2"],
             fields["y2"], fields["movement"], line_id),
        )
        conn.commit()
    return get_line(line_id)


def delete_line(line_id: int) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute("DELETE FROM lines WHERE id = ?", (line_id,))
        conn.commit()
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Conteos
# ---------------------------------------------------------------------------
def record_count(camera_id: int, line_id: int, class_name: str,
                 direction: str, count: int = 1,
                 timestamp: Optional[str] = None) -> None:
    ts = timestamp or datetime.now().isoformat()
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO counts(timestamp, camera_id, line_id, class_name, direction, count) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (ts, camera_id, line_id, class_name, direction, count),
        )
        conn.commit()


def query_counts(camera_id: Optional[int] = None, line_id: Optional[int] = None,
                 class_name: Optional[str] = None, date_from: Optional[str] = None,
                 date_to: Optional[str] = None, limit: Optional[int] = None) -> List[Dict]:
    q = """
        SELECT c.*, cam.name AS camera_name, l.name AS line_name, l.movement AS movement
        FROM counts c
        LEFT JOIN cameras cam ON cam.id = c.camera_id
        LEFT JOIN lines l ON l.id = c.line_id
        WHERE 1=1
    """
    params: List[Any] = []
    if camera_id is not None:
        q += " AND c.camera_id = ?"
        params.append(camera_id)
    if line_id is not None:
        q += " AND c.line_id = ?"
        params.append(line_id)
    if class_name:
        q += " AND c.class_name = ?"
        params.append(class_name)
    if date_from:
        q += " AND c.timestamp >= ?"
        params.append(date_from)
    if date_to:
        q += " AND c.timestamp <= ?"
        params.append(date_to)
    q += " ORDER BY c.timestamp DESC"
    if limit:
        q += " LIMIT ?"
        params.append(limit)
    with _lock, _connect() as conn:
        rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


def summary_by_hour(camera_id: Optional[int] = None, date_from: Optional[str] = None,
                    date_to: Optional[str] = None) -> List[Dict]:
    """Resumen agrupado por hora y clase."""
    q = """
        SELECT substr(c.timestamp, 1, 13) AS hour, c.class_name,
               SUM(c.count) AS total
        FROM counts c
        WHERE 1=1
    """
    params: List[Any] = []
    if camera_id is not None:
        q += " AND c.camera_id = ?"
        params.append(camera_id)
    if date_from:
        q += " AND c.timestamp >= ?"
        params.append(date_from)
    if date_to:
        q += " AND c.timestamp <= ?"
        params.append(date_to)
    q += " GROUP BY hour, c.class_name ORDER BY hour"
    with _lock, _connect() as conn:
        rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


def totals_today(camera_id: Optional[int] = None) -> Dict[str, int]:
    """Totales por clase del día actual."""
    today = date.today().isoformat()
    q = """
        SELECT class_name, SUM(count) AS total
        FROM counts
        WHERE timestamp >= ?
    """
    params: List[Any] = [today + "T00:00:00"]
    if camera_id is not None:
        q += " AND camera_id = ?"
        params.append(camera_id)
    q += " GROUP BY class_name"
    with _lock, _connect() as conn:
        rows = conn.execute(q, params).fetchall()
    return {r["class_name"]: r["total"] for r in rows}


def counts_by_camera_class(camera_id: int) -> Dict[str, int]:
    """Totales del día por clase para una cámara concreta."""
    return totals_today(camera_id)


# ---------------------------------------------------------------------------
# Mantenimiento / estadísticas de la base de datos
# ---------------------------------------------------------------------------
def count_old_records(days: int) -> int:
    """Cuántos registros de 'counts' son más antiguos que 'days' días."""
    if not days or days <= 0:
        return 0
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM counts WHERE timestamp < ?", (cutoff,)
        ).fetchone()
    return int(row["n"]) if row else 0


def clean_old_counts(days: int) -> int:
    """Elimina registros de 'counts' más antiguos que N días. Devuelve cuántos borró."""
    if not days or days <= 0:
        return 0
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    with _lock, _connect() as conn:
        cur = conn.execute("DELETE FROM counts WHERE timestamp < ?", (cutoff,))
        conn.commit()
        try:
            conn.execute("VACUUM;")
        except Exception:
            pass
    return cur.rowcount


def db_stats() -> Dict[str, Any]:
    """Estadísticas generales de la base de datos para la página de configuración."""
    import os as _os
    with _lock, _connect() as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM counts").fetchone()["n"]
        oldest = conn.execute("SELECT MIN(timestamp) AS t FROM counts").fetchone()["t"]
        newest = conn.execute("SELECT MAX(timestamp) AS t FROM counts").fetchone()["t"]
        cameras = conn.execute("SELECT COUNT(*) AS n FROM cameras").fetchone()["n"]
        lines = conn.execute("SELECT COUNT(*) AS n FROM lines").fetchone()["n"]

    size_bytes = 0
    try:
        for suffix in ("", "-wal", "-shm"):
            p = config.DB_PATH + suffix
            if _os.path.exists(p):
                size_bytes += _os.path.getsize(p)
    except Exception:
        pass

    return {
        "total_records": int(total or 0),
        "oldest": oldest,
        "newest": newest,
        "cameras": int(cameras or 0),
        "lines": int(lines or 0),
        "size_bytes": size_bytes,
        "size_mb": round(size_bytes / (1024 * 1024), 2),
    }
