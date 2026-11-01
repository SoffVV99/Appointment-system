CREATE TABLE IF NOT EXISTS staff (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    role VARCHAR(50) NOT NULL,
    email VARCHAR(100),
    password VARCHAR(100) DEFAULT '123456',
    calendar_id VARCHAR(150),
    is_active BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS services (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    duration_minutes INT NOT NULL,
    price VARCHAR(50),
    category VARCHAR(50),
    subcategory VARCHAR(50),
    staff_role VARCHAR(50) NOT NULL
);

CREATE TABLE IF NOT EXISTS appointments (
    id SERIAL PRIMARY KEY,
    client_name VARCHAR(100) NOT NULL,
    client_phone VARCHAR(20) NOT NULL,
    client_email VARCHAR(100),
    staff_id INT REFERENCES staff(id),
    service_id INT REFERENCES services(id),
    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP NOT NULL,
    status VARCHAR(20) DEFAULT 'active'
);

-- Автоматично добавяне на Деница и Христина, ако таблицата е празна
INSERT INTO staff (name, role, email, password, calendar_id)
SELECT 'Деница', 'Фризьор', 'denica.velinova@gmail.com', 'deni123', 'denica.velinova@gmail.com'
WHERE NOT EXISTS (SELECT 1 FROM staff WHERE name = 'Деница');

INSERT INTO staff (name, role, email, password, calendar_id)
SELECT 'Христина', 'Гримьор', 'sofiaviryanska@gmail.com', 'hrisi123', 'sofiaviryanska@gmail.com'
WHERE NOT EXISTS (SELECT 1 FROM staff WHERE name = 'Христина');