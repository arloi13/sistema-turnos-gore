import os
import json
from flask import Flask, render_template, request, redirect, jsonify, Response
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import unquote
import firebase_admin
from firebase_admin import credentials, firestore

app = Flask(__name__)

# --- CONFIGURACIÓN E INICIALIZACIÓN DE FIREBASE FIRESTORE ---
if 'FIREBASE_CREDENTIALS_JSON' in os.environ:
    cred_dict = json.loads(os.environ['FIREBASE_CREDENTIALS_JSON'])
    cred = credentials.Certificate(cred_dict)
else:
    cred = credentials.Certificate("firebase-key.json")

if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)

db = firestore.client()

# Zona horaria nativa para Perú
PERU_TZ = ZoneInfo("America/Lima")

def obtener_tiempo_peru():
    return datetime.now(PERU_TZ)

# Asignación correcta de operadores por ventanilla
OPERADORES = {
    "Ventanilla 01": "Yajaira", 
    "Ventanilla 02": "Sandra", 
    "Ventanilla 03": "Jhoe"
}

estado_visual = {
    "Ventanilla 01": 0, 
    "Ventanilla 02": 0, 
    "Ventanilla 03": 0
}

llamados_actuales = {
    "Ventanilla 01": {"turno": 0, "intentos": 0},
    "Ventanilla 02": {"turno": 0, "intentos": 0},
    "Ventanilla 03": {"turno": 0, "intentos": 0}
}

@app.route('/obtener_estado_colas')
def obtener_estado_colas():
    v_nombre = request.args.get('ventanilla', 'Ventanilla 02')
    v_nombre = unquote(v_nombre)
    
    tickets_ref = db.collection('tickets')
    
    if v_nombre == "Ventanilla 03":
        docs_espera = list(tickets_ref.where('estado', '==', 'ESPERA').stream())
    else:
        docs_espera = list(tickets_ref.where('estado', '==', 'ESPERA').where('preferencial', '==', False).stream())
    
    cola_espera = [doc.to_dict() for doc in docs_espera]
    cola_espera.sort(key=lambda x: x.get('turno', 0))
    
    docs_archivados = list(tickets_ref.where('estado', '==', 'ARCHIVADO').stream())
    cola_archivados = [doc.to_dict() for doc in docs_archivados]
    cola_archivados.sort(key=lambda x: x.get('turno', 0))
    
    turno_actual_activo = estado_visual.get(v_nombre, 0)
    
    return jsonify({
        "turno_actual": turno_actual_activo,
        "cola_espera": [{"turno": t.get('turno'), "hora": t.get('fecha_registro')} for t in cola_espera],
        "cola_archivados": [t.get('turno') for t in cola_archivados]
    })

@app.route('/actualizar_turno/<ventanilla>', methods=['GET', 'POST'])
def actualizar_turno(ventanilla):
    global estado_visual, llamados_actuales
    v_nombre = unquote(ventanilla)
    
    if v_nombre in estado_visual:
        tipo = request.args.get('tipo', 'normal')
        tickets_ref = db.collection('tickets')
        ticket_doc = None
        
        if v_nombre == "Ventanilla 03" and tipo == 'preferencial':
            pref_docs = list(tickets_ref.where('estado', '==', 'ESPERA').where('preferencial', '==', True).stream())
            if pref_docs:
                pref_docs.sort(key=lambda d: d.to_dict().get('turno', 0))
                ticket_doc = pref_docs[0]
        
        if not ticket_doc:
            if v_nombre == "Ventanilla 03":
                esp_docs = list(tickets_ref.where('estado', '==', 'ESPERA').stream())
            else:
                esp_docs = list(tickets_ref.where('estado', '==', 'ESPERA').where('preferencial', '==', False).stream())
            
            if esp_docs:
                esp_docs.sort(key=lambda d: d.to_dict().get('turno', 0))
                ticket_doc = esp_docs[0]
        
        if not ticket_doc:
            return jsonify({"status": "vacio"}), 200
        
        ticket_id = ticket_doc.id
        ticket_data = ticket_doc.to_dict()
        turno_real = ticket_data.get('turno')
        dni_ciudadano = ticket_data.get('dni')
        es_pref = ticket_data.get('preferencial', False)
        
        tickets_ref.document(ticket_id).update({'estado': 'ATENDIDO'})
        
        db.collection('historial_atenciones').add({
            'ventanilla': v_nombre,
            'turno': turno_real,
            'fecha': obtener_tiempo_peru().replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
            'dni': dni_ciudadano
        })
        
        estado_visual[v_nombre] = turno_real
        llamados_actuales[v_nombre] = {"turno": turno_real, "intentos": 1}
        
        return jsonify({
            "status": "ok", 
            "ventanilla": v_nombre, 
            "turno": turno_real, 
            "es_preferencial": es_pref
        })
    return jsonify({"status": "error"}), 400

@app.route('/estadisticas', methods=['GET'])
def estadisticas():
    filtro = request.args.get('filtro') # 'dia', 'mes', o vacío/historico
    fecha_personalizada = request.args.get('fecha') # Formato YYYY-MM-DD
    
    now_peru = obtener_tiempo_peru()
    hoy_str = now_peru.strftime("%Y-%m-%d")
    mes_str = now_peru.strftime("%Y-%m")
    
    historial_docs = db.collection('historial_atenciones').stream()
    conteo = {}
    
    for doc in historial_docs:
        d = doc.to_dict()
        fecha_str = str(d.get('fecha', ''))
        vent = d.get('ventanilla')
        if not vent:
            continue
        
        solo_fecha = fecha_str.split(" ")[0] if " " in fecha_str else fecha_str
        
        incluir = True
        if fecha_personalizada:
            if solo_fecha != fecha_personalizada:
                incluir = False
        else:
            if filtro == 'dia':
                if solo_fecha != hoy_str:
                    incluir = False
            elif filtro == 'mes':
                if not fecha_str.startswith(mes_str):
                    incluir = False
                
        if incluir:
            conteo[vent] = conteo.get(vent, 0) + 1
            
    return jsonify([{"ventanilla": v, "colaborador": OPERADORES.get(v), "total": t} for v, t in conteo.items()])

@app.route('/historial', methods=['GET'])
def historial():
    try:
        fecha_filtro = request.args.get('fecha')
        docs = list(db.collection('historial_atenciones').stream())
        registros = []
        
        for doc in docs:
            d = doc.to_dict()
            fecha_str = str(d.get('fecha', ''))
            
            if fecha_filtro:
                solo_fecha = fecha_str.split(" ")[0] if " " in fecha_str else fecha_str
                if solo_fecha != fecha_filtro:
                    continue
            
            class Record:
                def __init__(self, data, doc_id):
                    self.id = doc_id
                    self.ventanilla = data.get('ventanilla')
                    self.turno = data.get('turno')
                    self.fecha = data.get('fecha')
                    self.dni = data.get('dni')
            registros.append(Record(d, doc.id))
            
        registros.sort(key=lambda x: str(x.fecha), reverse=True)
        return render_template('historial.html', registros=registros, fecha_filtro=fecha_filtro or "")
    except Exception as e:
        return render_template('historial.html', registros=[], fecha_filtro="")

@app.route('/obtener_todos_los_turnos')
def obtener_todos_los_turnos():
    global estado_visual, timestamps_visual
    if 'timestamps_visual' not in globals():
        global timestamps_visual
        timestamps_visual = {"Ventanilla 01": 0, "Ventanilla 02": 0, "Ventanilla 03": 0}
        
    resultado = {}
    for v, t in estado_visual.items():
        resultado[v] = {
            "turno": t,
            "timestamp": timestamps_visual.get(v, 0)
        }
    return jsonify(resultado)

@app.route('/resetear_turnos', methods=['POST'])
def resetear_turnos():
    global estado_visual, llamados_actuales
    estado_visual = {"Ventanilla 01": 0, "Ventanilla 02": 0, "Ventanilla 03": 0}
    llamados_actuales = {
        "Ventanilla 01": {"turno": 0, "intentos": 0},
        "Ventanilla 02": {"turno": 0, "intentos": 0},
        "Ventanilla 03": {"turno": 0, "intentos": 0}
    }
    return jsonify({"status": "reseteado"})

@app.route('/', methods=['GET', 'POST'])
def index():
    tickets_ref = db.collection('tickets')
    if request.method == 'POST':
        dni = request.form.get('dni', '').strip()
        preferencial = True if request.form.get('preferencial') == 'on' else False
        
        if dni:
            # Validación estricta: si el DNI ya tiene un ticket en ESPERA, no duplicar
            existing = list(tickets_ref.where('dni', '==', dni).where('estado', '==', 'ESPERA').stream())
            if not existing:
                # Obtener el último turno de forma eficiente y segura
                ultimos_turnos = list(tickets_ref.order_by('turno', direction=firestore.Query.DESCENDING).limit(1).stream())
                max_t = 0
                if ultimos_turnos:
                    max_t = ultimos_turnos[0].to_dict().get('turno', 0)
                
                nuevo_turno = max_t + 1
                
                tickets_ref.add({
                    'dni': dni,
                    'nombre': "Ciudadano",
                    'fecha_registro': obtener_tiempo_peru().strftime("%d/%m/%Y %H:%M"),
                    'estado': 'ESPERA',
                    'turno': nuevo_turno,
                    'preferencial': preferencial
                })
        return redirect('/')
    
    docs_esp = list(tickets_ref.where('estado', '==', 'ESPERA').stream())
    tickets = []
    for doc in docs_esp:
        d = doc.to_dict()
        class TicketObj:
            def __init__(self, data):
                self.turno = data.get('turno')
                self.fecha_registro = data.get('fecha_registro')
                self.dni = data.get('dni')
                self.preferencial = data.get('preferencial', False)
        tickets.append(TicketObj(d))
    tickets.sort(key=lambda x: x.turno)
    return render_template('index.html', tickets=tickets)

@app.route('/registro', methods=['GET', 'POST'])
def registro():
    tickets_ref = db.collection('tickets')
    if request.method == 'POST':
        dni = request.form.get('dni', '').strip()
        preferencial = True if request.form.get('preferencial') == 'on' else False
        
        if dni:
            existing = list(tickets_ref.where('dni', '==', dni).where('estado', '==', 'ESPERA').stream())
            if not existing:
                ultimos_turnos = list(tickets_ref.order_by('turno', direction=firestore.Query.DESCENDING).limit(1).stream())
                max_t = 0
                if ultimos_turnos:
                    max_t = ultimos_turnos[0].to_dict().get('turno', 0)
                
                nuevo_turno = max_t + 1
                
                tickets_ref.add({
                    'dni': dni,
                    'nombre': "Ciudadano",
                    'fecha_registro': obtener_tiempo_peru().strftime("%d/%m/%Y %H:%M"),
                    'estado': 'ESPERA',
                    'turno': nuevo_turno,
                    'preferencial': preferencial
                })
        return redirect('/registro?exito=1')
    
    mensaje = "¡Turno generado con éxito!" if request.args.get('exito') else None
    return render_template('registro.html', mensaje=mensaje)

@app.route('/control')
def control_general(): 
    return render_template('control.html', operador='Sandra')

@app.route('/control/sandra')
def control_sandra():
    return render_template('control.html', operador='Sandra')

@app.route('/control/yajaira')
def control_yajaira():
    return render_template('control.html', operador='Yajaira')

@app.route('/control/jhoe')
def control_jhoe():
    return render_template('control.html', operador='Jhoe')

@app.route('/repetir_turno/<ventanilla>', methods=['POST'])
def repetir_turno(ventanilla):
    global estado_visual, timestamps_visual, llamados_actuales
    v_nombre = unquote(ventanilla)
    
    if v_nombre in estado_visual and estado_visual[v_nombre] > 0:
        turno_actual = estado_visual[v_nombre]
        
        if v_nombre not in llamados_actuales or llamados_actuales[v_nombre]["turno"] != turno_actual:
            llamados_actuales[v_nombre] = {"turno": turno_actual, "intentos": 1}
        
        llamados_actuales[v_nombre]["intentos"] += 1
        intentos = llamados_actuales[v_nombre]["intentos"]
        
        archivado = False
        tickets_ref = db.collection('tickets')
        if intentos > 3:
            t_docs = list(tickets_ref.where('turno', '==', turno_actual).where('estado', '==', 'ATENDIDO').stream())
            for t_doc in t_docs:
                tickets_ref.document(t_doc.id).update({'estado': 'ARCHIVADO'})
            archivado = True
            
            if v_nombre == "Ventanilla 03":
                esp_docs = list(tickets_ref.where('estado', '==', 'ESPERA').stream())
            else:
                esp_docs = list(tickets_ref.where('estado', '==', 'ESPERA').where('preferencial', '==', False).stream())
                
            if esp_docs:
                esp_docs.sort(key=lambda d: d.to_dict().get('turno', 0))
                siguiente_doc = esp_docs[0]
                siguiente_id = siguiente_doc.id
                siguiente_data = siguiente_doc.to_dict()
                
                tickets_ref.document(siguiente_id).update({'estado': 'ATENDIDO'})
                estado_visual[v_nombre] = siguiente_data.get('turno')
                llamados_actuales[v_nombre] = {"turno": siguiente_data.get('turno'), "intentos": 1}
                
                db.collection('historial_atenciones').add({
                    'ventanilla': v_nombre,
                    'turno': siguiente_data.get('turno'),
                    'fecha': obtener_tiempo_peru().replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
                    'dni': siguiente_data.get('dni')
                })
            else:
                estado_visual[v_nombre] = 0
                llamados_actuales[v_nombre] = {"turno": 0, "intentos": 0}

        if 'timestamps_visual' not in globals():
            timestamps_visual = {"Ventanilla 01": 0, "Ventanilla 02": 0, "Ventanilla 03": 0}
        timestamps_visual[v_nombre] = obtener_tiempo_peru().timestamp()
        
        return jsonify({
            "status": "ok", 
            "ventanilla": v_nombre, 
            "turno": estado_visual[v_nombre],
            "archivado": archivado
        })
        
    return jsonify({"status": "error", "mensaje": "No hay turno activo"}), 400

@app.route('/pantalla')
def pantalla(): 
    return render_template('pantalla.html')

@app.route('/historial_semanal', methods=['GET'])
def historial_semanal():
    try:
        docs = list(db.collection('historial_atenciones').stream())
        registros = []
        for doc in docs:
            d = doc.to_dict()
            class Record:
                def __init__(self, data, doc_id):
                    self.id = doc_id
                    self.ventanilla = data.get('ventanilla')
                    self.turno = data.get('turno')
                    self.fecha = data.get('fecha')
                    self.dni = data.get('dni')
            registros.append(Record(d, doc.id))
        registros.sort(key=lambda x: str(x.fecha), reverse=True)
        return render_template('historial_semanal.html', registros=registros)
    except Exception as e:
        return render_template('historial_semanal.html', registros=[])

@app.route('/limpiar_base_datos_secreto')
def limpiar_db():
    global estado_visual, llamados_actuales
    try:
        estado_visual = {"Ventanilla 01": 0, "Ventanilla 02": 0, "Ventanilla 03": 0}
        llamados_actuales = {
            "Ventanilla 01": {"turno": 0, "intentos": 0},
            "Ventanilla 02": {"turno": 0, "intentos": 0},
            "Ventanilla 03": {"turno": 0, "intentos": 0}
        }
        tickets_docs = db.collection('tickets').stream()
        for doc in tickets_docs:
            db.collection('tickets').document(doc.id).delete()
        return "¡Contadores en 0 y tickets de la semana reiniciados con éxito! El historial de atenciones se mantiene intacto."
    except Exception as e:
        return f"Error: {e}"

@app.route('/descargar_sql', methods=['GET'])
def descargar_sql():
    try:
        sql_lines = []
        sql_lines.append("-- --------------------------------------------------------")
        sql_lines.append("-- Respaldo directo desde la aplicación en Render (Firestore)")
        sql_lines.append("-- --------------------------------------------------------\n")
        
        tickets_ref = db.collection('tickets')
        
        # --- TABLA TICKETS ---
        sql_lines.append("DROP TABLE IF EXISTS tickets;")
        sql_lines.append("CREATE TABLE tickets (")
        sql_lines.append("    id SERIAL PRIMARY KEY,")
        sql_lines.append("    dni VARCHAR(50),")
        sql_lines.append("    nombre VARCHAR(100),")
        sql_lines.append("    fecha_registro VARCHAR(50),")
        sql_lines.append("    estado VARCHAR(50),")
        sql_lines.append("    turno INTEGER,")
        sql_lines.append("    preferencial BOOLEAN")
        sql_lines.append(");\n")
        
        for doc in tickets_ref.stream():
            d = doc.to_dict()
            dni = str(d.get('dni', ''))
            nombre = str(d.get('nombre', 'Ciudadano'))
            fecha_reg = str(d.get('fecha_registro', ''))
            estado = str(d.get('estado', ''))
            turno = int(d.get('turno', 0))
            preferencial = 'TRUE' if d.get('preferencial', False) else 'FALSE'
            
            sql_lines.append(
                f"INSERT INTO tickets (dni, nombre, fecha_registro, estado, turno, preferencial) "
                f"VALUES ('{dni}', '{nombre}', '{fecha_reg}', '{estado}', {turno}, {preferencial});"
            )
        
        sql_lines.append("\n")
        
        # --- TABLA HISTORIAL_ATENCIONES ---
        sql_lines.append("DROP TABLE IF EXISTS historial_atenciones;")
        sql_lines.append("CREATE TABLE historial_atenciones (")
        sql_lines.append("    id SERIAL PRIMARY KEY,")
        sql_lines.append("    ventanilla VARCHAR(100),")
        sql_lines.append("    turno INTEGER,")
        sql_lines.append("    fecha TIMESTAMP,")
        sql_lines.append("    dni VARCHAR(50)")
        sql_lines.append(");\n")
        
        for doc in db.collection('historial_atenciones').stream():
            d = doc.to_dict()
            ventanilla = str(d.get('ventanilla', ''))
            turno = int(d.get('turno', 0))
            fecha = str(d.get('fecha', ''))
            dni = str(d.get('dni', ''))
            
            sql_lines.append(
                f"INSERT INTO historial_atenciones (ventanilla, turno, fecha, dni) "
                f"VALUES ('{ventanilla}', {turno}, '{fecha}', '{dni}');"
            )
            
        contenido_sql = "\n".join(sql_lines)
        
        return Response(
            contenido_sql,
            mimetype="text/sql",
            headers={"Content-disposition": "attachment; filename=respaldo_turnos.sql"}
        )
    except Exception as e:
        return f"Error al generar el respaldo SQL: {e}", 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
