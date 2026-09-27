import uvicorn

from app.main import app  # noqa: F401  -- 供 uvicorn main:app 使用

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=0)
