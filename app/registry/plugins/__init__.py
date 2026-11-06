"""Source plugins: one file per external source under `parsers/`.

Each plugin fetches its own source and returns data in one shared format;
`runner.py` validates it and writes it to the registry tables.
"""
