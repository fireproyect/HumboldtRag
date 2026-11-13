from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import shutil
import os
from dotenv import load_dotenv

from rag_service import consultar_rag, procesar_y_guardar_archivo, eliminar_documento_por_fuente

load_dotenv()

app = FastAPI(
    title="Humboldt RAG Backend Multimodal e Híbrido", 
    description="Backend oficial para trabajo de grado - Universidad Alejandro de Humboldt",
    version="3.0"
)

# CORS abierto o restringido a tu URL de Vercel
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Aquí puedes poner la URL exacta de tu frontend en Vercel cuando lo despliegues
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class QueryRequest(BaseModel):
    pregunta: str

@app.post("/chat")
def chat_endpoint(request: QueryRequest):
    try:
        return consultar_rag(request.pregunta)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/admin/subir")
async def subir_archivo(file: UploadFile = File(...)):
    extension = file.filename.lower().split('.')[-1]
    if extension not in ["pdf", "docx", "xlsx", "png", "jpg", "jpeg"]:
        raise HTTPException(status_code=400, detail="Formato no soportado.")

    ruta_temporal = f"temp_{file.filename}"
    with open(ruta_temporal, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        total_unidades = procesar_y_guardar_archivo(ruta_temporal, file.filename)
        return {"status": "success", "mensaje": f"Archivo '{file.filename}' indexado ({total_unidades} unidades)."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(ruta_temporal):
            os.remove(ruta_temporal)

@app.delete("/admin/documentos/{nombre_archivo}")
def eliminar_archivo(nombre_archivo: str):
    try:
        eliminar_documento_por_fuente(nombre_archivo)
        return {"status": "success", "mensaje": f"Archivo '{nombre_archivo}' eliminado."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/admin/documentos/conteo")
def obtener_conteo_documentos():
    from supabase import create_client
    supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))
    try:
        response = supabase.table("documents").select("metadata").execute()
        documentos = response.data
        archivos_unicos = set(doc.get("metadata", {}).get("fuente") for doc in documentos if "fuente" in doc.get("metadata", {}))
        return {
            "status": "success",
            "total_registros_vectoriales": len(documentos),
            "total_archivos_subidos": len(archivos_unicos),
            "archivos": list(archivos_unicos)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def root():
    return {"status": "Backend Humboldt RAG operando en Render 🚀"}
