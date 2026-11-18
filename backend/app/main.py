import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import settings
from app.database import Base, engine
import app.models  # noqa: F401  (register models on metadata)
from app.routers import auth as auth_router
from app.routers import judge as judge_router


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
    wait_for_db()
    Base.metadata.create_all(bind=engine)
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


@app.get("/api/health")
def health() -> dict[str, bool]:
    return {"ok": True}
