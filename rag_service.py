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

# 2. Ingesta Multimodal: Ahora extrae texto de imágenes automáticamente al subirlas
def procesar_y_guardar_archivo(ruta_archivo: str, nombre_original: str):
    extension = ruta_archivo.lower().split('.')[-1]
    
    if extension in ["png", "jpg", "jpeg"]:
        # Subir archivo al servidor de Google para procesamiento multimodal
        archivo_subido = gemini_client.files.upload(file=ruta_archivo)
        
        # Opcional pero potente: usar gemini-3.5-flash-lite para "leer" y transcribir el contenido de la imagen (como el RIF)
        try:
            analisis_imagen = gemini_client.models.generate_content(
                model="gemini-3.5-flash-lite",
                contents=[
                    archivo_subido, 
                    "Extrae y transcribe absolutamente todo el texto visible en esta imagen, incluyendo números de RIF, rif, cédulas, nombres, direcciones y cualquier dato relevante."
                ]
            )
            texto_imagen = analisis_imagen.text
        except Exception:
            texto_imagen = "No se pudo extraer texto automático de la imagen."

        # Generar embedding multimodal con gemini-embedding-2
        embedding_response = gemini_client.models.embed_content(
            model="gemini-embedding-2",
            contents=archivo_subido,
            config=types.EmbedContentConfig(output_dimensionality=768)
        )
        vector = embedding_response.embeddings[0].values

        # Guardar en Supabase integrando el texto extraído para que la búsqueda vectorial lo encuentre
        contenido_completo = f"[Documento Multimedia/Imagen Fuente: {nombre_original}]\nContenido extraído de la imagen:\n{texto_imagen}"
        
        data = {
            "content": contenido_completo,
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

# 3. Consulta RAG con inventario y contexto
def consultar_rag(pregunta_usuario: str):
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

    # Generar vector de la pregunta con gemini-embedding-2
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
            "match_threshold": 0.3, # Umbral ligeramente más flexible para capturar coincidencias visuales
            "match_count": 4
        }
    ).execute()

    fragmentos = respuesta_db.data
    contexto = "\n\n".join([item["content"] for item in fragmentos]) if fragmentos else "No se encontró información relevante."

    prompt_final = f"""
    Eres un asistente institucional experto. Responde a la pregunta del usuario utilizando EXCLUSIVAMENTE 
    la información provista en el contexto y en el inventario de archivos. Si la respuesta no está allí, di amablemente 
    que no posees esa información. Si te preguntan por datos específicos (como números de RIF, cédulas o nombres) 
    busca detalladamente en el contexto provisto por los documentos e imágenes.

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
