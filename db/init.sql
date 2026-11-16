-- Розширення vector потрібне для векторного пошуку в таблиці videos.
-- Самі таблиці створює backend/server.py (SCHEMA) і backend/ingest.py при запуску.
CREATE EXTENSION IF NOT EXISTS vector;
