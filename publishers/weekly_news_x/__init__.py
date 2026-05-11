"""
제목: 주말 미국 뉴스 X 스레드 발행 패키지
내용: 매주 토요일 09:00 KST에 미국 주요뉴스를 Claude API로 수집하고,
      마스터 검수(PR Merge) 후 X 스레드로 발행한다.

      기존 publishers/x_publisher.py (단일 트윗)와 별개의 신규 모듈.
      스레드 체이닝(in_reply_to_tweet_id)을 함수형으로 구현.

주요 모듈:
  - collect: 뉴스 수집 + archive .md 생성
  - publish: tweepy 스레드 발행 (count_x_chars 포함)
  - comic_voice: (옵션) 코믹 캐릭터 한줄평 추가
  - notion_sync: (옵션) Notion DB 적재
"""
from __future__ import annotations

VERSION = "1.0.0"
