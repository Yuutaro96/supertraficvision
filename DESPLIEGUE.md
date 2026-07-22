# 🚀 Guía de Despliegue - SuperTraficVision

Esta guía te ayudará a instalar y lanzar el sistema de aforo vehicular en tu **Mini PC** para producción.

---

## 📋 Requisitos del Sistema

### Hardware Mínimo Recomendado

| Componente | Recomendación |
|------------|---------------|
| **CPU** | Intel Core i5 (12ª gen) o AMD Ryzen 5 |
| **RAM** | 16 GB mínimo |
| **GPU** | NVIDIA RTX 3060 (opcional pero muy recomendada para 4+ cámaras) |
| **Almacenamiento** | 50 GB disponibles |
| **Red** | Ethernet Gigabit o WiFi 6 |

### Software Requerido

- **Sistema Operativo**: Ubuntu 22.04 LTS / Windows 10/11 / Raspberry Pi OS
- **Python**: 3.9, 3.10 o 3.11
- **CUDA** (opcional): 11.8 o 12.x para aceleración GPU NVIDIA
- **Git**: Para clonar el repositorio

---

## 🔧 Paso 1: Preparar el Sistema

### En Ubuntu/Linux

```bash
# Actualizar el sistema
sudo apt update && sudo apt upgrade -y

# Instalar Python 3 y herramientas
sudo apt install -y python3 python3-pip python3-venv git

# (Opcional) Instalar CUDA si tienes GPU NVIDIA
# Visita: https://developer.nvidia.com/cuda-downloads
```

### En Windows

1. Instala **Python 3.11** desde [python.org](https://www.python.org/downloads/)
2. Instala **Git** desde [git-scm.com](https://git-scm.com/)
3. (Opcional) Instala **CUDA Toolkit** desde [NVIDIA](https://developer.nvidia.com/cuda-downloads)

---

## 📦 Paso 2: Clonar el Repositorio

```bash
# Clonar el proyecto desde GitHub
git clone https://github.com/Yuutaro96/supertraficvision.git

# Entrar al directorio
cd supertraficvision
```

---

## 🐍 Paso 3: Crear Entorno Virtual

### Linux/Mac

```bash
# Crear entorno virtual
python3 -m venv venv

# Activar entorno
source venv/bin/activate
```

### Windows

```cmd
# Crear entorno virtual
python -m venv venv

# Activar entorno
venv\Scripts\activate
```

---

## 📚 Paso 4: Instalar Dependencias

```bash
# Con el entorno virtual activado
pip install --upgrade pip
pip install -r requirements.txt
```

**Nota:** En la primera ejecución, YOLOv8m (~50 MB) se descargará automáticamente.

---

## ⚙️ Paso 5: Configuración Inicial (Opcional)

El sistema funciona con valores predeterminados, pero puedes ajustarlo:

### Configurar para usar GPU

El sistema detecta automáticamente si tienes GPU NVIDIA con CUDA. Para forzar CPU:

```python
# Editar config.py
device = "cpu"  # Cambiar a "cpu" si quieres forzar CPU
```

### Ajustar rendimiento

En `config.py` puedes modificar:

```python
# Frames por segundo a procesar por cámara
target_fps = 10  # Reducir a 5-8 para PCs lentos

# Umbral de confianza (0.0 - 1.0)
confidence = 0.5  # Subir a 0.6-0.7 para menos falsos positivos

# Tamaño del modelo
model_size = "m"  # Cambiar a "n" (más rápido) o "l" (más preciso)
```

---

## 🚀 Paso 6: Lanzar la Aplicación

### Modo Desarrollo (con recarga automática)

```bash
python main.py
```

### Modo Producción (recomendado para Mini PC)

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

**Parámetros explicados:**
- `--host 0.0.0.0`: Permite acceso desde cualquier dispositivo en la red
- `--port 8000`: Puerto de la aplicación (cámbialo si 8000 está ocupado)
- `--workers 1`: Un solo worker (suficiente para este tipo de app)

---

## 🌐 Paso 7: Acceder a la Aplicación

### Desde el Mini PC

```
http://localhost:8000
```

### Desde cualquier dispositivo en tu red WiFi/LAN

```
http://IP_DEL_MINI_PC:8000
```

**Para encontrar la IP del Mini PC:**

**Linux:**
```bash
hostname -I
# Salida ejemplo: 192.168.1.100
```

**Windows:**
```cmd
ipconfig
# Busca "Dirección IPv4"
```

---

## 📹 Paso 8: Configurar Cámaras IP

### 1. Ir a la sección "Cámaras"

### 2. Agregar cámara con formato RTSP/HTTP

Ejemplos de URLs:

```bash
# RTSP genérico
rtsp://admin:password123@192.168.1.50:554/stream

# Hikvision
rtsp://admin:12345@192.168.1.64:554/Streaming/Channels/101

# Dahua
rtsp://admin:admin@192.168.1.108:554/cam/realmonitor?channel=1&subtype=0

# HTTP/MJPEG
https://cdn01.capitolcam.net/catalog/product/cache/273c5b1870006d7e79ca62b731840689/h/s/hs4m212d-4mp-high-speed-dome-poe-camera-front.jpg

# Cámara USB local (solo en el Mini PC)
/dev/video0
# o en Windows:
0

# Archivo de video de prueba
/home/usuario/Videos/trafico.mp4
```

### 3. Activar la cámara

Marca la casilla "Activa" y guarda.

---

## 📏 Paso 9: Configurar Líneas Virtuales

### 1. Ir a la sección "Líneas"

### 2. Seleccionar cámara y cargar frame

### 3. Hacer clic en el canvas para definir:
   - **Punto A**: Primer clic (inicio de la línea)
   - **Punto B**: Segundo clic (fin de la línea)

### 4. Asignar nombre y dirección:
   - **Nombre**: "Entrada Principal", "Giro Izquierda Av. Central", etc.
   - **Movimiento**: NORTE, SUR, ESTE, OESTE, GIRO_IZQ, GIRO_DER, RECTO

### 5. Guardar línea

La línea aparecerá inmediatamente en el stream de video.

---

## 🔄 Mantener la Aplicación Ejecutándose 24/7

### Opción 1: systemd (Linux - Recomendado)

Crear servicio para que inicie automáticamente:

```bash
# Crear archivo de servicio
sudo nano /etc/systemd/system/supertrafic.service
```

Contenido del archivo:

```ini
[Unit]
Description=SuperTraficVision - Sistema de Aforo Vehicular
After=network.target

[Service]
Type=simple
User=TU_USUARIO
WorkingDirectory=/home/TU_USUARIO/supertraficvision
Environment="PATH=/home/TU_USUARIO/supertraficvision/venv/bin"
ExecStart=/home/TU_USUARIO/supertraficvision/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Reemplaza `TU_USUARIO` con tu nombre de usuario.**

Luego activa el servicio:

```bash
# Recargar systemd
sudo systemctl daemon-reload

# Habilitar inicio automático
sudo systemctl enable supertrafic.service

# Iniciar servicio
sudo systemctl start supertrafic.service

# Ver estado
sudo systemctl status supertrafic.service

# Ver logs en tiempo real
sudo journalctl -u supertrafic.service -f
```

### Opción 2: PM2 (Multiplataforma)

```bash
# Instalar PM2
npm install -g pm2

# Iniciar aplicación con PM2
pm2 start "uvicorn main:app --host 0.0.0.0 --port 8000" --name supertrafic

# Guardar para reinicio automático
pm2 save
pm2 startup

# Ver logs
pm2 logs supertrafic

# Monitorear
pm2 monit
```

### Opción 3: Screen (Simple, Linux)

```bash
# Instalar screen
sudo apt install screen

# Crear sesión
screen -S supertrafic

# Activar entorno y ejecutar
cd ~/supertraficvision
source venv/bin/activate
python main.py

# Presionar: Ctrl+A, luego D (para desconectar)

# Para volver a conectar:
screen -r supertrafic
```

### Opción 4: Tarea Programada (Windows)

1. Crear un archivo `start.bat`:

```batch
@echo off
cd C:\Users\TU_USUARIO\supertraficvision
call venv\Scripts\activate
uvicorn main:app --host 0.0.0.0 --port 8000
```

2. **Programador de Tareas de Windows:**
   - Abrir "Programador de tareas"
   - Crear tarea básica → "Al iniciar sesión"
   - Acción: "Iniciar programa" → Seleccionar `start.bat`

---

## 🔒 Seguridad (Opcional pero Recomendado)

### 1. Firewall

Permitir solo el puerto 8000:

**Linux (UFW):**
```bash
sudo ufw allow 8000/tcp
sudo ufw enable
```

**Windows Firewall:**
- Panel de Control → Firewall → Reglas de entrada
- Nueva regla → Puerto TCP 8000

### 2. Acceso Externo (desde Internet)

⚠️ **No recomendado sin autenticación**, pero si lo necesitas:

- Configura **Port Forwarding** en tu router (puerto 8000 → IP del Mini PC)
- Considera usar un túnel seguro como:
  - **Tailscale** (recomendado): [tailscale.com](https://tailscale.com/)
  - **Cloudflare Tunnel**: [cloudflare.com/products/tunnel](https://www.cloudflare.com/products/tunnel/)
  - **ngrok**: `ngrok http 8000`

---

## 📊 Exportar Reportes

### Desde la Interfaz Web

1. Ve a **Reportes**
2. Aplica filtros (cámara, fecha, clase de vehículo)
3. Clic en **"Exportar CSV"**

### Desde la Base de Datos (SQLite)

```bash
# Copiar la base de datos para análisis externo
cp aforo.db aforo_backup_$(date +%Y%m%d).db

# Consultar con sqlite3
sqlite3 aforo.db "SELECT * FROM counts WHERE date(timestamp) = date('now')"
```

---

## 🛠️ Mantenimiento

### Ver logs en tiempo real

```bash
# Si usas systemd
sudo journalctl -u supertrafic.service -f

# Si usas PM2
pm2 logs supertrafic

# Si ejecutas manualmente
# Los logs aparecen en la terminal
```

### Respaldar base de datos

```bash
# Hacer backup de la base de datos
cp aforo.db backups/aforo_$(date +%Y%m%d).db
```

### Limpiar registros antiguos (opcional)

```bash
# Conectar a SQLite
sqlite3 aforo.db

# Eliminar registros de hace más de 90 días
DELETE FROM counts WHERE timestamp < datetime('now', '-90 days');

# Salir
.exit
```

### Actualizar el código desde GitHub

```bash
cd ~/supertraficvision
git pull origin master
pip install -r requirements.txt --upgrade
# Reiniciar el servicio
sudo systemctl restart supertrafic.service
```

---

## 🐛 Solución de Problemas

### La cámara no conecta

1. Verifica la URL con VLC: `vlc rtsp://tu_url_aqui`
2. Revisa usuario/contraseña de la cámara
3. Confirma que la cámara está en la misma red
4. Desactiva firewall temporalmente para probar

### Bajo rendimiento / FPS bajo

1. Reducir `target_fps` en `config.py` a 5-8
2. Cambiar modelo a `yolov8n.pt` (más ligero)
3. Reducir número de cámaras activas simultáneas
4. Verificar uso de GPU con `nvidia-smi` (debe aparecer python)

### Error: "CUDA out of memory"

```python
# En config.py, forzar CPU
device = "cpu"
```

### El stream de video no se ve

- Verifica que estés accediendo desde `http://` (no `https://`)
- Prueba otro navegador (Chrome/Firefox recomendados)
- Revisa la consola del navegador (F12) para errores

---

## 📞 Soporte

- **Repositorio GitHub**: [github.com/Yuutaro96/supertraficvision](https://github.com/Yuutaro96/supertraficvision)
- **Issues**: Reporta problemas en la sección "Issues" de GitHub
- **Documentación técnica**: Ver `README.md` en el repositorio

---

## 📝 Licencia

Este proyecto es de código abierto. Consulta el archivo `LICENSE` para más detalles.

---

## 🎉 ¡Listo!

Tu sistema de aforo vehicular está funcionando 24/7 en tu Mini PC. Ahora puedes:

✅ Monitorear múltiples cámaras IP en tiempo real  
✅ Registrar aforos vehiculares con líneas virtuales  
✅ Analizar tráfico con reportes y gráficas  
✅ Exportar datos a CSV para informes ejecutivos  

**¡Bienvenido a SuperTraficVision!** 🚗📊
