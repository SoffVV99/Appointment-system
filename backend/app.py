import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, jsonify, render_template, request, send_from_directory, session, redirect, url_for
from datetime import datetime, timedelta, date, time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from zoneinfo import ZoneInfo
BULGARIA_TZ = ZoneInfo("Europe/Sofia")


app = Flask(__name__)

app.secret_key = 'salona-na-deni-super-taina'

def get_db_connection():
    conn = psycopg2.connect(
        host="db",
        database="salon",
        user="salon_admin",
        password="secretpassword",
    )
    return conn

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/staff')
def get_staff():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT * FROM staff WHERE is_active = True;')
    staff = cur.fetchall()
    cur.close()
    return jsonify(staff)

@app.route('/api/services')
def get_services():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT * FROM services;')
    services = cur.fetchall()
    cur.close()
    return jsonify(services)

@app.route('/api/slots')
def get_slots():
    staff_id = request.args.get('staff_id')
    service_id = request.args.get('service_id')
    date_str = request.args.get('date')

    if not staff_id or not service_id or not date_str:
        return jsonify([])

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute('SELECT duration_minutes FROM srvices WHERE id = %s;', (service_id))
    service = cur.fetchone()
    if not service:
        return jsonify([])

    duration = service['duration_minutes']

    target_date = datetime.strptime(date_str, '%Y-%m-%d')
    start_of_day = target_date.replace(hour=9, minute=0)
    end_of_day = target_date.replace(hour=20, minute=0)

    cur.execute('''
        SELECT start_time, end_time FROM appointments
        WHERE staff_id = %s AND (status = 'active' OR status IS NULL)
        AND start_time >= %s AND end_time <= %s
    ''', (staff_id, start_of_day, start_of_day + timedelta(days=1)))
    appointments = cur.fetchall()
    cur.close()
    conn.close()

    available_slots = []
    current_time = start_of_day

    while current_time + timedelta(minutes=duration) <= end_of_day:
        slot_end = current_time + timedelta(minutes=duration)
        is_free = True

        for appt in appointments:
            if current_time < appt['end_time'] and slot_end > appt['start_time']:
                is_free = False
                break

        if is_free:
            available_slots.append(current_time.strftime('%H:%M'))

        current_time += timedelta(minutes=5)

    return jsonify(available_slots)

from datetime import date, time

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
        return jsonify({})
    
    duration = service['duration_minutes']
    start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
    
    schedule = {}

    # Генерираме график за 3 поредни дни (както е в Superdoc)
    for i in range(3):
        current_day = start_date + timedelta(days=i)
        day_str = current_day.strftime('%Y-%m-%d')
        
        start_of_day = datetime.combine(current_day, time(9, 0)) # Начало на смяната: 09:00
        end_of_day = datetime.combine(current_day, time(18, 0))  # Край на смяната: 18:00

        cur.execute('''
            SELECT start_time, end_time FROM appointments 
            WHERE staff_id = %s AND (status = 'active' OR status IS NULL)
            AND start_time >= %s AND start_time < %s
        ''', (staff_id, start_of_day, start_of_day + timedelta(days=1)))
        appointments = cur.fetchall()

        slots = []
        curr_time = start_of_day
        while curr_time + timedelta(minutes=duration) <= end_of_day:
            slot_end = curr_time + timedelta(minutes=duration)
            is_free = True
            
            for appt in appointments:
                if curr_time < appt['end_time'] and slot_end > appt['start_time']:
                    is_free = False
                    break
            
            # Взимаме точното време в България, за да скрием миналите часове
            now_bg = datetime.now(BULGARIA_TZ).replace(tzinfo=None)
            
            if is_free and curr_time > now_bg:
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

    try:
        # Изрязваме евентуални секунди или 'Z', ако идват от календара, за да парсне чисто часа
        if 'Z' in start_time_str:
            start_time_str = start_time_str.replace('Z', '')
        if len(start_time_str) > 16:
            start_time_str = start_time_str[:16]
            
        start_time = datetime.strptime(start_time_str, '%Y-%m-%dT%H:%M')
    except ValueError:
        return jsonify({'error': 'Невалиден формат на датата.'}), 400

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute('SELECT duration_minutes FROM services WHERE id = %s;', (service_id,))
    service = cur.fetchone()

    if not service:
        cur.close()
        conn.close()
        return jsonify({'error': 'Услугата не е намерена.'}), 404

    duration = service['duration_minutes']
    end_time = start_time + timedelta(minutes=duration)

    # Проверка за застъпване (както си я имаше в стария код)
    overlap_query = """
        SELECT * FROM appointments 
        WHERE staff_id = %s 
        AND (status = 'active' OR status IS NULL)
        AND %s < end_time AND %s > start_time;
    """
    cur.execute(overlap_query, (int(staff_id), start_time, end_time))
    overlapping = cur.fetchall()

    if overlapping:
        cur.close()
        conn.close()
        return jsonify({'error': 'Съжаляваме, този час вече е зает!'}), 409

    # Записваме часа директно в базата
    insert_query = """
        INSERT INTO appointments (client_name, client_phone, staff_id, service_id, start_time, end_time)
        VALUES (%s, %s, %s, %s, %s, %s);
    """
    cur.execute(insert_query, (client_name, client_phone, staff_id, service_id, start_time, end_time))
    
    # Намираме имейла на фризьора
    cur.execute('SELECT email FROM staff WHERE id = %s;', (staff_id,))
    staff_record = cur.fetchone()
    staff_email = staff_record['email'] if staff_record and 'email' in staff_record else None
    
    conn.commit()
    cur.close()
    conn.close()
    
    msg_text = f"🌟 НОВ ЗАПИСАН ЧАС!\n\nКлиент: {client_name}\nТелефон: {client_phone}\nДата и час: {start_time}\nУслуга ID: {service_id}"
    send_email_notification(msg_text, staff_email)
    
    return jsonify({'message': 'Часът е записан успешно!'}), 201

@app.route('/admin')
def admin():
    return render_template('admin.html')

@app.route('/api/appointments')
def get_appointments():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    query = """
        SELECT a.id, a.client_name, a.start_time, a.end_time,
        s.name as staff_name, srv.name as service_name
        FROM appointments a
        JOIN staff s ON a.staff_id = s.id
        JOIN services srv ON a.service_id = srv.id
        WHERE a.status = 'active';
    """
    cur.execute(query)
    appointments = cur.fetchall()
    cur.close()
    conn.close()

    events = []
    for appt in appointments:
        events.append({
            'id': appt['id'],
            'title': f"{appt['client_name']} ({appt['service_name']})",
            'start': appt['start_time'].isoformat(),
            'end': appt['end_time'].isoformat(),
        })

    return jsonify(events)

# Намиране на часове по телефон
@app.route('/api/my_appointments', methods=['POST'])
def my_appointments():
    phone = request.json.get('phone')
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    cur.execute('''
        SELECT a.id, a.start_time, s.name as staff_name, srv.name as service_name 
        FROM appointments a
        JOIN staff s ON a.staff_id = s.id
        JOIN services srv ON a.service_id = srv.id
        WHERE a.client_phone = %s AND (a.status = 'active' OR a.status IS NULL) 
        AND a.start_time > NOW()
        ORDER BY a.start_time ASC;
    ''', (phone,))
    
    apps = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(apps)

# Функция за автоматично пращане на имейл при отмяна
def send_email_notification(text, receiver_email):
    SMTP_SERVER = "smtp.gmail.com"
    SMTP_PORT = 587
    SENDER_EMAIL = "tvoq.email@gmail.com"
    SENDER_PASSWORD = "tvoata_app_parola" 
    
    if not receiver_email:
        receiver_email = "salon.denistyle@gmail.com"

    try:
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = receiver_email
        msg['Subject'] = "🌟 Известие за час - Deni Style"

        msg.attach(MIMEText(text, 'plain', 'utf-8'))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, receiver_email, msg.as_string())
        server.quit()
        print("Имейлът е изпратен успешно!")
    except Exception as e:
        print(f"Грешка при изпращане на имейл: {e}")

# Отмяна на час и автоматичен имейл
@app.route('/api/cancel', methods=['POST'])
def cancel_appointment():
    app_id = request.json.get('id')
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    # 1. Взимаме данните преди да го отменим
    cur.execute('''
        SELECT a.start_time, s.name as staff_name, s.email as staff_email, srv.name as service_name, a.client_phone 
        FROM appointments a
        JOIN staff s ON a.staff_id = s.id
        JOIN services srv ON a.service_id = srv.id
        WHERE a.id = %s;
    ''', (app_id,))
    appt = cur.fetchone()
    
    # 2. Променяме статуса на 'cancelled'
    cur.execute("UPDATE appointments SET status = 'cancelled' WHERE id = %s;", (app_id,))
    conn.commit()
    
    cur.close()
    conn.close()
    
    # 3. Пращаме имейла автоматично
    # 3. Пращаме имейла автоматично
    if appt:
        msg_text = f"❌ Отменен е следният час в салон Deni Style:\n\nСлужител: {appt['staff_name']}\nУслуга: {appt['service_name']}\nДата и час: {appt['start_time']}\nТелефон на клиент: {appt['client_phone']}"
        send_email_notification(msg_text, appt.get('staff_email'))
        
    return jsonify({'message': 'Часът е отменен успешно!'})


# Зареждане на PWA файловете
@app.route('/manifest.json')
def serve_manifest():
    return send_from_directory('static', 'manifest.json')

@app.route('/sw.js')
def serve_sw():
    return send_from_directory('static', 'sw.js')





app.secret_key = 'denistyle_secret_key'

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('username') == 'admin' and request.form.get('password') == 'Nikola193712':
            session['logged_in'] = True
            return redirect('/admin')
        return "Грешно име или парола!", 401
    return '''
    <form method="post" style="text-align:center; margin-top:50px; font-family:sans-serif;">
        <h2>Вход - Deni Style</h2>
        <input name="username" placeholder="Име (admin)" required><br><br>
        <input type="password" name="password" placeholder="Парола" required><br><br>
        <button type="submit" style="padding:10px 20px; background:#00b894; color:white; border:none;">Влез</button>
    </form>'''

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# --- ПЕРСОНАЛ ---
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
    
    cur.execute('ALTER TABLE staff ADD COLUMN IF NOT EXISTS email VARCHAR(100);')
    cur.execute('INSERT INTO staff (name, role, email) VALUES (%s, %s, %s)', (data['name'], data['role'], data.get('email')))
    
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/delete_staff/<int:staff_id>', methods=['DELETE'])
def delete_staff(staff_id):
    if not session.get('logged_in'): return jsonify({'error': 'Отказан достъп'})
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('DELETE FROM staff WHERE id = %s', (staff_id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

# --- УСЛУГИ ---
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
    
    # Принудително променяме колоната price да е текст (VARCHAR), за да приема "17-20", "45+" и т.н.
    cur.execute('ALTER TABLE services ALTER COLUMN price TYPE VARCHAR(50);')
    cur.execute('ALTER TABLE services ADD COLUMN IF NOT EXISTS category VARCHAR(50);')
    cur.execute('ALTER TABLE services ADD COLUMN IF NOT EXISTS subcategory VARCHAR(50);')
    
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
    cur.execute('DELETE FROM services WHERE id = %s', (service_id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

# --- ПЕЧАТНИ МАТЕРИАЛИ ---
@app.route('/pricelist')
def pricelist():
    return render_template('pricelist.html')

@app.route('/card')
def business_card():
    return render_template('card.html')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')