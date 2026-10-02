"""Model registry: the backend's own record of models, providers and where they run.

Outside this package only `app/main.py` (for the API router), the migration
env and the registry scripts import it, so the old model catalog keeps working
unchanged.
"""
