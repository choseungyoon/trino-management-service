"""JSON API route modules, one per feature.

Registered from `api/main.py`. Split out because the frontend rewrite adds
roughly 40 endpoints to the 14 that were there, and one file holding all of
them stops being readable long before that.

Each module exposes `register(app, deps)`. `deps` carries what every route
needs - the services, and the dependency that resolves the session cookie into
a Principal.

Python 3.9 compatible.
"""
