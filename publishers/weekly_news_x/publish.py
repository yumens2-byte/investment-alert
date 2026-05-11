"""
제목: 주말 미국 뉴스 X 스레드 발행 모듈
내용: 마크다운(archive .md)을 '---' 구분자로 분할하여 X 스레드를 발행한다.
      기존 publishers/x_publisher.py(단일 트윗)와 별개로 함수형 구현.

      DRY_RUN=true 환경에서는 실제 발행 없이 시뮬레이션 로그만 출력.
      tweepy 4.14.0의 create_tweet API와 in_reply_to_tweet_id 체이닝을 사용한다.

      재발행 방지:
        - 발행 성공 시 archive 옆에 '.meta.json' sidecar 파일 생성
        - 다음 발행 시 sidecar 존재하면 자동 skip (exit 0)
        - FORCE_REPUBLISH=true 환경변수로 강제 재발행 가능

      Telegram 알림 (notifier.py 통합):
        - 발행 성공/실패 시 INTERNAL 운영자 채널로 자동 알림
        - TELEGRAM_BOT_TOKEN/INTERNAL_CHANNEL_ID 미설정 시 graceful skip
        - 알림 실패가 X 발행 자체를 막지 않음

주요 함수:
  - find_latest_archive(): logs/weekly_news/ 하위 최신 .md 탐색
  - parse_thread(md_text): '---' 구분 청크 리스트 반환
  - count_x_chars(text): X 공식 글자수 정책 반영 카운트
  - validate_tweets(tweets): 280자 초과 시 ValueError
  - get_x_client(): tweepy.Client 인증
  - upload_header_image(): (옵션) DALL-E 헤더 이미지 업로드
  - post_thread(client, tweets, header_media_id): 스레드 체이닝 발행
  - sidecar_path(archive_path): sidecar JSON 경로 계산
  - is_already_published(archive_path): sidecar 존재 여부 검사
  - write_sidecar(archive_path, posted_ids, screen_name, ...): sidecar 작성
  - main(): 위 단계 일괄 실행 + Telegram 알림
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import tweepy

from config.settings import get_env_bool
from core.logger import get_logger

VERSION = "1.2.0"  # notifier 통합 (Telegram INTERNAL 알림)

logger = get_logger(__name__)

# ────────────────────────────────────────────────────────
# 모듈 상수
# ────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
REPO_ROOT = _HERE.parent.parent
ARCHIVE_ROOT = REPO_ROOT / "logs" / "weekly_news"
IMAGE_DIR = ARCHIVE_ROOT / "images"
TWEET_LIMIT = 280
SIDECAR_SUFFIX = ".meta.json"
SIDECAR_VERSION = "1.0.0"


def find_latest_archive() -> Path:
    """
    제목: 최신 archive .md 탐색
    내용: logs/weekly_news/ 하위에서 mtime 가장 최신인 .md 반환.

    Returns:
        Path: 최신 마크다운 파일

    Raises:
        FileNotFoundError: archive 디렉토리가 비어있을 때
    """
    candidates = sorted(
        ARCHIVE_ROOT.rglob("*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(f"archive .md 없음 — {ARCHIVE_ROOT}")
    return candidates[0]


def parse_thread(md_text: str) -> list[str]:
    """
    제목: 마크다운을 스레드 청크 리스트로 분할
    내용: '\\n---\\n' 구분자로 split, 빈 청크 제거, 양쪽 공백 trim.

    Args:
        md_text: archive .md 본문

    Returns:
        list[str]: 트윗 청크 리스트
    """
    chunks = [c.strip() for c in md_text.split("\n---\n")]
    chunks = [c for c in chunks if c]
    return chunks


def count_x_chars(text: str) -> int:
    """
    제목: X 공식 글자수 정책 반영 카운트
    내용: X는 한글/CJK/일본어/이모지를 2자로 카운트한다.
          국가깃발(regional indicator 2개), ZWJ 시퀀스, variation selector(U+FE0F)를
          안전 측면에서 보수적으로 카운트한다.

    Args:
        text: 검증 대상 텍스트

    Returns:
        int: X 카운트 글자수
    """
    count = 0
    i = 0
    text_len = len(text)

    while i < text_len:
        ch = text[i]
        cp = ord(ch)

        cluster_end = i + 1
        # ZWJ(U+200D) + 다음 문자 흡수
        while (
            cluster_end < text_len
            and text_len > cluster_end + 1
            and ord(text[cluster_end]) == 0x200D
        ):
            cluster_end += 2
        # variation selector(U+FE0F) 흡수
        while cluster_end < text_len and ord(text[cluster_end]) == 0xFE0F:
            cluster_end += 1

        if (
            "\uac00" <= ch <= "\ud7af"      # Hangul syllables
            or "\u4e00" <= ch <= "\u9fff"   # CJK Unified Ideographs
            or "\u3040" <= ch <= "\u30ff"   # Hiragana + Katakana
        ):
            count += 2
        elif (
            0x1F300 <= cp <= 0x1FAFF        # Misc Symbols / Emoticons / Transport / Supplemental
            or 0x2600 <= cp <= 0x27BF       # Misc Symbols + Dingbats
            or 0x1F1E6 <= cp <= 0x1F1FF     # Regional Indicator
        ):
            count += 2
        else:
            count += 1

        i = cluster_end

    return count


def validate_tweets(tweets: list[str]) -> list[str]:
    """
    제목: 트윗 길이 검증
    내용: 각 청크가 280자 이하인지 검사. 초과 시 ValueError 발생.

    Args:
        tweets: 청크 리스트

    Returns:
        list[str]: 입력 그대로 (통과 시)

    Raises:
        ValueError: 한 개 이상 초과 시
    """
    errors = []
    for i, t in enumerate(tweets, 1):
        n = count_x_chars(t)
        if n > TWEET_LIMIT:
            errors.append(f"  - tweet #{i}: {n} chars (limit {TWEET_LIMIT})")
    if errors:
        msg = "Tweet length exceeded:\n" + "\n".join(errors)
        raise ValueError(msg)
    return tweets


def get_x_client() -> tweepy.Client:
    """
    제목: X API tweepy.Client 생성
    내용: OAuth 1.0a User Context로 인증.
          기존 alert.yml과 동일한 시크릿명을 사용한다.

    Returns:
        tweepy.Client: 인증된 클라이언트
    """
    return tweepy.Client(
        consumer_key=os.environ["X_API_KEY"],
        consumer_secret=os.environ["X_API_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"],
        access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
    )


def get_x_api_v1() -> tweepy.API:
    """
    제목: tweepy v1.1 API (이미지 업로드 전용)
    내용: media/upload 엔드포인트는 v2에 없으므로 v1.1을 별도 사용.

    Returns:
        tweepy.API: v1.1 인증 객체
    """
    auth = tweepy.OAuth1UserHandler(
        os.environ["X_API_KEY"],
        os.environ["X_API_SECRET"],
        os.environ["X_ACCESS_TOKEN"],
        os.environ["X_ACCESS_TOKEN_SECRET"],
    )
    return tweepy.API(auth)


def upload_header_image() -> str | None:
    """
    제목: (옵션) 헤더 이미지 업로드
    내용: ATTACH_IMAGE=true 일 때만 동작. image_gen 모듈로 생성 후 v1.1 업로드.
          openai 패키지/키 미설정 시 graceful skip.

    Returns:
        str | None: media_id_string 또는 None
    """
    if not get_env_bool("ATTACH_IMAGE", default=False):
        return None

    try:
        from publishers.weekly_news_x.image_gen import generate_header_image
    except ImportError:
        logger.warning("[publish] image_gen 임포트 불가 — 이미지 첨부 skip")
        return None

    latest_md = sorted(ARCHIVE_ROOT.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not latest_md:
        return None
    summary_ctx = latest_md[0].read_text(encoding="utf-8").split("---")[0][:300]

    from datetime import datetime
    from zoneinfo import ZoneInfo
    today_str = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
    img_path = IMAGE_DIR / f"{today_str}.png"

    saved = generate_header_image(summary_ctx, img_path)
    if not saved:
        return None

    try:
        api_v1 = get_x_api_v1()
        media = api_v1.media_upload(filename=str(saved))
        logger.info(f"[publish] 이미지 업로드 완료 media_id={media.media_id}")
        return media.media_id_string
    except Exception as e:
        logger.error(f"[publish] 이미지 업로드 실패: {type(e).__name__}: {e}")
        return None


def sidecar_path(archive_path: Path) -> Path:
    """
    제목: archive .md에 대응하는 sidecar 경로 계산
    내용: '/path/to/X.md' → '/path/to/X.md.meta.json'

    Args:
        archive_path: archive 마크다운 경로

    Returns:
        Path: sidecar JSON 경로
    """
    return archive_path.with_suffix(archive_path.suffix + SIDECAR_SUFFIX)


def is_already_published(archive_path: Path) -> bool:
    """
    제목: 재발행 방지 체크
    내용: sidecar 파일이 이미 존재하면 True. 단순 존재 여부만 검사
          (sha256 등 내용 비교는 미수행 — 설계 결정).

    Args:
        archive_path: archive .md 경로

    Returns:
        bool: 이미 발행됨 여부
    """
    return sidecar_path(archive_path).exists()


def write_sidecar(
    archive_path: Path,
    posted_ids: list[str],
    screen_name: str,
    force_republished: bool = False,
) -> Path:
    """
    제목: 발행 결과 sidecar JSON 작성
    내용: archive 옆에 '.meta.json' 형식으로 저장.
          force_republished=True이면 기존 sidecar가 'previous' 필드에 보존됨.

    Args:
        archive_path: archive .md 경로
        posted_ids: 발행된 tweet_id 리스트
        screen_name: X 핸들 (URL 조립용)
        force_republished: 강제 재발행 여부

    Returns:
        Path: 작성된 sidecar 경로
    """
    sc_path = sidecar_path(archive_path)
    now_kst = datetime.now(ZoneInfo("Asia/Seoul")).isoformat()

    thread_url = (
        f"https://x.com/{screen_name}/status/{posted_ids[0]}" if posted_ids else ""
    )
    tweets_meta = [
        {"index": i, "tweet_id": tid}
        for i, tid in enumerate(posted_ids, start=1)
    ]

    payload: dict = {
        "version": SIDECAR_VERSION,
        "archive_path": str(archive_path.relative_to(REPO_ROOT))
        if _is_inside(archive_path, REPO_ROOT)
        else str(archive_path),
        "published_at_kst": now_kst,
        "thread_url": thread_url,
        "tweet_count": len(posted_ids),
        "tweets": tweets_meta,
        "status": "republished" if force_republished else "published",
        "force_republished": force_republished,
    }

    # 기존 sidecar 보존 (force_republished 시)
    if force_republished and sc_path.exists():
        try:
            prev = json.loads(sc_path.read_text(encoding="utf-8"))
            payload["previous"] = prev
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[publish] 기존 sidecar 파싱 실패 — previous 미포함: {e}")

    sc_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"[publish] sidecar 작성 완료 → {sc_path}")
    return sc_path


def _is_inside(target: Path, root: Path) -> bool:
    """
    제목: target이 root 하위인지 검사
    내용: relative_to 예외 회피용 헬퍼.

    Args:
        target: 검사 대상 경로
        root: 기준 디렉토리

    Returns:
        bool: target이 root 하위면 True
    """
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def post_thread(
    client: tweepy.Client,
    tweets: list[str],
    header_media_id: str | None = None,
) -> list[str]:
    """
    제목: 스레드 체이닝 발행
    내용: 각 청크를 순서대로 발행하며 in_reply_to_tweet_id로 체이닝.
          첫 트윗에만 media_ids 첨부 가능.

    Args:
        client: tweepy.Client
        tweets: 청크 리스트
        header_media_id: (옵션) 첫 트윗에 첨부할 이미지 media_id

    Returns:
        list[str]: 발행된 tweet_id 리스트 (순서 보존)
    """
    posted_ids: list[str] = []
    prev_id: str | None = None
    for i, text in enumerate(tweets, 1):
        kwargs: dict = {"text": text}
        if prev_id is not None:
            kwargs["in_reply_to_tweet_id"] = prev_id
        if i == 1 and header_media_id:
            kwargs["media_ids"] = [header_media_id]
        response = client.create_tweet(**kwargs)
        tweet_id = str(response.data["id"])
        posted_ids.append(tweet_id)
        prev_id = tweet_id
        logger.info(f"[publish] #{i}/{len(tweets)} 발행 완료 tweet_id={tweet_id}")
    return posted_ids


def main() -> int:
    """
    제목: 발행 파이프라인 엔트리포인트
    내용: archive .md 로드 → 재발행 방지 검사 → 청크 분할 → 길이 검증
          → 스레드 발행 → sidecar 작성 → Telegram 알림 → URL 기록.

          재발행 방지:
            - sidecar 존재 + FORCE_REPUBLISH != true → exit 0 (skip, 알림 없음)
            - sidecar 존재 + FORCE_REPUBLISH = true → 발행 진행, previous 보존
            - sidecar 미존재 → 정상 발행

          Telegram 알림 (notifier 통합):
            - 발행 성공 시 → notify_success
            - 실패 시 (exit 1/2) → notify_failure with stage 식별자

    Returns:
        int: 0 성공/skip, 1 입력 오류, 2 길이 초과
    """
    # 지연 import (테스트에서 mock 용이)
    from publishers.weekly_news_x.notifier import notify_failure, notify_success

    archive_path_env = os.environ.get("ARCHIVE_PATH")
    if archive_path_env:
        md_path = REPO_ROOT / archive_path_env
        if not md_path.exists():
            logger.error(f"[publish] ARCHIVE_PATH 파일 없음: {md_path}")
            notify_failure(
                archive_name=archive_path_env,
                stage="archive_not_found",
                exit_code=1,
                error_msg=f"ARCHIVE_PATH 파일 없음: {md_path}",
            )
            return 1
    else:
        try:
            md_path = find_latest_archive()
        except FileNotFoundError as e:
            logger.error(f"[publish] {e}")
            notify_failure(
                archive_name="unknown",
                stage="archive_not_found",
                exit_code=1,
                error_msg=str(e),
            )
            return 1

    logger.info(f"[publish] v{VERSION} 시작 — source={md_path}")

    # ── 재발행 방지 게이트 ──
    force_republish = get_env_bool("FORCE_REPUBLISH", default=False)
    if is_already_published(md_path):
        if not force_republish:
            logger.info(
                f"[publish] 이미 발행됨 (sidecar 존재) — skip. "
                f"sidecar={sidecar_path(md_path)}. "
                "강제 재발행 시 FORCE_REPUBLISH=true 환경변수 설정."
            )
            # skip은 알림 안 함 (소음 방지 — 운영 결정 사항)
            return 0
        logger.warning(
            f"[publish] FORCE_REPUBLISH=true — 기존 sidecar 보존 후 재발행 진행. "
            f"archive={md_path}"
        )

    md_text = md_path.read_text(encoding="utf-8")
    tweets = parse_thread(md_text)
    logger.info(f"[publish] {len(tweets)}개 청크 파싱 완료")

    try:
        validate_tweets(tweets)
    except ValueError as e:
        logger.error(f"[publish] {e}")
        notify_failure(
            archive_name=md_path.name,
            stage="validation",
            exit_code=2,
            error_msg=str(e),
        )
        return 2

    if get_env_bool("DRY_RUN", default=True):
        logger.info("[publish] DRY_RUN=true — 발행 시뮬레이션만 수행 (sidecar/알림 미작성)")
        for i, t in enumerate(tweets, 1):
            logger.info(f"[publish] [DRY] #{i} ({count_x_chars(t)}자): {t[:60]}...")
        return 0

    # ── 실발행 단계 (tweepy 예외 발생 가능) ──
    try:
        client = get_x_client()
        header_media_id = upload_header_image()
        posted_ids = post_thread(client, tweets, header_media_id=header_media_id)
    except Exception as e:
        logger.error(f"[publish] X 발행 실패: {type(e).__name__}: {e}")
        notify_failure(
            archive_name=md_path.name,
            stage="tweepy_publish",
            exit_code=1,
            error_msg=f"{type(e).__name__}: {e}",
        )
        return 1

    # ── sidecar 작성 ──
    screen_name = os.environ.get("X_SCREEN_NAME", "i")
    try:
        sc_path = write_sidecar(
            archive_path=md_path,
            posted_ids=posted_ids,
            screen_name=screen_name,
            force_republished=force_republish,
        )
    except Exception as e:
        # sidecar 작성 실패해도 X 발행은 이미 완료된 상태 → 운영 알림 후 정상 종료
        logger.error(f"[publish] sidecar 작성 실패: {type(e).__name__}: {e}")
        notify_failure(
            archive_name=md_path.name,
            stage="sidecar_write",
            exit_code=0,  # X 발행 자체는 성공이므로 0
            error_msg=(
                f"{type(e).__name__}: {e}\n"
                f"⚠️ X 발행은 완료됨. 첫 트윗 ID={posted_ids[0] if posted_ids else 'N/A'}"
            ),
        )
        return 0

    # ── 성공 알림 + GITHUB_OUTPUT 기록 ──
    first_url = f"https://x.com/{screen_name}/status/{posted_ids[0]}" if posted_ids else ""
    try:
        sc_rel = sc_path.relative_to(REPO_ROOT)
    except ValueError:
        sc_rel = sc_path

    notify_success(
        archive_name=md_path.name,
        thread_url=first_url,
        tweet_count=len(posted_ids),
        sidecar_path=str(sc_rel),
        force_republished=force_republish,
    )

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output and posted_ids:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"thread_url={first_url}\n")
            f.write(f"tweet_count={len(posted_ids)}\n")
            f.write(f"sidecar_path={sc_rel}\n")
        logger.info(f"[publish] thread_url={first_url}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
