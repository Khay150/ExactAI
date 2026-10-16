-- ============================================================================
-- eXACTai - tabla del RAG (pgvector, en el mismo Postgres)
-- Lo ejecuta rag/indexar.py solo (reemplaza __DIM__ por la dimension del modelo
-- de embeddings). Es idempotente: se puede correr las veces que se quiera.
-- Requiere la imagen de Postgres con pgvector (ej. pgvector/pgvector:pg16).
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- Configuracion de busqueda de texto en espanol que ignora tildes.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'es_unaccent') THEN
        CREATE TEXT SEARCH CONFIGURATION es_unaccent (COPY = spanish);
        ALTER TEXT SEARCH CONFIGURATION es_unaccent
            ALTER MAPPING FOR hword, hword_part, word WITH unaccent, spanish_stem;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS rag_documento (
    id_doc          TEXT PRIMARY KEY,      -- clave natural estable, ej. comision:algebra_1:algebra_1_manana_tp1:2026_2c
    tipo            TEXT NOT NULL,         -- comision / materia / evento / docente / carrera / plan
    id_cuatri       TEXT,
    id_materia      TEXT,
    id_carrera      TEXT,
    id_docente      TEXT,
    titulo          TEXT NOT NULL,
    contenido       TEXT NOT NULL,
    metadata        JSONB NOT NULL DEFAULT '{}',
    hash_texto      TEXT NOT NULL,         -- sha256 de titulo + contenido (lo que "lee" el modelo)
    hash_embebido   TEXT,                  -- hash_texto con el que se calculo el embedding actual
    modelo          TEXT,                  -- modelo con el que se calculo el embedding actual
    embedding       vector(__DIM__),
    -- el titulo pesa mas que el contenido en el ranking por palabras
    tsv             tsvector GENERATED ALWAYS AS (
                        setweight(to_tsvector('es_unaccent', coalesce(titulo, '')), 'A') ||
                        setweight(to_tsvector('es_unaccent', coalesce(contenido, '')), 'B')) STORED,
    actualizado_en  TIMESTAMPTZ NOT NULL DEFAULT now(),
    embebido_en     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_rag_tipo        ON rag_documento (tipo);
CREATE INDEX IF NOT EXISTS idx_rag_cuatri      ON rag_documento (id_cuatri);
CREATE INDEX IF NOT EXISTS idx_rag_materia     ON rag_documento (id_materia);
CREATE INDEX IF NOT EXISTS idx_rag_tsv         ON rag_documento USING gin (tsv);
CREATE INDEX IF NOT EXISTS idx_rag_embedding   ON rag_documento USING hnsw (embedding vector_cosine_ops);
