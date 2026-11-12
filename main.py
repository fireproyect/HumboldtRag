from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel
import shutil
import os
from rag_service import consultar_rag, procesar_y_guardar_archivo, eliminar_documento_por_fuente

app = FastAPI(title="Tesis RAG Backend", version="1.0")

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

@app.get("/")
def root():
    return {"status": "Backend de RAG operando al 100% 🚀"}