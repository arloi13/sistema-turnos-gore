from flask import Flask, render_template, request, redirect, jsonify
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import unquote
import os
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text

app = Flask(__name__)

database_url = os.environ.get('DATABASE_URL', 'postgresql://turnos_user:tKEDL05OtBzDYWxtBJzjD8trXumanuci@dpg-d9lo31tg1s2s739u3n90-a/turnos_db_dcxx')

if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Zona horaria nativa para Perú (sin dependencias externas)
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

class Ticket(db.Model):
    __tablename__ = 'tickets'
    id = db.Column(db.Integer, primary_key=True)
    dni = db.Column(db.String(50))
    nombre = db.Column(db.String(100))
    fecha_registro = db.Column(db.String(50))
    estado = db.Column(db.String(50)) # 'ESPERA', 'ATENDIDO', 'ARCHIVADO'
    turno = db.Column(db.Integer)
    preferencial = db.Column(db.Boolean, default=False)

class HistorialAtencion(db.Model):
    __tablename__ = 'historial_atenciones'
    id = db.Column(db.Integer, primary_key=True)
    ventanilla = db.Column(db.String(100))
    turno = db.Column(db.Integer)
    fecha = db.Column(db.DateTime, default=lambda: obtener_tiempo_peru().replace(tzinfo=None))
    dni = db.Column(db.String(50))

with app.app_context():
    db.create_all()
    try:
        db.session.execute(text('ALTER TABLE tickets ADD COLUMN IF NOT EXISTS preferencial BOOLEAN DEFAULT FALSE;'))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print("Nota de migración automática:", e)

@app.route('/obtener_estado_colas')
def obtener_estado_colas():
    v_nombre = request.args.get('ventanilla', 'Ventanilla 02')
    v_nombre = unquote(v_nombre)
    
    if v_nombre == "Ventanilla 03":
        tickets_espera = Ticket.query.filter_by(estado="ESPERA").order_by(Ticket.id.asc()).all()
    else:
        tickets_espera = Ticket.query.filter_by(estado="ESPERA", preferencial=False).order_by(Ticket.id.asc()).all()
    
    tickets_archivados = Ticket.query.filter_by(estado="ARCHIVADO").order_by(Ticket.id.asc()).all()
    
    turno_actual_activo = estado_visual.get(v_nombre, 0)
    
    return jsonify({
        "turno_actual": turno_actual_activo,
        "cola_espera": [t.turno for t in tickets_espera],
        "cola_archivados": [t.turno for t in tickets_archivados]
    })

@app.route('/actualizar_turno/<ventanilla>', methods=['GET', 'POST'])
def actualizar_turno(ventanilla):
    global estado_visual, llamados_actuales
    v_nombre = unquote(ventanilla)
    
    if v_nombre in estado_visual:
        tipo = request.args.get('tipo', 'normal')
        ticket = None
        
        if v_nombre == "Ventanilla 03":
            if tipo == 'preferencial':
                ticket = Ticket.query.filter_by(estado="ESPERA", preferencial=True).order_by(Ticket.id.asc()).first()
            if not ticket:
                ticket = Ticket.query.filter_by(estado="ESPERA").order_by(Ticket.id.asc()).first()
        else:
            ticket = Ticket.query.filter_by(estado="ESPERA", preferencial=False).order_by(Ticket.id.asc()).first()
        
        if not ticket:
            return jsonify({"status": "vacio"}), 200
        
        turno_real = ticket.turno
        dni_ciudadano = ticket.dni
        
        ticket.estado = "ATENDIDO"
        
        nuevo_historial = HistorialAtencion(
            ventanilla=v_nombre,
            turno=turno_real,
            fecha=obtener_tiempo_peru().replace(tzinfo=None),
            dni=dni_ciudadano
        )
        db.session.add(nuevo_historial)
        
        estado_visual[v_nombre] = turno_real
        llamados_actuales[v_nombre] = {"turno": turno_real, "intentos": 1}
        db.session.commit()
        
        return jsonify({
            "status": "ok", 
            "ventanilla": v_nombre, 
            "turno": turno_real, 
            "es_preferencial": ticket.preferencial
        })
    return jsonify({"status": "error"}), 400

@app.route('/estadisticas', methods=['GET'])
def estadisticas():
    filtro = request.args.get('filtro')
    query = db.session.query(HistorialAtencion.ventanilla, db.func.count(HistorialAtencion.id).label('total'))
    
    if filtro == 'dia':
        if "postgresql" in app.config['SQLALCHEMY_DATABASE_URI']:
            query = query.filter(db.func.date(HistorialAtencion.fecha) == db.func.current_date())
        else:
            query = query.filter(db.func.date(HistorialAtencion.fecha) == db.func.date('now'))
    elif filtro == 'mes':
        if "postgresql" in app.config['SQLALCHEMY_DATABASE_URI']:
            query = query.filter(db.extract('month', HistorialAtencion.fecha) == db.extract('month', db.func.current_date()))
        else:
            query = query.filter(db.func.strftime('%m', HistorialAtencion.fecha) == db.func.strftime('%m', 'now'))
            
    registros = query.group_by(HistorialAtencion.ventanilla).all()
    return jsonify([{"ventanilla": r.ventanilla, "colaborador": OPERADORES.get(r.ventanilla), "total": r.total} for r in registros])

@app.route('/historial', methods=['GET'])
def historial():
    try:
        registros = HistorialAtencion.query.order_by(HistorialAtencion.id.desc()).all()
        return render_template('historial.html', registros=registros)
    except Exception as e:
        db.create_all()
        return render_template('historial.html', registros=[])

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
        dni = request.form.get('dni')
        preferencial = True if request.form.get('preferencial') == 'on' else False
        if dni:
            # Prevención de duplicados exactos si se envía dos veces seguidas con el mismo DNI en espera
            ultimo_ticket = Ticket.query.filter_by(dni=dni, estado='ESPERA').order_by(Ticket.id.desc()).first()
            if not ultimo_ticket:
                max_t = db.session.query(db.func.max(Ticket.turno)).scalar()
                nuevo_turno = (max_t or 0) + 1
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
    
    tickets = Ticket.query.filter_by(estado="ESPERA").order_by(Ticket.id.asc()).all()
    return render_template('index.html', tickets=tickets)

@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if request.method == 'POST':
        dni = request.form.get('dni')
        preferencial = True if request.form.get('preferencial') == 'on' else False
        if dni:
            # Prevención de duplicados exactos si se reenvía el formulario rápidamente
            ultimo_ticket = Ticket.query.filter_by(dni=dni, estado='ESPERA').order_by(Ticket.id.desc()).first()
            if not ultimo_ticket:
                max_t = db.session.query(db.func.max(Ticket.turno)).scalar()
                nuevo_turno = (max_t or 0) + 1
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
            return render_template('registro.html', mensaje="¡Turno generado con éxito!")
    return render_template('registro.html')

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
            ticket_obj = Ticket.query.filter_by(turno=turno_actual, estado="ATENDIDO").first()
            if ticket_obj:
                ticket_obj.estado = "ARCHIVADO"
                db.session.commit()
            archivado = True
            
            if v_nombre == "Ventanilla 03":
                siguiente = Ticket.query.filter_by(estado="ESPERA").order_by(Ticket.id.asc()).first()
            else:
                siguiente = Ticket.query.filter_by(estado="ESPERA", preferencial=False).order_by(Ticket.id.asc()).first()
                
            if siguiente:
                siguiente.estado = "ATENDIDO"
                estado_visual[v_nombre] = siguiente.turno
                llamados_actuales[v_nombre] = {"turno": siguiente.turno, "intentos": 1}
                
                nuevo_historial = HistorialAtencion(
                    ventanilla=v_nombre,
                    turno=siguiente.turno,
                    fecha=obtener_tiempo_peru().replace(tzinfo=None),
                    dni=siguiente.dni
                )
                db.session.add(nuevo_historial)
                db.session.commit()
            else:
                estado_visual[v_nombre] = 0
                llamados_actuales[v_nombre] = {"turno": 0, "intentos": 0}
                db.session.commit()

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
        db.session.execute(text('TRUNCATE TABLE tickets RESTART IDENTITY CASCADE;'))
        db.session.commit()
        return "¡Contadores en 0 y tickets de la semana reiniciados con éxito! El historial de atenciones se mantiene intacto."
    except Exception as e:
        db.session.rollback()
        return f"Error: {e}"

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
