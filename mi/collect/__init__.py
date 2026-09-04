"""Collector: holt Rohdaten aus RSS, HTML-Uebersichtsseiten und News-Queries."""

from .runner import CollectStats, collect

__all__ = ["collect", "CollectStats"]
