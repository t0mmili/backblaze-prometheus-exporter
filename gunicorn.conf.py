"""Gunicorn configuration. Auto-loaded from the working directory."""

def on_starting(server):
    """Refuse to start with more than one worker."""
    if server.cfg.workers > 1:
        raise RuntimeError(
            f"workers={server.cfg.worksers} is not supported: each worker "
            "starts its own collector thread. Run with --workers=1 and use "
            "--threads to handle concurrent scrapes."
        )
