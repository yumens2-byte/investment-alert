"""
제목: investment-alert API 진단 스크립트
내용: 모든 외부 API(뉴스 RSS, YouTube RSS, Supabase)의
      응답 상태와 실제 수집 값을 상세 출력합니다.
      run_alert.py와 별도로 실행하여 API 연동 상태를 점검합니다.

실행: python run_diagnostics.py
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import feedparser
import requests

VERSION = "1.0.0"

# ────────────────────────────────────────────────────────
# 출력 헬퍼
# ────────────────────────────────────────────────────────
_DIV = "─" * 70
_DIV_BOLD = "═" * 70

def _h(title: str) -> None:
    print(f"\n{_DIV_BOLD}")
    print(f"  {title}")
    print(_DIV_BOLD)

def _ok(msg: str) -> None:
    print(f"  ✅ {msg}")

def _warn(msg: str) -> None:
    print(f"  ⚠️  {msg}")

def _err(msg: str) -> None:
    print(f"  ❌ {msg}")

def _row(label: str, value: object) -> None:
    print(f"  {label:<30} {value}")


# ────────────────────────────────────────────────────────
# 1. 뉴스 RSS 소스별 점검
# ────────────────────────────────────────────────────────
# 변경 후 (settings.py 참조)
def check_news_rss():
    _h("2. 뉴스 RSS 소스 점검")
    from config.settings import NEWS_SOURCE_REGISTRY
    sources = {}
    for tier, tier_sources in NEWS_SOURCE_REGISTRY.items():
        for name, cfg in tier_sources.items():
            sources[f"{name} (Tier {tier})"] = cfg["url"]

    for name, url in sources.items():
        print(f"\n  [{name}]")
        _row("URL", url)
        try:
            feed = feedparser.parse(url)
            status = getattr(feed, "status", "N/A")
            entry_count = len(feed.entries)
            feed_title = feed.feed.get("title", "N/A") if feed.feed else "N/A"

            _row("HTTP 상태", status)
            _row("피드 제목", feed_title)
            _row("총 항목 수", f"{entry_count}건")

            if entry_count > 0:
                latest = feed.entries[0]
                _ok(f"최신 항목: {latest.get('title', 'N/A')[:60]}")
                _row("  발행일", latest.get("published", "N/A"))
                _row("  URL", latest.get("link", "N/A")[:70])
            else:
                _warn("항목 0건 — 피드가 비어있거나 접근 불가")

            if status == 200:
                _ok("접속 정상")
            elif status == 403:
                _err("403 Forbidden — 이 환경에서 차단됨 (GitHub Actions에서는 정상일 수 있음)")
            elif status == 404:
                _err("404 Not Found — URL 오류")
            else:
                _warn(f"상태코드: {status}")

        except Exception as e:
            _err(f"예외 발생: {type(e).__name__}: {e}")


# ────────────────────────────────────────────────────────
# 2. YouTube RSS 채널별 점검
# ────────────────────────────────────────────────────────
def check_youtube_rss() -> None:
    _h("📺 YouTube RSS 채널 점검")

    raw = os.getenv("YOUTUBE_CHANNELS", "")
    if not raw:
        _err("YOUTUBE_CHANNELS 환경변수 미설정")
        return

    channels = []
    for pair in raw.split(","):
        pair = pair.strip()
        if ":" in pair:
            name, ch_id = pair.split(":", 1)
            channels.append((name.strip(), ch_id.strip()))

    _row("등록 채널 수", f"{len(channels)}개")

    for name, ch_id in channels:
        print(f"\n  [{name}]")
        _row("채널 ID", ch_id)
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={ch_id}"
        try:
            feed = feedparser.parse(url)
            status = getattr(feed, "status", "N/A")
            count = len(feed.entries)
            feed_title = feed.feed.get("title", "N/A") if feed.feed else "N/A"

            _row("HTTP 상태", status)
            _row("채널명 (RSS)", feed_title)
            _row("영상 수", f"{count}건")

            if count > 0:
                latest = feed.entries[0]
                pub = latest.get("published", "N/A")
                _ok(f"최신 영상: {latest.get('title', 'N/A')[:60]}")
                _row("  발행일", pub)
            else:
                _warn("영상 0건 — 채널 ID 오류 또는 최근 영상 없음")

        except Exception as e:
            _err(f"예외 발생: {type(e).__name__}: {e}")


# ────────────────────────────────────────────────────────
# 3. Supabase 연동 점검
# ────────────────────────────────────────────────────────
def check_supabase() -> None:
    _h("🗄️  Supabase 연동 점검")

    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_KEY", "")

    _row("SUPABASE_URL", url if url else "❌ 미설정")
    _row("SUPABASE_KEY", f"{'설정됨 (' + key[:8] + '...)' if key else '❌ 미설정'}")

    if not url or not key:
        _err("환경변수 미설정 — 점검 불가")
        return

    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    # 테이블별 REST API 직접 호출
    tables = ["ia_alert_history", "ia_cooldown_state"]
    for table in tables:
        print(f"\n  [{table}]")
        endpoint = f"{url}/rest/v1/{table}?select=*&limit=3&order=id.desc"
        try:
            resp = requests.get(endpoint, headers=headers, timeout=10)
            _row("HTTP 상태", resp.status_code)

            if resp.status_code == 200:
                data = resp.json()
                _ok(f"접속 정상 — 최근 {len(data)}건 조회")
                for i, row in enumerate(data, 1):
                    if table == "ia_alert_history":
                        _row(f"  [{i}] alert_id", str(row.get("alert_id", ""))[:16])
                        _row("      level", row.get("level", ""))
                        _row("      score", row.get("score", ""))
                        _row("      created_at", row.get("created_at", ""))
                    elif table == "ia_cooldown_state":
                        _row(f"  [{i}] level", row.get("level", ""))
                        _row("      cooldown_until", row.get("cooldown_until", ""))
            elif resp.status_code == 404:
                _err("404 — PGRST125: 테이블 접근 불가")
                _err(f"응답: {resp.text[:200]}")
                _warn("SUPABASE_URL 끝에 '/' 또는 '/rest/v1' 포함 여부 확인 필요")
            elif resp.status_code == 401:
                _err("401 — SUPABASE_KEY 인증 오류")
            else:
                _err(f"오류 응답 {resp.status_code}: {resp.text[:200]}")

        except Exception as e:
            _err(f"예외 발생: {type(e).__name__}: {e}")


# ────────────────────────────────────────────────────────
# 4. 환경변수 전체 점검
# ────────────────────────────────────────────────────────
def check_env_vars() -> None:
    _h("🔑 환경변수 설정 현황")

    required = {
        "SUPABASE_URL": "Supabase 프로젝트 URL",
        "SUPABASE_KEY": "Supabase anon key",
        "YOUTUBE_CHANNELS": "YouTube 채널 목록",
        "TELEGRAM_BOT_TOKEN": "Telegram Bot 토큰",
        "TELEGRAM_FREE_CHANNEL_ID": "TG 무료 채널 ID",
        "TELEGRAM_PAID_CHANNEL_ID": "TG 유료 채널 ID",
        "X_API_KEY": "X API Key",
        "X_API_SECRET": "X API Secret",
        "X_ACCESS_TOKEN": "X Access Token",
        "X_ACCESS_TOKEN_SECRET": "X Access Token Secret",
        "DRY_RUN": "DRY_RUN 모드",
    }

    all_ok = True
    for key, desc in required.items():
        val = os.getenv(key, "")
        if val:
            masked = val[:6] + "..." if len(val) > 6 else val
            _ok(f"{key:<35} {masked}  ({desc})")
        else:
            _err(f"{key:<35} 미설정  ({desc})")
            all_ok = False

    print()
    if all_ok:
        _ok("모든 필수 환경변수 설정 완료")
    else:
        _warn("일부 환경변수 미설정 — 해당 기능 동작 불가")


# ────────────────────────────────────────────────────────
# 메인
# ────────────────────────────────────────────────────────
def main() -> None:
    print(_DIV_BOLD)
    print(f"  investment-alert 진단 스크립트 v{VERSION}")
    print(f"  실행 시각: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(_DIV_BOLD)

    check_env_vars()
    check_news_rss()
    check_youtube_rss()
    check_supabase()

    print(f"\n{_DIV_BOLD}")
    print("  진단 완료")
    print(_DIV_BOLD)


if __name__ == "__main__":
    main()
