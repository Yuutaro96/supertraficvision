"""
database.py (servidor central)
Almacenamiento temporal de conteos + estado de cámaras recibidos desde uno
o más mini PCs (sitios). No es un archivo histórico permanente: los datos se
purgan automáticamente pasados RETENTION_DAYS, porque el histórico real vive
en el mini PC y en los reportes que el usuario descarga desde aquí.

Usa Postgres en producción (variable DATABASE_URL, provista por Railway o
Render) y cae a un SQLite local si no está configurada, para poder probar
sin nube.
"""

import json
import os
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, Boolean, Float, LargeBinary, delete,
    inspect, text
)
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./server.db")
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "45"))

# Railway/Render suelen dar la URL como postgres://, SQLAlchemy 2.x requiere postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class CameraStatus(Base):
    __tablename__ = "camera_status"

    id = Column(Integer, primary_key=True)
    site_id = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    connected = Column(Boolean, default=False)
    fps = Column(Float, default=0.0)
    last_error = Column(String, default="")
    updated_at = Column(DateTime, default=datetime.utcnow)

    # --- Captura bajo demanda (para validar encuadre/detección sin gastar
    # datos constantemente): solo se guarda la última imagen por cámara, y
    # el mini PC solo la envía cuando snapshot_requested queda en True.
    # requested_snapshot_type / snapshot_image_type: "raw" (cruda, para ver
    # si apunta bien) o "annotated" (con las cajas/etiquetas del modelo,
    # para ver si detecta bien) — son dos diagnósticos distintos, no se
    # reemplaza uno por el otro. ---
    snapshot_requested = Column(Boolean, default=False)
    requested_snapshot_type = Column(String, nullable=True)
    snapshot_image = Column(LargeBinary, nullable=True)
    snapshot_image_type = Column(String, nullable=True)
    snapshot_captured_at = Column(DateTime, nullable=True)


class LineCommand(Base):
    """Comando pendiente (o ya procesado) de configuración de líneas,
    encolado desde el panel central y aplicado por el mini PC en su
    próximo ciclo de sync — mismo patrón que snapshot_requested, nunca
    una conexión nueva hacia el mini PC."""
    __tablename__ = "line_commands"

    id = Column(Integer, primary_key=True)
    site_id = Column(String, nullable=False, index=True)
    camera_name = Column(String, nullable=False)
    command_type = Column(String, nullable=False)  # "create" | "update" | "delete"
    payload = Column(String, nullable=False)        # JSON: line_id?, name, x1,y1,x2,y2, movement
    status = Column(String, nullable=False, default="pending")  # pending | applied | failed
    error = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    applied_at = Column(DateTime, nullable=True)


class LineMirror(Base):
    """Copia liviana de las líneas configuradas en cada mini PC, reportada
    en cada ingest — sirve solo para que el panel central sepa qué líneas
    ya existen (y pueda ofrecer editarlas/borrarlas); el mini PC sigue
    siendo la única fuente de verdad real."""
    __tablename__ = "line_mirror"

    id = Column(Integer, primary_key=True)
    site_id = Column(String, nullable=False, index=True)
    camera_name = Column(String, nullable=False)
    line_id = Column(Integer, nullable=False)  # id de la línea EN EL MINI PC
    name = Column(String, nullable=False)
    movement = Column(String, nullable=False)
    x1 = Column(Integer, nullable=False)
    y1 = Column(Integer, nullable=False)
    x2 = Column(Integer, nullable=False)
    y2 = Column(Integer, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow)


class Count(Base):
    __tablename__ = "counts"

    id = Column(Integer, primary_key=True)
    site_id = Column(String, nullable=False, index=True)
    timestamp = Column(String, nullable=False, index=True)
    camera_name = Column(String, default="")
    line_name = Column(String, default="")
    movement = Column(String, default="")
    class_name = Column(String, nullable=False)
    direction = Column(String, nullable=False)
    count = Column(Integer, default=1)
    received_at = Column(DateTime, default=datetime.utcnow, index=True)


def init_db():
    Base.metadata.create_all(engine)
    _migrate_add_missing_columns()


def _migrate_add_missing_columns():
    """create_all() no altera tablas ya existentes. Como el servicio en
    Railway/Render ya tiene `camera_status` creada de antes, agregamos acá
    las columnas nuevas que falten (migración mínima, sin Alembic)."""
    inspector = inspect(engine)
    existing = {col["name"] for col in inspector.get_columns("camera_status")}
    new_columns = {
        "snapshot_requested": "BOOLEAN DEFAULT FALSE",
        "requested_snapshot_type": "VARCHAR",
        "snapshot_image": "BYTEA" if engine.url.get_backend_name() == "postgresql" else "BLOB",
        "snapshot_image_type": "VARCHAR",
        "snapshot_captured_at": "TIMESTAMP",
    }
    with engine.begin() as conn:
        for name, ddl_type in new_columns.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE camera_status ADD COLUMN {name} {ddl_type}"))
                print(f"[server] Migración: columna '{name}' agregada a camera_status.")


def upsert_camera_status(site_id: str, cameras: list) -> None:
    with SessionLocal() as session:
        for cam in cameras:
            row = (
                session.query(CameraStatus)
                .filter_by(site_id=site_id, name=cam["name"])
                .one_or_none()
            )
            if row is None:
                row = CameraStatus(site_id=site_id, name=cam["name"])
                session.add(row)
            row.connected = bool(cam.get("connected"))
            row.fps = float(cam.get("fps") or 0)
            row.last_error = cam.get("last_error") or ""
            row.updated_at = datetime.utcnow()
        session.commit()


def insert_counts(site_id: str, counts: list) -> int:
    with SessionLocal() as session:
        for c in counts:
            session.add(Count(
                site_id=site_id,
                timestamp=c["timestamp"],
                camera_name=c.get("camera_name", ""),
                line_name=c.get("line_name", ""),
                movement=c.get("movement", ""),
                class_name=c["class_name"],
                direction=c["direction"],
                count=int(c.get("count", 1)),
            ))
        session.commit()
    return len(counts)


def get_camera_statuses() -> list:
    with SessionLocal() as session:
        rows = session.query(CameraStatus).order_by(CameraStatus.site_id, CameraStatus.name).all()
        return [
            {
                "site_id": r.site_id,
                "name": r.name,
                "connected": r.connected,
                "fps": r.fps,
                "last_error": r.last_error,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                "snapshot_requested": bool(r.snapshot_requested),
                "requested_snapshot_type": r.requested_snapshot_type,
                "has_snapshot": r.snapshot_image is not None,
                "snapshot_image_type": r.snapshot_image_type,
                "snapshot_captured_at": r.snapshot_captured_at.isoformat() if r.snapshot_captured_at else None,
            }
            for r in rows
        ]


def request_snapshot(site_id: str, camera_name: str, snapshot_type: str = "raw") -> bool:
    """Marca una cámara para que el mini PC mande una captura en su próximo
    ciclo de sincronización. snapshot_type: "raw" (cruda, para validar
    encuadre) o "annotated" (con las detecciones dibujadas, para validar
    calidad de detección). Devuelve False si la cámara no existe todavía
    (no ha hecho ningún /api/ingest)."""
    if snapshot_type not in ("raw", "annotated"):
        snapshot_type = "raw"
    with SessionLocal() as session:
        row = (
            session.query(CameraStatus)
            .filter_by(site_id=site_id, name=camera_name)
            .one_or_none()
        )
        if row is None:
            return False
        row.snapshot_requested = True
        row.requested_snapshot_type = snapshot_type
        session.commit()
        return True


def get_pending_snapshot_requests(site_id: str) -> list:
    with SessionLocal() as session:
        rows = (
            session.query(CameraStatus)
            .filter_by(site_id=site_id, snapshot_requested=True)
            .all()
        )
        return [
            {"camera_name": r.name, "type": r.requested_snapshot_type or "raw"}
            for r in rows
        ]


def save_snapshot(site_id: str, camera_name: str, image_bytes: bytes,
                   image_type: str = "raw") -> bool:
    if image_type not in ("raw", "annotated"):
        image_type = "raw"
    with SessionLocal() as session:
        row = (
            session.query(CameraStatus)
            .filter_by(site_id=site_id, name=camera_name)
            .one_or_none()
        )
        if row is None:
            return False
        row.snapshot_image = image_bytes
        row.snapshot_image_type = image_type
        row.snapshot_captured_at = datetime.utcnow()
        row.snapshot_requested = False
        row.requested_snapshot_type = None
        session.commit()
        return True


def get_snapshot(site_id: str, camera_name: str) -> Optional[bytes]:
    with SessionLocal() as session:
        row = (
            session.query(CameraStatus)
            .filter_by(site_id=site_id, name=camera_name)
            .one_or_none()
        )
        return row.snapshot_image if row else None


# ---------------------------------------------------------------------------
# Cola de comandos de líneas (crear/editar/borrar), aplicados por el mini PC
# en su próximo ciclo de sync — mismo patrón que la captura bajo demanda.
# ---------------------------------------------------------------------------
def queue_line_command(site_id: str, camera_name: str, command_type: str, payload: dict) -> int:
    with SessionLocal() as session:
        row = LineCommand(
            site_id=site_id,
            camera_name=camera_name,
            command_type=command_type,
            payload=json.dumps(payload),
            status="pending",
        )
        session.add(row)
        session.commit()
        return row.id


def get_pending_line_commands(site_id: str) -> list:
    with SessionLocal() as session:
        rows = (
            session.query(LineCommand)
            .filter_by(site_id=site_id, status="pending")
            .order_by(LineCommand.created_at)
            .all()
        )
        return [
            {
                "id": r.id,
                "camera_name": r.camera_name,
                "command_type": r.command_type,
                "payload": json.loads(r.payload),
            }
            for r in rows
        ]


def mark_line_command_result(command_id: int, ok: bool, error: str = "") -> None:
    with SessionLocal() as session:
        row = session.query(LineCommand).filter_by(id=command_id).one_or_none()
        if row is None:
            return
        row.status = "applied" if ok else "failed"
        row.error = error or None
        row.applied_at = datetime.utcnow()
        session.commit()


def get_line_commands(site_id: str, limit: int = 50) -> list:
    """Historial de comandos (para mostrar su estado en el panel)."""
    with SessionLocal() as session:
        rows = (
            session.query(LineCommand)
            .filter_by(site_id=site_id)
            .order_by(LineCommand.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "camera_name": r.camera_name,
                "command_type": r.command_type,
                "payload": json.loads(r.payload),
                "status": r.status,
                "error": r.error,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "applied_at": r.applied_at.isoformat() if r.applied_at else None,
            }
            for r in rows
        ]


# ---------------------------------------------------------------------------
# Espejo liviano de las líneas configuradas en cada mini PC (solo para que
# el panel central sepa qué ya existe; el mini PC sigue siendo la fuente
# de verdad).
# ---------------------------------------------------------------------------
def upsert_line_mirror(site_id: str, camera_name: str, lines: list) -> None:
    with SessionLocal() as session:
        reported_ids = {int(l["line_id"]) for l in lines}
        existing = {
            row.line_id: row
            for row in session.query(LineMirror).filter_by(site_id=site_id, camera_name=camera_name).all()
        }
        for l in lines:
            lid = int(l["line_id"])
            row = existing.get(lid)
            if row is None:
                row = LineMirror(site_id=site_id, camera_name=camera_name, line_id=lid)
                session.add(row)
            row.name = l["name"]
            row.movement = l["movement"]
            row.x1, row.y1, row.x2, row.y2 = l["x1"], l["y1"], l["x2"], l["y2"]
            row.updated_at = datetime.utcnow()
        # Las líneas que el mini PC ya no reporta (se borraron localmente,
        # o mediante un comando que ya se aplicó) se quitan del espejo.
        for lid, row in existing.items():
            if lid not in reported_ids:
                session.delete(row)
        session.commit()


def get_line_mirror(site_id: str, camera_name: str = None) -> list:
    with SessionLocal() as session:
        q = session.query(LineMirror).filter_by(site_id=site_id)
        if camera_name:
            q = q.filter_by(camera_name=camera_name)
        rows = q.order_by(LineMirror.camera_name, LineMirror.line_id).all()
        return [
            {
                "line_id": r.line_id, "camera_name": r.camera_name, "name": r.name,
                "movement": r.movement, "x1": r.x1, "y1": r.y1, "x2": r.x2, "y2": r.y2,
            }
            for r in rows
        ]


def totals_by_class(site_id: str = None) -> dict:
    with SessionLocal() as session:
        q = session.query(Count)
        if site_id:
            q = q.filter_by(site_id=site_id)
        totals: dict = {}
        for r in q.all():
            totals[r.class_name] = totals.get(r.class_name, 0) + r.count
        return totals


def counts_by_hour(site_id: str = None, hours: int = 24) -> list:
    """Resumen agrupado por hora y clase, para la gráfica de Reportes."""
    with SessionLocal() as session:
        q = session.query(Count)
        if site_id:
            q = q.filter_by(site_id=site_id)
        cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
        q = q.filter(Count.timestamp >= cutoff)
        buckets: dict = {}
        for r in q.all():
            hour = (r.timestamp or "")[:13]  # "YYYY-MM-DDTHH"
            key = (hour, r.class_name)
            buckets[key] = buckets.get(key, 0) + r.count
        return [
            {"hour": hour, "class_name": cls, "total": total}
            for (hour, cls), total in sorted(buckets.items())
        ]


def reset_counts(site_id: str = None) -> int:
    """Borra los conteos acumulados (reinicio manual de Reportes), en vez
    de esperar a la purga automática por RETENTION_DAYS. Si se pasa
    site_id, solo reinicia ese sitio; si no, reinicia todos."""
    with SessionLocal() as session:
        q = session.query(Count)
        if site_id:
            q = q.filter_by(site_id=site_id)
        removed = q.delete(synchronize_session=False)
        session.commit()
        return removed


def get_all_counts(site_id: str = None) -> list:
    with SessionLocal() as session:
        q = session.query(Count)
        if site_id:
            q = q.filter_by(site_id=site_id)
        rows = q.order_by(Count.timestamp.desc()).all()
        return [
            {
                "id": r.id,
                "site_id": r.site_id,
                "timestamp": r.timestamp,
                "camera_name": r.camera_name,
                "line_name": r.line_name,
                "movement": r.movement,
                "class_name": r.class_name,
                "direction": r.direction,
                "count": r.count,
            }
            for r in rows
        ]


def purge_old(days: int = None) -> int:
    days = days if days is not None else RETENTION_DAYS
    if not days or days <= 0:
        return 0
    cutoff = datetime.utcnow() - timedelta(days=days)
    with SessionLocal() as session:
        result = session.execute(delete(Count).where(Count.received_at < cutoff))
        session.commit()
        return result.rowcount or 0
