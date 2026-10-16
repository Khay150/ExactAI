#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Embeddings del RAG de eXACTai.

Por defecto usa fastembed (corre local, sin API key, sin costo) con el modelo
multilingue chico `minishlab/potion-multilingual-128M` (256 dimensiones, ~0,5 GB).
Se cambia con variables de entorno, sin tocar codigo:

    EXACTAI_EMB_MODEL   modelo de fastembed. Ejemplos:
                          minishlab/potion-multilingual-128M                      (256 dim, liviano, default)
                          sentence-transformers/paraphrase-multilingual-mpnet-base-v2   (768 dim, corta a 128 tokens)
                          intfloat/multilingual-e5-large                          (1024 dim, mejor calidad, ~2 GB de RAM)
    EXACTAI_MODEL_DIR   donde se guardan los modelos descargados (default: rag/modelos)
    EXACTAI_EMB=fake    embedder de PRUEBA (hash de palabras, sin descargar nada). No entiende
                        significado: sirve solo para probar el flujo, nunca para uso real.

Si cambias de modelo, la dimension cambia: corre `python rag/indexar.py --recrear` para
rehacer la tabla y volver a calcular todos los embeddings.
"""
import hashlib
import math
import os
import re
import unicodedata
from pathlib import Path

RAG_DIR = Path(__file__).resolve().parent
MODELO_DEFECTO = "minishlab/potion-multilingual-128M"


def _sin_acentos(s):
    s = unicodedata.normalize("NFD", str(s))
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _normalizar(vec):
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / n for x in vec]


def a_pgvector(vec):
    """Lista de floats -> literal de pgvector ('[0.1,0.2,...]'), para usar con %s::vector."""
    return "[" + ",".join(f"{x:.7g}" for x in vec) + "]"


class FastEmbedEmbedder:
    def __init__(self, modelo, cache_dir):
        try:
            from fastembed import TextEmbedding
        except ImportError:
            raise RuntimeError("Falta fastembed. Instalalo con: pip install fastembed")
        soportados = TextEmbedding.list_supported_models()
        info = next((m for m in soportados if m["model"].lower() == modelo.lower()), None)
        if info is None:
            nombres = ", ".join(sorted(m["model"] for m in soportados if "multiling" in m["model"].lower()))
            raise RuntimeError(f"fastembed no soporta el modelo {modelo!r}. Multilingues disponibles: {nombres}")
        self.nombre = info["model"]
        self.dim = int(info["dim"])
        self._e5 = "e5" in self.nombre.lower()   # los e5 necesitan prefijos "query: " / "passage: "
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        self._m = TextEmbedding(model_name=self.nombre, cache_dir=str(cache_dir))

    def _correr(self, textos):
        return [_normalizar([float(x) for x in v]) for v in self._m.embed(textos)]

    def docs(self, textos):
        if self._e5:
            textos = ["passage: " + t for t in textos]
        return self._correr(list(textos))

    def consulta(self, texto):
        return self._correr([("query: " + texto) if self._e5 else texto])[0]


class FakeEmbedder:
    """Solo para probar el flujo sin descargar un modelo. NO entiende significado."""
    nombre = "fake-hash-256"
    dim = 256

    def _vec(self, texto):
        v = [0.0] * self.dim
        for tok in re.findall(r"[a-z0-9]+", _sin_acentos(texto).lower()):
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            v[h % self.dim] += 1.0 if (h >> 8) & 1 else -1.0
        return _normalizar(v)

    def docs(self, textos):
        return [self._vec(t) for t in textos]

    def consulta(self, texto):
        return self._vec(texto)


_CACHE = {}


def obtener_embedder():
    """Devuelve el embedder configurado (se crea una sola vez por proceso)."""
    if os.environ.get("EXACTAI_EMB", "").strip().lower() == "fake":
        clave = ("fake",)
        if clave not in _CACHE:
            _CACHE[clave] = FakeEmbedder()
        return _CACHE[clave]
    modelo = os.environ.get("EXACTAI_EMB_MODEL", "").strip() or MODELO_DEFECTO
    cache = Path(os.environ.get("EXACTAI_MODEL_DIR", "").strip() or (RAG_DIR / "modelos"))
    clave = (modelo, str(cache))
    if clave not in _CACHE:
        _CACHE[clave] = FastEmbedEmbedder(modelo, cache)
    return _CACHE[clave]
