# 🚦 Aforo Vehicular

Aplicación web para **detección y conteo de vehículos** (aforos vehiculares) en tiempo real
usando **YOLOv8** + **Supervision** sobre múltiples cámaras IP. Pensada para ejecutarse en un
mini PC y ser accedida desde cualquier equipo de la red local (LAN).

Detecta y cuenta: 🚶 personas, 🚲 bicicletas, 🚗 autos, 🏍️ motos, 🚌 buses y 🚚 camiones.
Permite configurar **líneas virtuales** por cámara para contar cruces por dirección/movimiento
(NORTE, SUR, GIRO_IZQ, GIRO_DER, RECTO, etc.), incluyendo aforos direccionales y de giros.

---

## 🧩 Tecnologías

- **Backend:** FastAPI + Uvicorn
- **Detección:** Ultralytics YOLOv8m + Supervision (ByteTrack + LineZone)
- **Frontend:** HTML5 + JavaScript vanilla + Chart.js (tema oscuro)
- **Base de datos:** SQLite (configuraciones y conteos históricos)
- **Video:** OpenCV (RTSP / HTTP / webcam / archivo de video)

---

## 📦 Instalación

Requiere **Python 3.9+** (recomendado 3.10/3.11).

```bash
cd aforo_vehicular
python -m venv venv
source venv/bin/activate        # En Windows: venv\Scripts\activate
pip install -r requirements.txt
```

> La primera vez, el modelo **YOLOv8m** (`yolov8m.pt`, ~50 MB) se descarga
> automáticamente y se guarda en la carpeta `models/`.

---

## ▶️ Ejecución

```bash
python main.py
```

o bien:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Luego abre en el navegador:

- En el mismo equipo: **http://localhost:8000**
- Desde otro equipo de la red: **http://IP_DEL_MINI_PC:8000**
  (por ejemplo `http://192.168.1.100:8000`)

---

## 📷 Cómo agregar cámaras IP

1. Ve a la pestaña **Cámaras**.
2. Completa **Nombre** y **URL de origen**, y pulsa **Guardar**.

Formatos de URL soportados:

| Tipo | Ejemplo |
|------|---------|
| Cámara IP RTSP (Hikvision) | `rtsp://usuario:clave@192.168.1.50:554/Streaming/Channels/101` |
| Cámara IP RTSP (Dahua)     | `rtsp://usuario:clave@192.168.1.51:554/cam/realmonitor?channel=1&subtype=0` |
| Stream HTTP / MJPEG        | `https://videos.cctvcamerapros.com/wp-content/files/IP-Camera-Stream-to-Website.jpg` |
| Webcam local               | `0` (o `1`, `2`… según el dispositivo) |
| Archivo de video (pruebas) | `/home/ubuntu/videos/trafico.mp4` |

Cada cámara activa se procesa en su **propio hilo** y se reconecta automáticamente
si se pierde la señal.

### 🧪 Prueba local sin cámara IP

Puedes usar un archivo de video como fuente (útil para probar sin hardware):

```
Nombre: Video de prueba
URL:    /ruta/a/tu/video.mp4
```

o la webcam del equipo poniendo la URL en `0`.

---

## 📐 Cómo configurar líneas virtuales

1. Ve a la pestaña **Líneas**.
2. Selecciona la cámara y pulsa **Cargar frame** (muestra la imagen actual).
3. Haz **clic** sobre la imagen para marcar el **punto A**, y otro **clic** para el **punto B**.
4. Escribe un **nombre** y elige el **movimiento/dirección** (NORTE, GIRO_DER, RECTO…).
5. Pulsa **Guardar línea**.

Cada objeto que cruce la línea se contabiliza como **IN** o **OUT** según el sentido del cruce,
usando `supervision.LineZone`. El tracking (ByteTrack) evita el doble conteo.

---

## 📊 Dashboard y Reportes

- **Dashboard:** grid de videos en vivo (MJPEG) con anotaciones, contadores por cámara/clase,
  totales del día y tabla de últimos cruces actualizada en tiempo real (WebSocket).
- **Reportes:** filtros por cámara, clase, fecha/hora; histograma por hora, distribución por clase
  (Chart.js) y **exportación a CSV**.

---

## ⚙️ Configuración Global

Ajustable vía `POST /api/settings` (o editando `config.py`):

- `confidence` — umbral de confianza (default **0.5**)
- `model_size` — tamaño de YOLOv8: `n`, `s`, `m`, `l`, `x` (default **m**)
- `target_fps` — FPS de procesamiento por cámara (default **15**)
- `device` — `cpu` o `cuda` (GPU)
- `iou` — umbral IoU para NMS

---

## 🖥️ Requisitos de hardware recomendados

| Escenario | CPU | RAM | GPU |
|-----------|-----|-----|-----|
| 1–2 cámaras, YOLOv8n/s, CPU | Intel i5/i7 reciente | 8 GB | No obligatoria |
| 2–4 cámaras, YOLOv8m | Intel i7 / Ryzen 7 | 16 GB | NVIDIA GTX/RTX (recomendada) |
| 4+ cámaras, tiempo real fluido | i7/i9 o Xeon | 16–32 GB | NVIDIA RTX (CUDA) |

> Con **GPU NVIDIA** instala la versión de PyTorch con CUDA y establece `device = "cuda"`
> para un rendimiento mucho mayor. En CPU, usa `yolov8n`/`yolov8s` y baja `target_fps`
> para mantener fluidez con varias cámaras.

---

## 🗂️ Estructura del proyecto

```
aforo_vehicular/
├── main.py              # FastAPI: rutas, API, MJPEG, WebSocket
├── camera_manager.py    # Hilos de cámaras + generación MJPEG
├── detector.py          # YOLOv8 + ByteTrack (singleton)
├── counter.py           # Líneas virtuales (LineZone) + registro de cruces
├── database.py          # SQLite (cámaras, líneas, conteos, settings)
├── config.py            # Configuración global y clases
├── requirements.txt
├── README.md
├── models/              # Pesos YOLO descargados (auto)
└── static/              # Frontend (HTML/CSS/JS)
```

---

## 🔌 Endpoints principales

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/` `/cameras` `/lines` `/reports` | Páginas web |
| GET | `/stream/{camera_id}` | Stream MJPEG anotado |
| GET | `/api/frame/{camera_id}` | Frame estático (para dibujar líneas) |
| GET/POST/PUT/DELETE | `/api/cameras[/{id}]` | CRUD de cámaras |
| GET/POST/PUT/DELETE | `/api/lines[/{id}]` | CRUD de líneas |
| GET | `/api/counts` | Conteos con filtros |
| GET | `/api/counts/summary` | Resumen por hora/día |
| GET | `/api/counts/export/csv` | Exportar CSV |
| GET | `/api/status` | Estado del sistema |
| WS  | `/ws/counts` | Conteos en tiempo real |

---

## 🛠️ Notas

- El sistema maneja errores de forma controlada (cámara desconectada, modelo no cargado,
  frame no disponible) sin detener el servicio.
- La base de datos `aforo.db` se crea automáticamente al primer arranque.
- Todos los textos de la interfaz están en **español**.
