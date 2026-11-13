from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import shutil
import os
import uuid
from dotenv import load_dotenv

from rag_service import (
    consultar_rag,
    procesar_y_guardar_archivo,
    eliminar_documento_por_fuente,
    registrar_feedback_metrica,
    obtener_estadisticas_tesis
)

load_dotenv()

app = FastAPI(
    title="Humboldt RAG Backend Multimodal e Híbrido",
    description="Backend oficial para trabajo de grado - Universidad Alejandro de Humboldt",
    version="4.0"
)

# CORS restringido exclusivamente al frontend en Vercel (y localhost para pruebas)
ALLOWED_ORIGINS = [
    "https://humboldtrag-frontend.vercel.app",
    "http://localhost:5500",
    "http://127.0.0.1:5500",
    "http://localhost:3000"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def verificar_admin(authorization: Optional[str] = Header(None)):
    """Valida el token secreto de administrador enviado en el header Authorization."""
    admin_secret = os.getenv("ADMIN_SECRET_KEY")
    if not admin_secret:
        raise HTTPException(status_code=500, detail="ADMIN_SECRET_KEY no configurada en el servidor.")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Acceso no autorizado. Token requerido.")
    token = authorization.replace("Bearer ", "").strip()
    if token != admin_secret:
        raise HTTPException(status_code=403, detail="Contraseña de administrador incorrecta.")


class MensajeHistorial(BaseModel):
    rol: str
    texto: str


class QueryRequest(BaseModel):
    pregunta: str
    historial: list[MensajeHistorial] = []


class FeedbackRequest(BaseModel):
    metric_id: int
    feedback: int  # 1 o -1


@app.post("/chat")
def chat_endpoint(request: QueryRequest):
    if not request.pregunta.strip():
        raise HTTPException(status_code=400, detail="La pregunta no puede estar vacía.")
    try:
        historial_dict = [m.model_dump() for m in request.historial]
        return consultar_rag(request.pregunta, historial_dict)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/feedback")
def feedback_endpoint(request: FeedbackRequest):
    if request.feedback not in (1, -1):
        raise HTTPException(status_code=400, detail="Feedback debe ser 1 o -1.")
    try:
        registrar_feedback_metrica(request.metric_id, request.feedback)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/verificar", dependencies=[Depends(verificar_admin)])
def verificar_sesion_admin():
    return {"status": "authenticated"}


@app.post("/admin/subir", dependencies=[Depends(verificar_admin)])
async def subir_archivo(file: UploadFile = File(...)):
    extension = file.filename.lower().split('.')[-1]
    if extension not in ["pdf", "docx", "xlsx", "png", "jpg", "jpeg"]:
        raise HTTPException(status_code=400, detail="Formato no soportado. Use PDF, DOCX, XLSX, PNG o JPG.")

    ruta_temporal = f"temp_{uuid.uuid4().hex}_{file.filename}"
    with open(ruta_temporal, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        total_unidades = procesar_y_guardar_archivo(ruta_temporal, file.filename)
        return {
            "status": "success",
            "mensaje": f"Archivo '{file.filename}' indexado exitosamente ({total_unidades} fragmentos vectoriales)."
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(ruta_temporal):
            os.remove(ruta_temporal)


@app.delete("/admin/documentos/{nombre_archivo}", dependencies=[Depends(verificar_admin)])
def eliminar_archivo(nombre_archivo: str):
    try:
        eliminar_documento_por_fuente(nombre_archivo)
        return {"status": "success", "mensaje": f"Archivo '{nombre_archivo}' eliminado."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/documentos/conteo", dependencies=[Depends(verificar_admin)])
def obtener_conteo_documentos():
    try:
        datos = obtener_estadisticas_tesis()
        return {"status": "success", **datos}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
def root():
    return {"status": "Backend Humboldt RAG v4.0 operando en Render"}
