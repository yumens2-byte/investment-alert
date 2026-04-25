"""
제목: 프로젝트 공통 로거 설정
내용: 전역에서 일관된 포맷으로 로그를 출력하도록 설정하는 유틸리티.
      모듈명과 타임스탬프가 포함된 구조화된 로그를 생성합니다.

주요 함수:
  - get_logger(name): 지정된 이름의 로거 반환 (싱글톤 패턴)
  - configure_root_logger(): 루트 로거 전역 설정 (앱 시작 시 1회)
"""

from __future__ import annotations

import logging
import os
import sys

VERSION = "1.0.0"

# ────────────────────────────────────────────────────────
# 포맷 상수
# ────────────────────────────────────────────────────────
_LOG_FORMAT = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 전역 설정 완료 플래그 (중복 설정 방지)
_root_configured = False


def configure_root_logger(level: str | None = None) -> None:
    """
    제목: 루트 로거 초기 설정
    내용: 앱 시작 시 1회만 호출하여 전역 로그 포맷/레벨을 설정합니다.
          환경변수 LOG_LEVEL이 설정되어 있으면 우선 적용.

    처리 플로우:
      1. 중복 설정 방지 (_root_configured 체크)
      2. 로그 레벨 결정 (인자 > 환경변수 > INFO)
      3. 스트림 핸들러 구성 (stdout)
      4. 포맷터 적용
      5. 루트 로거에 연결

    Args:
        level: 강제 로그 레벨 ("DEBUG"/"INFO"/"WARNING"/"ERROR")
               None이면 환경변수 LOG_LEVEL 또는 INFO 사용
    """
    global _root_configured
    if _root_configured:
        return

    # 제목: 로그 레벨 결정
    # 내용: 명시적 인자 > 환경변수 > 기본값 INFO 순으로 우선순위
    effective_level = level or os.getenv("LOG_LEVEL", "INFO")
    log_level = getattr(logging, effective_level.upper(), logging.INFO)

    # 제목: 핸들러 구성
    # 내용: stdout 출력 + 구조화 포맷
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))

    # 제목: 루트 로거 설정
    # 내용: 기존 핸들러 제거 후 신규 핸들러 부착 (pytest 등의 환경 간섭 방지)
    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers.clear()
    root.addHandler(handler)

    _root_configured = True


def get_logger(name: str) -> logging.Logger:
    """
    제목: 모듈별 로거 반환
    내용: 호출 모듈용 logging.Logger 인스턴스를 반환합니다.
          루트 로거가 설정되지 않았으면 자동 설정합니다.

    처리 플로우:
      1. 루트 로거 초기화 보장
      2. 지정된 이름으로 로거 반환

    Args:
        name: 로거 이름 (일반적으로 __name__ 사용)

    Returns:
        logging.Logger: 설정된 로거 인스턴스
    """
    # 제목: 루트 로거 초기화 보장
    # 내용: get_logger만 호출해도 동작하도록 lazy init
    if not _root_configured:
        configure_root_logger()

    return logging.getLogger(name)
