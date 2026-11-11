"""从项目根目录运行：uv run python main.py。"""
import uvicorn


if __name__ == "__main__":
    uvicorn.run("media_ops.api:app", host="127.0.0.1", port=8000, reload=True)
