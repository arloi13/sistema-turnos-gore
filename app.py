import os
import json
from flask import Flask, render_template, request, redirect, jsonify, Response
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import unquote
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)

# Configuración de la base de datos PostgreSQL en Render
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Modelos de la base de datos relacional (PostgreSQL)
class Ticket(db.Model):
    __tablename__ = 'tickets'
    id = db.Column(db.Integer, primary_key=True)
    dni = db.Column(db.String(50))
    nombre = db.Column(db.String(100), default="Ciudadano")
    fecha_registro = db.Column(db.String(50))
    estado = db.Column(db.String(50), default='ESPERA')
    turno = db.Column(db.Integer)
    preferencial = db.Column(db.Boolean, default=False)

class HistorialAtencion(db.Model):
    __tablename__ = 'historial_atenciones'
    id = db.Column(db.Integer, primary_key=True)
    ventanilla = db.Column(db.String(100))
    turno = db.Column(db.Integer)
    fecha = db.Column(db.String(50))
    dni = db.Column(db.String(50))

# Crear las tablas automáticamente si no existen al iniciar
with app.app_context():
    db.create_all()

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
    
    docs_espera = Ticket.query.filter_by(estado='ESPERA').all()
    cola_espera_dicts = [{
        'turno': t.turno,
        'fecha_registro': t.fecha_registro,
        'preferencial': t.preferencial
    } for t in docs_espera]
    
    if v_nombre != "Ventanilla 03":
        cola_espera_dicts = [t for t in cola_espera_dicts if not t.get('preferencial', False)]
        
    cola_espera_dicts.sort(key=lambda x: x.get('turno', 0))
    
    docs_archivados = Ticket.query.filter_by(estado='ARCHIVADO').order_by(Ticket.turno.asc()).all()
    cola_archivados = [t.turno for t in docs_archivados]
    
    turno_actual_activo = estado_visual.get(v_nombre, 0)
    
    return jsonify({
        "turno_actual": turno_actual_activo,
        "cola_espera": [{"turno": t.get('turno'), "hora": t.get('fecha_registro')} for t in cola_espera_dicts],
        "cola_archivados": cola_archivados
    })

@app.route('/actualizar_turno/<ventanilla>', methods=['GET', 'POST'])
def actualizar_turno(ventanilla):
    global estado_visual, llamados_actuales
    v_nombre = unquote(ventanilla)
    
    if v_nombre in estado_visual:
        tipo = request.args.get('tipo', 'normal')
        ticket_obj = None
        
        todos_espera = Ticket.query.filter_by(estado='ESPERA').order_by(Ticket.turno.asc()).all()
        
        if v_nombre == "Ventanilla 03" and tipo == 'preferencial':
            pref_docs = [t for t in todos_espera if t.preferencial]
            if pref_docs:
                ticket_obj = pref_docs[0]
        
        if not ticket_obj:
            if v_nombre == "Ventanilla 03":
                esp_docs = todos_espera
            else:
                esp_docs = [t for t in todos_espera if not t.preferencial]
            
            if esp_docs:
                ticket_obj = esp_docs[0]
        
        if not ticket_obj:
            return jsonify({"status": "vacio"}), 200
        
        turno_real = ticket_obj.turno
        dni_ciudadano = ticket_obj.dni
        es_pref = ticket_obj.preferencial
        
        ticket_obj.estado = 'ATENDIDO'
        
        nuevo_historial = HistorialAtencion(
            ventanilla=v_nombre,
            turno=turno_real,
            fecha=obtener_tiempo_peru().replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
            dni=dni_ciudadano
        )
        db.session.add(nuevo_historial)
        db.session.commit()
        
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
    filtro = request.args.get('filtro')
    fecha_personalizada = request.args.get('fecha')
    
    now_peru = obtener_tiempo_peru()
    hoy_str = now_peru.strftime("%Y-%m-%d")
    mes_str = now_peru.strftime("%Y-%m")
    
    historial_docs = HistorialAtencion.query.all()
    conteo = {}
    
    for d in historial_docs:
        fecha_str = str(d.fecha or '')
        vent = d.ventanilla
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
        query = HistorialAtencion.query.order_by(HistorialAtencion.fecha.desc())
        
        registros = query.all()
        if fecha_filtro:
            registros = [r for r in registros if str(r.fecha).split(" ")[0] == fecha_filtro]
            
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
    if request.method == 'POST':
        dni = request.form.get('dni', '').strip()
        preferencial = True if request.form.get('preferencial') == 'on' else False
        
        if dni:
            existing = Ticket.query.filter_by(dni=dni, estado='ESPERA').first()
            
            if not existing:
                max_t = db.session.query(db.func.max(Ticket.turno)).scalar() or 0
                nuevo_turno = max_t + 1
                
                nuevo_ticket = Ticket(
                    dni=dni,
                    nombre="Ciudadano",
                    fecha_registro=obtener_tiempo_peru().strftime("%d/%m/%Y %H:%M"),
                    estado='ESPERA',
                    turno=nuevo_turno,
                    preferencial=preferencial
                )
                db.session.add(nuevo_ticket)
                db.session.commit()
        return redirect('/')
    
    tickets = Ticket.query.filter_by(estado='ESPERA').order_by(Ticket.turno.asc()).all()
    return render_template('index.html', tickets=tickets)

@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if request.method == 'POST':
        dni = request.form.get('dni', '').strip()
        preferencial = True if request.form.get('preferencial') == 'on' else False
        
        if dni:
            existing = Ticket.query.filter_by(dni=dni, estado='ESPERA').first()
            
            if not existing:
                max_t = db.session.query(db.func.max(Ticket.turno)).scalar() or 0
                nuevo_turno = max_t + 1
                
                nuevo_ticket = Ticket(
                    dni=dni,
                    nombre="Ciudadano",
                    fecha_registro=obtener_tiempo_peru().strftime("%d/%m/%Y %H:%M"),
                    estado='ESPERA',
                    turno=nuevo_turno,
                    preferencial=preferencial
                )
                db.session.add(nuevo_ticket)
                db.session.commit()
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
        if intentos > 3:
            t_docs = Ticket.query.filter_by(estado='ATENDIDO', turno=turno_actual).all()
            for t_doc in t_docs:
                t_doc.estado = 'ARCHIVADO'
            db.session.commit()
            archivado = True
            
            todos_espera = Ticket.query.filter_by(estado='ESPERA').order_by(Ticket.turno.asc()).all()
            if v_nombre == "Ventanilla 03":
                esp_docs = todos_espera
            else:
                esp_docs = [t for t in todos_espera if not t.preferencial]
                
            if esp_docs:
                siguiente_doc = esp_docs[0]
                siguiente_doc.estado = 'ATENDIDO'
                estado_visual[v_nombre] = siguiente_doc.turno
                llamados_actuales[v_nombre] = {"turno": siguiente_doc.turno, "intentos": 1}
                
                nuevo_historial = HistorialAtencion(
                    ventanilla=v_nombre,
                    turno=siguiente_doc.turno,
                    fecha=obtener_tiempo_peru().replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
                    dni=siguiente_doc.dni
                )
                db.session.add(nuevo_historial)
                db.session.commit()
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
        registros = HistorialAtencion.query.order_by(HistorialAtencion.fecha.desc()).all()
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
        Ticket.query.delete()
        db.session.commit()
        return "¡Contadores en 0 y tickets de la semana reiniciados con éxito! El historial de atenciones se mantiene intacto."
    except Exception as e:
        db.session.rollback()
        return f"Error: {e}"

@app.route('/descargar_sql', methods=['GET'])
def descargar_sql():
    try:
        sql_lines = []
        sql_lines.append("-- --------------------------------------------------------")
        sql_lines.append("-- Respaldo directo desde la aplicación en Render (PostgreSQL)")
        sql_lines.append("-- --------------------------------------------------------\n")
        
        tickets = Ticket.query.all()
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
        
        for t in tickets:
            preferencial = 'TRUE' if t.preferencial else 'FALSE'
            sql_lines.append(
                f"INSERT INTO tickets (dni, nombre, fecha_registro, estado, turno, preferencial) "
                f"VALUES ('{t.dni}', '{t.nombre}', '{t.fecha_registro}', '{t.estado}', {t.turno}, {preferencial});"
            )
        
        sql_lines.append("\n")
        historiales = HistorialAtencion.query.all()
        sql_lines.append("DROP TABLE IF EXISTS historial_atenciones;")
        sql_lines.append("CREATE TABLE historial_atenciones (")
        sql_lines.append("    id SERIAL PRIMARY KEY,")
        sql_lines.append("    ventanilla VARCHAR(100),")
        sql_lines.append("    turno INTEGER,")
        sql_lines.append("    fecha TIMESTAMP,")
        sql_lines.append("    dni VARCHAR(50)")
        sql_lines.append(");\n")
        
        for h in historiales:
            sql_lines.append(
                f"INSERT INTO historial_atenciones (ventanilla, turno, fecha, dni) "
                f"VALUES ('{h.ventanilla}', {h.turno}, '{h.fecha}', '{h.dni}');"
            )
            
        contenido_sql = "\n".join(sql_lines)
        
        return Response(
            contenido_sql,
            mimetype="text/sql",
            headers={"Content-disposition": "attachment; filename=respaldo_turnos_postgres.sql"}
        )
    except Exception as e:
        return f"Error al generar el respaldo SQL: {e}", 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
