CREATE TABLE staff (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    role VARCHAR(50) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE
);

CREATE TABLE services (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    duration_minutes INT NOT NULL,
    price DECIMAL(5, 2),
    staff_role VARCHAR(50) NOT NULL
);

CREATE TABLE appointments (
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