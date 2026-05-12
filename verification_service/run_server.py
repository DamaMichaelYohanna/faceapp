"""
Launch the Biometric Verification Service.
Usage: python run_server.py
"""
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
os.chdir(_here)
if _here not in sys.path:
    sys.path.insert(0, _here)

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "api.main:app",
        host="127.0.0.1",
        port=8002,
        reload=True,
        reload_dirs=[_here],
    )
