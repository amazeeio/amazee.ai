"""Model registry: the backend's own record of models, providers and where they run.

Nothing outside this package imports it, except the migration env and the
startup script, so the old model catalog keeps working unchanged.
"""
