# Aforo Visión — Servidor Central

Recibe los conteos y el estado de cámaras que el/los mini PC(s) sincronizan
periódicamente. No procesa video ni corre YOLO — eso vive en el mini PC.
Es un buffer temporal: los datos se purgan automáticamente cada
`RETENTION_DAYS` (por defecto 45), porque el histórico real que importa es
el que descargas en CSV y guardas tú.

## Desplegar en Railway

1. Sube esta carpeta (`server/`) a un repositorio de GitHub (puede ser el
   mismo repo del mini PC o uno aparte).
2. En Railway: **New Project → Deploy from GitHub repo**, selecciona el repo
   y, si el código no está en la raíz, indica `server/` como *root directory*
   del servicio.
3. **New → Database → PostgreSQL** dentro del mismo proyecto — Railway
   conecta `DATABASE_URL` automáticamente al servicio web.
4. En el servicio web, pestaña **Variables**, agrega:
   - `ADMIN_USER`, `ADMIN_PASSWORD` — para entrar al panel.
   - `INGEST_API_KEY` — una clave larga aleatoria; debe coincidir con
     `SYNC_API_KEY` que configures en el mini PC.
   - `RETENTION_DAYS` — opcional, por defecto 45.
5. En **Settings → Networking**, genera un dominio (`*.up.railway.app`) o
   conecta tu propio dominio/subdominio.

En Render el proceso es equivalente (Web Service + PostgreSQL addon +
variables de entorno); usa el mismo `Procfile`.

## Configurar el mini PC para que sincronice aquí

En el mini PC, define estas variables de entorno antes de correr `main.py`
(o en el archivo/servicio que uses para levantarlo):

```
SYNC_SERVER_URL=https://tu-servicio.up.railway.app
SYNC_API_KEY=la-misma-clave-que-pusiste-en-INGEST_API_KEY
SYNC_SITE_ID=interseccion-norte      # identifica a este mini PC
SYNC_INTERVAL_SECONDS=300            # cada cuánto sincroniza (segundos)
```

Si `SYNC_SERVER_URL` no está definido, el mini PC sigue funcionando igual
que siempre, solo en modo local (sin sincronizar nada).

## Endpoints

- `GET /` — panel (usuario/clave): estado de cámaras + totales + botón de
  descarga.
- `GET /api/export/csv` — descarga el CSV acumulado (usuario/clave).
- `POST /api/ingest` — usado por el mini PC (header `X-API-Key`).
- `GET /health` — chequeo de salud, sin autenticación.
