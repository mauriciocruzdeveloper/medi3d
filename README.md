# Medi3D

Visor interactivo de imágenes DICOM de resonancia magnética con capacidad de segmentación semiautomática y generación de modelos 3D en el navegador.

## Características

*   **Modo Visor Ligero:** Escanea carpetas con archivos DICOM, clasifica las series automáticamente (T1, T2, DP, plano, contraste) y permite explorarlas corte por corte. Funciona rápido y sin requerir capacidades 3D.
*   **Modelado 3D bajo demanda:** Al activar el modo 3D, puedes hacer clic en cualquier lesión u órgano en un corte para segmentarlo mediante algoritmos de crecimiento de regiones guiado por confianza.
*   **Exportación:** Genera modelos 3D y permite descargarlos en formatos estándar: STL, OBJ y GLB (optimizado para web).

## Uso rápido (Desarrollo)

Si tienes Python instalado:

1. Clona el repositorio.
2. Instala las dependencias:
   ```bash
   pip install -r requirements.txt
   ```
3. Ejecuta el servidor:
   ```bash
   uvicorn backend.app.main:app --port 8000
   ```
4. Abre `http://localhost:8000` en tu navegador.

## Distribución (Para usuarios sin conocimientos técnicos)

Para que cualquier persona pueda ejecutar el programa sin instalar Python ni dependencias, la mejor opción es crear un archivo ejecutable usando **PyInstaller**.

### Cómo compilar el ejecutable

1. Instala PyInstaller:
   ```bash
   pip install pyinstaller
   ```
2. Ejecuta el siguiente comando para generar un único archivo ejecutable. Este comando le indica a PyInstaller que incluya la carpeta `frontend` dentro del empaquetado:

   **En Linux/Mac:**
   ```bash
   pyinstaller --name Medi3D --onefile --add-data "frontend:frontend" backend/app/main.py
   ```

   **En Windows:**
   ```cmd
   pyinstaller --name Medi3D --onefile --add-data "frontend;frontend" backend/app/main.py
   ```

3. El archivo resultante estará en la carpeta `dist/`. Puedes compartir este archivo (`Medi3D.exe` en Windows o `Medi3D` en Linux/Mac) con cualquier persona. Al hacer doble clic, se levantará el servidor en segundo plano y el usuario solo tendrá que abrir `http://localhost:8000`.

*Nota:* Si compilas en Windows, el ejecutable solo servirá para Windows. Si compilas en Linux, solo para Linux. Para distribuir a varios sistemas operativos, debes correr el comando de PyInstaller en cada uno de ellos.
