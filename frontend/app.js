const API_URL = "https://humboldtrag.onrender.com";
let historialConversacion = [];
let usuarioActual = null;

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
    verificarSesionGuardada();
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

// =========================================================
// AUTENTICACIÓN (LOGIN / REGISTRO)
// =========================================================

function mostrarTabAuth(tab) {
    document.getElementById("auth-message").textContent = "";
    if (tab === "login") {
        document.getElementById("tab-login").classList.add("active");
        document.getElementById("tab-register").classList.remove("active");
        document.getElementById("form-login").style.display = "block";
        document.getElementById("form-register").style.display = "none";
    } else {
        document.getElementById("tab-register").classList.add("active");
        document.getElementById("tab-login").classList.remove("active");
        document.getElementById("form-register").style.display = "block";
        document.getElementById("form-login").style.display = "none";
    }
}

async function verificarSesionGuardada() {
    const token = localStorage.getItem("vl_token");
    if (!token) return;

    try {
        const res = await fetch(`${API_URL}/auth/me`, {
            headers: { "Authorization": `Bearer ${token}` }
        });
        if (!res.ok) throw new Error();
        const data = await res.json();
        activarInterfazChat(data.usuario, token);
    } catch {
        localStorage.removeItem("vl_token");
        localStorage.removeItem("vl_user");
    }
}

async function iniciarSesionUsuario() {
    const username = document.getElementById("login-username").value.trim();
    const password = document.getElementById("login-password").value;
    const msgEl = document.getElementById("auth-message");
    const btn = document.getElementById("btn-login-submit");

    if (!username || !password) {
        msgEl.textContent = "Completa usuario y contraseña.";
        return;
    }

    btn.disabled = true;
    msgEl.style.color = "var(--brand-accent)";
    msgEl.textContent = "Verificando credenciales...";

    try {
        const res = await fetch(`${API_URL}/auth/login`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username, password })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Error al iniciar sesión");

        activarInterfazChat(data.usuario, data.token);
    } catch (err) {
        msgEl.style.color = "#ef4444";
        msgEl.textContent = err.message;
    } finally {
        btn.disabled = false;
    }
}

async function registrarNuevoUsuario() {
    const nombre = document.getElementById("reg-nombre").value.trim();
    const username = document.getElementById("reg-username").value.trim();
    const departamento = document.getElementById("reg-departamento").value;
    const password = document.getElementById("reg-password").value;
    const msgEl = document.getElementById("auth-message");
    const btn = document.getElementById("btn-reg-submit");

    if (!nombre || !username || !departamento || !password) {
        msgEl.style.color = "#ef4444";
        msgEl.textContent = "Por favor completa todos los campos y selecciona tu departamento.";
        return;
    }

    btn.disabled = true;
    msgEl.style.color = "var(--brand-accent)";
    msgEl.textContent = "Creando cuenta corporativa...";

    try {
        const res = await fetch(`${API_URL}/auth/registro`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ nombre, username, password, departamento })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "No se pudo completar el registro");

        activarInterfazChat(data.usuario, data.token);
    } catch (err) {
        msgEl.style.color = "#ef4444";
        msgEl.textContent = err.message;
    } finally {
        btn.disabled = false;
    }
}

function activarInterfazChat(usuario, token) {
    usuarioActual = usuario;
    localStorage.setItem("vl_token", token);
    localStorage.setItem("vl_user", JSON.stringify(usuario));

    // Eliminar completamente el bloque de login del DOM para que el navegador no pida actualizar contraseñas al chatear
    const authScreen = document.getElementById("auth-screen");
    if (authScreen) authScreen.remove();

    document.getElementById("chat-container").style.display = "flex";
    document.getElementById("btn-nueva-consulta").style.display = "inline-block";
    document.getElementById("btn-logout").style.display = "inline-block";

    const userPill = document.getElementById("header-user-info");
    userPill.style.display = "inline-flex";
    userPill.textContent = usuario.rol === "admin"
        ? `👑 ${usuario.nombre} (Admin)`
        : `👤 ${usuario.nombre} · ${usuario.departamento}`;

    if (usuario.rol === "admin") {
        document.getElementById("link-admin").style.display = "inline-block";
        document.getElementById("admin-dept-filter").style.display = "inline-block";
    }

    limpiarChat();
}

function cerrarSesion() {
    localStorage.removeItem("vl_token");
    localStorage.removeItem("vl_user");
    location.reload();
}

// =========================================================
// LÓGICA DEL CHAT
// =========================================================

document.getElementById("user-input").addEventListener("keypress", function (event) {
    if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        enviarMensaje();
    }
});

function limpiarChat() {
    historialConversacion = [];
    const chatBox = document.getElementById("chat-messages");
    const ambito = usuarioActual?.rol === "admin"
        ? "todos los departamentos de <strong>Victory Logistic</strong>"
        : `el departamento de <strong>${usuarioActual?.departamento || ""}</strong> de <strong>Victory Logistic</strong>`;

    chatBox.innerHTML = `
        <div class="message bot">
            <div class="msg-content">Hola <strong>${usuarioActual?.nombre || ""}</strong>. Tienes acceso autorizado a la base de conocimiento de ${ambito}. ¿En qué puedo ayudarte hoy?</div>
        </div>
    `;
}

async function enviarMensaje() {
    const inputField = document.getElementById("user-input");
    const sendBtn = document.getElementById("send-btn");
    const textoPregunta = inputField.value.trim();
    const token = localStorage.getItem("vl_token");

    if (!textoPregunta || !token) return;

    inputField.disabled = true;
    sendBtn.disabled = true;

    await appendMessage(textoPregunta, "user");
    inputField.value = "";

    const idCargando = await appendMessage("Consultando base de conocimiento de Victory Logistic...", "bot");

    const filtroDepto = (usuarioActual?.rol === "admin")
        ? document.getElementById("admin-dept-filter").value
        : null;

    try {
        const response = await fetch(`${API_URL}/chat`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${token}`
            },
            body: JSON.stringify({
                pregunta: textoPregunta,
                historial: historialConversacion.slice(-4),
                filtro_departamento: filtroDepto
            })
        });

        const data = await response.json();

        const cargandoEl = document.getElementById(idCargando);
        if (cargandoEl) cargandoEl.remove();

        if (response.status === 401) {
            cerrarSesion();
            return;
        }

        if (!response.ok) {
            throw new Error(data.detail || `Error HTTP ${response.status}`);
        }

        historialConversacion.push({ rol: "user", texto: textoPregunta });
        historialConversacion.push({ rol: "assistant", texto: data.respuesta });

        document.getElementById("status-dot").className = "status-dot online";
        document.getElementById("status-text").textContent = "Sistema Activo";

        await appendMessage(data.respuesta, "bot", {
            latencia: data.latencia_ms,
            metricId: data.metric_id
        });

    } catch (error) {
        console.error("Error detallado en el chat:", error);
        const cargandoEl = document.getElementById(idCargando);
        if (cargandoEl) cargandoEl.remove();
        await appendMessage(`No se pudo procesar la consulta: ${error.message}`, "bot");
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
        let textoProcesado = texto.replace(/```chart\s*([\s\S]*?)```/gi, (_, jsonCode) => {
            const chartId = "chart-" + Math.random().toString(36).substring(2, 9);
            chartsPendientes.push({ id: chartId, code: jsonCode.trim() });
            return `<div class="chart-wrapper"><canvas id="${chartId}"></canvas></div>`;
        });

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
    if (remitente === "bot" && meta.metricId) {
        const tiempoTexto = meta.latencia ? `Respondido en ${(meta.latencia / 1000).toFixed(2)}s` : "";

        footerHtml = `
            <div class="msg-footer">
                <span>${tiempoTexto}</span>
                <div class="feedback-btns" id="fb-group-${meta.metricId}">
                    <button type="button" class="fb-btn" onclick="enviarFeedback(${meta.metricId}, 1, this)" title="Respuesta útil">👍 Útil</button>
                    <button type="button" class="fb-btn" onclick="enviarFeedback(${meta.metricId}, -1, this)" title="Respuesta imprecisa">👎 Mejorar</button>
                </div>
            </div>
        `;
    }

    div.innerHTML = `<div class="msg-content">${contenidoHtml}${footerHtml}</div>`;
    chatBox.appendChild(div);

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
    const token = localStorage.getItem("vl_token");
    const grupo = document.getElementById(`fb-group-${metricId}`);
    const botones = grupo.querySelectorAll("button");
    botones.forEach(b => b.disabled = true);

    try {
        const res = await fetch(`${API_URL}/feedback`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${token}`
            },
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
