const API_URL = "https://humboldtrag.onrender.com";
let historialConversacion = [];

// Inicializar tema (Claro / Oscuro) y Mermaid
(function inicializarTema() {
    const temaGuardado = localStorage.getItem("vl_theme") || "light";
    document.documentElement.setAttribute("data-theme", temaGuardado);
    if (typeof mermaid !== "undefined") {
        mermaid.initialize({
            startOnLoad: false,
            theme: temaGuardado === "dark" ? "dark" : "default",
            securityLevel: "loose"
        });
    }
})();

document.addEventListener("DOMContentLoaded", () => {
    actualizarIconoTema();
    verificarEstadoServidor();
});

function alternarTema() {
    const actual = document.documentElement.getAttribute("data-theme") || "light";
    const nuevo = actual === "light" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", nuevo);
    localStorage.setItem("vl_theme", nuevo);
    actualizarIconoTema();

    if (typeof mermaid !== "undefined") {
        mermaid.initialize({
            startOnLoad: false,
            theme: nuevo === "dark" ? "dark" : "default",
            securityLevel: "loose"
        });
    }
}

function actualizarIconoTema() {
    const btn = document.getElementById("theme-btn");
    if (!btn) return;
    const tema = document.documentElement.getAttribute("data-theme") || "light";
    btn.textContent = tema === "dark" ? "☀️" : "🌙";
}

async function verificarEstadoServidor() {
    const dot = document.getElementById("status-dot");
    const text = document.getElementById("status-text");
    try {
        const res = await fetch(`${API_URL}/`);
        if (res.ok) {
            dot.className = "status-dot online";
            text.textContent = "Sistema Activo";
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
            <div class="msg-content">Conversación reiniciada. ¿En qué puedo asistir al equipo de <strong>Victory Logistic</strong>?</div>
        </div>
    `;
}

async function enviarMensaje() {
    const inputField = document.getElementById("user-input");
    const sendBtn = document.getElementById("send-btn");
    const textoPregunta = inputField.value.trim();

    if (!textoPregunta) return;

    inputField.disabled = true;
    sendBtn.disabled = true;

    appendMessage(textoPregunta, "user");
    inputField.value = "";

    const idCargando = appendMessage("Consultando base de conocimiento de Victory Logistic...", "bot");

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

        historialConversacion.push({ rol: "user", texto: textoPregunta });
        historialConversacion.push({ rol: "assistant", texto: data.respuesta });

        document.getElementById("status-dot").className = "status-dot online";
        document.getElementById("status-text").textContent = "Sistema Activo";

        await appendMessage(data.respuesta, "bot", {
            fuentes: data.fuentes,
            latencia: data.latencia_ms,
            metricId: data.metric_id
        });

    } catch (error) {
        console.error("Error al conectar con el backend:", error);
        const cargandoEl = document.getElementById(idCargando);
        if (cargandoEl) cargandoEl.remove();
        appendMessage("Lo siento, ocurrió un error de conexión con el servidor de Victory Logistic. Intenta nuevamente en unos segundos.", "bot");
    } finally {
        inputField.disabled = false;
        sendBtn.disabled = false;
        inputField.focus();
    }
}

async function appendMessage(texto, remitente, meta = {}) {
    const chatBox = document.getElementById("chat-messages");
    const div = document.createElement("div");
    div.className = `message ${remitente}`;
    div.id = "msg-" + Math.random().toString(36).substring(2, 9);

    const chartsPendientes = [];
    const mermaidsPendientes = [];
    let contenidoHtml = "";

    if (remitente === "bot" && typeof marked !== "undefined") {
        // Extraer bloques ```chart ... ``` antes de parsear Markdown
        let textoProcesado = texto.replace(/```chart\s*([\s\S]*?)```/gi, (_, jsonCode) => {
            const chartId = "chart-" + Math.random().toString(36).substring(2, 9);
            chartsPendientes.push({ id: chartId, code: jsonCode.trim() });
            return `<div class="chart-wrapper"><canvas id="${chartId}"></canvas></div>`;
        });

        // Extraer bloques ```mermaid ... ```
        textoProcesado = textoProcesado.replace(/```mermaid\s*([\s\S]*?)```/gi, (_, mermaidCode) => {
            const mId = "mermaid-" + Math.random().toString(36).substring(2, 9);
            mermaidsPendientes.push({ id: mId, code: mermaidCode.trim() });
            return `<div class="mermaid-wrapper" id="${mId}"></div>`;
        });

        const rawHtml = marked.parse(textoProcesado);
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

    // Renderizar gráficos Chart.js si la IA generó alguno
    if (chartsPendientes.length > 0 && typeof Chart !== "undefined") {
        const esOscuro = document.documentElement.getAttribute("data-theme") === "dark";
        const colorTexto = esOscuro ? "#e2e8f0" : "#1e293b";
        const colorGrilla = esOscuro ? "rgba(148, 163, 184, 0.2)" : "rgba(100, 116, 139, 0.15)";
        const paleta = ["#2563eb", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4"];

        for (const item of chartsPendientes) {
            try {
                const config = JSON.parse(item.code);
                if (config.data && Array.isArray(config.data.datasets)) {
                    config.data.datasets.forEach((ds, idx) => {
                        if (!ds.backgroundColor) {
                            ds.backgroundColor = ["pie", "doughnut"].includes(config.type) ? paleta : paleta[idx % paleta.length];
                        }
                        if (!ds.borderColor && config.type === "line") {
                            ds.borderColor = paleta[idx % paleta.length];
                        }
                    });
                }
                config.options = {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { labels: { color: colorTexto } } },
                    scales: ["pie", "doughnut"].includes(config.type) ? {} : {
                        x: { ticks: { color: colorTexto }, grid: { color: colorGrilla } },
                        y: { ticks: { color: colorTexto }, grid: { color: colorGrilla } }
                    },
                    ...(config.options || {})
                };
                const canvas = document.getElementById(item.id);
                if (canvas) new Chart(canvas, config);
            } catch (err) {
                console.warn("No se pudo renderizar el gráfico Chart.js:", err);
            }
        }
    }

    // Renderizar diagramas Mermaid.js si la IA generó alguno
    if (mermaidsPendientes.length > 0 && typeof mermaid !== "undefined") {
        for (const item of mermaidsPendientes) {
            try {
                const contenedor = document.getElementById(item.id);
                if (contenedor) {
                    const { svg } = await mermaid.render(`svg-${item.id}`, item.code);
                    contenedor.innerHTML = svg;
                }
            } catch (err) {
                console.warn("No se pudo renderizar el diagrama Mermaid:", err);
            }
        }
    }

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
