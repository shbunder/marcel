"""Marcel's capabilities — one self-contained package per capability.

The tree mirrors the composition (ADR-260718-0cf8e8): each subpackage owns
one capability's classes, stores, and config, composes with the others only
through :mod:`marcel_core.composition`, and never imports a sibling. The
roadmap features (FEAT-260718-*) land their capability here as they port it.
"""
