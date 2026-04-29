"""
Launch uvicorn from the faceapp directory so all relative imports resolve correctly.
Usage: python run_server.py
"""
import os
import sys

# Ensure working directory and sys.path point to faceapp root
_here = os.path.dirname(os.path.abspath(__file__))
os.chdir(_here)
if _here not in sys.path:
    sys.path.insert(0, _here)

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "api.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        reload_dirs=[_here],
    )
