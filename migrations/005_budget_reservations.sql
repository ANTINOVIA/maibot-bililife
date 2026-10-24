CREATE TABLE IF NOT EXISTS budget_reservations(
 day TEXT NOT NULL, reservation_key TEXT NOT NULL, resources TEXT NOT NULL,
 created REAL NOT NULL, PRIMARY KEY(day,reservation_key));
