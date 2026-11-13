// URL de tu backend desplegado en Render (sin barra al final ni /api)
const API_URL = "https://humboldtrag.onrender.com";

document.getElementById("user-input").addEventListener("keypress", function(event) {
    if (event.key === "Enter") {
        enviarMensaje();
    }
});

async function enviarMensaje() {
    const inputField = document.getElementById("user-input");
    const textoPregunta = inputField.value.trim();
    
    if (!textoPregunta) return;

    // Mostrar mensaje del usuario en el chat
    appendMessage(textoPregunta, "user");
    inputField.value = "";

    // Crear indicador de "Pensando..." temporal
    const idCargando = appendMessage("Analizando base de conocimiento y contexto RAG...", "bot");

    try {
        const response = await fetch(`${API_URL}/chat`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({ pregunta: textoPregunta })
        });

        if (!response.ok) {
            throw new Error(`Error en el servidor: ${response.statusText}`);
        }

        const data = await response.json();
        
        // Remover el mensaje de carga temporal
        document.getElementById(idCargando).remove();

        // Mostrar la respuesta del bot con su respectiva lista de fuentes
        appendMessage(data.respuesta, "bot", data.fuentes);

    } catch (error) {
        console.error("Error al conectar con el backend:", error);
        document.getElementById(idCargando).remove();
        appendMessage("Lo siento, ocurrió un error de conexión con el servidor RAG en Render.", "bot");
    }
}

function appendMessage(texto, remitente, fuentes = []) {
    const chatBox = document.getElementById("chat-messages");
    const div = document.createElement("div");
    div.className = `message ${remitente}`;
    div.id = "msg-" + Math.random();
    
    let fuentesHtml = "";
    if (fuentes && fuentes.length > 0) {
        fuentesHtml = `<br><small style="color: #64748b; margin-top: 6px; display: block;"><b>Fuentes:</b> ${fuentes.join(', ')}</small>`;
    }

    // Si es respuesta del bot, procesamos el Markdown (tablas, negritas, listas) con marked.js
    // Si es mensaje del usuario, mostramos texto plano con saltos de línea seguros
    const contenidoHtml = (remitente === 'bot' && typeof marked !== 'undefined') 
        ? marked.parse(texto) 
        : texto.replace(/\n/g, '<br>');

    div.innerHTML = `<div class="msg-content">${contenidoHtml} ${fuentesHtml}</div>`;
    chatBox.appendChild(div);
    chatBox.scrollTop = chatBox.scrollHeight;
    
    return div.id;
}
