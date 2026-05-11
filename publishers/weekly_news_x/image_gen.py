"""
제목: 헤더 이미지 생성 모듈 (옵션)
내용: OpenAI DALL-E 3로 첫 트윗 첨부용 헤더 이미지를 생성한다.
      openai 패키지 미설치 또는 OPENAI_API_KEY 미설정 시 graceful skip.

주요 함수:
  - generate_header_image(brief_summary, out_path): 이미지 생성 후 파일 저장
"""
from __future__ import annotations

import base64
from pathlib import Path

from core.logger import get_logger

VERSION = "1.0.0"

logger = get_logger(__name__)


def generate_header_image(brief_summary: str, out_path: Path) -> Path | None:
    """
    제목: DALL-E 3 헤더 이미지 생성
    내용: 1024x1024 standard quality 이미지를 생성하여 out_path에 PNG로 저장.

    Args:
        brief_summary: 뉴스 요약 텍스트 (앞 500자만 사용)
        out_path: 저장 경로

    Returns:
        Path | None: 저장 경로 또는 실패 시 None
    """
    try:
        from openai import OpenAI  # type: ignore[import]
    except ImportError:
        logger.warning("[image_gen] openai 패키지 미설치 — skip")
        return None

    import os
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.warning("[image_gen] OPENAI_API_KEY 미설정 — skip")
        return None

    client = OpenAI(api_key=api_key)
    prompt = (
        "A clean, minimalist editorial illustration for a US financial news brief. "
        "Modern flat design, financial chart elements, US flag accents subtle. "
        "Bold typography area for headline. No text in image. "
        f"Theme context: {brief_summary[:500]}"
    )

    try:
        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
            quality="standard",
            n=1,
            response_format="b64_json",
        )
        image_data = base64.b64decode(response.data[0].b64_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(image_data)
        logger.info(f"[image_gen] 저장 완료 → {out_path}")
        return out_path
    except Exception as e:
        logger.error(f"[image_gen] 실패: {type(e).__name__}: {e}")
        return None
