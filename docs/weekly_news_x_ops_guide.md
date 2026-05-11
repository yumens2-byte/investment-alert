# weekly_news_x 운영 모니터링 가이드

> **작성일**: 2026-05-11
> **대상 모듈**: `publishers/weekly_news_x/` v1.1.0
> **대상 워크플로우**: `weekly_news_draft.yml`, `weekly_news_publish.yml`

---

## 1. 시스템 컴포넌트 맵

```
┌─────────────────────────────────────────────────────────────────┐
│ GitHub Actions (cron + PR merge)                                │
├─────────────────────────────────────────────────────────────────┤
│  weekly_news_draft.yml         weekly_news_publish.yml          │
│  (토 09:00 KST)                (PR merge 시)                     │
│  ├─ collect.py                 ├─ publish.py (재발행 게이트)      │
│  ├─ comic_voice.py(opt)        ├─ image_gen.py(opt)             │
│  ├─ notion_sync.py(opt)        ├─ notion_sync.py(opt)           │
│  └─ create-pull-request        ├─ sidecar 자동 commit           │
│                                └─ Step Summary                  │
│                                                                 │
│  weekly_news_draft_sunday.yml                                   │
│  (일 09:00 KST)                                                  │
│  └─ 위와 동일, branch만 'weekly-news-sun/{date}'                 │
└─────────────────────────────────────────────────────────────────┘
       ↓                              ↓
[logs/weekly_news/...md]      [X 스레드 + sidecar.meta.json]
[Notion: Draft]               [Notion: Published]
```

> 토/일 두 draft 워크플로우는 **독립 실행**되며 동일한 publish 워크플로우를 공유.
> 일요일 비활성화 원할 시: `weekly_news_draft_sunday.yml` 파일명 끝에 `.disabled` 접미사 추가.

---

## 2. 실패 패턴 카탈로그

### 2-1. Draft 단계 (`weekly_news_draft.yml`)

#### 2-1-1. `collect.py` exit code 1 — ANTHROPIC_API_KEY 미설정

| 항목 | 내용 |
|---|---|
| **증상** | Step "Collect news"에서 즉시 실패 |
| **로그 키워드** | `[collect] ANTHROPIC_API_KEY 미설정` |
| **원인** | GitHub Secret 미등록 또는 오타 |
| **조치** | `Settings → Secrets → Actions`에서 `ANTHROPIC_API_KEY` 등록 확인 |
| **빈도** | 초기 설정 시 1회 가능 |

#### 2-1-2. `collect.py` exit code 1 — Claude API 호출 실패

| 항목 | 내용 |
|---|---|
| **증상** | Step "Collect news"에서 1~2분 후 실패 |
| **로그 키워드** | `[collect] Claude API 호출 실패: {ExceptionType}: {message}` |
| **원인** | (a) API rate limit / (b) 네트워크 일시 장애 / (c) 모델명 변경 / (d) 잔액 부족 |
| **조치** | (a) Actions → Re-run failed jobs / (b)~(c) console.anthropic.com에서 사용량·청구·모델 확인 |
| **빈도** | 추정 월 1회 미만 |

#### 2-1-3. `collect.py` exit code 1 — 응답 텍스트 비어있음

| 항목 | 내용 |
|---|---|
| **증상** | API는 성공했으나 텍스트 추출 결과 없음 |
| **로그 키워드** | `[collect] 응답 텍스트 비어있음` |
| **원인** | Claude가 도구 호출만 하고 최종 답변을 안 했거나, max_tokens 부족 |
| **조치** | `publishers/weekly_news_x/collect.py`의 `MAX_TOKENS` 증가 (현재 4096) |
| **빈도** | 드묾. 발생 시 코드 튜닝 필요 |

#### 2-1-4. peter-evans/create-pull-request 실패

| 항목 | 내용 |
|---|---|
| **증상** | PR 생성 단계에서 "GitHub Actions is not permitted to create or approve PRs" |
| **로그 키워드** | `Pull request creation failed` |
| **원인** | 저장소 권한 미설정 |
| **조치** | `Settings → Actions → General → Workflow permissions` → "Allow GitHub Actions to create and approve pull requests" 체크 |
| **빈도** | 초기 설정 시 1회 |

#### 2-1-5. (옵션) `comic_voice.py` 생성 실패

| 항목 | 내용 |
|---|---|
| **증상** | "Append comic character voice" step이 warning 후 진행 (workflow는 PASS) |
| **로그 키워드** | `[comic_voice] 생성 실패 — archive 무변경` |
| **원인** | Claude API 일시 장애 |
| **조치** | 무시 가능. archive 본문은 정상이며 한줄평만 누락. PR 본문에 수동 추가 가능 |
| **빈도** | 추정 월 1~2회 |
| **참고** | `continue-on-error: true`로 설정되어 있어 워크플로우 전체 실패는 발생 안 함 |

#### 2-1-6. (옵션) `notion_sync.py` 실패

| 항목 | 내용 |
|---|---|
| **증상** | "Sync draft to Notion" step warning |
| **로그 키워드** | `[notion_sync] 실패: {ExceptionType}` |
| **원인** | (a) Notion Integration이 DB에 미연결 / (b) DB 속성명 불일치 / (c) Notion API rate limit |
| **조치** | Notion에서 해당 DB → 우상단 ⋯ → Connections → Integration 추가 확인. 속성명은 README의 6개 정확히 일치해야 함 |
| **빈도** | 초기 설정 시 |

---

### 2-2. Publish 단계 (`weekly_news_publish.yml`)

#### 2-2-1. `publish.py` exit code 0 (skip) — 이미 발행됨

| 항목 | 내용 |
|---|---|
| **증상** | "Publish thread to X" step PASS하지만 발행되지 않음. Thread URL 비어있음 |
| **로그 키워드** | `[publish] 이미 발행됨 (sidecar 존재) — skip` |
| **원인** | 같은 archive에 대해 publish 워크플로우가 두 번 트리거됨 (예: revert→re-merge) |
| **조치** | 의도된 동작이라면 무시. 재발행 필요 시 `Settings → Variables → FORCE_REPUBLISH=true` 설정 후 PR 재머지 |
| **빈도** | 드묾. 실수 방지 안전장치로 동작 |

#### 2-2-2. `publish.py` exit code 1 — archive 파일 없음

| 항목 | 내용 |
|---|---|
| **증상** | Step 즉시 실패 |
| **로그 키워드** | `[publish] ARCHIVE_PATH 파일 없음` 또는 `archive .md 없음` |
| **원인** | (a) Merge 후 archive .md가 삭제됨 / (b) PR이 archive 외 다른 파일만 포함 |
| **조치** | PR 변경 내용 재확인. archive 파일이 main에 포함되어야 함 |
| **빈도** | 사용자 실수 시 |

#### 2-2-3. `publish.py` exit code 2 — Tweet length exceeded

| 항목 | 내용 |
|---|---|
| **증상** | 길이 검증에서 실패. 발행은 시도되지 않음 |
| **로그 키워드** | `Tweet length exceeded:\n  - tweet #{N}: {chars} chars (limit 280)` |
| **원인** | PR 검수 중 본문 수정으로 청크 한도(X 카운트 280자) 초과 |
| **조치** | 1) revert PR / 2) 새 PR로 본문 수정 → 글자수 줄이기 → 머지 |
| **빈도** | 마스터 수정 빈도에 비례 |

#### 2-2-4. `tweepy.errors.Forbidden` (HTTP 403)

| 항목 | 내용 |
|---|---|
| **증상** | post_thread 호출 시 403 |
| **로그 키워드** | `tweepy.errors.Forbidden` |
| **원인** | (a) App permissions가 Read only / (b) 토큰 무효화 / (c) X 계정 제재 |
| **조치** | developer.x.com → App settings → User authentication settings에서 Read+Write 확인 후 Access Token 재발급 → GitHub Secret 갱신 |
| **빈도** | 토큰 재발급 후 미갱신 시 |

#### 2-2-5. `tweepy.errors.TooManyRequests` (HTTP 429)

| 항목 | 내용 |
|---|---|
| **증상** | post_thread 도중 429 |
| **로그 키워드** | `TooManyRequests` |
| **원인** | X API 한도 초과 |
| **조치** | Basic 등급 기준 월 50K → 한도 충분. 다른 워크플로우(alert.yml)와 합산 사용량 확인. 필요 시 Actions → Re-run failed jobs (15분 후) |
| **빈도** | 마스터 Basic 등급 기준 거의 없음 |

#### 2-2-6. sidecar git commit 실패

| 항목 | 내용 |
|---|---|
| **증상** | "Commit sidecar metadata" step에서 git push 실패 |
| **로그 키워드** | `non-fast-forward` 또는 `Permission denied` |
| **원인** | (a) main 브랜치 보호 규칙 / (b) GITHUB_TOKEN write 권한 없음 |
| **조치** | (a) Branch protection에서 GitHub Actions bot 예외 추가 / (b) workflow의 `permissions: contents: write` 확인 |
| **영향** | sidecar 파일은 생성되었으나 git에 commit 안 됨. 다음 실행 시 재발행 방지 작동 안 함 |
| **빈도** | 초기 설정 시 |

#### 2-2-7. (옵션) `image_gen.py` 실패

| 항목 | 내용 |
|---|---|
| **증상** | 이미지 첨부 없이 텍스트만 발행 |
| **로그 키워드** | `[image_gen] 실패: {ExceptionType}` |
| **원인** | OpenAI API 장애 / 잔액 부족 / 콘텐츠 정책 위반 |
| **조치** | 텍스트만 발행으로 graceful degradation. 무시 가능 |
| **빈도** | 추정 월 1회 미만 |

---

### 2-3. 시스템 외 실패

#### 2-3-1. cron 실행 누락

| 항목 | 내용 |
|---|---|
| **증상** | 토요일 09:00 KST 지나도 PR 생성 안 됨 |
| **원인** | GitHub Actions의 cron은 베스트 에포트 (지연 가능, 가끔 누락) |
| **조치** | Actions → "Weekly News Draft" → Run workflow로 수동 트리거 |
| **빈도** | 추정 분기당 1회 |

#### 2-3-2. PR이 자동 머지되지 않음 (의도된 동작)

| 항목 | 내용 |
|---|---|
| **증상** | PR 생성됐는데 X 발행 안 됨 |
| **원인** | **마스터 검수 게이트 정상 동작** — PR Merge 전엔 발행 안 함 |
| **조치** | PR 검수 후 Merge 클릭 |

---

## 3. 로그 확인 가이드

### 3-1. GitHub Actions 로그

```
저장소 → Actions → Weekly News Draft (또는 Publish)
   → 최근 run 클릭 → 각 job → 각 step 펼치기
```

**핵심 로그 키워드:**

| 단계 | 정상 키워드 | 실패 키워드 |
|---|---|---|
| collect | `[collect] 저장 완료: ...md` | `[collect] ... 미설정`, `[collect] Claude API 호출 실패` |
| publish | `[publish] #{N}/{total} 발행 완료 tweet_id=` | `Tweet length exceeded`, `Forbidden`, `TooManyRequests` |
| publish skip | `[publish] 이미 발행됨 (sidecar 존재) — skip` | - |
| sidecar | `[publish] sidecar 작성 완료` | `[publish] 기존 sidecar 파싱 실패` |
| sidecar commit | `✅ sidecar committed:` | `non-fast-forward`, `Permission denied` |

### 3-2. Step Summary (GitHub UI 우상단 패널)

publish 워크플로우 PASS 시 다음이 자동 표시됨:
```
### Weekly News Publish 결과
- Thread URL: https://x.com/{handle}/status/...
- Tweet count: 8
- Sidecar: logs/weekly_news/2026/05/2026-05-09-saturday.md.meta.json
```

### 3-3. Artifact 다운로드

`weekly_news_draft.yml`은 `logs/weekly_news/` 전체를 artifact로 업로드:
```
저장소 → Actions → Weekly News Draft → 해당 run → Artifacts
   → "weekly-news-draft-{number}" 다운로드 (14일 보관)
```

### 3-4. sidecar JSON 직접 확인

```bash
git log --oneline -- logs/weekly_news/
git show HEAD -- logs/weekly_news/2026/05/2026-05-09-saturday.md.meta.json
```

또는 GitHub UI에서 `logs/weekly_news/` 디렉토리 탐색.

### 3-5. Notion (옵션 활성 시)

설정한 Notion DB에서 다음 필터:
- `Status: Draft` — 발행 전
- `Status: Published` — 발행 완료 (Tweet URL 포함)
- 빈 결과 = sync 실패 (워크플로우 로그 확인)

---

## 4. 비용 추적

### 4-1. 추정 월 비용 (현재 설정 기준 — 토/일 발행)

> 모든 가격은 2026-05 기준 추정. 변동 가능하므로 각 서비스 콘솔에서 실측 권장.

| 항목 | 단가 (추정) | 월 사용량 | 월 비용 |
|---|---|---|---|
| **Claude Sonnet 4.5** (입력) | $3 / 1M tokens | ~5K × 8회 = 40K | ~$0.12 |
| **Claude Sonnet 4.5** (출력) | $15 / 1M tokens | ~2K × 8회 = 16K | ~$0.24 |
| **web_search 도구** | 호출당 추가 비용 | 8회 × 8 = 64회 | ~$0.60 (추정) |
| **X API Basic** | $200/월 (마스터 보유) | 8 posts × 8회 = 64 (한도 50K) | $200 (기존) |
| **GitHub Actions** (Public) | $0 | 약 6분 × 8회 = 48분 | $0 |
| **(옵션) DALL-E 3 standard** | $0.04/이미지 | 8회 | $0.32 |
| **(옵션) Notion API** | 무료 | - | $0 |
| **신규 모듈 추가 비용** | - | - | **~$1.0 ~ $1.5/월** |

> **참고**: 토요일만 운영 시 위 표의 절반 수준. 일요일 비활성화 방법은 §1 컴포넌트 맵 하단 참조.

### 4-2. 비용 모니터링 위치

| 서비스 | URL | 확인 항목 |
|---|---|---|
| Anthropic | console.anthropic.com/settings/billing | 월 사용량, 잔액, alert 설정 |
| X Developer | developer.x.com/portal/dashboard | API 사용량, 한도 |
| OpenAI (옵션) | platform.openai.com/usage | DALL-E 호출 수 |
| GitHub Actions | 저장소 → Settings → Billing | Public repo는 항상 $0 |

### 4-3. 비용 안전장치

- **Claude API**: console.anthropic.com에서 monthly spend limit 설정 권장
- **X API**: Basic 등급 50K 한도 → alert.yml(45분 cron 1회=2880회/월) + weekly_news(8회/월) = 약 2890회. **여유 매우 큼**
- **DALL-E**: `Settings → Variables → ATTACH_IMAGE`를 미설정하면 비용 0

### 4-4. 비용 급증 트리거

| 시나리오 | 영향 |
|---|---|
| FORCE_REPUBLISH 반복 사용 | Claude API 비용 증가 없음 (publish.py만 호출, collect.py 재실행 아님) |
| collect.py 실패 재시도 | 1회당 ~$0.5 추가 |
| max_uses(web_search) 상향 | 검색당 비용 ↑ |
| 모델을 Opus로 변경 | 5~10배 비용 |

---

## 5. 정기 점검 체크리스트

### 5-1. 주간 (매주 토·일 발행 후)

**토요일 09:30 KST 기준:**
- [ ] 토요일 draft PR 자동 생성 확인
- [ ] PR 본문 팩트체크 → Merge
- [ ] publish.yml Step Summary에서 Thread URL 확인
- [ ] X 스레드 가시성 확인

**일요일 09:30 KST 기준:**
- [ ] 일요일 draft PR 자동 생성 확인 (branch명 `weekly-news-sun/...`)
- [ ] 토요일 발행 이후 24h 변동분 위주로 내용 변경되었는지 확인
- [ ] PR 본문 팩트체크 → Merge
- [ ] publish.yml Step Summary에서 Thread URL 확인
- [ ] sidecar `.meta.json`이 main에 commit되었는지 확인 (토/일 각각)

### 5-2. 월간 (월말)

- [ ] console.anthropic.com에서 월 사용량 확인 (예상 $1~$1.5)
- [ ] developer.x.com에서 API 호출 수 확인 (Basic 한도 대비)
- [ ] `logs/weekly_news/` 누적 파일 8개(토/일 × 4주) 확인
- [ ] 실패한 워크플로우 run 없는지 Actions 탭 점검 (draft / draft_sunday / publish 3개)
- [ ] (옵션 사용 시) Notion DB의 Status=Failed 항목 확인

### 5-3. 분기 (3개월)

- [ ] 프롬프트 v3 품질 평가 — 최근 13주 archive 검토
- [ ] 비용 합계 vs 예산 비교
- [ ] X 부캐 계정 활성도 추적 (조회수, 팔로워 변화)
- [ ] 사용 안 한 옵션 모듈 정리 검토

---

## 6. 알람 메커니즘

### 6-1. Telegram INTERNAL 알림 (현재 구현)

발행 결과를 운영자 전용 Telegram 채널(`TELEGRAM_INTERNAL_CHANNEL_ID`)로 자동 발송.

**알림 시점:**

| 시점 | 알림 |
|---|---|
| publish 성공 | `✅ PUBLISHED` — Thread URL, tweet 수, sidecar 경로 |
| publish 실패 (모든 분기) | `❌ FAILED` — stage 식별자, exit code, error 메시지 |
| 강제 재발행 성공 | `🔁 RE-PUBLISHED` |
| skip (이미 발행됨) | 알림 없음 (소음 방지) |

**stage 식별자 종류:**

| stage | 의미 | exit_code |
|---|---|---|
| `archive_not_found` | ARCHIVE_PATH 미발견 / archive 비어있음 | 1 |
| `validation` | X 글자수 280 초과 | 2 |
| `tweepy_publish` | tweepy create_tweet 예외 (Forbidden/Rate Limit 등) | 1 |
| `sidecar_write` | X 발행은 성공했으나 sidecar 작성 실패 | 0 |

**알림 메시지 형식:**

성공:
```
✅ PUBLISHED · weekly_news_x
━━━━━━━━━━━━━━━
🗂 archive: 2026-05-09-saturday.md
🧵 tweets: 8
🔗 thread: https://x.com/{handle}/status/...
📝 sidecar: logs/weekly_news/2026/05/...meta.json
🕒 2026-05-09 09:15 KST
```

실패:
```
❌ FAILED · weekly_news_x
━━━━━━━━━━━━━━━
🗂 archive: 2026-05-09-saturday.md
⚙️ stage: tweepy_publish
🔢 exit code: 1
📋 error:
RuntimeError: 403 Forbidden — Read+Write 권한 필요
🕒 2026-05-09 09:15 KST
```

**환경변수:**

기존 `alert.yml`과 동일 시크릿 재활용 — 추가 등록 불필요.
- `TELEGRAM_BOT_TOKEN` (이미 등록됨)
- `TELEGRAM_INTERNAL_CHANNEL_ID` (이미 등록됨)

**Graceful Skip 동작:**
- 시크릿 미설정 시: `[notifier] 시크릿 미설정 — skip` 로그 후 진행 (X 발행 자체는 영향 없음)
- Telegram API 예외 시: warning 로그 후 진행 (X 발행 자체는 영향 없음)

### 6-2. 기타 향후 옵션

- Slack webhook (기존 시스템에 없는 채널)
- Email (Gmail API) — 마스터 기존 GAS 시스템 재활용 가능
- GitHub 기본 알림 (워크플로우 실패 시 이메일 자동 발송)

---

## 7. 자주 묻는 질문 (FAQ)

### Q1. 토요일이 공휴일이면 발행 안 되나?
A. cron은 요일 기준으로만 동작하므로 공휴일 무관하게 매주 토요일 09:00 KST 트리거됨.

### Q2. 같은 주에 두 번 발행하고 싶으면?
A. 1) 다른 archive 파일을 PR에 포함 → 자동으로 다른 sidecar 생성됨 / 2) 같은 archive를 재발행: `FORCE_REPUBLISH=true` Variable 설정.

### Q3. archive 파일을 수정하면?
A. sidecar는 단순 존재 여부만 체크하므로, 수정 후 재발행 시 `FORCE_REPUBLISH=true` 필요. 이때 sidecar의 `previous` 필드에 이전 발행 기록 보존.

### Q4. 기존 alert.yml과 X 한도가 충돌하지 않나?
A. X API Basic 한도 50,000 posts/월. alert.yml은 45분 cron당 최대 1 트윗 = 약 960/월. weekly_news는 토/일 8/회 × 8회 = 64/월. **합 ~1,024/월, 한도 대비 2.1% 미만**.

### Q5. PR을 검토 안 하고 자동 머지하려면?
A. 권장 안 함. 본 시스템의 안전성은 **마스터 검수 게이트**에 의존. 자동 머지는 팩트 오류·환각 발행을 막을 수 없음.

### Q5-2. 일요일 발행만 일시 중단하려면?
A. 두 가지 방법:
1) **파일명 변경**: `weekly_news_draft_sunday.yml` → `weekly_news_draft_sunday.yml.disabled` 로 rename 후 commit. 토요일은 그대로 동작.
2) **저장소 Actions UI**: 저장소 → Actions → "Weekly News Draft (Sunday)" → 우상단 ⋯ → Disable workflow. (파일 무변경, UI에서만 비활성)

재활성화는 반대로 수행.

### Q6. 비용 초과 알람 자동화 가능한가?
A. Anthropic console에서 monthly spend limit 설정 시 자동 차단 가능. GitHub Actions로 매일 사용량 폴링하는 워크플로우도 추가 가능 (현재 미구현).

---

## 8. 긴급 대응 시나리오

### 8-1. 잘못된 내용이 X에 발행됨

```
1. X 웹/앱에서 해당 트윗 즉시 삭제
2. logs/weekly_news/.../{date}.md.meta.json의 tweet_id로 추적
3. archive 파일 수정 PR 생성 → 머지 + FORCE_REPUBLISH=true (필요 시)
4. 사후 분석: 어느 단계에서 검수 실패했는지 PR 코멘트로 기록
```

### 8-2. Claude API 잔액 소진

```
1. console.anthropic.com에서 잔액 충전
2. Actions → 실패한 draft run → Re-run failed jobs
```

### 8-3. X 계정 정지

```
1. 발행 워크플로우 즉시 비활성화:
   - .github/workflows/weekly_news_draft.yml 파일명을 .disabled로 변경 후 PR
2. X 계정 복구 작업 진행
3. 복구 후 .yml로 원복
```

---

## 9. 변경 이력

| 일자 | 변경 | 비고 |
|---|---|---|
| 2026-05-11 | 초안 작성 | publish.py v1.1.0 / 재발행 방지 도입 기준 |
| 2026-05-11 | 일요일 발행 활성화 | `weekly_news_draft_sunday.yml` 추가 (토요일 워크플로우와 독립 실행) |
| 2026-05-11 | Telegram 알림 통합 | publish.py v1.2.0 / notifier.py 신규 / 기존 TelegramPublisher 재활용 |
