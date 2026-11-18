import os
import re
import time
import hashlib
import secrets
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


# =========================================================
# GESTIÓN DE USUARIOS Y SEGURIDAD
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

    u = res.data[0]
    return {"id": u["id"], "nombre": u["nombre"], "username": u["username"], "rol": u["rol"], "departamento": u["departamento"]}


def autenticar_usuario(username: str, password: str) -> dict:
    username_limpio = username.strip().lower()
    admin_secret = os.getenv("ADMIN_SECRET_KEY", "")

    if username_limpio == "admin" and admin_secret and secrets.compare_digest(password, admin_secret):
        return {"id": 0, "nombre": "Administrador Victory Logistic", "username": "admin", "rol": "admin", "departamento": "Todos"}

    res = supabase.table("usuarios").select("*").eq("username", username_limpio).execute()
    if not res.data or not verificar_password(password, res.data[0]["password_hash"]):
        raise ValueError("Usuario o contraseña incorrectos.")

    u = res.data[0]
    return {"id": u["id"], "nombre": u["nombre"], "username": u["username"], "rol": u["rol"], "departamento": u["departamento"]}


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
# GENERACIÓN CON GEMINI 3.5 FLASH LITE (BAJA LATENCIA)
# =========================================================

def generar_respuesta_rapida(contents):
    """Usa gemini-3.5-flash-lite sin thinking overhead y con fallback inmediato ante 503."""
    modelos = ["gemini-3.5-flash-lite", "gemini-2.5-flash-lite"]
    ultimo_error = None

    for modelo in modelos:
        try:
            return gemini_client.models.generate_content(
                model=modelo,
                contents=contents,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    thinking_config=types.ThinkingConfig(thinking_budget=0)
                )
            )
        except Exception as e:
            ultimo_error = e
            if any(c in str(e) for c in ["503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "overloaded"]):
                continue
            # Si el modelo no acepta thinking_config, reintentar sin esa config
            try:
                return gemini_client.models.generate_content(model=modelo, contents=contents)
            except Exception as e2:
                ultimo_error = e2
                continue
    raise ultimo_error


# =========================================================
# INGESTA Y PROCESAMIENTO DOCUMENTAL
# =========================================================

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
            analisis_imagen = generar_respuesta_rapida([
                archivo_subido,
                "Extrae y transcribe todo el texto visible en esta imagen, incluyendo números, nombres, fechas, montos y tablas."
            ])
            texto_imagen = analisis_imagen.text
        except Exception as e:
            texto_imagen = f"No se pudo extraer texto de la imagen ({str(e)})."
        finally:
            try:
                gemini_client.files.delete(name=archivo_subido.name)
            except Exception:
                pass

        contenido_completo = f"[Departamento: {departamento} | Imagen: {nombre_original}]\n{texto_imagen}"
        vector = generar_embeddings_con_reintento([contenido_completo]).embeddings[0].values

        supabase.table("documents").insert({
            "content": contenido_completo,
            "metadata": {"fuente": nombre_original, "departamento": departamento, "tipo": "imagen"},
            "embedding": vector
        }).execute()
        return 1

    unidades = extraer_unidades_de_archivo(ruta_archivo)
    items_preparados = []
    indice_global = 0

    for nombre_hoja, texto_unidad in unidades:
        chunks = dividir_texto_recursivo(texto_unidad, tamano_chunk=1000, solapamiento=200)
        for chunk in chunks:
            if nombre_hoja:
                texto_formateado = f"[Departamento: {departamento} | Archivo: {nombre_original} | Hoja: {nombre_hoja}]\n{chunk}"
                meta = {"fuente": nombre_original, "departamento": departamento, "hoja": nombre_hoja, "fragmento": indice_global, "tipo": "excel"}
            else:
                texto_formateado = f"[Departamento: {departamento} | Archivo: {nombre_original}]\n{chunk}"
                meta = {"fuente": nombre_original, "departamento": departamento, "fragmento": indice_global, "tipo": "texto"}
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


# =========================================================
# CONSULTA RAG DIRECTA (OBJETIVO: 1 A 2 SEGUNDOS)
# =========================================================

def es_interaccion_casual(pregunta: str) -> str | None:
    limpia = re.sub(r"[¿?¡!.,;:]+", "", pregunta.strip().lower())
    if len(limpia.split()) > 6:
        return None

    saludos = {
        "hola", "buenos dias", "buenos días", "buenas tardes", "buenas noches",
        "saludos", "hey", "que tal", "qué tal", "como estas", "cómo estás",
        "todo bien", "todo bien y tu", "todo bien y tú", "hola como estas", "hola qué tal"
    }
    agradecimientos = {"gracias", "muchas gracias", "excelente", "perfecto", "genial", "ok", "vale", "listo"}
    despedidas = {"adios", "adiós", "hasta luego", "nos vemos", "chao", "bye"}

    if limpia in saludos:
        return "¡Todo excelente por aquí! Como asistente de **Victory Logistic**, estoy listo para ayudarte con consultas de tu departamento. ¿Qué necesitas revisar?"
    if limpia in agradecimientos:
        return "¡Con mucho gusto! Aquí estoy si necesitas consultar otro dato de **Victory Logistic**."
    if limpia in despedidas:
        return "¡Hasta luego! Que tengas una excelente jornada en **Victory Logistic**."
    return None


def registrar_metrica_en_segundo_plano(pregunta: str, latencia_ms: int, similitud_promedio: float, fuentes_usadas: list[str], ambito: str):
    try:
        supabase.table("rag_metrics").insert({
            "pregunta": pregunta,
            "latencia_ms": latencia_ms,
            "similitud_promedio": similitud_promedio,
            "fuentes_usadas": fuentes_usadas,
            "departamento": ambito
        }).execute()
    except Exception:
        pass


def consultar_rag(pregunta: str, historial: list[dict] = None, usuario_actual: dict = None, filtro_admin_depto: str = None) -> dict:
    """Flujo directo de 3 pasos: 1) Embedding pregunta -> 2) RPC Supabase (top 5) -> 3) Gemini 3.5 Flash-Lite."""
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
            "ambito": "Casual"
        }

    es_admin = usuario_actual.get("rol") == "admin"
    if es_admin:
        depto_efectivo = filtro_admin_depto if (filtro_admin_depto and filtro_admin_depto != "Todos") else None
    else:
        depto_efectivo = usuario_actual.get("departamento")

    ambito_usuario = depto_efectivo if depto_efectivo else "Todos los departamentos"

    try:
        # Paso 1: Vectorizar únicamente la pregunta actual (más rápido y preciso)
        embedding_response = gemini_client.models.embed_content(
            model="gemini-embedding-2",
            contents=pregunta,
            config=types.EmbedContentConfig(output_dimensionality=768)
        )
        vector_pregunta = embedding_response.embeddings[0].values

        # Paso 2: Búsqueda Híbrida directa en Supabase (Top 5 fragmentos)
        respuesta_db = supabase.rpc(
            "match_documents_hybrid",
            {
                "query_text": pregunta,
                "query_embedding": vector_pregunta,
                "filter_departamento": depto_efectivo,
                "match_threshold": 0.30,
                "match_count": 5
            }
        ).execute()

        fragmentos = respuesta_db.data or []
        contexto = "\n\n".join([item["content"] for item in fragmentos]) if fragmentos else "Sin coincidencias documentales."

        fuentes_usadas = sorted(list({
            f"{item['metadata']['fuente']} (Hoja: {item['metadata']['hoja']})" if item.get("metadata", {}).get("hoja") else item["metadata"]["fuente"]
            for item in fragmentos if "fuente" in item.get("metadata", {})
        }))

        similitudes = [item.get("similarity", 0) for item in fragmentos if item.get("similarity") is not None]
        similitud_promedio = round(sum(similitudes) / len(similitudes), 4) if similitudes else 0.0

        texto_historial = ""
        if historial:
            lineas = [f"{'Usuario' if m.get('rol') == 'user' else 'Asistente'}: {m.get('texto', '')}" for m in historial[-2:]]
            texto_historial = "Historial:\n" + "\n".join(lineas) + "\n\n"

        # Paso 3: Prompt compacto para que gemini-3.5-flash-lite responda en < 1 segundo
        prompt_final = f"""Eres el asistente de **Victory Logistic** para el área **{ambito_usuario}**.
{texto_historial}Contexto documental:
{contexto}

Pregunta: {pregunta}

Reglas:
- Responde directo y conciso usando solo el contexto de **{ambito_usuario}**. Si no está en el contexto, dilo con cortesía.
- No uses tablas para respuestas simples o datos puntuales; úsalas solo para listar o comparar múltiples registros.
- Si piden gráfico, incluye bloque `chart` con JSON válido de Chart.js; si piden diagrama, bloque `mermaid`."""

        response = generar_respuesta_rapida(prompt_final)
        latencia_ms = int((time.perf_counter() - inicio_tiempo) * 1000)

        return {
            "respuesta": response.text,
            "fuentes": fuentes_usadas,
            "latencia_ms": latencia_ms,
            "similitud_promedio": similitud_promedio,
            "ambito": ambito_usuario
        }
    except Exception as e:
        raise Exception(f"Error en el servicio RAG: {str(e)}")


def eliminar_documento_por_fuente(nombre_archivo: str):
    try:
        supabase.table("documents").delete().eq("metadata->>fuente", nombre_archivo).execute()
    except Exception as e:
        raise Exception(f"Error al eliminar el documento en Supabase: {str(e)}")


def registrar_feedback_metrica(metric_id: int, feedback: int):
    supabase.table("rag_metrics").update({"feedback": feedback}).eq("id", metric_id).execute()


def obtener_estadisticas_tesis() -> dict:
    res_docs = supabase.table("documents").select("metadata").execute()
    documentos = res_docs.data or []

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
