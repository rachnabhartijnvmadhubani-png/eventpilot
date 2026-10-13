CREATE DATABASE IF NOT EXISTS eventpilot CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
-- Optional local-only database user. Change this password before running in your environment.
CREATE USER IF NOT EXISTS 'eventpilot'@'localhost' IDENTIFIED BY 'CHANGE_ME_LOCAL_DB_PASSWORD';
GRANT ALL PRIVILEGES ON eventpilot.* TO 'eventpilot'@'localhost';
FLUSH PRIVILEGES;
