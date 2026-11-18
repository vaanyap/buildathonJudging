import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import settings
from app.database import Base, SessionLocal, engine
import app.models  # noqa: F401  (register models on metadata)
from app.seed import seed_if_empty
from app.routers import auth as auth_router
from app.routers import judge as judge_router
from app.routers import organizer as organizer_router
from app.auth import require_jwt_secret
from app.services.domain import DomainError


def wait_for_db() -> None:
    last_error: Exception | None = None
    for _ in range(30):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except Exception as exc:  # database may still be starting in compose
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"Database did not become ready: {last_error}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    require_jwt_secret()
    wait_for_db()
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE evaluations ADD COLUMN IF NOT EXISTS revision integer NOT NULL DEFAULT 1"))
    if settings.seed_on_start:
        db = SessionLocal()
        try:
            seed_if_empty(db)
        finally:
            db.close()
    yield


app = FastAPI(title="Tech@NYU Buildathon Judging", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router.router, prefix="/api")
app.include_router(judge_router.router, prefix="/api")
app.include_router(organizer_router.router, prefix="/api")


@app.exception_handler(DomainError)
async def domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.get("/api/health")
def health() -> dict[str, bool]:
    return {"ok": True}
