import os
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

def extraer_texto_de_archivo(ruta_archivo: str) -> str:
    """Extrae texto plano de archivos PDF, Word o Excel."""
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

def procesar_y_guardar_archivo(ruta_archivo: str, nombre_original: str):
    """Procesa documentos de texto o imágenes multimodales, generando embeddings y guardando en Supabase."""
    extension = ruta_archivo.lower().split('.')[-1]
    
    # 1. Procesamiento para Imágenes (Multimodal con OCR automático)
    if extension in ["png", "jpg", "jpeg"]:
        archivo_subido = gemini_client.files.upload(file=ruta_archivo)
        try:
            analisis_imagen = gemini_client.models.generate_content(
                model="gemini-3.5-flash-lite",
                contents=[
                    archivo_subido, 
                    "Extrae y transcribe absolutamente todo el texto visible en esta imagen, incluyendo números de RIF, cédulas, nombres, direcciones y datos institucionales."
                ]
            )
            texto_imagen = analisis_imagen.text
        except Exception:
            texto_imagen = "No se pudo extraer texto automático de la imagen."

        # Generar embedding vectorial de 768 dimensiones para la imagen
        embedding_response = gemini_client.models.embed_content(
            model="gemini-embedding-2",
            contents=archivo_subido,
            config=types.EmbedContentConfig(output_dimensionality=768)
        )
        vector = embedding_response.embeddings[0].values

        contenido_completo = f"[Documento Multimedia/Imagen Fuente: {nombre_original}]\nContenido extraído:\n{texto_imagen}"
        
        data = {
            "content": contenido_completo,
            "metadata": {"fuente": nombre_original, "tipo": "imagen"},
            "embedding": vector
        }
        supabase.table("documents").insert(data).execute()
        return 1

    # 2. Procesamiento para Documentos de Texto (PDF, Word, Excel)
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

def consultar_rag(pregunta: str) -> dict:
    """Realiza la recuperación vectorial en Supabase y genera la respuesta contextual con Gemini."""
    try:
        # Obtener inventario general de archivos disponibles en la base de conocimiento
        response_meta = supabase.table("documents").select("metadata").execute()
        archivos_unicos = set()
        for doc in response_meta.data:
            meta = doc.get("metadata", {})
            if "fuente" in meta:
                archivos_unicos.add(meta["fuente"])
        inventario_archivos = list(archivos_unicos)

        # Generar vector de la pregunta del usuario
        embedding_response = gemini_client.models.embed_content(
            model="gemini-embedding-2",
            contents=pregunta,
            config=types.EmbedContentConfig(output_dimensionality=768)
        )
        vector_pregunta = embedding_response.embeddings[0].values

        # Búsqueda por similitud en Supabase (pgvector) optimizada con umbral flexible
        respuesta_db = supabase.rpc(
            "match_documents",
            {
                "query_embedding": vector_pregunta,
                "match_threshold": 0.2,  # Umbral flexible para garantizar captura de imágenes y textos afines
                "match_count": 6         # Cantidad de fragmentos recuperados simultáneamente
            }
        ).execute()

        fragmentos = respuesta_db.data
        contexto = "\n\n".join([item["content"] for item in fragmentos]) if fragmentos else "No se encontró información relevante."

        # Construcción del prompt institucional estructurado
        prompt_final = f"""
        Eres un asistente institucional experto para el Trabajo de Grado de la Universidad Alejandro de Humboldt. 
        Responde utilizando EXCLUSIVAMENTE la información del contexto y el inventario proporcionados. 
        Si la información no se encuentra allí, indícalo amablemente.

        [INVENTARIO DE ARCHIVOS DISPONIBLES]:
        {inventario_archivos}

        [CONTEXTO RECUPERADO]:
        {contexto}

        [PREGUNTA DEL USUARIO]:
        {pregunta}
        """

        response = gemini_client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=prompt_final
        )

        return {
            "respuesta": response.text,
            "fuentes": list(set([item["metadata"]["fuente"] for item in fragmentos])) if fragmentos else []
        }
    except Exception as e:
        raise Exception(f"Error en el servicio RAG: {str(e)}")

def eliminar_documento_por_fuente(nombre_archivo: str):
    """Elimina todos los registros vectoriales asociados a un nombre de archivo específico."""
    try:
        supabase.table("documents").delete().eq("metadata->>fuente", nombre_archivo).execute()
    except Exception as e:
        raise Exception(f"Error al eliminar el documento en Supabase: {str(e)}")
