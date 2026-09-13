# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

This repo contains **two independent FastAPI applications** with separate dependencies — always install/run them from their own directory.

**Edge app (root) — runs on the on-site mini PC:**
```bash
pip install -r requirements.txt
python main.py                              # or: uvicorn main:app --host 0.0.0.0 --port 8000
pytest tests/                               # full suite
pytest tests/test_detector.py -q            # single file
pytest tests/test_detector.py::test_use_openvino_flag -q   # single test
```
Tests use `conftest.py`'s `temp_db` fixture (isolated SQLite via `monkeypatch`) — never point at the real `aforo.db`. There's no pyproject/pytest.ini; run pytest from the repo root so `conftest.py`'s `sys.path` insert resolves the top-level modules.

**Central server (`server/`) — deployed separately on Railway/Render:**
```bash
cd server
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```
Falls back to local SQLite (`sqlite:///./server.db`) if `DATABASE_URL` isn't set, so it runs standalone for local testing without Postgres.

In production the edge app runs as a Windows service via NSSM (`AforoVehicular`), configured with `AppEnvironmentExtra` for env vars (`SYNC_*`, `EZVIZ_*`) and `AppStdout`/`AppStderr` pointed at a log file for debugging — plain `print()`/uvicorn logs are the only visibility into that service.

## Architecture

### Two apps, one repo, connected by a poll-based sync, not a live link

- **Edge app** (`main.py` + friends) does all video processing (YOLO + tracking + counting) locally on a mini PC per physical site. It has no dependency on internet connectivity to function.
- **`server/`** is a thin, video-free aggregator meant for the cloud: it never runs YOLO or touches video, only ingests small JSON (counts + camera status) and — on demand — single JPEG snapshots. Its own `database.py`/`main.py` are a **separate** SQLAlchemy+Postgres/SQLite model, unrelated to the edge app's raw-SQLite `database.py`.
- The two are connected only by `sync_client.py` (edge side), which runs as a daemon thread, POSTs to the server's `/api/ingest` on an interval (`SYNC_INTERVAL_SECONDS`), and is a no-op entirely if `SYNC_SERVER_URL` isn't set — the edge app always works standalone/offline first.
- **Cloud→edge commands never push.** The mini PC is assumed to sit behind CGNAT/4G with no inbound reachability, so any "remote control" feature (currently: on-demand snapshots) is a **poll-based command queue**: the server flags a pending request (e.g. `snapshot_requested` on a `CameraStatus` row), the flag rides back as a field in the *response* to the edge's next routine `/api/ingest` POST, and `sync_client._sync_once()` acts on it and uploads the result via a follow-up POST. There is no other channel from server to edge. Any future "configure X remotely" feature should follow this exact same request-flag → poll → fulfill → report-back shape rather than adding a new persistent connection, to stay consistent with the CGNAT constraint and the project's low-data-usage design goal.

### Edge app: per-camera thread model (`camera_manager.py`)

- `CameraManager` owns one `CameraStream` thread per active camera row. Each `CameraStream` holds its own `detector.get_detector()` handle (a process-wide singleton model), its own `ByteTrack` instance (via `VehicleDetector.create_tracker()`), and its own `LineCounter` (from `counter.py`).
- A `CameraStream` publishes two different frame views, and code elsewhere must pick the right one:
  - `_raw_frame` (via `get_raw_frame()`) — undecorated, used for line/ROI configuration and any "does this look pointed correctly" check.
  - `_frame_q` (via `get_frame()`, consumed by `mjpeg_generator`) — the same frame *after* `_process()` draws trace/ellipse/label/line annotations. Anything that needs to show detections (not just raw video) must pull from this queue, not `get_raw_frame()`.
- Detection only runs inside the camera's own ROI polygon if one is set (`database.get_roi`/`_apply_roi`) — a mask is cached and only rebuilt when frame shape changes.
- Reconnection uses exponential backoff (`_next_backoff`), except when the source is a local video file, which just loops immediately — this matters when testing with a downloaded sample video instead of a real camera.

### Detection/config singletons (`detector.py`, `config.py`)

- `VehicleDetector` is a thread-safe singleton; `ensure_model()` is called per-frame-cycle-adjacent code paths and only actually reloads the model when `model_size`, `resolved_device`, or the OpenVINO preference changed since last load — cheap to call often.
- `config.settings` is a single mutable `Settings` instance, not per-request config. It's hydrated from the SQLite `settings` key/value table at startup (`database._load_settings`) and any runtime change (via `/api/settings`) both mutates the in-memory singleton and persists via `database.save_settings`, so a service restart doesn't lose tuning.
- `config.settings.use_openvino`/`resolved_device` are derived properties, not stored fields — `device` itself can be `"auto"|"cpu"|"cuda"|"openvino"`; `resolved_device` collapses `"openvino"` to `"cpu"` for anything that needs a torch device string, while `use_openvino` is the separate flag `detector.py` checks to decide whether to export/load the OpenVINO IR model instead.

### Counting (`counter.py`)

- Uses `supervision.LineZone` for crossing detection, but the "movement" (NORTE/GIRO_IZQ/etc., from `config.MOVEMENT_LABELS`) is a **manual label the user assigns per line** — it is not geometrically inferred from the crossing itself. A single line can only cleanly represent one movement if it's physically placed across a lane that only carries that movement; shared-lane approaches (straight+right in one lane) can't be disambiguated by line-crossing alone.
- Class assignment at a crossing uses a **majority vote across the track's history** (`_track_votes`), not the class detected on the exact frame of the crossing — this smooths out single-frame misclassifications (e.g. car vs. truck) for the same `tracker_id`.
- `LineCounter.load_lines()` preserves existing `LineZoneWrapper` objects (and therefore their live in/out counters) for any line whose geometry/name/movement hasn't changed, so editing one line doesn't reset the running counts of the others on that camera.

### PTZ control (`ptz_controller.py`)

Protocol-abstracted by design: `cameras` rows carry `control_protocol` + a free-form `control_config` JSON blob, and `get_ptz_controller(camera_dict)` is the only place that needs to know which concrete class to instantiate. `EzvizCloudPTZController` is the only working implementation (wraps the third-party `pyezvizapi` package, which talks to EZVIZ's cloud, not the camera locally — many EZVIZ "Smart Home" tier cameras, e.g. the CS-C8PF, block ONVIF/local web admin entirely by firmware policy, so cloud is the only control path for that hardware). `OnvifPTZController` exists only as a stub (raises `PTZError`) for cameras that do expose ONVIF — implement it, don't design a new abstraction, when one shows up. Zoom and presets are explicitly best-effort/unsupported respectively — see the docstrings before assuming either works for a given camera model.

### Database migrations

Both `database.py` files (root and `server/`) use hand-rolled `ALTER TABLE ... ADD COLUMN` migrations guarded by `try/except sqlite3.OperationalError` (root) or a `sqlalchemy.inspect()` column-diff (server) run at every `init_db()` — there's no Alembic/migration tool. When adding a column to either `cameras`/`camera_status` table, follow the existing pattern in `init_db()`/`_migrate_add_missing_columns()` rather than assuming `create_all()` alone will update an already-deployed database (it only creates missing tables, never alters existing ones).
