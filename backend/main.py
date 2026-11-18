from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Header, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import shutil
import os
import uuid
import json
import hmac
import hashlib
import base64
import time
from dotenv import load_dotenv

from rag_service import (
    DEPARTAMENTOS_VALIDOS,
    registrar_usuario,
    autenticar_usuario,
    listar_usuarios,
    actualizar_usuario_admin,
    eliminar_usuario_admin,
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
    version="5.0"
)

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

estado_tareas: dict[str, dict] = {}


# =========================================================
# TOKENS FIRMADOS (HMAC-SHA256)
# =========================================================

def generar_token_sesion(usuario: dict) -> str:
    secret = os.getenv("ADMIN_SECRET_KEY", "victory_logistic_default_secret")
    payload = {
        **usuario,
        "exp": int(time.time()) + 86400 * 7  # Válido por 7 días
    }
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    firma = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{firma}"


def decodificar_token_sesion(token: str) -> dict:
    secret = os.getenv("ADMIN_SECRET_KEY", "victory_logistic_default_secret")
    try:
        payload_b64, firma = token.split(".", 1)
        firma_esperada = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(firma, firma_esperada):
            raise ValueError("Firma inválida")
        payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode())
        if payload.get("exp", 0) < time.time():
            raise ValueError("Sesión expirada")
        return payload
    except Exception:
        raise HTTPException(status_code=401, detail="Sesión inválida o expirada. Inicia sesión nuevamente.")


def obtener_usuario_actual(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Debes iniciar sesión para acceder.")
    token = authorization.replace("Bearer ", "").strip()
    return decodificar_token_sesion(token)


def verificar_admin(usuario: dict = Depends(obtener_usuario_actual)) -> dict:
    if usuario.get("rol") != "admin":
        raise HTTPException(status_code=403, detail="Acceso restringido solo a administradores.")
    return usuario


# =========================================================
# ESQUEMAS PYDANTIC
# =========================================================

class RegistroRequest(BaseModel):
    nombre: str
    username: str
    password: str
    departamento: str


class LoginRequest(BaseModel):
    username: str
    password: str


class UpdateUserRequest(BaseModel):
    rol: Optional[str] = None
    departamento: Optional[str] = None


class MensajeHistorial(BaseModel):
    rol: str
    texto: str


class QueryRequest(BaseModel):
    pregunta: str
    historial: list[MensajeHistorial] = []
    filtro_departamento: Optional[str] = None


class FeedbackRequest(BaseModel):
    metric_id: int
    feedback: int


# =========================================================
# ENDPOINTS DE AUTENTICACIÓN Y USUARIOS
# =========================================================

@app.get("/auth/departamentos")
def get_departamentos():
    return {"departamentos": [d for d in DEPARTAMENTOS_VALIDOS if d != "General"]}


@app.post("/auth/registro")
def endpoint_registro(req: RegistroRequest):
    try:
        usuario = registrar_usuario(req.nombre, req.username, req.password, req.departamento)
        token = generar_token_sesion(usuario)
        return {"status": "success", "token": token, "usuario": usuario}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/auth/login")
def endpoint_login(req: LoginRequest):
    try:
        usuario = autenticar_usuario(req.username, req.password)
        token = generar_token_sesion(usuario)
        return {"status": "success", "token": token, "usuario": usuario}
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/auth/me")
def endpoint_me(usuario: dict = Depends(obtener_usuario_actual)):
    return {"status": "authenticated", "usuario": usuario}


# =========================================================
# ENDPOINTS DE CHAT RAG
# =========================================================

@app.post("/chat")
def chat_endpoint(request: QueryRequest, usuario: dict = Depends(obtener_usuario_actual)):
    if not request.pregunta.strip():
        raise HTTPException(status_code=400, detail="La pregunta no puede estar vacía.")
    try:
        historial_dict = [m.model_dump() for m in request.historial]
        return consultar_rag(
            pregunta=request.pregunta,
            historial=historial_dict,
            usuario_actual=usuario,
            filtro_admin_depto=request.filtro_departamento
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/feedback")
def feedback_endpoint(request: FeedbackRequest, usuario: dict = Depends(obtener_usuario_actual)):
    if request.feedback not in (1, -1):
        raise HTTPException(status_code=400, detail="Feedback debe ser 1 o -1.")
    try:
        registrar_feedback_metrica(request.metric_id, request.feedback)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================
# ENDPOINTS DE ADMINISTRACIÓN
# =========================================================

def tarea_indexar_en_segundo_plano(task_id: str, ruta_temporal: str, nombre_original: str, departamento: str):
    try:
        estado_tareas[task_id] = {
            "status": "procesando",
            "mensaje": f"Indexando '{nombre_original}' en el departamento '{departamento}'..."
        }
        total_unidades = procesar_y_guardar_archivo(ruta_temporal, nombre_original, departamento)
        estado_tareas[task_id] = {
            "status": "completado",
            "mensaje": f"¡Éxito! '{nombre_original}' indexado en '{departamento}' ({total_unidades} fragmentos)."
        }
    except Exception as e:
        estado_tareas[task_id] = {
            "status": "error",
            "mensaje": f"Error al procesar '{nombre_original}': {str(e)}"
        }
    finally:
        if os.path.exists(ruta_temporal):
            os.remove(ruta_temporal)


@app.post("/admin/subir", dependencies=[Depends(verificar_admin)])
async def subir_archivo(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    departamento: str = Form(...)
):
    if departamento not in DEPARTAMENTOS_VALIDOS:
        raise HTTPException(status_code=400, detail="Selecciona un departamento válido.")

    extension = file.filename.lower().split('.')[-1]
    if extension not in ["pdf", "docx", "xlsx", "png", "jpg", "jpeg"]:
        raise HTTPException(status_code=400, detail="Formato no soportado. Use PDF, DOCX, XLSX, PNG o JPG.")

    task_id = uuid.uuid4().hex
    ruta_temporal = f"temp_{task_id}_{file.filename}"
    with open(ruta_temporal, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    estado_tareas[task_id] = {
        "status": "procesando",
        "mensaje": f"Archivo '{file.filename}' recibido para '{departamento}'. Iniciando..."
    }
    background_tasks.add_task(tarea_indexar_en_segundo_plano, task_id, ruta_temporal, file.filename, departamento)

    return {
        "status": "accepted",
        "task_id": task_id,
        "mensaje": f"Procesando '{file.filename}' ({departamento}) en segundo plano..."
    }


@app.get("/admin/tarea/{task_id}", dependencies=[Depends(verificar_admin)])
def consultar_estado_tarea(task_id: str):
    tarea = estado_tareas.get(task_id)
    if not tarea:
        raise HTTPException(status_code=404, detail="Tarea no encontrada.")
    return tarea


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
        usuarios = listar_usuarios()
        return {"status": "success", **datos, "usuarios": usuarios}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/admin/usuarios/{user_id}", dependencies=[Depends(verificar_admin)])
def editar_usuario(user_id: int, req: UpdateUserRequest):
    try:
        actualizar_usuario_admin(user_id, req.rol, req.departamento)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/admin/usuarios/{user_id}", dependencies=[Depends(verificar_admin)])
def borrar_usuario(user_id: int):
    try:
        eliminar_usuario_admin(user_id)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
def root():
    return {"status": "Backend Victory Logistic RAG v5.0 operando en Render"}
