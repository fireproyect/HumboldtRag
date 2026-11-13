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

# Inicializar clientes oficiales con la SDK moderna google-genai
gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))

# Función auxiliar para extraer texto de documentos tradicionales (PDF, DOCX, XLSX)
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

# Ingesta Híbrida y Multimodal (Maneja texto, PDFs, Excel, Word e Imágenes)
def procesar_y_guardar_archivo(ruta_archivo: str, nombre_original: str):
    extension = ruta_archivo.lower().split('.')[-1]
    
    # CASO A: Si es una imagen, aplicamos la estrategia multimodal e híbrida
    if extension in ["png", "jpg", "jpeg"]:
        # 1. Subir archivo nativamente a la API de Gemini
        archivo_subido = gemini_client.files.upload(file=ruta_archivo)
        
        # 2. Extracción de texto automático (OCR/Visión) usando gemini-3.5-flash-lite para capturar datos exactos (RIF, cédulas, etc.)
        try:
            analisis_imagen = gemini_client.models.generate_content(
                model="gemini-3.5-flash-lite",
                contents=[
                    archivo_subido, 
                    "Extrae y transcribe absolutamente todo el texto visible en esta imagen, incluyendo números de RIF, rif, cédulas, nombres, direcciones y cualquier dato institucional relevante."
                ]
            )
            texto_imagen = analisis_imagen.text
        except Exception:
            texto_imagen = "No se pudo extraer texto automático de la imagen."

        # 3. Generación del embedding multimodal unificado usando el modelo moderno gemini-embedding-2
        embedding_response = gemini_client.models.embed_content(
            model="gemini-embedding-2",
            contents=archivo_subido,
            config=types.EmbedContentConfig(output_dimensionality=768)
        )
        vector = embedding_response.embeddings[0].values

        # 4. Consolidar el contenido combinando la fuente, la etiqueta y la transcripción textual
        contenido_completo = f"[Documento Multimedia/Imagen Fuente: {nombre_original}]\nContenido extraído de la imagen:\n{texto_imagen}"
        
        data = {
            "content": contenido_completo,
            "metadata": {"fuente": nombre_original, "tipo": "imagen"},
            "embedding": vector
        }
        supabase.table("documents").insert(data).execute()
        
        return 1 # Retorna 1 unidad atómica vectorizada

    # CASO B: Documentos de texto tradicionales (PDF, Word, Excel)
    else:
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
            chunk_con_fuente = f"[Documento Fuente: {nombre_original}]\n{chunk}"

            # Vectorización del chunk con gemini-embedding-2
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

# Consulta RAG con inventario global, búsqueda semántica y generación de respuesta
def consultar_rag(pregunta_usuario: str):
    # 1. Obtener el inventario global de archivos únicos directamente de Supabase
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

    # 2. Vectorizar la pregunta del usuario utilizando gemini-embedding-2
    embedding_response = gemini_client.models.embed_content(
        model="gemini-embedding-2",
        contents=pregunta_usuario,
        config=types.EmbedContentConfig(output_dimensionality=768)
    )
    vector_pregunta = embedding_response.embeddings[0].values

    # 3. Búsqueda semántica por similitud vectorial usando la función RPC match_documents en Supabase
    respuesta_db = supabase.rpc(
        "match_documents",
        {
            "query_embedding": vector_pregunta,
            "match_threshold": 0.3, # Umbral optimizado para capturar similitudes semánticas e imágenes
            "match_count": 4
        }
    ).execute()

    fragmentos = respuesta_db.data
    contexto = "\n\n".join([item["content"] for item in fragmentos]) if fragmentos else "No se encontró información relevante."

    # 4. Prompt institucional con restricciones estrictas y contexto enriquecido
    prompt_final = f"""
    Eres un asistente institucional experto. Responde a la pregunta del usuario utilizando EXCLUSIVAMENTE 
    la información provista en el contexto y en el inventario de archivos. Si la respuesta no está allí, di amablemente 
    que no posees esa información. Si te preguntan por archivos disponibles, revisa el inventario. Si te preguntan por datos específicos (como números de RIF, cédulas o nombres), búscalos detalladamente en el contexto.

    [INVENTARIO DE ARCHIVOS DISPONIBLES EN EL SISTEMA]:
    {inventario_archivos}

    [CONTEXTO DE FRAGMENTOS Y REGISTROS RECUPERADOS]:
    {contexto}

    [PREGUNTA]:
    {pregunta_usuario}
    """

    # 5. Generación de la respuesta final con gemini-3.5-flash-lite (baja latencia)
    response = gemini_client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=prompt_final
    )

    return {
        "respuesta": response.text,
        "fuentes": list(set([item["metadata"]["fuente"] for item in fragmentos])) if fragmentos else []
    }

# Función para eliminar todos los registros asociados a un archivo fuente
def eliminar_documento_por_fuente(nombre_archivo: str):
    supabase.table("documents").delete().eq("metadata->>fuente", nombre_archivo).execute()
