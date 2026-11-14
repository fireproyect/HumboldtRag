import os
import time
import pypdf
import docx
import pandas as pd
from google import genai
from google.genai import types
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))


def dividir_texto_recursivo(texto: str, tamano_chunk: int = 1000, solapamiento: int = 200) -> list[str]:
    """Divide el texto respetando párrafos, saltos de línea y oraciones sin cortar palabras."""
    separadores = ["\n\n", "\n", ". ", " "]
    
    def _split(text: str, seps: list[str]) -> list[str]:
        if len(text) <= tamano_chunk:
            return [text.strip()] if text.strip() else []
        if not seps:
            return [text[i:i + tamano_chunk].strip() for i in range(0, len(text), tamano_chunk - solapamiento)]

        sep = seps[0]
        partes = text.split(sep)
        chunks_resultado = []
        actual = ""

        for parte in partes:
            candidato = f"{actual}{sep}{parte}" if actual else parte
            if len(candidato) <= tamano_chunk:
                actual = candidato
            else:
                if actual:
                    chunks_resultado.append(actual.strip())
                if len(parte) > tamano_chunk:
                    chunks_resultado.extend(_split(parte, seps[1:]))
                    actual = ""
                else:
                    cola_solapada = actual[-solapamiento:] if len(actual) > solapamiento else actual
                    actual = f"{cola_solapada}{sep}{parte}".strip() if cola_solapada else parte

        if actual.strip():
            chunks_resultado.append(actual.strip())
        return chunks_resultado

    return _split(texto, separadores)


def extraer_texto_de_archivo(ruta_archivo: str) -> str:
    """Extrae texto plano de archivos PDF, Word o todas las hojas de Excel."""
    extension = ruta_archivo.lower().split('.')[-1]
    texto_extraido = ""

    if extension == "pdf":
        reader = pypdf.PdfReader(ruta_archivo)
        for num_pag, pagina in enumerate(reader.pages, start=1):
            contenido_pag = pagina.extract_text() or ""
            if contenido_pag.strip():
                texto_extraido += f"\n--- Página {num_pag} ---\n{contenido_pag}\n"
    elif extension == "docx":
        doc = docx.Document(ruta_archivo)
        for para in doc.paragraphs:
            if para.text.strip():
                texto_extraido += para.text + "\n"
        for tabla in doc.tables:
            for fila in tabla.rows:
                celdas = [celda.text.strip() for celda in fila.cells]
                texto_extraido += " | ".join(celdas) + "\n"
    elif extension == "xlsx":
        hojas = pd.read_excel(ruta_archivo, sheet_name=None)
        for nombre_hoja, df in hojas.items():
            texto_extraido += f"\n--- Hoja: {nombre_hoja} ---\n{df.to_string(index=False)}\n"
    else:
        raise ValueError(f"Formato .{extension} no compatible.")

    return texto_extraido


def procesar_y_guardar_archivo(ruta_archivo: str, nombre_original: str) -> int:
    """Procesa documentos o imágenes, genera embeddings en lote y los guarda en Supabase."""
    extension = ruta_archivo.lower().split('.')[-1]

    eliminar_documento_por_fuente(nombre_original)

    if extension in ["png", "jpg", "jpeg"]:
        archivo_subido = gemini_client.files.upload(file=ruta_archivo)
        try:
            analisis_imagen = gemini_client.models.generate_content(
                model="gemini-3.5-flash-lite",
                contents=[
                    archivo_subido,
                    "Extrae y transcribe absolutamente todo el texto visible en esta imagen, incluyendo números de RIF, cédulas, nombres, fechas, montos, direcciones y datos operativos. Si hay tablas o sellos, descríbelos con exactitud."
                ]
            )
            texto_imagen = analisis_imagen.text
        except Exception as e:
            texto_imagen = f"No se pudo extraer texto automático de la imagen ({str(e)})."
        finally:
            try:
                gemini_client.files.delete(name=archivo_subido.name)
            except Exception:
                pass

        contenido_completo = f"[Documento Multimedia/Imagen Fuente: {nombre_original}]\nContenido extraído:\n{texto_imagen}"

        embedding_response = gemini_client.models.embed_content(
            model="gemini-embedding-2",
            contents=contenido_completo,
            config=types.EmbedContentConfig(output_dimensionality=768)
        )
        vector = embedding_response.embeddings[0].values

        data = {
            "content": contenido_completo,
            "metadata": {"fuente": nombre_original, "tipo": "imagen"},
            "embedding": vector
        }
        supabase.table("documents").insert(data).execute()
        return 1

    texto_completo = extraer_texto_de_archivo(ruta_archivo)
    chunks = dividir_texto_recursivo(texto_completo, tamano_chunk=1000, solapamiento=200)

    if not chunks:
        raise ValueError("El documento está vacío o no contiene texto extraíble.")

    textos_con_fuente = [f"[Documento Fuente: {nombre_original}]\n{chunk}" for chunk in chunks]

    registros_a_insertar = []
    tamano_lote = 50

    for inicio in range(0, len(textos_con_fuente), tamano_lote):
        lote_textos = textos_con_fuente[inicio:inicio + tamano_lote]
        embedding_response = gemini_client.models.embed_content(
            model="gemini-embedding-2",
            contents=lote_textos,
            config=types.EmbedContentConfig(output_dimensionality=768)
        )
        for idx, emb_obj in enumerate(embedding_response.embeddings):
            indice_global = inicio + idx
            registros_a_insertar.append({
                "content": lote_textos[idx],
                "metadata": {"fuente": nombre_original, "fragmento": indice_global, "tipo": "texto"},
                "embedding": emb_obj.values
            })

    supabase.table("documents").insert(registros_a_insertar).execute()
    return len(registros_a_insertar)


def consultar_rag(pregunta: str, historial: list[dict] = None) -> dict:
    """Ejecuta búsqueda híbrida (Vectorial + FTS), mantiene contexto de conversación y registra métricas."""
    inicio_tiempo = time.perf_counter()
    historial = historial or []

    try:
        response_meta = supabase.table("documents").select("metadata").execute()
        archivos_unicos = {
            doc.get("metadata", {}).get("fuente")
            for doc in response_meta.data
            if "fuente" in doc.get("metadata", {})
        }
        inventario_archivos = sorted(list(archivos_unicos))

        consulta_busqueda = pregunta
        if historial:
            ultimos_turnos = "\n".join([f"{m.get('rol', 'user')}: {m.get('texto', '')}" for m in historial[-4:]])
            consulta_busqueda = f"{ultimos_turnos}\nPregunta actual: {pregunta}"

        embedding_response = gemini_client.models.embed_content(
            model="gemini-embedding-2",
            contents=consulta_busqueda,
            config=types.EmbedContentConfig(output_dimensionality=768)
        )
        vector_pregunta = embedding_response.embeddings[0].values

        respuesta_db = supabase.rpc(
            "match_documents_hybrid",
            {
                "query_text": pregunta,
                "query_embedding": vector_pregunta,
                "match_threshold": 0.35,
                "match_count": 8
            }
        ).execute()

        fragmentos = respuesta_db.data or []
        contexto = "\n\n".join([item["content"] for item in fragmentos]) if fragmentos else "No se encontró información relevante en los documentos indexados."
        fuentes_usadas = sorted(list({item["metadata"]["fuente"] for item in fragmentos if "fuente" in item.get("metadata", {})}))

        similitudes = [item.get("similarity", 0) for item in fragmentos if item.get("similarity") is not None]
        similitud_promedio = round(sum(similitudes) / len(similitudes), 4) if similitudes else 0.0

        texto_historial = ""
        if historial:
            lineas_hist = [f"- {'Usuario' if m.get('rol') == 'user' else 'Asistente'}: {m.get('texto', '')}" for m in historial[-4:]]
            texto_historial = "[HISTORIAL RECIENTE DE LA CONVERSACIÓN]:\n" + "\n".join(lineas_hist) + "\n\n"

        prompt_final = f"""
        Eres el Asistente Corporativo inteligente diseñado exclusivamente para la empresa **Victory Logistic**.
        Analiza el contexto recuperado de los documentos (PDF, Word, Excel e imágenes transcritas) para asistir al equipo de Victory Logistic con precisión operativa y corporativa.

        [INVENTARIO DE ARCHIVOS INDEXADOS]:
        {inventario_archivos}

        {texto_historial}[CONTEXTO RECUPERADO DE LA BASE DE DATOS]:
        {contexto}

        [PREGUNTA ACTUAL DEL USUARIO]:
        {pregunta}

        Reglas estrictas de formato y respuesta:
        1. **Identidad y Rigor:** Responde siempre como el asistente oficial de **Victory Logistic**. Basa tu respuesta ÚNICAMENTE en el contexto recuperado y el inventario. Si el dato no está en los documentos cargados, indícalo con claridad y cortesía sin inventar información.
        2. **Uso Opcional de Tablas:** NO uses tablas para respuestas simples, definiciones o datos puntuales (como un solo RIF, una dirección o un nombre); en esos casos responde en párrafos claros y directos. Utiliza tablas Markdown **únicamente** cuando el usuario pida una tabla explícitamente o cuando sea necesario comparar múltiples registros, ítems o columnas de datos.
        3. **Gráficos Estadísticos (Chart.js):** Si el usuario solicita un gráfico (barras, líneas, torta/dona) o si los datos numéricos recuperados se visualizan mejor gráficamente, incluye un bloque de código con la etiqueta `chart` que contenga ÚNICAMENTE un JSON válido compatible con Chart.js:
           ```chart
           {{
             "type": "bar",
             "data": {{
               "labels": ["Etiqueta 1", "Etiqueta 2"],
               "datasets": [{{ "label": "Serie", "data": [10, 20] }}]
             }}
           }}
           ```
           (Tipos válidos: "bar", "line", "pie", "doughnut". No agregues comentarios dentro del JSON).
        4. **Diagramas de Flujo o Procesos (Mermaid.js):** Si el usuario solicita un diagrama, flujo de procesos, organigrama o mapa conceptual, incluye un bloque de código con la etiqueta `mermaid` usando sintaxis Mermaid válida (ej. `flowchart TD` o `sequenceDiagram`).
        """

        response = gemini_client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=prompt_final
        )

        latencia_ms = int((time.perf_counter() - inicio_tiempo) * 1000)

        metric_id = None
        try:
            res_metric = supabase.table("rag_metrics").insert({
                "pregunta": pregunta,
                "latencia_ms": latencia_ms,
                "similitud_promedio": similitud_promedio,
                "fuentes_usadas": fuentes_usadas
            }).execute()
            if res_metric.data:
                metric_id = res_metric.data[0]["id"]
        except Exception:
            pass

        return {
            "respuesta": response.text,
            "fuentes": fuentes_usadas,
            "latencia_ms": latencia_ms,
            "similitud_promedio": similitud_promedio,
            "metric_id": metric_id
        }
    except Exception as e:
        raise Exception(f"Error en el servicio RAG: {str(e)}")


def eliminar_documento_por_fuente(nombre_archivo: str):
    """Elimina todos los registros vectoriales asociados a un archivo."""
    try:
        supabase.table("documents").delete().eq("metadata->>fuente", nombre_archivo).execute()
    except Exception as e:
        raise Exception(f"Error al eliminar el documento en Supabase: {str(e)}")


def registrar_feedback_metrica(metric_id: int, feedback: int):
    """Actualiza la calificación (1 = útil, -1 = no útil) de una consulta registrada."""
    supabase.table("rag_metrics").update({"feedback": feedback}).eq("id", metric_id).execute()


def obtener_estadisticas_tesis() -> dict:
    """Calcula KPIs de rendimiento y precisión para el panel de administrador."""
    res_docs = supabase.table("documents").select("metadata").execute()
    documentos = res_docs.data or []
    archivos_unicos = sorted(list({
        doc.get("metadata", {}).get("fuente")
        for doc in documentos
        if "fuente" in doc.get("metadata", {})
    }))

    res_metrics = supabase.table("rag_metrics").select("*").order("created_at", desc=True).limit(100).execute()
    metricas = res_metrics.data or []

    total_consultas = len(metricas)
    latencia_media = round(sum(m["latencia_ms"] for m in metricas) / total_consultas) if total_consultas else 0
    similitud_media = round((sum(m["similitud_promedio"] for m in metricas) / total_consultas) * 100, 1) if total_consultas else 0.0

    calificadas = [m for m in metricas if m["feedback"] != 0]
    positivas = sum(1 for m in calificadas if m["feedback"] == 1)
    tasa_satisfaccion = round((positivas / len(calificadas)) * 100, 1) if calificadas else None

    return {
        "total_registros_vectoriales": len(documentos),
        "total_archivos_subidos": len(archivos_unicos),
        "archivos": archivos_unicos,
        "kpis": {
            "total_consultas": total_consultas,
            "latencia_promedio_ms": latencia_media,
            "similitud_promedio_pct": similitud_media,
            "tasa_satisfaccion_pct": tasa_satisfaccion,
            "total_calificadas": len(calificadas)
        },
        "ultimas_consultas": metricas[:10]
    }
