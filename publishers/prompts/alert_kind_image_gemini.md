# Alert 친근 톤 이미지 생성 프롬프트 (Gemini)

> **모델**: `gemini-2.5-flash-image` (Nano Banana, 안정판)
> **출력**: 16:9 와이드 (1024×576)
> **연관 모듈**: `publishers/alert_formatter_kind.py::generate_alert_image_kind()`

---

## 시스템 프롬프트

```
Generate a single editorial illustration image. No multiple images.
Output a high quality flat editorial illustration suitable for an investment alert message.

STYLE:
- Editorial minimalist illustration, The Economist magazine style
- Korean lifestyle magazine sensibility, calm and thoughtful
- Flat illustration, soft rounded shapes, minimal shadows
- Aspect ratio 16:9 wide layout

MOOD (this alert's tone):
{LEVEL_MOOD}

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

CONTEXT (for visual feel only):
{REASONING_HINT}

HEADLINE FOR IMAGE:
"{ENGLISH_HEADLINE}"

VISUAL HINT BY LEVEL MOOD:
- For "calm but slightly somber" (L1): a slow descending soft curve, lilac dot resting at lower
- For "warm thoughtful cozy" (L2): horizontal flowing line, sage green accent, like a calm afternoon
- For "gentle easy" (L3): light curve, lilac dot at center, peaceful
```

---

## 프롬프트 변수

| 변수 | 입력 | 예시 |
|------|------|------|
| `{LEVEL_MOOD}` | 등급별 무드 텍스트 | "calm but slightly somber, quiet reflection, soft twilight tones" |
| `{ENGLISH_HEADLINE}` | 영문 헤드라인 | "A Pause to Look" |
| `{REASONING_HINT}` | 시스템 판정 근거 (200자 이내) | "VIX 32.5 surge, S&P 500 -3.2% close" |

---

## 변경 이력

| 버전 | 일자 | 변경 내용 |
|------|------|----------|
| 1.0.0 | 2026-05-23 | 초안 (alert kind tone 전용 이미지 프롬프트) |
