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

    # --- Captura bajo demanda (para validar encuadre sin gastar datos
    # constantemente): solo se guarda la última imagen por cámara, y el
    # mini PC solo la envía cuando snapshot_requested queda en True. ---
    snapshot_requested = Column(Boolean, default=False)
    snapshot_image = Column(LargeBinary, nullable=True)
    snapshot_captured_at = Column(DateTime, nullable=True)


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
        "snapshot_image": "BYTEA" if engine.url.get_backend_name() == "postgresql" else "BLOB",
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
                "has_snapshot": r.snapshot_image is not None,
                "snapshot_captured_at": r.snapshot_captured_at.isoformat() if r.snapshot_captured_at else None,
            }
            for r in rows
        ]


def request_snapshot(site_id: str, camera_name: str) -> bool:
    """Marca una cámara para que el mini PC mande una captura en su próximo
    ciclo de sincronización. Devuelve False si la cámara no existe todavía
    (no ha hecho ningún /api/ingest)."""
    with SessionLocal() as session:
        row = (
            session.query(CameraStatus)
            .filter_by(site_id=site_id, name=camera_name)
            .one_or_none()
        )
        if row is None:
            return False
        row.snapshot_requested = True
        session.commit()
        return True


def get_pending_snapshot_requests(site_id: str) -> list:
    with SessionLocal() as session:
        rows = (
            session.query(CameraStatus)
            .filter_by(site_id=site_id, snapshot_requested=True)
            .all()
        )
        return [r.name for r in rows]


def save_snapshot(site_id: str, camera_name: str, image_bytes: bytes) -> bool:
    with SessionLocal() as session:
        row = (
            session.query(CameraStatus)
            .filter_by(site_id=site_id, name=camera_name)
            .one_or_none()
        )
        if row is None:
            return False
        row.snapshot_image = image_bytes
        row.snapshot_captured_at = datetime.utcnow()
        row.snapshot_requested = False
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


def totals_by_class() -> dict:
    with SessionLocal() as session:
        rows = session.query(Count).all()
        totals: dict = {}
        for r in rows:
            totals[r.class_name] = totals.get(r.class_name, 0) + r.count
        return totals


def get_all_counts() -> list:
    with SessionLocal() as session:
        rows = session.query(Count).order_by(Count.timestamp.desc()).all()
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
