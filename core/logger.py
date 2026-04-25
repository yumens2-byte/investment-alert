"""
제목: 프로젝트 공통 로거 설정
내용: 전역에서 일관된 포맷으로 로그를 출력하도록 설정하는 유틸리티.
      log_file 파라미터 지정 시 콘솔 + 파일 동시 출력합니다.

주요 함수:
  - get_logger(name): 지정된 이름의 로거 반환 (싱글톤 패턴)
  - configure_root_logger(): 루트 로거 전역 설정 (앱 시작 시 1회)
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

VERSION = "1.1.0"

_LOG_FORMAT = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_root_configured = False


def configure_root_logger(
    level: str | None = None,
    log_file: str | None = None,
) -> None:
    """
    제목: 루트 로거 초기 설정
    내용: 콘솔 핸들러는 최초 1회만 설정합니다.
          파일 핸들러는 log_file이 지정될 때마다 추가합니다.
          import 단계에서 _root_configured가 True가 되어도
          log_file 지정 시 파일 핸들러는 항상 정상 추가됩니다.

    처리 플로우:
      1. 콘솔 핸들러: _root_configured=False일 때만 1회 추가
      2. 파일 핸들러: log_file 지정 시 항상 추가 (_root_configured 무관)

    Args:
        level: 강제 로그 레벨 ("DEBUG"/"INFO"/"WARNING"/"ERROR")
        log_file: 로그 파일 경로 (None이면 콘솔만 출력)
    """
    global _root_configured

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)
    root = logging.getLogger()

    # 제목: 콘솔 핸들러 — 최초 1회만 설정
    if not _root_configured:
        effective_level = level or os.getenv("LOG_LEVEL", "INFO")
        log_level = getattr(logging, effective_level.upper(), logging.INFO)

        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)

        root.setLevel(log_level)
        root.handlers.clear()
        root.addHandler(stream_handler)
        _root_configured = True

    # 제목: 파일 핸들러 — log_file 지정 시 항상 추가
    # 내용: import 순서와 무관하게 main()에서 호출 시 반드시 추가됨
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    """
    제목: 모듈별 로거 반환

    Args:
        name: 로거 이름 (일반적으로 __name__ 사용)

    Returns:
        logging.Logger: 설정된 로거 인스턴스
    """
    if not _root_configured:
        configure_root_logger()

    return logging.getLogger(name)
