import os
import re
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


def extraer_unidades_de_archivo(ruta_archivo: str) -> list[tuple[str | None, str]]:
    """
    Extrae el texto del archivo devolviendo una lista de tuplas (nombre_hoja, texto).
    Para PDF y Word, nombre_hoja es None. Para Excel, devuelve cada hoja por separado con su nombre exacto.
    """
    extension = ruta_archivo.lower().split('.')[-1]

    if extension == "pdf":
        texto_extraido = ""
        reader = pypdf.PdfReader(ruta_archivo)
        for num_pag, pagina in enumerate(reader.pages, start=1):
            contenido_pag = pagina.extract_text() or ""
            if contenido_pag.strip():
                texto_extraido += f"\n--- Página {num_pag} ---\n{contenido_pag}\n"
        return [(None, texto_extraido)]

    elif extension == "docx":
        texto_extraido = ""
        doc = docx.Document(ruta_archivo)
        for para in doc.paragraphs:
            if para.text.strip():
                texto_extraido += para.text + "\n"
        for tabla in doc.tables:
            for fila in tabla.rows:
                celdas = [celda.text.strip() for celda in fila.cells if celda.text.strip()]
                if celdas:
                    texto_extraido += " | ".join(celdas) + "\n"
        return [(None, texto_extraido)]

    elif extension == "xlsx":
        hojas = pd.read_excel(ruta_archivo, sheet_name=None)
        unidades_hojas = []
        for nombre_hoja, df in hojas.items():
            df_limpio = df.dropna(how="all").dropna(axis=1, how="all").fillna("")
            if df_limpio.empty:
                continue
            nombre_hoja_limpio = str(nombre_hoja).strip()
            texto_hoja = f"Hoja de Excel: {nombre_hoja_limpio}\n"
            columnas = [str(c).strip() for c in df_limpio.columns]
            texto_hoja += " | ".join(columnas) + "\n"
            for _, fila in df_limpio.iterrows():
                valores = [str(v).strip() for v in fila.values if str(v).strip() != ""]
                if valores:
                    texto_hoja += " | ".join(valores) + "\n"
            unidades_hojas.append((nombre_hoja_limpio, texto_hoja))
        return unidades_hojas

    else:
        raise ValueError(f"Formato .{extension} no compatible.")


def generar_embeddings_con_reintento(textos: list[str], max_reintentos: int = 4):
    """Genera embeddings respetando el límite de 30,000 tokens/minuto del Free Tier con espera automática."""
    for intento in range(max_reintentos):
        try:
            return gemini_client.models.embed_content(
                model="gemini-embedding-2",
                contents=textos,
                config=types.EmbedContentConfig(output_dimensionality=768)
            )
        except Exception as e:
            mensaje_error = str(e)
            if "429" in mensaje_error or "RESOURCE_EXHAUSTED" in mensaje_error:
                match_segundos = re.search(r"retry in (\d+(?:\.\d+)?)s", mensaje_error, re.IGNORECASE)
                espera = float(match_segundos.group(1)) + 2.0 if match_segundos else 32.0
                if intento < max_reintentos - 1:
                    time.sleep(espera)
                    continue
            raise e


def procesar_y_guardar_archivo(ruta_archivo: str, nombre_original: str) -> int:
    """Procesa documentos o imágenes, guarda nombres de hojas en Excel y vectoriza respetando cuotas."""
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

        embedding_response = generar_embeddings_con_reintento([contenido_completo])
        vector = embedding_response.embeddings[0].values

        data = {
            "content": contenido_completo,
            "metadata": {"fuente": nombre_original, "tipo": "imagen"},
            "embedding": vector
        }
        supabase.table("documents").insert(data).execute()
        return 1

    unidades = extraer_unidades_de_archivo(ruta_archivo)
    items_preparados = []
    indice_global = 0

    for nombre_hoja, texto_unidad in unidades:
        chunks = dividir_texto_recursivo(texto_unidad, tamano_chunk=1000, solapamiento=200)
        for chunk in chunks:
            if nombre_hoja:
                texto_formateado = f"[Documento Fuente: {nombre_original} | Hoja: {nombre_hoja}]\n{chunk}"
                meta = {
                    "fuente": nombre_original,
                    "hoja": nombre_hoja,
                    "fragmento": indice_global,
                    "tipo": "excel"
                }
            else:
                texto_formateado = f"[Documento Fuente: {nombre_original}]\n{chunk}"
                meta = {
                    "fuente": nombre_original,
                    "fragmento": indice_global,
                    "tipo": "texto"
                }
            items_preparados.append((texto_formateado, meta))
            indice_global += 1

    if not items_preparados:
        raise ValueError("El documento está vacío o no contiene texto extraíble.")

    registros_a_insertar = []
    tamano_lote = 15

    for inicio in range(0, len(items_preparados), tamano_lote):
        lote = items_preparados[inicio:inicio + tamano_lote]
        lote_textos = [item[0] for item in lote]
        embedding_response = generar_embeddings_con_reintento(lote_textos)

        for idx, emb_obj in enumerate(embedding_response.embeddings):
            registros_a_insertar.append({
                "content": lote[idx][0],
                "metadata": lote[idx][1],
                "embedding": emb_obj.values
            })

        if inicio + tamano_lote < len(items_preparados):
            time.sleep(2.5)

    for i in range(0, len(registros_a_insertar), 100):
        supabase.table("documents").insert(registros_a_insertar[i:i + 100]).execute()

    return len(registros_a_insertar)


def detectar_archivo_hoja_y_modo_global(
    pregunta: str,
    historial: list[dict],
    inventario: list[str],
    mapa_hojas: dict[str, list[str]]
) -> tuple[bool, str | None, str | None]:
    """Detecta si la consulta pide leer un archivo completo o una hoja específica de un Excel."""
    texto_combinado = pregunta.lower()
    if historial:
        texto_combinado += " " + " ".join(m.get("texto", "").lower() for m in historial[-2:])

    archivo_objetivo = None
    for nombre_archivo in inventario:
        base_sin_ext = os.path.splitext(nombre_archivo)[0].lower()
        if nombre_archivo.lower() in texto_combinado or (len(base_sin_ext) >= 4 and base_sin_ext in texto_combinado):
            archivo_objetivo = nombre_archivo
            break

    # Verificar si el usuario menciona el nombre de alguna hoja de Excel indexada
    hoja_objetivo = None
    for archivo, hojas in mapa_hojas.items():
        for h in hojas:
            if len(h) >= 3 and h.lower() in texto_combinado:
                hoja_objetivo = h
                if not archivo_objetivo:
                    archivo_objetivo = archivo
                break
        if hoja_objetivo:
            break

    palabras_globales = [
        "todo", "todos", "todas", "completo", "completa", "total", "totales",
        "cuantos", "cuántos", "cuantas", "cuántas", "cantidad", "conteo",
        "lista", "listar", "listado", "resumen", "resume", "analiza", "analisis",
        "estadistica", "estadísticas", "grafico", "gráfico", "grafica", "gráfica",
        "consolidado", "exhaustivo", "archivo entero", "base completa", "hoja", "hojas", "pestaña"
    ]
    es_consulta_global = any(palabra in texto_combinado for palabra in palabras_globales) or (hoja_objetivo is not None)

    if es_consulta_global and not archivo_objetivo:
        archivos_excel = [f for f in inventario if f.lower().endswith(".xlsx")]
        if len(archivos_excel) == 1:
            archivo_objetivo = archivos_excel[0]

    return es_consulta_global, archivo_objetivo, hoja_objetivo


def recuperar_documento_completo(nombre_archivo: str, nombre_hoja: str | None = None) -> list[dict]:
    """Recupera todos los fragmentos de un archivo (o de una hoja específica de Excel) ordenados."""
    query = (
        supabase.table("documents")
        .select("content, metadata")
        .eq("metadata->>fuente", nombre_archivo)
    )
    if nombre_hoja:
        query = query.eq("metadata->>hoja", nombre_hoja)

    res = query.limit(1000).execute()
    datos = res.data or []
    datos.sort(key=lambda x: x.get("metadata", {}).get("fragmento", 0))
    for item in datos:
        item["similarity"] = 0.95
    return datos


def consultar_rag(pregunta: str, historial: list[dict] = None) -> dict:
    """Ejecuta búsqueda híbrida o recuperación completa (por archivo o por hoja de Excel) y registra métricas."""
    inicio_tiempo = time.perf_counter()
    historial = historial or []

    try:
        response_meta = supabase.table("documents").select("metadata").execute()
        archivos_unicos = set()
        mapa_hojas_sets: dict[str, set] = {}

        for doc in response_meta.data:
            meta = doc.get("metadata", {})
            fuente = meta.get("fuente")
            if fuente:
                archivos_unicos.add(fuente)
                hoja = meta.get("hoja")
                if hoja:
                    mapa_hojas_sets.setdefault(fuente, set()).add(hoja)

        inventario_archivos = sorted(list(archivos_unicos))
        mapa_hojas = {k: sorted(list(v)) for k, v in mapa_hojas_sets.items()}

        es_global, archivo_objetivo, hoja_objetivo = detectar_archivo_hoja_y_modo_global(
            pregunta, historial, inventario_archivos, mapa_hojas
        )

        fragmentos = []
        modo_recuperacion = "hibrido"

        if es_global and archivo_objetivo:
            fragmentos = recuperar_documento_completo(archivo_objetivo, hoja_objetivo)
            modo_recuperacion = "completo"

        if not fragmentos:
            consulta_busqueda = pregunta
            if historial:
                ultimos_turnos = "\n".join([f"{m.get('rol', 'user')}: {m.get('texto', '')}" for m in historial[-4:]])
                consulta_busqueda = f"{ultimos_turnos}\nPregunta actual: {pregunta}"

            embedding_response = generar_embeddings_con_reintento([consulta_busqueda])
            vector_pregunta = embedding_response.embeddings[0].values

            try:
                respuesta_db = supabase.rpc(
                    "match_documents_hybrid",
                    {
                        "query_text": pregunta,
                        "query_embedding": vector_pregunta,
                        "match_threshold": 0.30,
                        "match_count": 20
                    }
                ).execute()
            except Exception:
                respuesta_db = supabase.rpc(
                    "match_documents",
                    {
                        "query_embedding": vector_pregunta,
                        "match_threshold": 0.30,
                        "match_count": 20
                    }
                ).execute()

            fragmentos = respuesta_db.data or []

        if modo_recuperacion == "completo" and fragmentos:
            partes_limpias = []
            for item in fragmentos:
                c = re.sub(r"^\[Documento Fuente: [^\]]+\]\n?", "", item["content"])
                partes_limpias.append(c)
            etiqueta_hoja = f" | Hoja: {hoja_objetivo}" if hoja_objetivo else ""
            contexto = f"=== CONTENIDO ÍNTEGRO: {archivo_objetivo}{etiqueta_hoja} ({len(fragmentos)} bloques procesados) ===\n" + "\n".join(partes_limpias)
        else:
            contexto = "\n\n".join([item["content"] for item in fragmentos]) if fragmentos else "No se encontró información relevante en los documentos indexados."

        fuentes_usadas = sorted(list({
            f"{item['metadata']['fuente']} (Hoja: {item['metadata']['hoja']})" if item.get("metadata", {}).get("hoja") else item["metadata"]["fuente"]
            for item in fragmentos if "fuente" in item.get("metadata", {})
        }))

        similitudes = [item.get("similarity", 0) for item in fragmentos if item.get("similarity") is not None]
        similitud_promedio = round(sum(similitudes) / len(similitudes), 4) if similitudes else 0.0

        texto_historial = ""
        if historial:
            lineas_hist = [f"- {'Usuario' if m.get('rol') == 'user' else 'Asistente'}: {m.get('texto', '')}" for m in historial[-4:]]
            texto_historial = "[HISTORIAL RECIENTE DE LA CONVERSACIÓN]:\n" + "\n".join(lineas_hist) + "\n\n"

        info_hojas_excel = f"\n[HOJAS POR ARCHIVO EXCEL INDEXADO]:\n{mapa_hojas}\n" if mapa_hojas else ""

        prompt_final = f"""
        Eres el Asistente Corporativo inteligente diseñado exclusivamente para la empresa **Victory Logistic**.
        Analiza el contexto recuperado de los documentos (PDF, Word, Excel e imágenes transcritas) para asistir al equipo de Victory Logistic con precisión operativa y corporativa.

        [INVENTARIO DE ARCHIVOS INDEXADOS]:
        {inventario_archivos}
        {info_hojas_excel}
        {texto_historial}[CONTEXTO RECUPERADO DE LA BASE DE DATOS]:
        {contexto}

        [PREGUNTA ACTUAL DEL USUARIO]:
        {pregunta}

        Reglas estrictas de formato y respuesta:
        1. **Identidad y Procesamiento Completo:** Responde siempre como el asistente oficial de **Victory Logistic**. Cuando recibas el contenido íntegro de un archivo o de una hoja de Excel en el contexto, procésalo en su totalidad para realizar conteos exactos, agrupaciones, totales o análisis completos; nunca digas que tienes un límite técnico ni que estás usando una muestra parcial si los datos están presentes en el contexto.
        2. **Rigor Documental:** Basa tu respuesta ÚNICAMENTE en el contexto recuperado y el inventario. Si un dato específico no aparece en los documentos cargados, indícalo con claridad y cortesía sin inventar información.
        3. **Uso Opcional de Tablas:** NO uses tablas para respuestas simples, definiciones o datos puntuales (como un solo RIF, una dirección o un nombre); en esos casos responde en párrafos claros y directos. Utiliza tablas Markdown **únicamente** cuando el usuario pida una tabla explícitamente o cuando sea necesario comparar o listar múltiples registros.
        4. **Gráficos Estadísticos (Chart.js):** Si el usuario solicita un gráfico (barras, líneas, torta/dona) o si los datos numéricos recuperados se visualizan mejor gráficamente, incluye un bloque de código con la etiqueta `chart` que contenga ÚNICAMENTE un JSON válido compatible con Chart.js:
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
        5. **Diagramas de Flujo o Procesos (Mermaid.js):** Si el usuario solicita un diagrama, flujo de procesos, organigrama o mapa conceptual, incluye un bloque de código con la etiqueta `mermaid` usando sintaxis Mermaid válida (ej. `flowchart TD` o `sequenceDiagram`).
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
