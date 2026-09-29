"""RepoMind API. Run: uvicorn app.main:app --reload --port 8000  (from the backend/ folder)."""
from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import get_settings
from .memory import HindsightMemoryProvider, MemoryProviderError
from .models import EditRuleRequest, FeedbackRequest, RestoreRequest, RetireRequest, ReviewRequest, SupersedeRequest, TeachRequest
from .service import Service
from .store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(hindsight: HindsightMemoryProvider | None = None) -> FastAPI:
    settings = get_settings()
    svc = Service(settings, Store(settings.data_dir / "state.json"), hindsight)
    app = FastAPI(title="RepoMind API", version="1.0.0")
    app.state.svc = svc
    app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin, "http://127.0.0.1:5173"],
                       allow_methods=["*"], allow_headers=["*"])

    def err(status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(x) for x in first.get("loc", [])[1:])
        return err(422, "validation_error", f"{loc}: {first.get('msg', 'invalid input')}".strip(": "))

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return err(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(MemoryProviderError)
    async def _mem(_: Request, exc: MemoryProviderError) -> JSONResponse:
        return err(502, "memory_unavailable", str(exc))

    @app.exception_handler(Exception)
    async def _any(_: Request, exc: Exception) -> JSONResponse:
        logging.getLogger("repomind").error("Unhandled error: %s", type(exc).__name__)
        return err(500, "internal_error", "Something went wrong on the server.")

    @app.get("/api/health")
    async def health():
        return await svc.health()

    @app.post("/api/review")
    async def review(req: ReviewRequest):
        return await svc.review(req)

    @app.post("/api/compare")
    async def compare(req: ReviewRequest):
        return await svc.compare(req)

    @app.get("/api/impact")
    async def impact():
        return await svc.impact()

    @app.get("/api/playbook")
    async def playbook():
        return await svc.playbook()

    @app.post("/api/teach")
    async def teach(req: TeachRequest):
        return await svc.teach(req)

    @app.get("/api/memories")
    async def memories(q: str = "", category: str = "", status: str = "active"):
        """status: active (default) | inactive (retired or replaced) | all"""
        return await svc.memories(q[:200], category[:40], status if status in ("active", "inactive", "all") else "active")

    async def _lifecycle(call):
        try:
            return await call
        except KeyError:
            raise HTTPException(404, "Rule not found")
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.patch("/api/memories/{mem_id}")
    async def edit_memory(mem_id: str, req: EditRuleRequest):
        return await _lifecycle(svc.edit_memory(mem_id, req))

    @app.post("/api/memories/{mem_id}/retire")
    async def retire_memory(mem_id: str, req: RetireRequest):
        return await _lifecycle(svc.retire_memory(mem_id, req))

    @app.post("/api/memories/{mem_id}/restore")
    async def restore_memory(mem_id: str, req: RestoreRequest):
        return await _lifecycle(svc.restore_memory(mem_id, req))

    @app.post("/api/memories/{mem_id}/supersede")
    async def supersede_memory(mem_id: str, req: SupersedeRequest):
        return await _lifecycle(svc.supersede_memory(mem_id, req))

    @app.delete("/api/memories/{mem_id}")
    async def delete_memory(mem_id: str, actor: str = ""):
        return await _lifecycle(svc.delete_memory(mem_id, actor[:80]))

    @app.post("/api/seed")
    async def seed():
        return await svc.seed()

    @app.get("/api/history")
    async def history():
        return {"reviews": svc.history()}

    @app.get("/api/history/{review_id}")
    async def history_detail(review_id: str):
        r = svc.review_detail(review_id)
        if not r:
            raise HTTPException(404, "Review not found")
        return r

    @app.post("/api/feedback")
    async def feedback(req: FeedbackRequest):
        try:
            return await svc.feedback(req)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0]).capitalize())
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.get("/api/analytics")
    async def analytics():
        return svc.analytics()

    @app.get("/api/repository-dna")
    async def dna():
        return await svc.dna()

    @app.post("/api/reset-demo")
    async def reset_demo():
        """Clears LOCAL demo state only (history, analytics, demo memory). Never touches Hindsight."""
        svc.store.reset()
        return {"success": True, "message": "Local demo state cleared. Hindsight memory was not modified."}

    return app


app = create_app()
