from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .db import Store
from .models import Decision, TaskInput
from .streaming import stream_updates


def create_app(store=None, settings=None):
    settings = settings or Settings()
    store = store or Store(settings.database_url)

    @asynccontextmanager
    async def lifespan(app):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        yield

    app = FastAPI(title="AI 自媒体运营工作台", lifespan=lifespan)

    @app.exception_handler(LookupError)
    async def not_found(request, exc):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def invalid_operation(request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok"}

    @app.post("/api/v1/tasks", status_code=202)
    def create_task(brief: TaskInput):
        return store.create(brief)

    @app.get("/api/v1/tasks")
    def list_tasks():
        return store.list()

    @app.get("/api/v1/tasks/{task_id}")
    def get_task(task_id: str):
        task = store.get(task_id)
        if task is None:
            raise HTTPException(404, "任务不存在")
        return task

    @app.get("/api/v1/tasks/{task_id}/history")
    def history(task_id: str):
        get_task(task_id)
        return store.history(task_id)

    @app.get("/api/v1/tasks/{task_id}/metrics")
    def metrics(task_id: str):
        get_task(task_id)
        return {"nodes": store.node_runs(task_id)}

    @app.get("/api/v1/tasks/{task_id}/stream")
    def stream(task_id: str):
        get_task(task_id)
        return StreamingResponse(stream_updates(store, task_id), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/v1/tasks/{task_id}/decisions", status_code=202)
    def decide(task_id: str, decision: Decision):
        store.decide(task_id, decision)
        return {"status": "queued"}

    @app.post("/api/v1/tasks/{task_id}/retry", status_code=202)
    def retry(task_id: str):
        store.retry(task_id)
        return {"status": "queued"}

    app.mount("/assets", StaticFiles(directory=settings.data_dir, check_dir=False), name="assets")
    return app


app = create_app()
