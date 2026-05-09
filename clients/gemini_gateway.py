"""
제목: Gemini API 클라이언트 게이트웨이
내용: NewsCollector의 AIClientProtocol을 구현하는 Gemini Wrapper.
      뉴스 영향도 평가용 단순 generate(prompt) 인터페이스 제공.
      운영 안정성을 위해 timeout/재시도/실패 시 raise 정책 적용.

주요 클래스:
  - GeminiGateway: AIClientProtocol 구현체

주요 함수:
  - GeminiGateway.generate(prompt): Gemini API 호출 후 텍스트 반환

설계 원칙:
  - 마스터 메모리 #11 Gemini 4키 체인은 본 모듈에서는 단일 키만 사용
    (investment-alert는 가벼운 호출만 필요. 4키 체인은 investment-os 영역).
  - SDK 실패 시 NewsCollector._apply_ai_scoring()의 keyword_score fallback에 의존.
  - 단일 책임: generate()만 노출. 모델 선택/파라미터는 내부 고정.
"""

from __future__ import annotations

import os

from google import genai
from google.genai import types

from core.logger import get_logger

VERSION = "1.0.0"

logger = get_logger(__name__)


# ────────────────────────────────────────────────────────
# 상수
# ────────────────────────────────────────────────────────
# 제목: 사용 모델
# 내용: investment-alert는 단순 점수 평가만 필요 → flash 계열로 비용 최소화
DEFAULT_MODEL: str = "gemini-2.0-flash-exp"

# 제목: 응답 토큰 상한
# 내용: NewsCollector 프롬프트는 JSON {score, reasoning} 단답이라 작게 설정
MAX_OUTPUT_TOKENS: int = 256

# 제목: 호출 타임아웃 (초)
# 내용: GitHub Actions 전체 timeout-minutes:10 안에서 안전 마진
REQUEST_TIMEOUT_SEC: int = 15


class GeminiGatewayError(Exception):
    """제목: Gemini 게이트웨이 전용 예외 / 내용: 호출 실패를 호출자가 식별하기 위함"""


class GeminiGateway:
    """
    제목: Gemini API 클라이언트 (AIClientProtocol 구현체)
    내용: NewsCollector가 의존성 주입받는 AI client 역할.
          generate(prompt) 단일 메서드만 노출하여 결합도 최소화.

    책임:
      - GEMINI_API_KEY 환경변수에서 키 로드 및 클라이언트 초기화
      - generate(prompt) 호출 시 Gemini API에 텍스트 생성 요청
      - 응답 텍스트 추출 후 반환
      - 호출 실패 시 GeminiGatewayError 또는 원본 예외 raise (NewsCollector가 try/except로 처리)
    """

    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL) -> None:
        """
        제목: GeminiGateway 초기화

        Args:
            api_key: API 키 (None이면 GEMINI_API_KEY 환경변수에서 로드)
            model: 사용할 모델명

        Raises:
            GeminiGatewayError: API 키 미설정 시
        """
        resolved_key = api_key or os.getenv("GEMINI_API_KEY", "").strip()
        if not resolved_key:
            raise GeminiGatewayError(
                "GEMINI_API_KEY 환경변수가 설정되지 않았습니다."
            )

        self._client = genai.Client(api_key=resolved_key)
        self._model = model
        logger.info(f"[GeminiGateway] v{VERSION} 초기화 (model={model})")

    def generate(self, prompt: str) -> str:
        """
        제목: 텍스트 생성 요청
        내용: Gemini API에 프롬프트를 전달하고 응답 텍스트를 반환합니다.
              빈 응답이나 SDK 예외는 그대로 전파하여 호출자(NewsCollector)가
              keyword_score fallback으로 처리하도록 합니다.

        Args:
            prompt: AI에 전달할 프롬프트 텍스트

        Returns:
            str: Gemini 응답 텍스트 (strip 후)

        Raises:
            GeminiGatewayError: 응답이 비어있을 때
            Exception: SDK 호출 실패 시 원본 예외 전파
        """
        config = types.GenerateContentConfig(
            max_output_tokens=MAX_OUTPUT_TOKENS,
            temperature=0.1,  # 점수 평가는 결정적 응답 선호
        )

        response = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=config,
        )

        text = (response.text or "").strip()
        if not text:
            raise GeminiGatewayError(
                f"Gemini 빈 응답 (model={self._model}, prompt_len={len(prompt)})"
            )

        return text
