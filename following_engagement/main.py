from __future__ import annotations

import logging
import os
from pathlib import Path

from .clients import OpenAiAnalyzer, XTimelineClient, XWriteClient
from .config import FollowingConfig
from .execution import (
    DryRunActionExecutor,
    ExecutionModeRouter,
    LiveActionExecutor,
    LiveSafetyGuard,
    ShadowActionExecutor,
)
from .models import ExecutionMode
from .service import FollowingService
from .state import StateRepository


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s"
    )
    config = FollowingConfig.from_env()
    if not config.enabled:
        logging.info("FOLLOWING_ENGAGEMENT disabled (FOLLOWING_ENABLED=false)")
        return 0
    required = {
        "X_USER_ID": config.user_id,
        "X_API_KEY": config.x_api_key,
        "X_API_SECRET": config.x_api_secret,
        "X_ACCESS_TOKEN": config.access_token,
        "X_ACCESS_TOKEN_SECRET": config.access_token_secret,
        "OPENAI_API_KEY": config.openai_api_key,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Required configuration missing: {', '.join(missing)}")
    state = StateRepository(config.state_path)
    live = None
    if config.execution_mode == ExecutionMode.LIVE:
        guard = LiveSafetyGuard(config, state)
        live = LiveActionExecutor(state, guard, XWriteClient())
    router = ExecutionModeRouter(
        config.execution_mode, DryRunActionExecutor(), ShadowActionExecutor(state), live
    )
    summary = FollowingService(
        config,
        XTimelineClient(
            config.x_api_key,
            config.x_api_secret,
            config.access_token,
            config.access_token_secret,
        ),
        OpenAiAnalyzer(config.openai_api_key, config.openai_model),
        state,
        router,
    ).run()
    markdown = summary.markdown()
    print(markdown)
    if path := os.getenv("GITHUB_STEP_SUMMARY"):
        with Path(path).open("a", encoding="utf-8") as stream:
            stream.write(markdown + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
