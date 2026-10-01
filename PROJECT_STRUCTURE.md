# OpenSimul 프로젝트 구조 분석 보고서

> **프로젝트 설명**: Azure OpenAI 기반 영수증·지출·재고 관리 플랫폼  
> **스택**: FastAPI · PostgreSQL · Azure OpenAI · Capacitor(iOS)  
> **작성일**: 2026-06-16

---

## 목차

1. [전체 디렉터리 구조](#1-전체-디렉터리-구조)
2. [백엔드 Python 파일 분석](#2-백엔드-python-파일-분석)
3. [프론트엔드 정적 파일 분석](#3-프론트엔드-정적-파일-분석)
4. [테스트 구조](#4-테스트-구조)
5. [인프라 및 배포](#5-인프라-및-배포)
6. [iOS 모바일 앱](#6-ios-모바일-앱)
7. [데이터베이스 스키마](#7-데이터베이스-스키마)
8. [API 엔드포인트 목록](#8-api-엔드포인트-목록)
9. [의존성](#9-의존성)
10. [주요 기능 요약](#10-주요-기능-요약)

---

## 1. 전체 디렉터리 구조

```
opensimul/                          # 루트 (총 ~5.8MB)
│
├── 📄 README.md                    # "ai 실험실" (3줄)
├── 📄 requirements.txt             # Python 패키지 9종
├── 📄 .gitignore
├── 📄 .env.example                 # 환경변수 예제 (438B)
│
├── 🐍 app.py                       # FastAPI 서버 (935줄, 33KB)
├── 🐍 db.py                        # PostgreSQL 작업 (973줄, 42KB)
├── 🐍 azure_openai_client.py       # Azure OpenAI 클라이언트 (424줄, 19KB)
├── 🐍 agent_tools.py               # 에이전트 도구 정의 (257줄, 12KB)
├── 🐍 grocery_pass1.py             # 영수증 OCR 추출 (272줄, 12KB)
├── 🐍 session_manager.py           # 세션 캐시 관리 (42줄)
├── 🐍 repl.py                      # 터미널 REPL (134줄, 8KB)
├── 🐍 pantry_utils.py              # 수량/단위 추론 유틸 (36줄)
├── 🐍 example.py                   # SDK 사용 예제 (42줄)
│
├── 🧪 test_basic.py                # 기본 유틸 단위 테스트 (179줄)
├── 🧪 conftest.py                  # pytest 공통 픽스처 (181줄)
│
├── 📁 static/                      # 프론트엔드 정적 파일 (총 204KB)
│   ├── index.html                  # 메인 영수증 분석 페이지 (2,565줄, 92KB)
│   ├── grocery.html                # 영수증 목록/편집 페이지 (1,681줄, 57KB)
│   ├── pantry.html                 # 재고 관리 페이지 (446줄, 17KB)
│   ├── trash.html                  # 휴지통 페이지 (369줄, 15KB)
│   ├── common.js                   # 공통 UI 로직 (118줄, 4.6KB)
│   └── native.js                   # Capacitor 네이티브 연동 (78줄, 2.6KB)
│
├── 📁 tests/                       # 신규 단위 테스트 (총 544줄)
│   ├── __init__.py
│   ├── test_auth.py                # 인증 테스트 (161줄)
│   ├── test_db.py                  # DB 함수 테스트 (225줄)
│   └── test_agent_tools.py         # 에이전트 도구 테스트 (158줄)
│
├── 📁 mobile/                      # iOS Capacitor 앱 (총 24KB)
│   ├── README.md                   # iOS 빌드 가이드 (76줄)
│   ├── package.json                # Node 의존성
│   ├── capacitor.config.json       # Capacitor 설정
│   └── www/
│       └── index.html              # 기본 웹뷰 HTML (353B)
│
├── 📁 docs/
│   └── requirements.html           # 요구사항 문서 (44KB)
│
├── 📁 .github/
│   └── workflows/
│       └── deploy.yml              # Azure CD 파이프라인 (42줄)
│
└── 📁 .claude/
    ├── settings.json               # Claude Code 훅 설정 (301B)
    └── worktrees/                  # 다중 에이전트 워크트리 캐시
```

---

## 2. 백엔드 Python 파일 분석

### 2.1 `app.py` — FastAPI 웹 서버 (935줄)

전체 HTTP 레이어. 라우팅, 이미지 처리, 인증, 백그라운드 스케줄러를 담당한다.

**주요 클래스**

| 클래스 | 역할 |
|--------|------|
| `AppState` | 전역 앱 상태 (`session_manager`, `usd_to_krw`, `db_enabled`) |
| `RateLimiter` | 슬라이딩 윈도우 로그인 시도 제한 (15분/5회) |
| `AuthRequest` | 로그인 요청 모델 |
| `RegisterRequest` | 회원가입 요청 모델 (비밀번호 복잡도 검증 포함) |
| `ChatRequest` | 채팅 요청 모델 |
| `GrocerySaveRequest` | 영수증 저장 요청 모델 |
| `SpellCheckRequest` | 맞춤법 교정 요청 모델 |
| `PushTokenRequest` | 푸시 토큰 등록 모델 |

**주요 함수**

| 함수 | 역할 |
|------|------|
| `_resize_image()` | 이미지 리사이즈 (최대 1400×9000px), EXIF 회전 보정 |
| `_daily_trash_cleanup()` | 매일 08:00 KST 30일 초과 휴지통 자동 삭제 |
| `_client()` | 세션별 `AzureOpenAIClient` 취득 |
| `get_current_user()` | `sid` 쿠키 → `user_code` 변환 (FastAPI `Depends`) |
| `_validate_date()` | ISO 날짜 형식 검증 |
| `_build_purchase_summary()` | 최근 구매 이력 → 시스템 프롬프트 컨텍스트 문자열 |
| `_inject_grocery_context()` | 채팅 시 구매 이력을 LLM에 주입 |
| `_reset_for_testing()` | 테스트용 전역 상태 초기화 (pytest 전용) |

**lifespan 처리**
- 앱 시작: 환율 조회 → DB 초기화 → 만료 세션 정리 → 휴지통 스케줄러 시작
- 앱 종료: 백그라운드 태스크 취소

---

### 2.2 `db.py` — PostgreSQL 작업 (973줄)

모든 데이터베이스 CRUD. 40+ 함수, 전부 `_get_conn()`을 통해 연결한다.

**의존성 주입 구조** (테스트 지원)
```python
_connection_factory = None          # None이면 환경변수 사용

def set_connection_factory(factory): # 테스트에서 Mock 주입
    global _connection_factory
    _connection_factory = factory

def _get_conn():
    if _connection_factory is not None:
        return _connection_factory()
    return psycopg2.connect(os.environ["POSTGRESQL_CONNECTION_STRING"])
```

**함수 그룹별 분류**

| 그룹 | 주요 함수 |
|------|----------|
| **DB 초기화** | `init_db()` |
| **채팅 세션** | `touch_session`, `load_messages`, `save_messages`, `clear_messages`, `cleanup_old_sessions` |
| **이미지 분석** | `save_analysis`, `list_analyses` |
| **영수증** | `save_grocery_receipt`, `list_grocery_receipts`, `get_grocery_receipt_detail`, `delete_grocery_receipt`, `update_grocery_receipt_meta`, `search_receipts_by_merchant`, `search_grocery_items` |
| **영수증 품목** | `add_grocery_item`, `update_grocery_item`, `delete_grocery_item` |
| **재고(Pantry)** | `add_pantry_item`, `update_pantry_item`, `delete_pantry_item`, `list_pantry`, `upsert_pantry_from_purchase`, `deduct_pantry_qty`, `get_known_item_names` |
| **휴지통** | `move_to_trash`, `restore_trash_item`, `list_trash_items`, `delete_trash_item`, `empty_trash`, `preview_for_trash`, `list_trash_merchants` |
| **사용자 세션** | `create_user_session`, `get_user_from_session`, `delete_user_session`, `cleanup_expired_user_sessions` |
| **인증** | `register_user`, `login_user`, `_hash_password`, `_verify_password` |
| **푸시 알림** | `register_push_token`, `list_push_tokens` |
| **자동 정리** | `cleanup_old_sessions`, `cleanup_expired_user_sessions`, `cleanup_old_trash` |

**비밀번호 해싱 포맷**
```
신버전: "300000:salt:hash"    (PBKDF2-SHA256, 300,000회)
구버전: "salt:hash"           (100,000회, 하위 호환)
```

---

### 2.3 `azure_openai_client.py` — Azure OpenAI 클라이언트 (424줄)

Azure OpenAI API 래핑, 재시도 로직, 대화 관리, 토큰 비용 추적.

**주요 클래스**

| 클래스 | 역할 |
|--------|------|
| `ClientConfig` | 설정 dataclass (`endpoint`, `api_key`, `deployment`, `verify_ssl`, `max_history_turns`) |
| `TurnUsage` | 토큰 사용량 추적 (`prompt_tokens`, `completion_tokens`, `cost()` 메서드) |
| `AzureOpenAIClient` | 메인 클라이언트 (슬라이딩 윈도우 히스토리, 메모리 주입) |

**`AzureOpenAIClient` 주요 메서드**

| 메서드 | 역할 |
|--------|------|
| `chat(message)` | 동기 단일 턴 채팅 |
| `stream_chat(message)` | 스트리밍 응답 (제너레이터) |
| `chat_with_tools(message, tools, executor)` | 함수 호출 기반 에이전트 채팅 |
| `stream_chat_with_tools(...)` | 스트리밍 + 함수 호출 |
| `analyze_image(image_bytes, mime_type, prompt)` | Vision API 이미지 분석 |
| `set_memory(key, value)` / `get_memory(key)` | 장기 메모리 관리 (JSON 파일) |
| `set_transient(key, value)` | 세션 내 임시 컨텍스트 주입 |
| `reset()` | 대화 이력 초기화 |

---

### 2.4 `agent_tools.py` — 에이전트 도구 (257줄)

LLM 함수 호출(Function Calling) 기반 지출 분석 도구 정의 및 실행.

**도구 목록 (`TOOLS` 배열)**

| 도구명 | 설명 |
|--------|------|
| `query_spending_by_period` | 기간별 지출 이력 조회 |
| `query_spending_by_merchant` | 업체별 지출 및 구매 이력 조회 |
| `search_items` | 품목명 키워드로 구매 이력 검색 |
| `get_spending_summary` | 기간별·업체별 지출 통계 집계 |
| `get_pantry_items` | 냉장고/재고 현황 조회 |
| `update_pantry_current_qty` | 재료 사용 후 재고 차감 |

**실행 흐름**
```
LLM 응답 → tool_call 감지 → execute_tool(name, args, user_id)
  → _query_spending_by_period() / _search_items() / ... → db 함수 호출
  → 결과 JSON → LLM 컨텍스트 주입 → 최종 응답 생성
```

---

### 2.5 `grocery_pass1.py` — 영수증 OCR (272줄)

Azure Vision API + LLM을 통한 영수증 이미지/텍스트 → 구조화 JSON 추출.

**주요 함수**

| 함수 | 입력 | 출력 |
|------|------|------|
| `extract_pass1_bytes(image_bytes, mime_type, config)` | 이미지 바이너리 | `(result_dict, raw_text)` |
| `extract_pass1_text(text, config)` | 텍스트 문자열 | `(result_dict, raw_text)` |
| `validate(result)` | 추출 결과 dict | `list[str]` 경고 메시지 |

**추출 결과 스키마**
```json
{
  "merchant": "이마트",
  "purchase_date": "2026-05-01",
  "total": 45000,
  "currency": "KRW",
  "is_refund": false,
  "items": [
    {
      "raw_name": "우유 1000ml",
      "qty": 2,
      "unit_price": 1800,
      "amount": 3600,
      "is_cancelled": false
    }
  ]
}
```

---

### 2.6 `session_manager.py` — 세션 캐시 (42줄)

`AzureOpenAIClient` 인스턴스를 세션별로 캐싱하여 DB 대화 이력 재로드를 최소화.

- 최대 100개 세션 캐시 (LRU 방식 eviction)
- cache miss 시 DB에서 대화 이력 복원 (`db.load_messages`)
- `invalidate(session_id)`: 수동 캐시 삭제

---

### 2.7 `repl.py` — 터미널 REPL (134줄)

로컬 개발용 인터랙티브 채팅 클라이언트.

- `fetch_usd_to_krw(verify_ssl)`: frankfurter.dev API로 실시간 환율 조회
- 토큰 사용량 및 비용(USD/KRW) 실시간 출력
- 스트리밍 응답 지원

---

### 2.8 `pantry_utils.py` — 수량 추론 유틸 (36줄)

영수증 품목명에서 수량과 단위를 추론하는 순수 함수.

```python
infer_qty("우유 1000ml", 1)   → (1000, "ml")
infer_qty("계란 10개입", 1)   → (10, "개")
infer_qty("닭가슴살", 2)      → (2, "개")  # 기본값
```

---

## 3. 프론트엔드 정적 파일 분석

### 3.1 `index.html` — 메인 영수증 분석 페이지 (2,565줄, 92KB)

영수증 이미지 업로드 → Azure LLM 분석 → 채팅 인터페이스.

**주요 기능**
- 이미지/PDF 드래그앤드롭 또는 카메라 촬영 (Capacitor)
- 스트리밍 채팅 응답 표시 (SSE)
- 지출 분석 에이전트 도구 호출 결과 시각화
- 주간 HTML 리포트 생성 및 공유 (SaveAndShare)
- 메모리(장기 기억) 관리 UI

### 3.2 `grocery.html` — 영수증 가져오기 페이지 (1,681줄, 57KB)

추출된 영수증의 목록 조회, 편집, 재고 연동.

**주요 기능**
- 이미지 스크롤 캡처 이어붙이기 (Canvas API)
- 업체/날짜/품목 인라인 편집
- 한국어 맞춤법 교정 제안 (`/grocery/suggest-names`)
- 휴지통 이동 미리보기 및 일괄 삭제
- 영수증 삭제 시 재고 자동 차감 연동

### 3.3 `pantry.html` — 재고 관리 페이지 (446줄, 17KB)

냉장고/팬트리 재고 현황 조회 및 수동 수정.

**주요 기능**
- 품목 추가/수정/삭제
- 현재 수량 차감 (사용 기록)
- 총 구매량 vs 현재 재고 표시

### 3.4 `trash.html` — 휴지통 페이지 (369줄, 15KB)

삭제된 영수증 관리.

**주요 기능**
- 삭제된 영수증 목록 및 복원
- 날짜 범위/업체 필터로 일괄 삭제 미리보기
- 영구 삭제 (개별/전체)

### 3.5 `common.js` — 공통 UI 로직 (118줄)

모든 페이지에서 공유하는 인증 및 UI 초기화.

**주요 함수**
- `initCommonUI({ onLogin })`: 로그인/회원가입 폼 이벤트 바인딩
- `submitAuth()`: POST `/auth/login` or `/auth/register` 호출
- `showAuth()` / `hideAuth()`: 인증 오버레이 표시/숨기기
- `updateUserBar()`: 상단 사용자 코드 표시
- `fmtDate(str)`: ISO 날짜 → `M/DD` 형식 변환

### 3.6 `native.js` — Capacitor 네이티브 연동 (78줄)

iOS 네이티브 기능과 웹 폴백을 분기하는 어댑터 레이어.

**주요 함수**
- `isNativeApp()`: `window.Capacitor` 존재 여부 확인
- `captureOrPickImage()`: 카메라 촬영 or 사진 선택 → `File` 객체 반환
- `saveAndShareFile(filename, content, mimeType)`: 파일 저장 + iOS 공유 시트
- `initPushNotifications()`: APNs 토큰 취득 → POST `/push/register`

---

## 4. 테스트 구조

### 4.1 테스트 현황 (총 72개, 전부 통과)

| 파일 | 테스트 수 | 커버 영역 |
|------|----------|----------|
| `test_basic.py` | 17 | 날짜 포맷, dedup 로직, 수량 추론, 날짜 계산 |
| `tests/test_auth.py` | 19 | 로그인/회원가입/로그아웃/세션/rate limit |
| `tests/test_db.py` | 23 | 비밀번호 해싱, 세션 생성, 영수증 저장, 팬트리, 정리 |
| `tests/test_agent_tools.py` | 13 | execute_tool 6종, 오류 처리 |

### 4.2 테스트 인프라 (`conftest.py`)

```python
class MockCursor:
    """psycopg2 cursor 모방 (execute, fetchone, fetchall, rowcount)"""

class MockConnection:
    """psycopg2 connection 모방 (cursor_factory=RealDictCursor 지원)"""

def cursor_sequence(conn, *specs):
    """단일 커넥션에서 cursor() 호출 순서별 다른 반환값 설정"""

# 픽스처
@pytest.fixture def db_mock(mock_conn)       # DB 연결 교체
@pytest.fixture def client(db_mock)          # DB 활성화 TestClient
@pytest.fixture def authed_client(db_mock)   # 인증된 TestClient
```

### 4.3 테스트 실행

```bash
pytest -v                                    # 전체
pytest tests/ -v                             # 신규 테스트만
pytest --cov=db --cov=app --cov-report=term  # 커버리지 측정
```

**현재 추정 커버리지**: ~60% (기존 5% → 개선)

---

## 5. 인프라 및 배포

### 5.1 GitHub Actions (`.github/workflows/deploy.yml`)

**트리거**: `main` 또는 `claude/blissful-carson-akyH2` 브랜치 push

```yaml
jobs:
  test-and-deploy:
    steps:
      - Python 3.11 설정
      - pip install -r requirements.txt && pip install pytest
      - python -m pytest test_basic.py -v      # CI 테스트
      - 배포 패키지 생성 (zip)
      - Azure Web App 배포 (publish-profile 방식)
```

**배포 대상**: `openai-mini-poc-kmh-02` (Azure Web App)

### 5.2 Claude Code 설정 (`.claude/settings.json`)

```json
{
  "hooks": {
    "PostToolUse": [{
      "matcher": "Edit|Write",
      "hooks": [{
        "type": "command",
        "command": "cd /home/user/opensimul && python -m pytest test_basic.py tests/ --tb=short -q 2>&1 | tail -35"
      }]
    }]
  }
}
```

파일 수정 직후 자동으로 테스트를 실행하여 즉시 통과/실패 피드백 제공.

### 5.3 환경변수 (`.env.example` 참고)

| 변수 | 필수 | 설명 |
|------|------|------|
| `POSTGRESQL_CONNECTION_STRING` | ✅ | PostgreSQL 연결 문자열 |
| `AZURE_OPENAI_ENDPOINT` | ✅ | Azure OpenAI 엔드포인트 URL |
| `AZURE_OPENAI_API_KEY` | ✅ | Azure OpenAI API 키 |
| `AZURE_OPENAI_DEPLOYMENT` | ✅ | 배포 모델명 (예: gpt-4o) |
| `MEMORY_FILE` | ⬜ | 장기 메모리 JSON 파일 경로 (기본: `memory.json`) |
| `VERIFY_SSL` | ⬜ | SSL 인증서 검증 여부 (기본: `true`) |

---

## 6. iOS 모바일 앱

**방식**: Capacitor를 통한 웹뷰 래퍼 (실제 로직은 서버의 FastAPI 앱 그대로)

### 6.1 구성

```
mobile/
├── capacitor.config.json     # appId: kr.mo.opensimul
├── package.json              # @capacitor/* 6.x
└── www/index.html            # webDir (빌드 아티팩트 위치)
```

### 6.2 Capacitor 플러그인

| 플러그인 | 기능 |
|----------|------|
| `@capacitor/camera` | 영수증 촬영 / 사진 선택 |
| `@capacitor/filesystem` | 로컬 파일 저장 |
| `@capacitor/share` | iOS 공유 시트 |
| `@capacitor/push-notifications` | APNs 푸시 알림 |

### 6.3 빌드 요구사항

- macOS + Xcode 15+
- Node.js 18+
- HTTPS 배포 도메인 (Apple ATS 정책)

### 6.4 빌드 절차

```bash
cd mobile
npm install
npx cap add ios
npx cap sync ios
npx cap open ios       # Xcode 열기 → App Store 제출
```

---

## 7. 데이터베이스 스키마

```sql
-- 채팅 세션 및 메시지
CREATE TABLE sessions (session_id TEXT PRIMARY KEY, last_active TIMESTAMPTZ);
CREATE TABLE messages  (id SERIAL, session_id TEXT REFERENCES sessions, role TEXT, content TEXT);

-- 이미지 분석 결과
CREATE TABLE image_analyses (id SERIAL, filename TEXT, analysis TEXT, cost_usd NUMERIC);

-- 영수증
CREATE TABLE grocery_receipts (
    id            SERIAL PRIMARY KEY,
    user_id       TEXT,
    merchant      TEXT,
    purchase_date DATE,
    total         INTEGER,
    currency      TEXT DEFAULT 'KRW',
    is_refund     BOOLEAN DEFAULT FALSE,
    raw_json      TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE grocery_items (
    id           SERIAL PRIMARY KEY,
    receipt_id   INTEGER REFERENCES grocery_receipts ON DELETE CASCADE,
    raw_name     TEXT,
    qty          INTEGER,
    unit_price   INTEGER,
    amount       INTEGER,
    is_cancelled BOOLEAN DEFAULT FALSE
);

-- 재고 (Pantry)
CREATE TABLE pantry_items (
    id          SERIAL PRIMARY KEY,
    user_id     TEXT,
    raw_name    TEXT,
    total_qty   INTEGER,
    current_qty INTEGER,
    unit        TEXT,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 휴지통 (소프트 삭제)
CREATE TABLE trash_bin (
    id         SERIAL PRIMARY KEY,
    user_id    TEXT,
    data       JSONB,          -- 원본 영수증 + 품목 스냅샷
    deleted_at TIMESTAMPTZ DEFAULT NOW()
);

-- 사용자 인증
CREATE TABLE users          (user_code TEXT PRIMARY KEY, password_hash TEXT);
CREATE TABLE user_sessions  (token TEXT PRIMARY KEY, user_code TEXT, expires_at DATE);

-- 푸시 알림
CREATE TABLE push_tokens (token TEXT PRIMARY KEY, user_code TEXT, platform TEXT);
```

---

## 8. API 엔드포인트 목록

### 채팅

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/chat` | 단일 응답 채팅 (에이전트 도구 포함) |
| POST | `/chat/stream` | 스트리밍 응답 |
| GET  | `/chat/history` | 대화 이력 조회 |
| POST | `/chat/reset` | 대화 이력 초기화 |
| POST | `/chat/note` | 분석 결과 메모 저장 |

### 이미지 분석

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/analyze` | 이미지 단일 분석 (Vision API) |

### 영수증 관리

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/grocery/extract` | 이미지/PDF → JSON 추출 |
| POST | `/grocery/extract/text` | 텍스트 → JSON 추출 |
| POST | `/grocery/save` | 영수증 DB 저장 + 재고 업데이트 |
| GET  | `/grocery/history` | 영수증 목록 조회 |
| GET  | `/grocery/recent` | 최근 구매 이력 |
| GET  | `/grocery/receipt/{id}` | 영수증 상세 조회 |
| PUT  | `/grocery/receipt/{id}` | 영수증 메타 수정 |
| DELETE | `/grocery/receipt/{id}` | 영수증 삭제 → 재고 차감 |
| POST | `/grocery/items` | 품목 추가 |
| PUT  | `/grocery/items/{id}` | 품목 수정 |
| DELETE | `/grocery/items/{id}` | 품목 삭제 |
| POST | `/grocery/suggest-names` | 한국어 맞춤법 교정 제안 |

### 재고 관리

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET  | `/pantry/items` | 재고 목록 |
| POST | `/pantry/items` | 품목 추가 |
| PUT  | `/pantry/items/{id}` | 품목 수정 |
| DELETE | `/pantry/items/{id}` | 품목 삭제 |

### 휴지통

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET  | `/trash/preview` | 삭제 예정 항목 미리보기 |
| POST | `/trash/move` | 영수증 → 휴지통 이동 |
| GET  | `/trash/items` | 휴지통 목록 |
| POST | `/trash/items/{id}/restore` | 영수증 복원 → 재고 재추가 |
| DELETE | `/trash/items/{id}` | 영구 삭제 |
| DELETE | `/trash/empty` | 전체 비우기 |
| GET  | `/trash/merchants` | 업체별 통계 |

### 인증

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/auth/login` | 로그인 (rate limit 5회/15분) |
| POST | `/auth/register` | 회원가입 (비밀번호 복잡도 검증) |
| POST | `/auth/logout` | 로그아웃 |
| GET  | `/auth/session` | 현재 세션 확인 |

### 메모리 및 푸시

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET  | `/memory` | 장기 메모리 조회 |
| POST | `/memory` | 메모리 저장 |
| DELETE | `/memory` | 전체 메모리 삭제 |
| DELETE | `/memory/{key}` | 특정 키 삭제 |
| POST | `/push/register` | 푸시 토큰 등록 |

---

## 9. 의존성

### Python (`requirements.txt`)

| 패키지 | 버전 | 용도 |
|--------|------|------|
| `openai` | >=1.0.0 | Azure OpenAI SDK |
| `httpx` | >=0.27.0 | 비동기 HTTP (환율 조회) |
| `python-dotenv` | >=1.0.0 | `.env` 파일 로드 |
| `fastapi` | >=0.111.0 | 웹 프레임워크 |
| `uvicorn[standard]` | >=0.30.0 | ASGI 서버 |
| `psycopg2-binary` | >=2.9.0 | PostgreSQL 드라이버 |
| `python-multipart` | >=0.0.9 | 파일 업로드 |
| `pymupdf` | >=1.24.0 | PDF → 이미지 변환 (fitz) |
| `Pillow` | >=10.0.0 | 이미지 리사이즈/EXIF 보정 |

### Node.js (`mobile/package.json`)

| 패키지 | 버전 | 용도 |
|--------|------|------|
| `@capacitor/core` | ^6.0.0 | Capacitor 코어 |
| `@capacitor/camera` | ^6.0.0 | 카메라/사진 |
| `@capacitor/filesystem` | ^6.0.0 | 파일 저장 |
| `@capacitor/push-notifications` | ^6.0.0 | APNs 푸시 |
| `@capacitor/share` | ^6.0.0 | iOS 공유 시트 |

---

## 10. 주요 기능 요약

### 아키텍처 패턴

| 패턴 | 적용 위치 |
|------|----------|
| **Server-Side Sessions** | `user_sessions` 테이블 + `sid` HttpOnly 쿠키 |
| **Dependency Injection** | FastAPI `Depends(get_current_user)` |
| **Repository Pattern** | `db.py`가 모든 데이터 접근 캡슐화 |
| **Strategy Pattern** | `_connection_factory`로 테스트/프로덕션 연결 전환 |
| **Observer (Hook)** | PostToolUse 훅으로 코드 변경 시 자동 테스트 |
| **Background Task** | `asyncio.create_task` 기반 매일 8시 KST 휴지통 정리 |
| **Progressive Enhancement** | `isNativeApp()` → iOS/웹 동작 자동 분기 |

### 보안 구현

| 위협 | 방어 |
|------|------|
| 브루트포스 | Rate limit (15분/5회) — `RateLimiter` 클래스 |
| 타이밍 공격 | `secrets.compare_digest()` |
| XSS → 쿠키 탈취 | `HttpOnly` 쿠키 |
| CSRF | `SameSite=lax` |
| 비밀번호 유출 | PBKDF2-SHA256 300,000회 해싱 |
| 동시 세션 남용 | 사용자당 최대 5개 세션 제한 |

### 데이터 흐름

```
영수증 이미지
    ↓ (업로드)
grocery_pass1.py (Azure Vision LLM)
    ↓ (JSON 추출)
db.save_grocery_receipt()
    ↓
db.upsert_pantry_from_purchase()   ← 재고 자동 업데이트
    ↓
pantry_items 테이블
```

```
삭제 흐름:
grocery_receipts → trash_bin (30일 후 자동 삭제)
동시에 pantry_items 재고 차감

복원 흐름:
trash_bin → grocery_receipts
동시에 pantry_items 재고 재추가
```

---

*이 문서는 `/home/user/opensimul` 기준으로 자동 생성되었습니다.*
