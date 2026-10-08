# meta-camp-analysis

광고주(고객)의 메타(Facebook·Instagram) 광고를 대신 운영할 때 쓰는 자동화 도구입니다.

| 주기 | 하는 일 | 결과물 |
|---|---|---|
| **매일** | 소재(광고) 단위로 최근 3일 성과를 직전 7일, 최근 4주 최고치와 비교해 이상 징후를 찾습니다. 소재 교체·OFF·증액 같은 조치를 제안합니다. | `reports/<고객>/daily/YYYY-MM-DD.md/.json`, Slack·Telegram 알림 |
| **매월** | 지난달 성과를 집계하고 전월과 비교합니다. 캠페인·소재·형식·연령/성별·지면별로 분석해 다음 달 예산 배분, 실행 계획, 목표를 제시합니다. | `reports/<고객>/monthly/YYYY-MM.html/.md/.xlsx` |

예시 결과물은 [`samples/`](samples/)에 있습니다. 모두 **모의 데이터**로 만든 것입니다. 월간 보고서는 `samples/monthly-2026-09.html`을 브라우저로 열어 보세요.

## 빠른 시작

```bash
pip install -r requirements.txt

# 1) 토큰 없이 모의 데이터로 전체 흐름 확인
python -m meta_ads daily --mock --no-notify
python -m meta_ads monthly --mock --month 2026-09

# 2) 실제 계정 연결
export META_ACCESS_TOKEN=EAAB...          # 아래 '토큰 발급' 참고
python -m meta_ads check-token            # 계정 이름/통화가 나오면 성공
python -m meta_ads daily                  # 오늘 기준(어제까지 데이터) 점검 + 알림
python -m meta_ads monthly                # 지난달 보고서
python -m meta_ads daily --all            # config.yaml 의 모든 고객 실행
```

## 설정: `config.yaml`

고객마다 광고계정, 전환 기준, 목표(CPA·ROAS·CTR), 월 예산을 적습니다. 비밀값은 이 파일에 넣지 말고 환경변수로 넘깁니다.

```yaml
clients:
  - name: 봄날
    account_id: "1828231244405674"
    conversion_actions: [offsite_conversion.fb_pixel_purchase]  # 리드면 lead 등
    monthly_budget: 6000000
    targets: { cpa: 18000, roas: 3.0, ctr: 0.008 }
```

- **전환 기준(`conversion_actions`)**: 쇼핑몰은 `offsite_conversion.fb_pixel_purchase`(또는 `omni_purchase`), 리드 수집은 `lead`, 메시지 문의는 `onsite_conversion.messaging_conversation_started_7d`를 씁니다.
- **인지·트래픽 캠페인**: 캠페인 이름에 `트래픽`, `브랜딩`, `도달` 같은 단어가 들어 있으면 CPA·ROAS 경고와 효율 순위에서 뺍니다(`awareness_keywords`).
- 임계값은 `rules:`에서 바꿉니다. 고객별로 다르게 하려면 `clients[].rules`에 같은 키를 넣습니다.

| 환경변수 | 용도 |
|---|---|
| `META_ACCESS_TOKEN` | Marketing API 토큰 (필수) |
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook (선택) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram 봇 알림 (선택) |

## 토큰 발급 (시스템 사용자 · 만료 없음)

1. [developers.facebook.com](https://developers.facebook.com)에서 앱을 만들고(유형: 비즈니스) **Marketing API** 제품을 추가합니다.
2. 비즈니스 설정 → **시스템 사용자**에서 관리자 시스템 사용자를 만듭니다.
3. **자산 할당**에서 광고 계정 `1828231244405674`(봄날 포트폴리오)를 할당합니다. 읽기 전용 점검이면 '성과 보기' 권한이면 충분합니다.
4. **토큰 생성**을 누르고 위 앱을 고른 뒤 권한 `ads_read`(+ 필요하면 `read_insights`)를 체크합니다. 만료는 '사용 안 함'으로 둡니다.
5. 발급된 토큰을 `META_ACCESS_TOKEN`으로 설정합니다. GitHub에서 돌린다면 저장소 Settings → Secrets에 넣습니다.

> 다른 고객 계정은 그 고객의 비즈니스 포트폴리오에서 **파트너 액세스**로 대행사 비즈니스에 계정을 공유받으면 됩니다. 그다음 같은 시스템 사용자에게 할당하면 토큰 하나로 여러 고객을 관리할 수 있습니다.

## 자동 실행 (GitHub Actions)

`.github/workflows/meta-ads.yml`이 두 가지 일정으로 실행됩니다.

- 매일 **08:50 KST**: `daily --all`. 점검 결과를 알림으로 보내고, 보고서를 Actions 아티팩트로 남깁니다.
- 매월 **1일 09:40 KST**: `monthly --all`(지난달)

저장소 Secrets에 `META_ACCESS_TOKEN`과 알림 채널 값을 넣으면 동작합니다. Actions 탭의 **Run workflow**로 수동 실행할 수 있고, 모의 데이터 실행도 됩니다.

## 일일 점검 규칙

비교 구간은 **최근 3일**(어제 포함)과 **직전 7일**입니다. 빈도는 최근 7일 순도달 기준이고, 노출 1,000회 미만인 소재는 판단하지 않습니다.

| 코드 | 수준 | 조건 | 제안 |
|---|---|---|---|
| `CREATIVE_FATIGUE` | 🔴 | CTR이 직전 7일 대비 25% 이상 떨어지고(99% 유의) 빈도가 3 이상이거나 하락폭이 40% 이상인 경우. 또는 **4주 최고 CTR 대비 40% 이상 하락**(서서히 진행된 피로) | 신규 소재 2~3개 투입 후 기존 OFF, 훅 변형, 타겟 확장 |
| `NO_CONVERSION` | 🔴 | 평소 전환율로 보면 전환이 3건 이상 나와야 하는데 0건인 경우 | 픽셀·CAPI·랜딩·결제·품절 점검, 일시중지 |
| `HIGH_CPA` | 🔴/🟠 | 지출이 목표 CPA의 2배 이상이고 CPA가 목표의 1.3배(🟠) 또는 1.6배(🔴)를 넘는 경우 | 저효율 소재 OFF, CVR 낮으면 랜딩 개선 |
| `LOW_ROAS` | 🟠 | CPA는 괜찮은데 ROAS가 목표의 80% 미만인 경우 | 객단가 올리는 번들·세트 소재 |
| `CTR_DROP` | 🟠 | 피로 초기 신호(CTR 하락, 빈도는 낮음) | 교체 소재 준비 |
| `HIGH_FREQUENCY` | 🟠 | 7일 빈도 4.5 이상 | 모수 확대 |
| `LOW_CTR` | 🟠 | 7일 링크 CTR이 기준 미만 | 영상·릴스로 재제작, 혜택을 숫자로 |
| `CPM_SPIKE` | 🔵 | CPM 40% 이상 상승 | 경쟁인지 품질 문제인지 구분 |
| `FEW_CREATIVES` | 🔵 | 광고세트의 활성 소재가 2개 미만 | 광고세트당 3~5개 운영 |
| `BUDGET_PACING` | 🟠 | 월 예산 소진 속도가 계획보다 ±15% 넘게 벗어난 경우 | 일 예산 재조정 |
| `WINNER` | 🟢 | ROAS가 목표의 1.3배 이상이고 전환 3건 이상 | 20% 이내 증액, 변형 소재로 확장 |

CTR 하락은 두 비율 z-검정으로 판정합니다. 노출이 적은 소재에서 우연히 생긴 등락 때문에 '교체' 알림이 나가지 않게 하려는 장치입니다.

## 월간 보고서 구성

1. 핵심 지표 8개(광고비·매출·ROAS·전환·CPA·CTR·CPC·CPM), 전월 대비 변화, 목표 달성 여부
2. 일별 추이: 광고비, ROAS와 링크 CTR(목표선 표시)
3. 핵심 분석: 성과, 예산, 소재, 타겟, 지면 영역별 자동 코멘트
4. 캠페인 성과와 **다음 달 예산 제안**: 효율 지수에 비례해 ±30% 범위로 조정하고, 합계는 월 예산에 맞춥니다.
5. 소재 효율 상위·하위 5개, 월 전반→후반 CTR 변화로 본 피로 판정, 형식별(릴스·영상·이미지·캐러셀·카드뉴스) 성과
   - 형식은 **광고 이름에 든 키워드로 분류**합니다. 광고 이름을 `형식_콘셉트_버전`처럼 지어 두면 분석이 정확해집니다.
6. 연령·성별, 노출 지면별 광고비 비중 대 전환 비중
7. **다음 달 방향**: 목표(예산·CPA·ROAS·전환·CTR)와 우선순위별 실행 계획

Excel 파일에는 위 표의 원본 데이터가 시트별로 들어 있어서 고객 맞춤 가공에 쓸 수 있습니다.

## 구조

```
meta_ads/
  client.py      Graph API 클라이언트 (페이지네이션, 요청 한도 재시도, actions 정규화)
  mock.py        모의 데이터 (피로/전환0/저CTR/위너 시나리오 내장)
  rules.py       일일 점검 규칙
  daily.py       일일 점검 실행·마크다운 렌더링
  notify.py      콘솔/Slack/Telegram 알림
  monthly.py     월간 집계·분석·다음 달 계획·Excel
  report_html.py, templates/monthly.html   월간 HTML 보고서 (단일 파일, 다크 모드, 인쇄 대응)
tests/           pytest
```

```bash
pip install pytest && python -m pytest -q
```
