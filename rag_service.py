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

# 1. Función para extraer texto de diferentes formatos
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
        raise ValueError(f"Formato .{extension} no compatible.")

    return texto_extraido

# 2. Ingesta: Fragmentar, vectorizar y guardar en Supabase
def procesar_y_guardar_archivo(ruta_archivo: str, nombre_original: str):
    texto_completo = extraer_texto_de_archivo(ruta_archivo)
    
    # Chunking inteligente (bloques de 1000 caracteres con 200 de solapamiento)
    tamano_chunk = 1000
    solapamiento = 200
    chunks = []
    for i in range(0, len(texto_completo), tamano_chunk - solapamiento):
        chunk = texto_completo[i:i + tamano_chunk]
        if chunk.strip():
            chunks.append(chunk)

    for i, chunk in enumerate(chunks):
        # Generar embedding con el modelo actual y forzar 768 dimensiones
        embedding_response = gemini_client.models.embed_content(
            model="gemini-embedding-001",
            contents=chunk,
            config=types.EmbedContentConfig(output_dimensionality=768)
        )
        vector = embedding_response.embeddings[0].values

        # Insertar en Supabase
        data = {
            "content": chunk,
            "metadata": {"fuente": nombre_original, "fragmento": i},
            "embedding": vector
        }
        supabase.table("documents").insert(data).execute()

    return len(chunks)

# 3. Consulta (RAG): Buscar en Supabase y responder con Gemini
def consultar_rag(pregunta_usuario: str):
    # Generar vector de la pregunta con el modelo actual
    embedding_response = gemini_client.models.embed_content(
        model="gemini-embedding-001",
        contents=pregunta_usuario,
        config=types.EmbedContentConfig(output_dimensionality=768)
    )
    vector_pregunta = embedding_response.embeddings[0].values

    # Buscar fragmentos similares en Supabase usando la función RPC
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

    # Prompt con restricciones institucionales
    prompt_final = f"""
    Eres un asistente institucional experto. Responde a la pregunta del usuario utilizando EXCLUSIVAMENTE 
    la información provista en el contexto. Si la respuesta no está en el contexto, di amablemente 
    que no posees esa información.

    [CONTEXTO]:
    {contexto}

    [PREGUNTA]:
    {pregunta_usuario}
    """

    response = gemini_client.models.generate_content(
        model="gemini-1.5-flash",
        contents=prompt_final
    )

    return {
        "respuesta": response.text,
        "fuentes": list(set([item["metadata"]["fuente"] for item in fragmentos])) if fragmentos else []
    }

# 4. Eliminar archivo de la base de datos
def eliminar_documento_por_fuente(nombre_archivo: str):
    supabase.table("documents").delete().eq("metadata->>fuente", nombre_archivo).execute()
