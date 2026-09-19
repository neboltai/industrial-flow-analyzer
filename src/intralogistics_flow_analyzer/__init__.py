"""Intralogistics Flow & Slotting Analyzer — Community Edition.

Pickwege sichtbar machen. Lagerplätze datenbasiert verbessern.
Der Algorithmus rechnet. Die KI ordnet ein.

A local, dependency-free analysis engine that turns structured warehouse exports
into reproducible route metrics, spaghetti diagrams and constraint-safe slotting
simulations. It is not a WMS, it does not manage stock, and it never evaluates a
person.
"""

from __future__ import annotations

from .models import ENGINE_VERSION, SCHEMA_VERSION, Dataset, Issue

__version__ = ENGINE_VERSION

__all__ = [
    "Dataset",
    "Issue",
    "ENGINE_VERSION",
    "SCHEMA_VERSION",
    "__version__",
]
