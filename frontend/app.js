// URL pública de tu backend desplegado en Render
const API_URL = "https://humboldtrag.onrender.com"; 

// Lógica de la Vista de Usuario (Chat)
const chatForm = document.getElementById("chat-form");
if (chatForm) {
    chatForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const input = document.getElementById("user-input");
        const pregunta = input.value.trim();
        if (!pregunta) return;

        appendMessage(pregunta, "user");
        input.value = "";

        const loadingId = appendMessage("Pensando...", "bot");

        try {
            const res = await fetch(`${API_URL}/chat`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ pregunta })
            });
            const data = await res.json();
            
            removeMessage(loadingId);
            appendMessage(data.respuesta, "bot", data.fuentes);
        } catch (err) {
            removeMessage(loadingId);
            appendMessage("Error conectando con el servidor.", "bot");
        }
    });
}

function appendMessage(texto, remitente, fuentes = []) {
    const chatBox = document.getElementById("chat-messages");
    const div = document.createElement("div");
    div.className = `message ${remitente}`;
    div.id = "msg-" + Math.random();
    
    let fuentesHtml = "";
    if (fuentes && fuentes.length > 0) {
        fuentesHtml = `<br><small style="color: #64748b;"><b>Fuentes:</b> ${fuentes.join(', ')}</small>`;
    }

    div.innerHTML = `<div class="msg-content">${texto.replace(/\n/g, '<br>')}${fuentesHtml}</div>`;
    chatBox.appendChild(div);
    chatBox.scrollTop = chatBox.scrollHeight;
    return div.id;
}

function removeMessage(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}

// Lógica de la Vista de Administrador
const uploadForm = document.getElementById("upload-form");
if (uploadForm) {
    cargarEstadisticas();

    uploadForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fileInput = document.getElementById("file-input");
        const status = document.getElementById("upload-status");
        if (fileInput.files.length === 0) return;

        const formData = new FormData();
        formData.append("file", fileInput.files[0]);

        status.textContent = "Subiendo e indexando multimodalmente...";
        try {
            const res = await fetch(`${API_URL}/admin/subir`, {
                method: "POST",
                body: formData
            });
            const data = await res.json();
            status.textContent = data.mensaje;
            fileInput.value = "";
            cargarEstadisticas();
        } catch (err) {
            status.textContent = "Error al subir el archivo.";
        }
    });

    document.getElementById("btn-refresh").addEventListener("click", cargarEstadisticas);
}

async function cargarEstadisticas() {
    try {
        const res = await fetch(`${API_URL}/admin/documentos/conteo`);
        const data = await res.json();
        
        document.getElementById("stat-files").textContent = data.total_archivos_subidos;
        document.getElementById("stat-vectors").textContent = data.total_registros_vectoriales;

        const list = document.getElementById("document-list");
        list.innerHTML = "";
        
        if (data.archivos.length === 0) {
            list.innerHTML = "<li>No hay archivos indexados.</li>";
            return;
        }

        data.archivos.forEach(archivo => {
            const li = document.createElement("li");
            li.innerHTML = `<span>📄 ${archivo}</span> <button onclick="eliminarArchivo('${archivo}')" style="background:#ef4444; color:white; border:none; padding:5px 10px; border-radius:4px; cursor:pointer;">Eliminar</button>`;
            list.appendChild(li);
        });
    } catch (err) {
        console.error("Error cargando estadísticas", err);
    }
}

async function eliminarArchivo(nombre) {
    if (!confirm(`¿Eliminar ${nombre}?`)) return;
    try {
        await fetch(`${API_URL}/admin/documentos/${encodeURIComponent(nombre)}`, { method: "DELETE" });
        cargarEstadisticas();
    } catch (err) {
        alert("Error al eliminar.");
    }
}
