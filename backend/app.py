import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, jsonify, render_template, request, send_from_directory, session, redirect
from datetime import datetime, timedelta, date, time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from zoneinfo import ZoneInfo

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    GOOGLE_API_AVAILABLE = True
except ImportError:
    GOOGLE_API_AVAILABLE = False

BULGARIA_TZ = ZoneInfo("Europe/Sofia")

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'denistyle_secret_key')

# Паролите вече се четат автоматично и сигурно от файла .env
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'sofiaviryanska@gmail.com')
SENDER_PASSWORD = os.environ.get('SENDER_PASSWORD', '')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'Nikola193712')

CONFIG_FILE = 'config.json'

def load_config():
    default_cfg = {
        "salon_address": "ул. „Възраждане“ №75, с. Драгичево (салонът се намира в сградата на кметството).",
        "salon_phone": "0896184325",
        "weekday_open": "10:00",
        "weekday_close": "18:00",
        "sat_open": "10:00",
        "sat_close": "17:00",
        "sun_open": "",
        "sun_close": ""
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                default_cfg.update(data)
        except Exception:
            pass
    return default_cfg

def save_config(data):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def get_db_connection():
    return psycopg2.connect(
        host="db",
        database="salon",
        user="salon_admin",
        password=os.environ.get('POSTGRES_PASSWORD', 'secretpassword'),
    )

def init_db_safely():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('ALTER TABLE staff ADD COLUMN IF NOT EXISTS email VARCHAR(100);')
    cur.execute("ALTER TABLE staff ADD COLUMN IF NOT EXISTS password VARCHAR(100) DEFAULT '123456';")
    cur.execute('ALTER TABLE services ALTER COLUMN price TYPE VARCHAR(50);')
    cur.execute('ALTER TABLE services ADD COLUMN IF NOT EXISTS category VARCHAR(50);')
    cur.execute('ALTER TABLE services ADD COLUMN IF NOT EXISTS subcategory VARCHAR(50);')
    conn.commit()
    cur.close()
    conn.close()

def send_email_notification(subject, text, receiver_email):
    if not receiver_email or not SENDER_PASSWORD:
        print("Имейл не е изпратен: липсва получател или не е настроен SENDER_PASSWORD.")
        return
    try:
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = receiver_email
        msg['Subject'] = subject
        msg.attach(MIMEText(text, 'plain', 'utf-8'))

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD.replace(" ", ""))
        server.sendmail(SENDER_EMAIL, receiver_email, msg.as_string())
        server.quit()
        print(f"Имейл изпратен успешно до {receiver_email}!")
    except Exception as e:
        print(f"Грешка при изпращане на имейл: {e}")

def add_to_google_calendar(summary, description, start_dt, end_dt, staff_email):
    if not GOOGLE_API_AVAILABLE or not os.path.exists('service_account.json') or not staff_email:
        print("Google Calendar: Липсва service_account.json или имейл на служител.")
        return
    try:
        SCOPES = ['https://www.googleapis.com/auth/calendar']
        creds = service_account.Credentials.from_service_account_file('service_account.json', scopes=SCOPES)
        service = build('calendar', 'v3', credentials=creds)
        event = {
            'summary': summary,
            'description': description,
            'start': {'dateTime': start_dt.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Europe/Sofia'},
            'end': {'dateTime': end_dt.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Europe/Sofia'},
        }
        service.events().insert(calendarId=staff_email.strip(), body=event).execute()
        print(f"Успешно добавено събитие в Google Календара на {staff_email}")
    except Exception as e:
        print(f"Грешка при Google Calendar: {e}")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/info')
def get_info():
    cfg = load_config()
    return jsonify({
        "address": cfg.get("salon_address", ""),
        "phone": cfg.get("salon_phone", ""),
        "weekday": f"{cfg.get('weekday_open')} - {cfg.get('weekday_close')}",
        "saturday": f"{cfg.get('sat_open')} - {cfg.get('sat_close')}",
        "sunday": "Почивен ден" if not cfg.get('sun_open') else f"{cfg.get('sun_open')} - {cfg.get('sun_close')}",
        "is_staff_logged_in": bool(session.get('logged_in'))
    })

@app.route('/api/staff')
def get_staff():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT id, name, role, email, is_active FROM staff WHERE is_active = True ORDER BY id ASC;')
    staff = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(staff)

@app.route('/api/services')
def get_services():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT * FROM services ORDER BY id ASC;')
    services = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(services)

@app.route('/api/schedule')
def get_schedule():
    staff_id = request.args.get('staff_id')
    service_id = request.args.get('service_id')
    start_date_str = request.args.get('start_date') 

    if not staff_id or not service_id or not start_date_str:
        return jsonify({})

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT duration_minutes FROM services WHERE id = %s;', (service_id,))
    service = cur.fetchone()
    if not service:
        cur.close()
        conn.close()
        return jsonify({})
    
    duration = int(service['duration_minutes'])
    start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
    cfg = load_config()
    is_staff_logged = bool(session.get('logged_in'))
    schedule = {}

    for i in range(7):
        current_day = start_date + timedelta(days=i)
        day_str = current_day.strftime('%Y-%m-%d')
        weekday = current_day.weekday()

        if is_staff_logged:
            open_str, close_str = "08:00", "21:00"
        else:
            if weekday < 5:
                open_str, close_str = cfg.get("weekday_open", "10:00"), cfg.get("weekday_close", "18:00")
            elif weekday == 5:
                open_str, close_str = cfg.get("sat_open", "10:00"), cfg.get("sat_close", "17:00")
            else:
                open_str, close_str = cfg.get("sun_open", ""), cfg.get("sun_close", "")

        if not open_str or not close_str:
            schedule[day_str] = []
            continue

        open_h, open_m = map(int, open_str.split(':'))
        close_h, close_m = map(int, close_str.split(':'))

        start_of_day = datetime.combine(current_day, time(open_h, open_m))
        end_of_day = datetime.combine(current_day, time(close_h, close_m))

        cur.execute('''
            SELECT start_time, end_time FROM appointments 
            WHERE staff_id = %s AND (status = 'active' OR status IS NULL)
            AND start_time >= %s AND start_time < %s
        ''', (staff_id, datetime.combine(current_day, time(0, 0)), datetime.combine(current_day, time(23, 59))))
        appointments = cur.fetchall()

        slots = []
        curr_time = start_of_day
        now_bg = datetime.now(BULGARIA_TZ).replace(tzinfo=None)

        while curr_time + timedelta(minutes=duration) <= end_of_day:
            slot_end = curr_time + timedelta(minutes=duration)
            is_free = True
            
            for appt in appointments:
                if curr_time < appt['end_time'] and slot_end > appt['start_time']:
                    is_free = False
                    break
            
            if is_free and (curr_time > now_bg or is_staff_logged):
                slots.append(curr_time.strftime('%H:%M'))
            
            curr_time += timedelta(minutes=30)
        
        schedule[day_str] = slots

    cur.close()
    conn.close()
    return jsonify(schedule)

@app.route('/api/book', methods=['POST'])
def book_appointment():
    data = request.json
    client_name = data.get('client_name')
    client_phone = data.get('client_phone')
    staff_id = data.get('staff_id')
    service_id = data.get('service_id')
    start_time_str = data.get('start_time')

    is_staff = bool(session.get('logged_in'))

    try:
        if 'Z' in start_time_str:
            start_time_str = start_time_str.replace('Z', '')
        if len(start_time_str) > 16:
            start_time_str = start_time_str[:16]
        start_time = datetime.strptime(start_time_str, '%Y-%m-%dT%H:%M')
    except ValueError:
        return jsonify({'error': 'Невалиден формат на датата.'}), 400

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute('SELECT name, duration_minutes, price FROM services WHERE id = %s;', (service_id,))
    service = cur.fetchone()
    if not service:
        cur.close()
        conn.close()
        return jsonify({'error': 'Услугата не е намерена.'}), 404

    service_name = service['name']
    duration = int(service['duration_minutes'])

    # Ако е логнат персонал и изрично е въвел друга продължителност, използваме нея, но ПАЗИМ името на услугата!
    if is_staff and data.get('custom_duration'):
        try:
            custom_dur = int(data.get('custom_duration'))
            if custom_dur > 0:
                duration = custom_dur
        except ValueError:
            pass

    end_time = start_time + timedelta(minutes=duration)

    overlap_query = """
        SELECT * FROM appointments 
        WHERE staff_id = %s 
        AND (status = 'active' OR status IS NULL)
        AND %s < end_time AND %s > start_time;
    """
    cur.execute(overlap_query, (int(staff_id), start_time, end_time))
    if cur.fetchall():
        cur.close()
        conn.close()
        return jsonify({'error': 'Съжаляваме, този час вече е зает!'}), 409

    cur.execute("""
        INSERT INTO appointments (client_name, client_phone, staff_id, service_id, start_time, end_time, status)
        VALUES (%s, %s, %s, %s, %s, %s, 'active');
    """, (client_name, client_phone, staff_id, service_id, start_time, end_time))
    
    cur.execute('SELECT name, email FROM staff WHERE id = %s;', (staff_id,))
    staff_record = cur.fetchone()
    staff_email = staff_record['email'] if staff_record else None
    staff_name = staff_record['name'] if staff_record else "Специалист"
    
    conn.commit()
    cur.close()
    conn.close()
    
    formatted_time = start_time.strftime('%d.%m.%Y от %H:%M ч.')
    msg_text = (
        f"🌟 НОВ ЗАПИСАН ЧАС в Deni Style!\n\n"
        f"Специалист: {staff_name}\n"
        f"Процедура: {service_name} ({duration} мин.)\n"
        f"Дата и час: {formatted_time} (до {end_time.strftime('%H:%M')} ч.)\n"
        f"Клиент: {client_name}\n"
        f"Телефон: {client_phone}"
    )
    
    send_email_notification("🌟 Нов записан час - Deni Style", msg_text, staff_email)
    add_to_google_calendar(
        summary=f"{client_name} - {service_name} ({client_phone})",
        description=msg_text,
        start_dt=start_time,
        end_dt=end_time,
        staff_email=staff_email
    )
    
    return jsonify({'message': 'Часът е записан успешно!'}), 201

@app.route('/api/cancel', methods=['POST'])
def cancel_appointment():
    app_id = request.json.get('id')
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    cur.execute('''
        SELECT a.start_time, a.client_name, a.client_phone, s.name as staff_name, s.email as staff_email, 
               COALESCE(srv.name, 'Служебен / Извънреден час') as service_name 
        FROM appointments a
        JOIN staff s ON a.staff_id = s.id
        LEFT JOIN services srv ON a.service_id = srv.id
        WHERE a.id = %s;
    ''', (app_id,))
    appt = cur.fetchone()
    
    cur.execute("UPDATE appointments SET status = 'cancelled' WHERE id = %s;", (app_id,))
    conn.commit()
    cur.close()
    conn.close()
    
    if appt:
        msg_text = (
            f"❌ ОТМЕНЕН ЧАС в салон Deni Style:\n\n"
            f"Служител: {appt['staff_name']}\n"
            f"Услуга: {appt['service_name']}\n"
            f"Дата и час: {appt['start_time'].strftime('%d.%m.%Y от %H:%M ч.')}\n"
            f"Клиент: {appt['client_name']} ({appt['client_phone']})"
        )
        send_email_notification("❌ Отменен час - Deni Style", msg_text, appt.get('staff_email'))
        
    return jsonify({'message': 'Часът е отменен успешно!'})

@app.route('/api/my_appointments', methods=['POST'])
def my_appointments():
    phone = request.json.get('phone')
    now_bg = datetime.now(BULGARIA_TZ).replace(tzinfo=None)
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('''
        SELECT a.id, a.start_time, s.name as staff_name, 
               COALESCE(srv.name, 'Служебен / Извънреден час') as service_name 
        FROM appointments a
        JOIN staff s ON a.staff_id = s.id
        LEFT JOIN services srv ON a.service_id = srv.id
        WHERE a.client_phone = %s AND (a.status = 'active' OR a.status IS NULL) 
        AND a.start_time > %s
        ORDER BY a.start_time ASC;
    ''', (phone, now_bg))
    apps = cur.fetchall()
    cur.close()
    conn.close()

    for app_item in apps:
        app_item['start_time'] = app_item['start_time'].strftime('%d.%m.%Y г. от %H:%M ч.')

    return jsonify(apps)

@app.route('/admin')
def admin():
    if not session.get('logged_in'):
        return redirect('/login')
    return render_template('admin.html')

@app.route('/api/appointments')
def get_appointments():
    if not session.get('logged_in'):
        return jsonify([])
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    if session.get('role') == 'staff' and session.get('staff_id'):
        cur.execute("""
            SELECT a.id, a.client_name, a.client_phone, a.start_time, a.end_time,
            s.name as staff_name, COALESCE(srv.name, 'Служебен / Извънреден час') as service_name
            FROM appointments a
            JOIN staff s ON a.staff_id = s.id
            LEFT JOIN services srv ON a.service_id = srv.id
            WHERE (a.status = 'active' OR a.status IS NULL) AND a.staff_id = %s;
        """, (session.get('staff_id'),))
    else:
        cur.execute("""
            SELECT a.id, a.client_name, a.client_phone, a.start_time, a.end_time,
            s.name as staff_name, COALESCE(srv.name, 'Служебен / Извънреден час') as service_name
            FROM appointments a
            JOIN staff s ON a.staff_id = s.id
            LEFT JOIN services srv ON a.service_id = srv.id
            WHERE a.status = 'active' OR a.status IS NULL;
        """)

    appointments = cur.fetchall()
    cur.close()
    conn.close()

    events = []
    for appt in appointments:
        events.append({
            'id': appt['id'],
            'title': f"{appt['client_name']} ({appt['client_phone']}) - {appt['service_name']} [{appt['staff_name']}]",
            'start': appt['start_time'].isoformat(),
            'end': appt['end_time'].isoformat(),
        })
    return jsonify(events)

@app.route('/login', methods=['GET', 'POST'])
def login():
    init_db_safely()
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        if username == 'admin' and password == ADMIN_PASSWORD:
            session['logged_in'] = True
            session['role'] = 'admin'
            session['user_name'] = 'Администратор'
            return redirect('/admin')

        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute('SELECT * FROM staff WHERE (LOWER(email) = LOWER(%s) OR LOWER(name) = LOWER(%s)) AND password = %s;', (username, username, password))
        staff_user = cur.fetchone()
        cur.close()
        conn.close()

        if staff_user:
            session['logged_in'] = True
            session['role'] = 'staff'
            session['staff_id'] = staff_user['id']
            session['user_name'] = staff_user['name']
            return redirect('/admin')

        return "Грешно име/имейл или парола!", 401

    return '''
    <form method="post" style="text-align:center; margin-top:60px; font-family:sans-serif;">
        <h2>Вход за персонал - Deni Style</h2>
        <input name="username" placeholder="Имейл или Име (напр. Деница)" required style="padding:10px; width:260px;"><br><br>
        <input type="password" name="password" placeholder="Парола" required style="padding:10px; width:260px;"><br><br>
        <button type="submit" style="padding:10px 30px; background:#111; color:#d4af37; border:1px solid #d4af37; cursor:pointer;">Влез</button>
    </form>'''

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

@app.route('/admin/settings', methods=['GET', 'POST'])
def admin_settings():
    if not session.get('logged_in'):
        return redirect('/login')
    cfg = load_config()
    msg = ""
    if request.method == 'POST':
        cfg['salon_address'] = request.form.get('salon_address', '').strip()
        cfg['salon_phone'] = request.form.get('salon_phone', '').strip()
        cfg['weekday_open'] = request.form.get('weekday_open', '10:00').strip()
        cfg['weekday_close'] = request.form.get('weekday_close', '18:00').strip()
        cfg['sat_open'] = request.form.get('sat_open', '10:00').strip()
        cfg['sat_close'] = request.form.get('sat_close', '17:00').strip()
        cfg['sun_open'] = request.form.get('sun_open', '').strip()
        cfg['sun_close'] = request.form.get('sun_close', '').strip()
        save_config(cfg)
        msg = "<p style='color:green; font-weight:bold;'>✅ Настройките са запазени успешно!</p>"

    return f'''
    <!DOCTYPE html>
    <html lang="bg">
    <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Настройки на салона</title></head>
    <body style="font-family:sans-serif; max-width:600px; margin:30px auto; padding:20px; background:#f9f9f9;">
        <a href="/admin" style="text-decoration:none; color:#2980b9; font-weight:bold;">← Назад към графика</a>
        <h2>⚙️ Настройки на салона</h2>
        {msg}
        <form method="post" style="background:white; padding:20px; border-radius:8px; box-shadow:0 2px 8px rgba(0,0,0,0.1);">
            <label><b>📍 Адрес на салона:</b></label><br>
            <input type="text" name="salon_address" value="{cfg.get('salon_address', '')}" style="width:100%; padding:8px; margin:6px 0 15px 0; box-sizing:border-box;" required><br>

            <label><b>📞 Телефон за бутона „Обади се“:</b></label><br>
            <input type="text" name="salon_phone" value="{cfg.get('salon_phone', '')}" style="width:100%; padding:8px; margin:6px 0 15px 0; box-sizing:border-box;" required><br>
            
            <label><b>🕒 Понеделник - Петък (Отваряне и Край на последна процедура):</b></label><br>
            От: <input type="time" name="weekday_open" value="{cfg.get('weekday_open', '10:00')}" style="padding:6px;">
            До: <input type="time" name="weekday_close" value="{cfg.get('weekday_close', '18:00')}" style="padding:6px;"><br><br>

            <label><b>🕒 Събота:</b></label><br>
            От: <input type="time" name="sat_open" value="{cfg.get('sat_open', '10:00')}" style="padding:6px;">
            До: <input type="time" name="sat_close" value="{cfg.get('sat_close', '17:00')}" style="padding:6px;"><br><br>

            <label><b>🕒 Неделя (изтрий часовете за Почивен ден):</b></label><br>
            От: <input type="time" name="sun_open" value="{cfg.get('sun_open', '')}" style="padding:6px;">
            До: <input type="time" name="sun_close" value="{cfg.get('sun_close', '')}" style="padding:6px;"><br><br>

            <button type="submit" style="background:#27ae60; color:white; padding:12px 25px; border:none; border-radius:4px; cursor:pointer; font-size:15px;">Запази промените</button>
        </form>
    </body>
    </html>
    '''

@app.route('/admin/staff')
def admin_staff():
    if not session.get('logged_in'): return redirect('/login')
    return render_template('admin_staff.html')

@app.route('/api/add_staff', methods=['POST'])
def add_staff():
    if not session.get('logged_in'): return jsonify({'error': 'Отказан достъп'})
    data = request.json
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('INSERT INTO staff (name, role, email, password) VALUES (%s, %s, %s, %s)', 
                (data['name'], data['role'], data.get('email'), data.get('password') or '123456'))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/delete_staff/<int:staff_id>', methods=['DELETE'])
def delete_staff(staff_id):
    if not session.get('logged_in'): return jsonify({'error': 'Отказан достъп'})
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('DELETE FROM appointments WHERE staff_id = %s', (staff_id,))
    cur.execute('DELETE FROM staff WHERE id = %s', (staff_id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

@app.route('/admin/services')
def admin_services():
    if not session.get('logged_in'): return redirect('/login')
    return render_template('admin_services.html')

@app.route('/api/add_service', methods=['POST'])
def add_service():
    if not session.get('logged_in'): return jsonify({'error': 'Отказан достъп'})
    data = request.json
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('INSERT INTO services (name, duration_minutes, staff_role, price, category, subcategory) VALUES (%s, %s, %s, %s, %s, %s)',
                (data['name'], data['duration_minutes'], data['staff_role'], data['price'], data.get('category'), data.get('subcategory')))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/edit_service/<int:service_id>', methods=['PUT'])
def edit_service(service_id):
    if not session.get('logged_in'): return jsonify({'error': 'Отказан достъп'})
    data = request.json
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        UPDATE services 
        SET name = %s, duration_minutes = %s, price = %s, category = %s, subcategory = %s, staff_role = %s 
        WHERE id = %s
    ''', (data['name'], data['duration_minutes'], data['price'], data.get('category'), data.get('subcategory'), data['staff_role'], service_id))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/delete_service/<int:service_id>', methods=['DELETE'])
def delete_service(service_id):
    if not session.get('logged_in'): return jsonify({'error': 'Отказан достъп'})
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('DELETE FROM appointments WHERE service_id = %s', (service_id,))
    cur.execute('DELETE FROM services WHERE id = %s', (service_id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

@app.route('/pricelist')
def pricelist():
    return render_template('pricelist.html')

@app.route('/card')
def business_card():
    return render_template('card.html')

@app.route('/manifest.json')
def serve_manifest():
    return send_from_directory('static', 'manifest.json')

@app.route('/sw.js')
def serve_sw():
    return send_from_directory('static', 'sw.js')

@app.route('/privacy')
def privacy_policy():
    return '''
    <div style="font-family:sans-serif; max-width:800px; margin:40px auto; padding:20px; line-height:1.6;">
        <h1>Политика за поверителност - Deni Style</h1>
        <p>Салон за красота <b>Deni Style</b> (denistyle.bg) събира единствено име и телефонен номер на клиентите с цел резервиране на часове.</p>
    </div>'''

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')