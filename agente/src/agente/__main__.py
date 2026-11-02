import os

import uvicorn

from .a2a import app_do_ambiente

if __name__ == "__main__":
    uvicorn.run(
        app_do_ambiente(),
        host=os.environ.get("AGENTE_HOST", "127.0.0.1"),
        port=int(os.environ.get("AGENTE_PORT", 7300)),
        log_level="warning",
    )
