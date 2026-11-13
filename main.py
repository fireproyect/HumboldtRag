from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel
import shutil
import os
from supabase import create_client
from dotenv import load_dotenv

from rag_service import consultar_rag, procesar_y_guardar_archivo, eliminar_documento_por_fuente

load_dotenv()

app = FastAPI(title="Tesis RAG Backend", version="1.0")

# Inicializar cliente de Supabase para consultas directas de administración
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))

class QueryRequest(BaseModel):
    pregunta: str

@app.post("/chat")
def chat_endpoint(request: QueryRequest):
    try:
        resultado = consultar_rag(request.pregunta)
        return resultado
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/admin/subir")
async def subir_archivo(file: UploadFile = File(...)):
    extension = file.filename.lower().split('.')[-1]
    if extension not in ["pdf", "docx", "xlsx"]:
        raise HTTPException(status_code=400, detail="Formato no soportado. Usa PDF, Word o Excel.")

    ruta_temporal = f"temp_{file.filename}"
    with open(ruta_temporal, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        total_chunks = procesar_y_guardar_archivo(ruta_temporal, file.filename)
        return {
            "status": "success",
            "mensaje": f"Archivo '{file.filename}' procesado e indexado correctamente en {total_chunks} fragmentos."
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(ruta_temporal):
            os.remove(ruta_temporal)

@app.delete("/admin/documentos/{nombre_archivo}")
def eliminar_archivo(nombre_archivo: str):
    try:
        eliminar_documento_por_fuente(nombre_archivo)
        return {
            "status": "success",
            "mensaje": f"El archivo '{nombre_archivo}' fue eliminado de la base de datos vectorial."
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# NUEVO ENDPOINT: Conteo y lista de archivos almacenados
@app.get("/admin/documentos/conteo", tags=["Admin"])
def obtener_conteo_documentos():
    try:
        response = supabase.table("documents").select("metadata").execute()
        
        documentos = response.data
        total_chunks = len(documentos)
        
        archivos_unicos = set()
        for doc in documentos:
            meta = doc.get("metadata", {})
            if "fuente" in meta:
                archivos_unicos.add(meta["fuente"])

        return {
            "status": "success",
            "total_fragmentos_chunks": total_chunks,
            "total_archivos_subidos": len(archivos_unicos),
            "archivos": list(archivos_unicos)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def root():
    return {"status": "Backend de RAG operando al 100% 🚀"}
