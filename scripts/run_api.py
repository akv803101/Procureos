"""Dev launcher: run the FastAPI app on the port the preview harness assigns
via $PORT (falling back to 8000 for a plain `python -m scripts.run_api`)."""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("api.main:app", host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))
