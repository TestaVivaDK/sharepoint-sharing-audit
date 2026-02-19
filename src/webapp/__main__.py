"""Webapp entry point: python -m webapp"""

import uvicorn
from webapp.app import create_app

app = create_app()

if __name__ == "__main__":
    uvicorn.run("webapp.__main__:app", host="0.0.0.0", port=8000, reload=True)
