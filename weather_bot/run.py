#!/usr/bin/env python3
"""Run the trading bot backend server."""
import os
import uvicorn
from backend.models.database import init_db

if __name__ == "__main__":
    print("Initializing database...")
    init_db()

    port = int(os.environ.get("PORT", 8000))
    print(f"Starting server on http://0.0.0.0:{port}")
    print(f"API docs available at http://localhost:{port}/docs")

    # Reload only for local dev — Fly/Railway set FLY_APP_NAME / RAILWAY_ENVIRONMENT
    is_production = bool(
        os.environ.get("FLY_APP_NAME") or os.environ.get("RAILWAY_ENVIRONMENT")
    )
    uvicorn.run(
        "backend.api.main:app",
        host="0.0.0.0",
        port=port,
        reload=not is_production,
    )
