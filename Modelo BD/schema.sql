-- ============================================================================
-- eXACTai - schema de base de datos (PostgreSQL)
-- Generado a partir de JSON/*.json y Modelo BD/DER.drawio, MLR.drawio.
-- Decisiones de diseño documentadas en el doc de proyecto "diseno-base-datos.md".
--
-- Los archivos JSON originales NO se modifican: este schema se llena con un
-- script de carga (ETL) aparte que lee los JSON tal cual están y resuelve
-- las referencias (aula, dia, docente, etc.) al insertar.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gist; -- para un EXCLUDE de solapamiento aula/horario mas adelante

-- ----------------------------------------------------------------------------
-- Catalogos
-- ----------------------------------------------------------------------------

CREATE TABLE departamento (
    id_departamento TEXT PRIMARY KEY,
    nombre          TEXT NOT NULL,
    url             TEXT
);

CREATE TABLE dia (
    id_dia  TEXT PRIMARY KEY,
    nombre  TEXT NOT NULL
);

CREATE TABLE cuatrimestre (
    id_cuatri TEXT PRIMARY KEY,
    anio      INT  NOT NULL,
    periodo   TEXT NOT NULL,   -- "1".."4", "verano", "invierno" (heterogeneo en el JSON de origen)
    tipo      TEXT NOT NULL,   -- cuatrimestre / bimestre / intensivo / vacaciones
    orden     INT  NOT NULL
);

CREATE TABLE aula (
    id_aula   TEXT PRIMARY KEY,
    numero    TEXT NOT NULL,             -- numerica o texto libre ("1101", "Aula Magna", "E24")
    pabellon  INT  NOT NULL,
    aliases   TEXT[] NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_aula_pabellon_numero ON aula (pabellon, numero);

-- ----------------------------------------------------------------------------
-- Materias / carreras
-- ----------------------------------------------------------------------------

CREATE TABLE materia (
    id                TEXT PRIMARY KEY,
    carga_horaria     INT,
    aliases_externos  JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE carrera (
    id                          TEXT PRIMARY KEY,
    nombre                      TEXT NOT NULL,
    duracion                    NUMERIC(3,1),
    titulo                      TEXT,
    cant_materias_obligatorias  INT,
    modalidad_optativas         TEXT,
    cant_materias_optativas     INT
);

CREATE TABLE grupo_optativo (
    id_grupo           TEXT PRIMARY KEY,
    nombre             TEXT NOT NULL,
    cantidad_a_elegir  INT
);

CREATE TABLE grupo_materia (
    id_grupo    TEXT REFERENCES grupo_optativo(id_grupo) ON DELETE CASCADE,
    id_materia  TEXT REFERENCES materia(id) ON DELETE CASCADE,
    PRIMARY KEY (id_grupo, id_materia)
);

-- Un item del "plan" de una carrera: puede apuntar a una materia puntual
-- o a un grupo optativo (de ahi el CHECK).
CREATE TABLE carrera_materia (
    id_carrera_materia  SERIAL PRIMARY KEY,
    id_carrera          TEXT NOT NULL REFERENCES carrera(id) ON DELETE CASCADE,
    id_materia          TEXT REFERENCES materia(id),
    id_grupo            TEXT REFERENCES grupo_optativo(id_grupo),
    tipo                TEXT,     -- ej: "taller", "materia"
    tipo_materia        TEXT,     -- ej: "obligatoria", "optativa"
    anio                INT,
    cuatrimestre        INT,
    CHECK (id_materia IS NOT NULL OR id_grupo IS NOT NULL)
);
CREATE INDEX idx_carrera_materia_carrera ON carrera_materia (id_carrera);
CREATE INDEX idx_carrera_materia_materia ON carrera_materia (id_materia);

CREATE TABLE correlativa (
    id_carrera             TEXT NOT NULL REFERENCES carrera(id) ON DELETE CASCADE,
    id_materia             TEXT NOT NULL REFERENCES materia(id) ON DELETE CASCADE,
    id_materia_correlativa TEXT NOT NULL REFERENCES materia(id) ON DELETE CASCADE,
    PRIMARY KEY (id_carrera, id_materia, id_materia_correlativa)
);

-- ----------------------------------------------------------------------------
-- Docentes
-- ----------------------------------------------------------------------------

CREATE TABLE docente (
    id_docente       TEXT PRIMARY KEY,   -- slug generado en el ETL (a partir de nombre_completo)
    nombre           TEXT NOT NULL,
    apellido         TEXT NOT NULL,
    nombre_completo  TEXT NOT NULL,
    mail             TEXT UNIQUE
);

CREATE TABLE docente_departamento (
    id_docente      TEXT REFERENCES docente(id_docente) ON DELETE CASCADE,
    id_departamento TEXT REFERENCES departamento(id_departamento) ON DELETE CASCADE,
    PRIMARY KEY (id_docente, id_departamento)
);

-- ----------------------------------------------------------------------------
-- Dictado: comisiones, bloques horarios, aulas de cada bloque
-- ----------------------------------------------------------------------------

CREATE TABLE comision (
    id_comision  SERIAL PRIMARY KEY,
    id_materia   TEXT NOT NULL REFERENCES materia(id),
    id_cuatri    TEXT NOT NULL REFERENCES cuatrimestre(id_cuatri),
    comision     TEXT NOT NULL,   -- slug tal cual viene en el JSON (ej. "algebra_1_manana_tp1")
    turno        TEXT,
    UNIQUE (id_materia, comision, id_cuatri)
);
CREATE INDEX idx_comision_materia_cuatri ON comision (id_materia, id_cuatri);

CREATE TABLE comision_docente (
    id_comision INT  REFERENCES comision(id_comision) ON DELETE CASCADE,
    id_docente  TEXT REFERENCES docente(id_docente),
    rol         TEXT NOT NULL,   -- Prof. / JTP / Ay1 / Ay2 / etc.
    PRIMARY KEY (id_comision, id_docente, rol)
);

-- Una fila por cada elemento del array "horarios" de una comision: un dia y
-- un rango horario concretos. NO tiene columna de aula (ver bloque_horario_aula).
CREATE TABLE bloque_horario (
    id_bloque    SERIAL PRIMARY KEY,
    id_comision  INT  NOT NULL REFERENCES comision(id_comision) ON DELETE CASCADE,
    id_dia       TEXT NOT NULL REFERENCES dia(id_dia),
    hora_inicio  TIME NOT NULL,
    hora_fin     TIME NOT NULL,
    turno        TEXT,
    tipo         TEXT,           -- Teorica / Practica / TP1 / etc. (texto libre por ahora)
    CHECK (hora_inicio < hora_fin)
);
CREATE INDEX idx_bloque_comision ON bloque_horario (id_comision);
CREATE INDEX idx_bloque_dia ON bloque_horario (id_dia);

-- Tabla puente N:M: el array "aulas" de cada horario. 0, 1 o varias aulas
-- por bloque (ver justificacion en el doc de diseño).
CREATE TABLE bloque_horario_aula (
    id_bloque INT  REFERENCES bloque_horario(id_bloque) ON DELETE CASCADE,
    id_aula   TEXT REFERENCES aula(id_aula),
    PRIMARY KEY (id_bloque, id_aula)
);
CREATE INDEX idx_bloque_aula_aula ON bloque_horario_aula (id_aula);

-- ----------------------------------------------------------------------------
-- Fechas importantes
-- ----------------------------------------------------------------------------

CREATE TABLE evento (
    id_evento    SERIAL PRIMARY KEY,
    titulo       TEXT NOT NULL,
    descripcion  TEXT,
    fecha_ini    DATE,
    fecha_fin    DATE,
    tipo         TEXT,
    id_cuatri    TEXT REFERENCES cuatrimestre(id_cuatri),
    CHECK (fecha_fin IS NULL OR fecha_fin >= fecha_ini)
);
CREATE INDEX idx_evento_cuatri ON evento (id_cuatri);
CREATE INDEX idx_evento_fechas ON evento (fecha_ini, fecha_fin);

-- ----------------------------------------------------------------------------
-- Pendiente para mas adelante (no bloquea la carga inicial):
--   EXCLUDE USING gist en base a (id_aula, id_dia, tsrange(hora_inicio,hora_fin))
--   para que Postgres impida solapar dos comisiones en la misma aula/horario.
--   Requiere resolver bloque_horario_aula + bloque_horario en una vista o
--   columnas generadas; lo conviene dejar para cuando ya este la carga andando.
-- ----------------------------------------------------------------------------
