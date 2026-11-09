"""The controlled implementation: the real service backed by an in-memory provider.

CI runs the shared conformance suite against it on every pull request, without
network access or provider credentials (`server.py`).
"""
