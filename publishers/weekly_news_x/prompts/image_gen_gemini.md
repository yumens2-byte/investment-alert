# Gemini 이미지 생성 프롬프트 (Nano Banana 2)

> **모델**: `gemini-2.5-flash-image` (Nano Banana, 안정판) 또는
> `gemini-3.1-flash-image-preview` (Nano Banana 2, 최신)
>
> **출력**: 1024×1024 또는 16:9 비율
>
> **연관 문서**: `docs/visual_guidelines_gemini.md`

---

## 시스템 프롬프트 (이미지 생성 지시)

```
Generate a single editorial illustration image. No multiple images.
Output a high quality flat editorial illustration suitable for an investment news brief.

STYLE:
- Editorial minimalist illustration, The Economist magazine style
- Korean lifestyle magazine sensibility
- Calm, sophisticated, gender-neutral, calm mood
- Flat illustration, soft rounded shapes, minimal shadows
- Aspect ratio 16:9 wide layout

COLOR PALETTE (strict):
- Background: soft off-white beige (around #F5F1EA)
- Secondary: pale blue tint (around #E5EBF2)
- Accent (only one point): muted lilac (around #C9B8D9) OR soft sage green
- No bright red, no pure black, no neon, no US flag colors

COMPOSITION:
- 70% negative space — background MUST BE MOSTLY EMPTY, plain solid color
- Single focal element, center or one-third aligned
- Background MUST be a single FLAT SOLID color, NOT a pattern
- NO background textures, NO abstract shapes filling the background
- The empty beige background ITSELF is the primary design element
- One thin curved line (1-2 px equivalent) for chart hint
- Round dots over angular squares
- No clutter, no busy details, no decorative fillers

PEOPLE:
- Either no humans, or one silhouette/partial figure (back view or hand)
- Strictly gender-neutral, no facial close-up, no expression
- Never include two or more people

TEXT IN IMAGE:
- One short English headline at top, sans-serif, clean
- No Korean text in image (Korean is in caption)
- No numbers larger than 2 digits
- No more than one line of headline text

STRICTLY FORBIDDEN:
- US flag, American symbols, Washington landmarks
- Bull or bear mascots, animal mascots
- Suited businessman stereotype
- Dark backgrounds, black charts, red crashing arrows
- Religious or political symbols
- More than one accent color point
- Heavy shadows, 3D effects
- Explosion, breaking, crashing imagery
- Background patterns, textures, or shapes filling the background
- Pebbles, stones, abstract organic shapes covering the background
- Any decorative element other than the single focal point
- Any visual element occupying more than 30% of total area

TOPIC CONTEXT:
{TOPIC_HINT}

HEADLINE FOR IMAGE:
"{ENGLISH_HEADLINE}"

VISUAL HINT BY MARKET MOOD:
- For rally: soft upward curve, lilac accent
- For decline: calm downward curve, pale blue accent (NEVER threatening)
- For sideways: horizontal flow, sage green accent
- For volatility: gentle wave shapes, lilac accent
- For macro (rates/FX): abstract geometry, pale blue accent
- For earnings: geometric pattern with one lilac dot
```

---

## 프롬프트 변수 정의

| 변수 | 입력 | 예시 |
|------|------|------|
| `{TOPIC_HINT}` | archive에서 추출한 주제 키워드 (300자 이내) | "Big tech earnings beat. Nasdaq rallied. NVIDIA breakout." |
| `{ENGLISH_HEADLINE}` | LLM 또는 규칙 기반 영문 짧은 헤드라인 (10단어 이내) | "Tech Earnings Lift Markets" |

---

## 안전 검증 (사후)

이미지 생성 후 다음 후처리 검증을 통과해야 archive에 사용 가능:

1. 파일 크기 50KB ~ 5MB (정상 PNG 범위)
2. 가로:세로 비율 16:9 ± 5%
3. 평균 채도 검증 (너무 강하거나 너무 약함 X) — Phase 2 도입 검토
4. 텍스트 OCR 후 영문만인지 확인 — Phase 2 도입 검토

Phase 1에서는 1, 2번만 자동 검증. 3, 4번은 사후 운영 모니터링.

---

## 변경 이력

| 버전 | 일자 | 변경 내용 |
|------|------|----------|
| 1.0.0 | 2026-05-23 | 초안 작성 |
