import os
import re
import time
import hashlib
import secrets
from concurrent.futures import ThreadPoolExecutor
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

DEPARTAMENTOS_VALIDOS = [
    "Dispatch",
    "Managment",
    "Track and Trace",
    "ELD",
    "Maintenance",
    "Human Resources",
    "Recruitment",
    "Accounting",
    "General"
]

_CACHE_INVENTARIO = {
    "timestamp": 0.0,
    "datos": []
}
CACHE_TTL_SEGUNDOS = 120.0


def invalidar_cache_inventario():
    _CACHE_INVENTARIO["timestamp"] = 0.0


def obtener_metadatos_cacheados() -> list[dict]:
    ahora = time.time()
    if ahora - _CACHE_INVENTARIO["timestamp"] < CACHE_TTL_SEGUNDOS and _CACHE_INVENTARIO["datos"]:
        return _CACHE_INVENTARIO["datos"]

    res = supabase.table("documents").select("metadata").execute()
    _CACHE_INVENTARIO["datos"] = res.data or []
    _CACHE_INVENTARIO["timestamp"] = ahora
    return _CACHE_INVENTARIO["datos"]


# =========================================================
# GESTIÓN DE USUARIOS Y SEGURIDAD DE CONTRASEÑAS
# =========================================================

def hashear_password(password: str) -> str:
    salt = secrets.token_hex(16)
    pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000).hex()
    return f"{salt}${pwd_hash}"


def verificar_password(password: str, stored_hash: str) -> bool:
    try:
        salt, original_hash = stored_hash.split("$", 1)
        check_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000).hex()
        return secrets.compare_digest(check_hash, original_hash)
    except Exception:
        return False


def registrar_usuario(nombre: str, username: str, password: str, departamento: str) -> dict:
    username_limpio = username.strip().lower()
    if departamento not in DEPARTAMENTOS_VALIDOS or departamento == "General":
        raise ValueError("Departamento no válido.")
    if len(password) < 4:
        raise ValueError("La contraseña debe tener al menos 4 caracteres.")

    existente = supabase.table("usuarios").select("id").eq("username", username_limpio).execute()
    if existente.data:
        raise ValueError("El nombre de usuario o correo ya está registrado.")

    res = supabase.table("usuarios").insert({
        "nombre": nombre.strip(),
        "username": username_limpio,
        "password_hash": hashear_password(password),
        "rol": "usuario",
        "departamento": departamento
    }).execute()

    usuario = res.data[0]
    return {
        "id": usuario["id"],
        "nombre": usuario["nombre"],
        "username": usuario["username"],
        "rol": usuario["rol"],
        "departamento": usuario["departamento"]
    }


def autenticar_usuario(username: str, password: str) -> dict:
    username_limpio = username.strip().lower()
    admin_secret = os.getenv("ADMIN_SECRET_KEY", "")

    if username_limpio == "admin" and admin_secret and secrets.compare_digest(password, admin_secret):
        return {
            "id": 0,
            "nombre": "Administrador Victory Logistic",
            "username": "admin",
            "rol": "admin",
            "departamento": "Todos"
        }

    res = supabase.table("usuarios").select("*").eq("username", username_limpio).execute()
    if not res.data:
        raise ValueError("Usuario o contraseña incorrectos.")

    user_db = res.data[0]
    if not verificar_password(password, user_db["password_hash"]):
        raise ValueError("Usuario o contraseña incorrectos.")

    return {
        "id": user_db["id"],
        "nombre": user_db["nombre"],
        "username": user_db["username"],
        "rol": user_db["rol"],
        "departamento": user_db["departamento"]
    }


def listar_usuarios() -> list[dict]:
    res = supabase.table("usuarios").select("id, created_at, nombre, username, rol, departamento").order("created_at", desc=True).execute()
    return res.data or []


def actualizar_usuario_admin(user_id: int, rol: str = None, departamento: str = None):
    updates = {}
    if rol in ("admin", "usuario"):
        updates["rol"] = rol
    if departamento in DEPARTAMENTOS_VALIDOS:
        updates["departamento"] = departamento
    if updates:
        supabase.table("usuarios").update(updates).eq("id", user_id).execute()


def eliminar_usuario_admin(user_id: int):
    supabase.table("usuarios").delete().eq("id", user_id).execute()


# =========================================================
# GENERACIÓN RÁPIDA CON FALLBACK INMEDIATO (SIN ESPERAS MUERTAS)
# =========================================================

def generar_respuesta_con_reintento(contents):
    """Usa gemini-2.5-flash-lite como modelo principal de baja latencia con fallback inmediato."""
    modelos = ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-3.5-flash-lite"]
    ultimo_error = None

    for modelo_actual in modelos:
        try:
            return gemini_client.models.generate_content(
                model=modelo_actual,
                contents=contents
            )
        except Exception as e:
            ultimo_error = e
            msg = str(e)
            if any(code in msg for code in ["503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "overloaded", "404"]):
                continue
            raise e
    raise ultimo_error


def dividir_texto_recursivo(texto: str, tamano_chunk: int = 1000, solapamiento: int = 200) -> list[str]:
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


def generar_embeddings_con_reintento(textos: list[str], max_reintentos: int = 3):
    for intento in range(max_reintentos):
        try:
            return gemini_client.models.embed_content(
                model="gemini-embedding-2",
                contents=textos,
                config=types.EmbedContentConfig(output_dimensionality=768)
            )
        except Exception as e:
            mensaje_error = str(e)
            if any(code in mensaje_error for code in ["429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE"]):
                match_segundos = re.search(r"retry in (\d+(?:\.\d+)?)s", mensaje_error, re.IGNORECASE)
                espera = float(match_segundos.group(1)) + 2.0 if match_segundos else 10.0
                if intento < max_reintentos - 1:
                    time.sleep(espera)
                    continue
            raise e


def procesar_y_guardar_archivo(ruta_archivo: str, nombre_original: str, departamento: str = "General") -> int:
    if departamento not in DEPARTAMENTOS_VALIDOS:
        raise ValueError(f"Departamento '{departamento}' no reconocido.")

    extension = ruta_archivo.lower().split('.')[-1]
    eliminar_documento_por_fuente(nombre_original)

    if extension in ["png", "jpg", "jpeg"]:
        archivo_subido = gemini_client.files.upload(file=ruta_archivo)
        try:
            analisis_imagen = generar_respuesta_con_reintento([
                archivo_subido,
                "Extrae y transcribe absolutamente todo el texto visible en esta imagen, incluyendo números de RIF, cédulas, nombres, fechas, montos, direcciones y datos operativos. Si hay tablas o sellos, descríbelos con exactitud."
            ])
            texto_imagen = analisis_imagen.text
        except Exception as e:
            texto_imagen = f"No se pudo extraer texto automático de la imagen ({str(e)})."
        finally:
            try:
                gemini_client.files.delete(name=archivo_subido.name)
            except Exception:
                pass

        contenido_completo = f"[Departamento: {departamento} | Imagen Fuente: {nombre_original}]\nContenido extraído:\n{texto_imagen}"

        embedding_response = generar_embeddings_con_reintento([contenido_completo])
        vector = embedding_response.embeddings[0].values

        data = {
            "content": contenido_completo,
            "metadata": {
                "fuente": nombre_original,
                "departamento": departamento,
                "tipo": "imagen"
            },
            "embedding": vector
        }
        supabase.table("documents").insert(data).execute()
        invalidar_cache_inventario()
        return 1

    unidades = extraer_unidades_de_archivo(ruta_archivo)
    items_preparados = []
    indice_global = 0

    for nombre_hoja, texto_unidad in unidades:
        chunks = dividir_texto_recursivo(texto_unidad, tamano_chunk=1000, solapamiento=200)
        for chunk in chunks:
            if nombre_hoja:
                texto_formateado = f"[Departamento: {departamento} | Documento: {nombre_original} | Hoja: {nombre_hoja}]\n{chunk}"
                meta = {
                    "fuente": nombre_original,
                    "departamento": departamento,
                    "hoja": nombre_hoja,
                    "fragmento": indice_global,
                    "tipo": "excel"
                }
            else:
                texto_formateado = f"[Departamento: {departamento} | Documento: {nombre_original}]\n{chunk}"
                meta = {
                    "fuente": nombre_original,
                    "departamento": departamento,
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

    invalidar_cache_inventario()
    return len(registros_a_insertar)


# =========================================================
# CONSULTA RAG DE ALTA VELOCIDAD
# =========================================================

def es_interaccion_casual(pregunta: str) -> str | None:
    limpia = re.sub(r"[¿?¡!.,;:]+", "", pregunta.strip().lower())
    palabras = limpia.split()
    if len(palabras) > 6:
        return None

    saludos = {
        "hola", "buenos dias", "buenos días", "buenas tardes", "buenas noches",
        "saludos", "hey", "que tal", "qué tal", "como estas", "cómo estás",
        "todo bien", "todo bien y tu", "todo bien y tú", "hola como estas", "hola qué tal"
    }
    agradecimientos = {
        "gracias", "muchas gracias", "excelente", "perfecto", "genial", "ok", "vale", "listo", "entendido"
    }
    despedidas = {
        "adios", "adiós", "hasta luego", "nos vemos", "chao", "bye"
    }

    if limpia in saludos:
        return "¡Todo excelente por aquí! Como asistente oficial de **Victory Logistic**, estoy listo para ayudarte con consultas operativas, reportes o información de tu departamento. ¿Qué necesitas revisar?"
    if limpia in agradecimientos:
        return "¡Con mucho gusto! Si necesitas consultar otro documento, generar un gráfico o revisar algún registro de **Victory Logistic**, aquí estoy."
    if limpia in despedidas:
        return "¡Hasta luego! Que tengas una excelente jornada en **Victory Logistic**."
    return None


def detectar_archivo_hoja_y_modo_global(
    pregunta: str,
    inventario: list[str],
    mapa_hojas: dict[str, list[str]]
) -> tuple[bool, str | None, str | None]:
    texto = pregunta.lower()

    archivo_objetivo = None
    for nombre_archivo in inventario:
        base_sin_ext = os.path.splitext(nombre_archivo)[0].lower()
        if nombre_archivo.lower() in texto or (len(base_sin_ext) >= 4 and base_sin_ext in texto):
            archivo_objetivo = nombre_archivo
            break

    hoja_objetivo = None
    for archivo, hojas in mapa_hojas.items():
        for h in hojas:
            if len(h) >= 3 and h.lower() in texto:
                hoja_objetivo = h
                if not archivo_objetivo:
                    archivo_objetivo = archivo
                break
        if hoja_objetivo:
            break

    frases_globales = [
        "archivo completo", "excel completo", "documento completo", "base completa",
        "todos los registros", "todas las filas", "todos los datos", "analiza todo",
        "resumen completo", "reporte consolidado", "sin muestra parcial", "hoja completa"
    ]
    es_consulta_global = any(frase in texto for frase in frases_globales) or (
        hoja_objetivo is not None and any(w in texto for w in ["todo", "todos", "cuántos", "cuantos", "analiza", "gráfico", "grafico"])
    )

    if es_consulta_global and not archivo_objetivo:
        archivos_excel = [f for f in inventario if f.lower().endswith(".xlsx")]
        if len(archivos_excel) == 1:
            archivo_objetivo = archivos_excel[0]

    return es_consulta_global, archivo_objetivo, hoja_objetivo


def recuperar_documento_completo(
    nombre_archivo: str,
    nombre_hoja: str | None = None,
    departamento_filtro: str | None = None
) -> list[dict]:
    query = (
        supabase.table("documents")
        .select("content, metadata")
        .eq("metadata->>fuente", nombre_archivo)
    )
    if nombre_hoja:
        query = query.eq("metadata->>hoja", nombre_hoja)

    res = query.limit(1000).execute()
    datos = res.data or []

    if departamento_filtro:
        datos = [
            d for d in datos
            if d.get("metadata", {}).get("departamento") in (departamento_filtro, "General")
        ]

    datos.sort(key=lambda x: x.get("metadata", {}).get("fragmento", 0))
    for item in datos:
        item["similarity"] = 0.95
    return datos


def _crear_registro_metrica_inicial(pregunta: str, ambito: str) -> int | None:
    try:
        res = supabase.table("rag_metrics").insert({
            "pregunta": pregunta,
            "latencia_ms": 0,
            "similitud_promedio": 0.0,
            "fuentes_usadas": [],
            "departamento": ambito
        }).execute()
        if res.data:
            return res.data[0]["id"]
    except Exception:
        pass
    return None


def _actualizar_metrica_final(metric_id: int | None, latencia_ms: int, similitud_promedio: float, fuentes_usadas: list[str]):
    if not metric_id:
        return
    try:
        supabase.table("rag_metrics").update({
            "latencia_ms": latencia_ms,
            "similitud_promedio": similitud_promedio,
            "fuentes_usadas": fuentes_usadas
        }).eq("id", metric_id).execute()
    except Exception:
        pass


def consultar_rag(pregunta: str, historial: list[dict] = None, usuario_actual: dict = None, filtro_admin_depto: str = None) -> dict:
    inicio_tiempo = time.perf_counter()
    historial = historial or []
    usuario_actual = usuario_actual or {"rol": "usuario", "departamento": "General"}

    respuesta_casual = es_interaccion_casual(pregunta)
    if respuesta_casual:
        latencia_ms = max(1, int((time.perf_counter() - inicio_tiempo) * 1000))
        return {
            "respuesta": respuesta_casual,
            "fuentes": [],
            "latencia_ms": latencia_ms,
            "similitud_promedio": 1.0,
            "metric_id": None
        }

    es_admin = usuario_actual.get("rol") == "admin"
    if es_admin:
        depto_efectivo = filtro_admin_depto if (filtro_admin_depto and filtro_admin_depto != "Todos") else None
    else:
        depto_efectivo = usuario_actual.get("departamento")

    ambito_usuario = depto_efectivo if depto_efectivo else "Todos los departamentos (Admin)"

    try:
        metadata_rows = obtener_metadatos_cacheados()

        archivos_unicos = set()
        mapa_hojas_sets: dict[str, set] = {}

        for doc in metadata_rows:
            meta = doc.get("metadata", {})
            depto_doc = meta.get("departamento", "General")
            if depto_efectivo and depto_doc not in (depto_efectivo, "General"):
                continue

            fuente = meta.get("fuente")
            if fuente:
                archivos_unicos.add(fuente)
                hoja = meta.get("hoja")
                if hoja:
                    mapa_hojas_sets.setdefault(fuente, set()).add(hoja)

        inventario_archivos = sorted(list(archivos_unicos))
        mapa_hojas = {k: sorted(list(v)) for k, v in mapa_hojas_sets.items()}

        es_global, archivo_objetivo, hoja_objetivo = detectar_archivo_hoja_y_modo_global(
            pregunta, inventario_archivos, mapa_hojas
        )

        fragmentos = []
        modo_recuperacion = "hibrido"

        if es_global and archivo_objetivo:
            fragmentos = recuperar_documento_completo(archivo_objetivo, hoja_objetivo, depto_efectivo)
            modo_recuperacion = "completo"

        if not fragmentos:
            consulta_busqueda = pregunta
            if historial:
                ultimos_turnos = "\n".join([f"{m.get('rol', 'user')}: {m.get('texto', '')}" for m in historial[-2:]])
                consulta_busqueda = f"{ultimos_turnos}\nPregunta actual: {pregunta}"

            embedding_response = generar_embeddings_con_reintento([consulta_busqueda])
            vector_pregunta = embedding_response.embeddings[0].values

            respuesta_db = supabase.rpc(
                "match_documents_hybrid",
                {
                    "query_text": pregunta,
                    "query_embedding": vector_pregunta,
                    "filter_departamento": depto_efectivo,
                    "match_threshold": 0.30,
                    "match_count": 6
                }
            ).execute()

            fragmentos = respuesta_db.data or []

        if modo_recuperacion == "completo" and fragmentos:
            partes_limpias = []
            for item in fragmentos:
                c = re.sub(r"^\[Departamento: [^\]]+\]\n?", "", item["content"])
                partes_limpias.append(c)
            etiqueta_hoja = f" | Hoja: {hoja_objetivo}" if hoja_objetivo else ""
            contexto = f"=== CONTENIDO ÍNTEGRO: {archivo_objetivo}{etiqueta_hoja} ({len(fragmentos)} bloques) ===\n" + "\n".join(partes_limpias)
        else:
            contexto = "\n\n".join([item["content"] for item in fragmentos]) if fragmentos else "Sin coincidencias en los documentos del departamento."

        fuentes_usadas = sorted(list({
            f"{item['metadata']['fuente']} (Hoja: {item['metadata']['hoja']})" if item.get("metadata", {}).get("hoja") else item["metadata"]["fuente"]
            for item in fragmentos if "fuente" in item.get("metadata", {})
        }))

        similitudes = [item.get("similarity", 0) for item in fragmentos if item.get("similarity") is not None]
        similitud_promedio = round(sum(similitudes) / len(similitudes), 4) if similitudes else 0.0

        texto_historial = ""
        if historial:
            lineas_hist = [f"- {'Usuario' if m.get('rol') == 'user' else 'Asistente'}: {m.get('texto', '')}" for m in historial[-2:]]
            texto_historial = "[HISTORIAL]:\n" + "\n".join(lineas_hist) + "\n\n"

        info_hojas_excel = f" | Hojas Excel: {mapa_hojas}" if mapa_hojas else ""

        prompt_final = f"""
        Eres el Asistente Corporativo oficial de **Victory Logistic**.
        Usuario: **{usuario_actual.get('nombre')}** (Ámbito autorizado: **{ambito_usuario}**).
        Archivos accesibles: {inventario_archivos}{info_hojas_excel}
        {texto_historial}[CONTEXTO]:
        {contexto}

        [PREGUNTA]: {pregunta}

        Reglas:
        1. Responde solo con datos de **{ambito_usuario}**. Si no está en el contexto, dilo con cortesía.
        2. Si piden analizar todo un archivo grande, indica escribir *"analiza el archivo completo [nombre]"*.
        3. No uses tablas para respuestas simples o datos puntuales; úsalas solo para comparar o listar múltiples registros.
        4. Si piden gráfico, usa bloque `chart` con JSON válido de Chart.js; si piden diagrama, bloque `mermaid`.
        """

        # Ejecutar generación del LLM y creación del metric_id en paralelo para no sumar latencia
        with ThreadPoolExecutor(max_workers=2) as executor:
            futuro_llm = executor.submit(generar_respuesta_con_reintento, prompt_final)
            futuro_metric = executor.submit(_crear_registro_metrica_inicial, pregunta, ambito_usuario)

            response = futuro_llm.result()
            metric_id = futuro_metric.result()

        latencia_ms = int((time.perf_counter() - inicio_tiempo) * 1000)

        # Actualizar latencia real y fuentes en segundo plano
        executor_bg = ThreadPoolExecutor(max_workers=1)
        executor_bg.submit(_actualizar_metrica_final, metric_id, latencia_ms, similitud_promedio, fuentes_usadas)
        executor_bg.shutdown(wait=False)

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
    try:
        supabase.table("documents").delete().eq("metadata->>fuente", nombre_archivo).execute()
        invalidar_cache_inventario()
    except Exception as e:
        raise Exception(f"Error al eliminar el documento en Supabase: {str(e)}")


def registrar_feedback_metrica(metric_id: int, feedback: int):
    supabase.table("rag_metrics").update({"feedback": feedback}).eq("id", metric_id).execute()


def obtener_estadisticas_tesis() -> dict:
    documentos = obtener_metadatos_cacheados()

    mapa_archivos_depto: dict[str, str] = {}
    for doc in documentos:
        meta = doc.get("metadata", {})
        fuente = meta.get("fuente")
        if fuente:
            mapa_archivos_depto[fuente] = meta.get("departamento", "General")

    archivos_detalle = [
        {"nombre": nombre, "departamento": depto}
        for nombre, depto in sorted(mapa_archivos_depto.items())
    ]

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
        "total_archivos_subidos": len(archivos_detalle),
        "archivos": archivos_detalle,
        "kpis": {
            "total_consultas": total_consultas,
            "latencia_promedio_ms": latencia_media,
            "similitud_promedio_pct": similitud_media,
            "tasa_satisfaccion_pct": tasa_satisfaccion,
            "total_calificadas": len(calificadas)
        },
        "ultimas_consultas": metricas[:10]
    }
