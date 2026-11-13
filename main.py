from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel
import shutil
import os
from supabase import create_client
from dotenv import load_dotenv

from rag_service import consultar_rag, procesar_y_guardar_archivo, eliminar_documento_por_fuente

load_dotenv()

app = FastAPI(
    title="Tesis RAG Backend Multimodal e Híbrido", 
    description="Backend oficial con FastAPI, Supabase pgvector, gemini-embedding-2 y gemini-3.5-flash-lite",
    version="2.1"
)

# Cliente Supabase para endpoints administrativos directos
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))

class QueryRequest(BaseModel):
    pregunta: str

# Endpoint principal de chat RAG
@app.post("/chat", tags=["RAG"])
def chat_endpoint(request: QueryRequest):
    try:
        resultado = consultar_rag(request.pregunta)
        return resultado
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Endpoint para subir e indexar archivos multimedia y de texto
@app.post("/admin/subir", tags=["Admin"])
async def subir_archivo(file: UploadFile = File(...)):
    extension = file.filename.lower().split('.')[-1]
    
    formatos_permitidos = ["pdf", "docx", "xlsx", "png", "jpg", "jpeg"]
    if extension not in formatos_permitidos:
        raise HTTPException(
            status_code=400, 
            detail=f"Formato .{extension} no soportado. Usa PDF, Word, Excel o Imágenes (PNG, JPG)."
        )

    ruta_temporal = f"temp_{file.filename}"
    with open(ruta_temporal, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        total_unidades = procesar_y_guardar_archivo(ruta_temporal, file.filename)
        return {
            "status": "success",
            "mensaje": f"Archivo '{file.filename}' procesado e indexado correctamente ({total_unidades} elementos vectorizados)."
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(ruta_temporal):
            os.remove(ruta_temporal)

# Endpoint para eliminar documentos por nombre de archivo
@app.delete("/admin/documentos/{nombre_archivo}", tags=["Admin"])
def eliminar_archivo(nombre_archivo: str):
    try:
        eliminar_documento_por_fuente(nombre_archivo)
        return {
            "status": "success",
            "mensaje": f"El archivo '{nombre_archivo}' fue eliminado de la base de datos vectorial."
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Endpoint administrativo para conteo y listado de archivos en Supabase
@app.get("/admin/documentos/conteo", tags=["Admin"])
def obtener_conteo_documentos():
    try:
        response = supabase.table("documents").select("metadata").execute()
        
        documentos = response.data
        total_registros = len(documentos)
        
        archivos_unicos = set()
        for doc in documentos:
            meta = doc.get("metadata", {})
            if "fuente" in meta:
                archivos_unicos.add(meta["fuente"])

        return {
            "status": "success",
            "total_registros_vectoriales": total_registros,
            "total_archivos_subidos": len(archivos_unicos),
            "archivos": list(archivos_unicos)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Endpoint raíz de verificación
@app.get("/", tags=["General"])
def root():
    return {"status": "Backend RAG Multimodal Híbrido operando al 100% 🚀"}
