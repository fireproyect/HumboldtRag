const API_URL = "https://humboldtrag.onrender.com";
let historialConversacion = [];

// Despertar el servidor de Render apenas carga la página (mitiga Cold Start)
document.addEventListener("DOMContentLoaded", () => {
    verificarEstadoServidor();
});

async function verificarEstadoServidor() {
    const dot = document.getElementById("status-dot");
    const text = document.getElementById("status-text");
    try {
        const res = await fetch(`${API_URL}/`);
        if (res.ok) {
            dot.className = "status-dot online";
            text.textContent = "Motor RAG Activo";
        } else {
            throw new Error();
        }
    } catch {
        dot.className = "status-dot offline";
        text.textContent = "Iniciando servidor...";
    }
}

document.getElementById("user-input").addEventListener("keypress", function (event) {
    if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        enviarMensaje();
    }
});

function limpiarChat() {
    historialConversacion = [];
    const chatBox = document.getElementById("chat-messages");
    chatBox.innerHTML = `
        <div class="message bot">
            <div class="msg-content">Conversación reiniciada. ¿En qué puedo ayudarte hoy?</div>
        </div>
    `;
}

async function enviarMensaje() {
    const inputField = document.getElementById("user-input");
    const sendBtn = document.getElementById("send-btn");
    const textoPregunta = inputField.value.trim();

    if (!textoPregunta) return;

    // Bloquear interfaz mientras procesa
    inputField.disabled = true;
    sendBtn.disabled = true;

    appendMessage(textoPregunta, "user");
    inputField.value = "";

    const idCargando = appendMessage("Analizando base de conocimiento híbrida...", "bot");

    try {
        const response = await fetch(`${API_URL}/chat`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                pregunta: textoPregunta,
                historial: historialConversacion.slice(-4)
            })
        });

        if (!response.ok) {
            throw new Error(`Error en el servidor: ${response.statusText}`);
        }

        const data = await response.json();
        document.getElementById(idCargando).remove();

        // Actualizar memoria conversacional
        historialConversacion.push({ rol: "user", texto: textoPregunta });
        historialConversacion.push({ rol: "assistant", texto: data.respuesta });

        // Actualizar badge a online por si estaba iniciando
        document.getElementById("status-dot").className = "status-dot online";
        document.getElementById("status-text").textContent = "Motor RAG Activo";

        appendMessage(data.respuesta, "bot", {
            fuentes: data.fuentes,
            latencia: data.latencia_ms,
            similitud: data.similitud_promedio,
            metricId: data.metric_id
        });

    } catch (error) {
        console.error("Error al conectar con el backend:", error);
        const cargandoEl = document.getElementById(idCargando);
        if (cargandoEl) cargandoEl.remove();
        appendMessage("Lo siento, ocurrió un error de conexión con el servidor RAG. Si el servidor estaba suspendido, intenta nuevamente en unos segundos.", "bot");
    } finally {
        inputField.disabled = false;
        sendBtn.disabled = false;
        inputField.focus();
    }
}

function appendMessage(texto, remitente, meta = {}) {
    const chatBox = document.getElementById("chat-messages");
    const div = document.createElement("div");
    div.className = `message ${remitente}`;
    div.id = "msg-" + Math.random().toString(36).substring(2, 9);

    let contenidoHtml = "";
    if (remitente === "bot" && typeof marked !== "undefined") {
        const rawHtml = marked.parse(texto);
        contenidoHtml = (typeof DOMPurify !== "undefined") ? DOMPurify.sanitize(rawHtml) : rawHtml;
    } else {
        const divTemp = document.createElement("div");
        divTemp.textContent = texto;
        contenidoHtml = divTemp.innerHTML.replace(/\n/g, "<br>");
    }

    let footerHtml = "";
    if (remitente === "bot" && (meta.fuentes?.length || meta.metricId)) {
        const fuentesTexto = meta.fuentes && meta.fuentes.length > 0
            ? `<b>Fuentes:</b> ${meta.fuentes.join(", ")}`
            : `<b>Fuentes:</b> Sin coincidencia documental`;

        const tiempoTexto = meta.latencia ? ` · ${(meta.latencia / 1000).toFixed(2)}s` : "";

        const feedbackHtml = meta.metricId ? `
            <div class="feedback-btns" id="fb-group-${meta.metricId}">
                <button class="fb-btn" onclick="enviarFeedback(${meta.metricId}, 1, this)" title="Respuesta útil">👍 Útil</button>
                <button class="fb-btn" onclick="enviarFeedback(${meta.metricId}, -1, this)" title="Respuesta imprecisa">👎 Mejorar</button>
            </div>
        ` : "";

        footerHtml = `
            <div class="msg-footer">
                <span>${fuentesTexto}${tiempoTexto}</span>
                ${feedbackHtml}
            </div>
        `;
    }

    div.innerHTML = `<div class="msg-content">${contenidoHtml}${footerHtml}</div>`;
    chatBox.appendChild(div);
    chatBox.scrollTop = chatBox.scrollHeight;

    return div.id;
}

async function enviarFeedback(metricId, valor, btnElement) {
    const grupo = document.getElementById(`fb-group-${metricId}`);
    const botones = grupo.querySelectorAll("button");
    botones.forEach(b => b.disabled = true);

    try {
        const res = await fetch(`${API_URL}/feedback`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ metric_id: metricId, feedback: valor })
        });
        if (res.ok) {
            btnElement.classList.add(valor === 1 ? "active-like" : "active-dislike");
        }
    } catch (e) {
        console.error("Error enviando feedback:", e);
        botones.forEach(b => b.disabled = false);
    }
}
