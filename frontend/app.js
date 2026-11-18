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
    msgEl.textContent = "Creando cuenta corporativa
