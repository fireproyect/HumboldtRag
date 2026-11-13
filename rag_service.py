import os
import shutil
import pypdf
import docx
import pandas as pd
from google import genai
from google.genai import types
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

# Inicializar clientes oficiales
gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))

# 1. Función para extraer texto de documentos tradicionales
def extraer_texto_de_archivo(ruta_archivo: str) -> str:
    extension = ruta_archivo.lower().split('.')[-1]
    texto_extraido = ""

    if extension == "pdf":
        reader = pypdf.PdfReader(ruta_archivo)
        for pagina in reader.pages:
            texto_extraido += pagina.extract_text() + "\n"
    elif extension == "docx":
        doc = docx.Document(ruta_archivo)
        for para in doc.paragraphs:
            texto_extraido += para.text + "\n"
    elif extension == "xlsx":
        df = pd.read_excel(ruta_archivo)
        texto_extraido = df.to_string(index=False)
    else:
        raise ValueError(f"Formato .{extension} no compatible para extracción de texto directo.")

    return texto_extraido

# 2. Ingesta Multimodal: Maneja texto, PDFs, Excel, Word e Imágenes
def procesar_y_guardar_archivo(ruta_archivo: str, nombre_original: str):
    extension = ruta_archivo.lower().split('.')[-1]
    
    if extension in ["png", "jpg", "jpeg"]:
        archivo_subido = gemini_client.files.upload(file=ruta_archivo)
        
        embedding_response = gemini_client.models.embed_content(
            model="gemini-embedding-2",
            contents=archivo_subido,
            config=types.EmbedContentConfig(output_dimensionality=768)
        )
        vector = embedding_response.embeddings[0].values

        data = {
            "content": f"[Documento Multimedia/Imagen Fuente: {nombre_original}]",
            "metadata": {"fuente": nombre_original, "tipo": "imagen"},
            "embedding": vector
        }
        supabase.table("documents").insert(data).execute()
        
        return 1
    else:
        texto_completo = extraer_texto_de_archivo(ruta_archivo)
        
        tamano_chunk = 1000
        solapamiento = 200
        chunks = []
        for i in range(0, len(texto_completo), tamano_chunk - solapamiento):
            chunk = texto_completo[i:i + tamano_chunk]
            if chunk.strip():
                chunks.append(chunk)

        for i, chunk in enumerate(chunks):
            chunk_con_fuente = f"[Documento Fuente: {nombre_original}]\n{chunk}"

            embedding_response = gemini_client.models.embed_content(
                model="gemini-embedding-2",
                contents=chunk_con_fuente,
                config=types.EmbedContentConfig(output_dimensionality=768)
            )
            vector = embedding_response.embeddings[0].values

            data = {
                "content": chunk_con_fuente,
                "metadata": {"fuente": nombre_original, "fragmento": i, "tipo": "texto"},
                "embedding": vector
            }
            supabase.table("documents").insert(data).execute()

        return len(chunks)

# 3. Consulta RAG con inventario de archivos integrado para el modelo
def consultar_rag(pregunta_usuario: str):
    # Obtener el inventario completo de archivos únicos desde Supabase para dárselo a Gemini
    try:
        response_meta = supabase.table("documents").select("metadata").execute()
        archivos_unicos = set()
        for doc in response_meta.data:
            meta = doc.get("metadata", {})
            if "fuente" in meta:
                archivos_unicos.add(meta["fuente"])
        inventario_archivos = list(archivos_unicos)
    except Exception:
        inventario_archivos = []

    # Generar vector de la pregunta usando gemini-embedding-2
    embedding_response = gemini_client.models.embed_content(
        model="gemini-embedding-2",
        contents=pregunta_usuario,
        config=types.EmbedContentConfig(output_dimensionality=768)
    )
    vector_pregunta = embedding_response.embeddings[0].values

    # Buscar fragmentos similares en Supabase
    respuesta_db = supabase.rpc(
        "match_documents",
        {
            "query_embedding": vector_pregunta,
            "match_threshold": 0.4,
            "match_count": 3
        }
    ).execute()

    fragmentos = respuesta_db.data
    contexto = "\n\n".join([item["content"] for item in fragmentos]) if fragmentos else "No se encontró información relevante."

    # Prompt institucional mejorado con el inventario global de archivos
    prompt_final = f"""
    Eres un asistente institucional experto. Responde a la pregunta del usuario utilizando EXCLUSIVAMENTE 
    la información provista en el contexto y en la lista de archivos disponibles. Si la respuesta no está en el contexto ni en el inventario, di amablemente que no posees esa información.

    [INVENTARIO DE ARCHIVOS DISPONIBLES EN EL SISTEMA]:
    {inventario_archivos}

    [CONTEXTO DE FRAGMENTOS RECUPERADOS]:
    {contexto}

    [PREGUNTA]:
    {pregunta_usuario}
    """

    response = gemini_client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=prompt_final
    )

    return {
        "respuesta": response.text,
        "fuentes": list(set([item["metadata"]["fuente"] for item in fragmentos])) if fragmentos else []
    }

# 4. Eliminar archivo de la base de datos
def eliminar_documento_por_fuente(nombre_archivo: str):
    supabase.table("documents").delete().eq("metadata->>fuente", nombre_archivo).execute()
