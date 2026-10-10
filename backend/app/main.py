"""Medi3D backend: escaneo DICOM, segmentación asistida y mallas 3D."""
import io
import os
import sys
import uuid
from pathlib import Path

import numpy as np
import SimpleITK as sitk
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel
# Las librerías de modelado (scipy, scikit-image, trimesh) se importan solo al
# usar el modelado 3D, para que el visor DICOM funcione sin ellas.

app = FastAPI(title="Medi3D")

SERIES: dict[str, dict] = {}   # uid -> metadata + files
VOLUMES: dict[str, sitk.Image] = {}
MASKS: dict[str, dict] = {}


@app.get("/api/capabilities")
def capabilities():
    """Indica si esta máquina tiene lo necesario para el modelado 3D."""
    try:
        import scipy, skimage, trimesh  # noqa: F401
        return {"modelado3d": True}
    except ImportError as e:
        return {"modelado3d": False, "motivo": f"Falta instalar: {e.name}"}


# ---------------------------------------------------------------- escaneo
def classify(desc: str) -> dict:
    d = desc.lower()
    plane = "ax" if ("ax" in d or "tra" in d) else "cor" if "cor" in d else "sag" if "sag" in d else "?"
    seq = "DP" if d.startswith("dp") else "T1" if "t1" in d else "T2" if "t2" in d else "FLAIR" if "flair" in d else "?"
    contrast = "post" if "con cte" in d or "gd" in d or "+c" in d else "pre" if "pre" in d else ""
    fat = "fatsat" if "fs" in d else ""
    return {"plano": plane, "secuencia": seq, "contraste": contrast, "fatsat": fat}


class ScanReq(BaseModel):
    path: str


@app.post("/api/scan")
def scan(req: ScanReq):
    root = Path(os.path.expanduser(req.path))
    if not root.is_dir():
        raise HTTPException(404, f"No existe la carpeta: {root}")
    SERIES.clear()
    for dirpath, _, _ in os.walk(root):
        for uid in sitk.ImageSeriesReader.GetGDCMSeriesIDs(dirpath) or []:
            files = sitk.ImageSeriesReader.GetGDCMSeriesFileNames(dirpath, uid)
            r = sitk.ImageFileReader()
            r.SetFileName(files[0])
            r.ReadImageInformation()
            def g(k):
                if not r.HasMetaDataKey(k):
                    return ""
                raw = r.GetMetaData(k).encode("utf-8", "surrogateescape")
                try:
                    return raw.decode("utf-8").strip()
                except UnicodeDecodeError:
                    return raw.decode("latin-1").strip()
            desc = g("0008|103e")
            SERIES[uid] = {
                "uid": uid, "descripcion": desc, "cortes": len(files),
                "modalidad": g("0008|0060"), "estudio": g("0008|1030"),
                "grosor": g("0018|0050"), "files": list(files), **classify(desc),
            }
    out = [{k: v for k, v in s.items() if k != "files"} for s in SERIES.values()]
    return sorted(out, key=lambda s: (s["plano"], s["secuencia"], s["descripcion"]))


def volume(uid: str) -> sitk.Image:
    if uid not in SERIES:
        raise HTTPException(404, "Serie desconocida")
    if uid not in VOLUMES:
        rd = sitk.ImageSeriesReader()
        rd.SetFileNames(SERIES[uid]["files"])
        VOLUMES[uid] = sitk.Cast(rd.Execute(), sitk.sitkFloat32)
    return VOLUMES[uid]


@app.get("/api/series/{uid}/info")
def info(uid: str):
    v = volume(uid)
    w, h, d = v.GetSize()
    return {"ancho": w, "alto": h, "cortes": d, "espaciado": v.GetSpacing()}


def window(arr: np.ndarray) -> np.ndarray:
    lo, hi = np.percentile(arr, [1, 99.5])
    return (np.clip((arr - lo) / max(hi - lo, 1e-6), 0, 1) * 255).astype(np.uint8)


@app.get("/api/series/{uid}/slice/{z}.png")
def slice_png(uid: str, z: int, mask: str | None = None):
    arr = sitk.GetArrayFromImage(volume(uid))
    z = max(0, min(z, arr.shape[0] - 1))
    gray = window(arr)[z]
    rgb = np.stack([gray] * 3, -1)
    if mask and mask in MASKS and MASKS[mask]["uid"] == uid:
        m = MASKS[mask]["array"][z]
        rgb[m] = (0.5 * rgb[m] + 0.5 * np.array([255, 60, 60])).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, "PNG")
    return Response(buf.getvalue(), media_type="image/png")


# ---------------------------------------------------------- segmentación
class SegReq(BaseModel):
    uid: str
    x: int
    y: int
    z: int
    multiplicador: float = 2.5
    radio_max_mm: float = 60.0


@app.post("/api/segment")
def segment(req: SegReq):
    """Segmentación semiautomática por clic: crecimiento de regiones por
    confianza, limitado a una esfera para evitar fugas."""
    from scipy import ndimage as ndi
    v = volume(req.uid)
    size = v.GetSize()
    if not (0 <= req.x < size[0] and 0 <= req.y < size[1] and 0 <= req.z < size[2]):
        raise HTTPException(400, "Semilla fuera del volumen")
    smooth = sitk.CurvatureAnisotropicDiffusion(v, timeStep=0.025, numberOfIterations=3)
    seg = sitk.ConfidenceConnected(
        smooth, seedList=[(req.x, req.y, req.z)], numberOfIterations=4,
        multiplier=req.multiplicador, initialNeighborhoodRadius=2, replaceValue=1)
    m = sitk.GetArrayFromImage(seg).astype(bool)
    # limitar a esfera alrededor de la semilla (en mm)
    sp = v.GetSpacing()
    zz, yy, xx = np.indices(m.shape)
    dist = np.sqrt(((xx - req.x) * sp[0]) ** 2 + ((yy - req.y) * sp[1]) ** 2 + ((zz - req.z) * sp[2]) ** 2)
    m &= dist <= req.radio_max_mm
    m = ndi.binary_opening(m, iterations=1)
    lab, n = ndi.label(m)
    if n == 0 or lab[req.z, req.y, req.x] == 0:
        raise HTTPException(422, "No se pudo segmentar desde ese punto; probá otro clic o multiplicador.")
    m = lab == lab[req.z, req.y, req.x]
    m = ndi.binary_fill_holes(m)
    mid = uuid.uuid4().hex[:8]
    vol_ml = float(m.sum() * sp[0] * sp[1] * sp[2] / 1000)
    MASKS[mid] = {"uid": req.uid, "array": m}
    return {"mask": mid, "volumen_ml": round(vol_ml, 2), "voxeles": int(m.sum())}


# ------------------------------------------------------------------ malla
def build_mesh(mid: str, suavizado: int = 20):
    import trimesh
    from scipy import ndimage as ndi
    from skimage import measure
    if mid not in MASKS:
        raise HTTPException(404, "Máscara desconocida")
    entry = MASKS[mid]
    v = volume(entry["uid"])
    sp = np.array(v.GetSpacing()[::-1])  # z,y,x en mm
    m = entry["array"].astype(np.float32)
    # remuestreo isotrópico (~sub-mm) de la máscara suavizada para evitar escalones
    target = min(sp.min(), 1.0)
    zoom = sp / target
    m = ndi.gaussian_filter(m, sigma=0.7)
    m = ndi.zoom(np.pad(m, 2), zoom, order=1)
    m = ndi.gaussian_filter(m, sigma=1.0)
    verts, faces, _, _ = measure.marching_cubes(m, level=0.5, spacing=(target,) * 3)
    verts = verts - 2 * target
    mesh = trimesh.Trimesh(vertices=verts[:, ::-1], faces=faces)  # x,y,z mm
    if suavizado:
        trimesh.smoothing.filter_taubin(mesh, iterations=suavizado)
    return mesh


@app.get("/api/mesh/{mid}.{fmt}")
def mesh_export(mid: str, fmt: str, suavizado: int = 20):
    if fmt not in ("stl", "obj", "glb"):
        raise HTTPException(400, "Formato no soportado")
    mesh = build_mesh(mid, suavizado)
    if fmt == "glb" and len(mesh.faces) > 150_000:  # liviano para el visor web
        mesh = mesh.simplify_quadric_decimation(face_count=150_000)
    data = mesh.export(file_type={"glb": "glb", "stl": "stl", "obj": "obj"}[fmt])
    mt = {"glb": "model/gltf-binary", "stl": "model/stl", "obj": "text/plain"}[fmt]
    return Response(data if isinstance(data, bytes) else data.encode(), media_type=mt,
                    headers={"Content-Disposition": f'inline; filename="modelo.{fmt}"'})


# ------------------------------------------------------------------ frontend
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    # Ejecutándose como binario compilado (PyInstaller)
    frontend_path = Path(sys._MEIPASS) / "frontend"
else:
    # Ejecutándose desde el código fuente
    frontend_path = Path(__file__).parents[2] / "frontend"

app.mount("/", StaticFiles(directory=frontend_path, html=True), name="web")

if __name__ == "__main__":
    import uvicorn
    # Permite ejecutar el script directamente (python backend/app/main.py) o como .exe
    uvicorn.run(app, host="0.0.0.0", port=8000)
