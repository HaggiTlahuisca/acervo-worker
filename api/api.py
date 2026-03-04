import os
import time
import threading
from datetime import datetime

import httpx
import jwt
from jwt import PyJWKClient
from pymongo.mongo_client import MongoClient
from fastapi import FastAPI, Query, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# ============================
# CONFIGURACIÓN
# ============================
load_dotenv()

CLERK_SECRET_KEY = os.getenv("CLERK_SECRET_KEY")
CLERK_JWKS_URL   = os.getenv("CLERK_JWKS_URL")   # lo agregas al .env

client_mongo = None
db           = None
coleccion    = None
cola         = None

security   = HTTPBearer()
jwks_client = PyJWKClient(CLERK_JWKS_URL) if CLERK_JWKS_URL else None

def conectar_mongo():
    while True:
        try:
            client = MongoClient(os.getenv("MONGO_URI"), serverSelectionTimeoutMS=5000)
            client.server_info()
            print("Conectado a MongoDB")
            return client
        except Exception as e:
            print(f"Error conectando a MongoDB, reintentando: {e}")
            time.sleep(5)

app = FastAPI(title="TepantlatAI API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # restringir al dominio real cuando se lance
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================
# STARTUP
# ============================
def conectar_en_background():
    global client_mongo, db, coleccion, cola
    client_mongo = conectar_mongo()
    db           = client_mongo["tepantlatia_db"]
    coleccion    = db["acervo_historico"]
    cola         = db["cola_tesis"]
    print("API conectada a MongoDB.")

@app.on_event("startup")
def startup_event():
    hilo = threading.Thread(target=conectar_en_background, daemon=True)
    hilo.start()

# ============================
# VERIFICACIÓN DE SESIÓN CLERK
# ============================
def verificar_sesion(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Verifica el JWT firmado por Clerk usando sus claves públicas (JWKS).
    """
    token = credentials.credentials
    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_exp": True},
        )
        return payload   # contiene sub (user_id), email, etc.
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado.")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Token inválido: {e}")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Error verificando sesión: {e}")

# ============================
# ENDPOINTS PÚBLICOS
# ============================
@app.get("/health")
def health_check():
    return JSONResponse({"status": "ok"})

@app.get("/", response_class=HTMLResponse)
def dashboard(
    epoca:   str | None = Query(default=None),
    materia: str | None = Query(default=None),
):
    if cola is None:
        return HTMLResponse(
            "<html><body>La API está iniciando. Recarga en unos segundos.</body></html>",
            status_code=503,
        )
    total       = cola.count_documents({})
    pendientes  = cola.count_documents({"estado": "pendiente"})
    procesando  = cola.count_documents({"estado": "procesando"})
    completados = cola.count_documents({"estado": "completado"})
    errores     = cola.count_documents({"estado": "error"})

    filtro = {"procesado": True}
    if epoca:
        filtro["epoca"] = epoca
    if materia:
        filtro["materia"] = materia

    ultimos = list(
        coleccion.find(filtro).sort("actualizado_en", -1).limit(10)
    )
    filas = ""
    for d in ultimos:
        filas += (
            f"<tr><td>{d.get('registro','')}</td>"
            f"<td>{d.get('rubro','')[:80]}</td>"
            f"<td>{d.get('epoca','')}</td>"
            f"<td>{d.get('materia','')}</td></tr>"
        )

    html = f"""
    <html><head><title>TepantlatAI Dashboard</title></head>
    <body>
    <h2>TepantlatAI — Estado del sistema</h2>
    <p>Total: {total} | Pendientes: {pendientes} | Procesando: {procesando} | Completados: {completados} | Errores: {errores}</p>
    <table border='1'>
      <tr><th>Registro</th><th>Rubro</th><th>Época</th><th>Materia</th></tr>
      {filas}
    </table>
    </body></html>
    """
    return HTMLResponse(html)

# ============================
# ENDPOINTS PRIVADOS (requieren sesión Clerk)
# ============================
@app.get("/yo")
def mi_perfil(sesion: dict = Depends(verificar_sesion)):
    return {
        "user_id": sesion.get("sub"),
        "email":   sesion.get("email"),
        "mensaje": "Sesión válida ✅"
    }

@app.get("/buscar")
def buscar(
    q:      str  = Query(..., description="Pregunta o búsqueda"),
    sesion: dict = Depends(verificar_sesion),
):
    return {
        "query":   q,
        "user_id": sesion.get("sub"),
        "mensaje": "Búsqueda recibida. Lógica semántica en construcción."
    }
