"""
Pass 1 영수증 추출 테스트 스크립트.

사용법:
  python grocery_pass1.py receipt.jpg
  python grocery_pass1.py receipt.jpg --raw        # LLM 원본 응답 출력
  python grocery_pass1.py img1.jpg img2.jpg img3.jpg  # 여러 장 비교
"""

import argparse
import base64
import json
import sys
import warnings
from pathlib import Path

from dotenv import load_dotenv
from openai import AzureOpenAI
import httpx

from azure_openai_client import ClientConfig

load_dotenv()

# ── Pass 1 프롬프트 ────────────────────────────────────────────────────────────
PASS1_SYSTEM = """You are a receipt data extractor.
Your ONLY job is to read text visible in the image and output JSON.

Strict rules:
1. Extract text EXACTLY as printed. No translation, interpretation, or classification.
2. Numbers: digits only. Strip commas and currency symbols. "12,900원" → 12900
3. Unreadable or missing field → null. Never guess or infer.
4. purchase_date → YYYY-MM-DD. Partial dates: fill what is visible, null the rest.
5. merchant → store or platform name as printed (이마트, 쿠팡, 네이버쇼핑, etc.)
6. raw_name → copy item name character-by-character as it appears.
7. qty → integer. If not shown, use 1.
8. unit_price → null if not printed separately.
9. If sum(items.amount) ≠ total: add "needs_review": true at root level.
10. is_refund → true only if the receipt is clearly a refund or cancellation.
11. Return ONLY the JSON object. No markdown fences, no explanation."""

PASS1_USER = """Extract all purchase data from this receipt image.

Return exactly this JSON structure:
{
  "merchant": "string or null",
  "purchase_date": "YYYY-MM-DD or null",
  "currency": "KRW",
  "items": [
    {"raw_name": "string", "qty": 1, "unit_price": null, "amount": 0}
  ],
  "total": 0,
  "is_refund": false
}"""


# ── 추출 함수 ──────────────────────────────────────────────────────────────────

def extract_pass1_bytes(image_bytes: bytes, mime_type: str, config: ClientConfig) -> tuple[dict, str]:
    """bytes로 받은 이미지에서 Pass 1 JSON 추출. (result_dict, raw_text) 반환."""
    b64 = base64.b64encode(image_bytes).decode()

    client = AzureOpenAI(
        azure_endpoint=config.endpoint,
        api_key=config.api_key,
        api_version=config.api_version,
        http_client=httpx.Client(verify=config.verify_ssl),
    )

    response = client.chat.completions.create(
        model=config.deployment,
        messages=[
            {"role": "system", "content": PASS1_SYSTEM},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
                {"type": "text", "text": PASS1_USER},
            ]},
        ],
        max_completion_tokens=4096,
        temperature=0,
    )

    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    return json.loads(raw), raw


def extract_pass1(image_path: str, config: ClientConfig) -> tuple[dict, str]:
    """파일 경로로 Pass 1 추출 (CLI용)."""
    suffix = Path(image_path).suffix.lower().lstrip(".")
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "webp": "image/webp", "gif": "image/gif"}.get(suffix, "image/jpeg")
    with open(image_path, "rb") as f:
        return extract_pass1_bytes(f.read(), mime, config)


# ── 검증 ──────────────────────────────────────────────────────────────────────

def validate(result: dict) -> list[str]:
    issues = []
    if not result.get("merchant"):
        issues.append("⚠  merchant: null")
    if not result.get("purchase_date"):
        issues.append("⚠  purchase_date: null")

    items = result.get("items", [])
    if not items:
        issues.append("🔴 items: 비어있음")
    else:
        null_amounts = [i["raw_name"] for i in items if i.get("amount") in (None, 0)]
        if null_amounts:
            issues.append(f"⚠  amount=0/null 항목: {null_amounts}")

    total = result.get("total", 0) or 0
    items_sum = sum(i.get("amount") or 0 for i in items)
    if items and total and abs(items_sum - total) > 1:
        issues.append(f"⚠  합계 불일치: items합={items_sum:,} total={total:,} (차이 {items_sum-total:+,})")

    if result.get("needs_review"):
        issues.append("🔴 needs_review: true (모델이 합계 불일치 감지)")
    if result.get("is_refund"):
        issues.append("ℹ  is_refund: true (환불 영수증)")

    return issues


# ── 출력 ──────────────────────────────────────────────────────────────────────

def report(path: str, result: dict, issues: list[str], raw: str, show_raw: bool):
    print(f"\n{'='*60}")
    print(f"📄 {path}")
    print(f"{'='*60}")
    print(f"  merchant      : {result.get('merchant')}")
    print(f"  purchase_date : {result.get('purchase_date')}")
    print(f"  total         : {result.get('total'):,}" if result.get('total') else "  total         : null")
    print(f"  items ({len(result.get('items', []))}개)")
    for item in result.get("items", []):
        qty = item.get("qty", 1)
        amt = item.get("amount")
        up = item.get("unit_price")
        amt_str = f"{amt:,}원" if amt else "null"
        up_str  = f"  (단가 {up:,})" if up else ""
        print(f"    [{qty}x] {item.get('raw_name')}  →  {amt_str}{up_str}")

    if issues:
        print("\n[검증 결과]")
        for iss in issues:
            print(f"  {iss}")
    else:
        print("\n  ✅ 검증 통과")

    if show_raw:
        print("\n[LLM 원본 응답]")
        print(raw)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="영수증 Pass 1 추출 테스트")
    parser.add_argument("images", nargs="+", help="이미지 파일 경로")
    parser.add_argument("--raw", action="store_true", help="LLM 원본 응답 출력")
    args = parser.parse_args()

    warnings.filterwarnings("ignore")  # SSL 경고 억제
    config = ClientConfig()

    ok = err = 0
    for path in args.images:
        try:
            result, raw = extract_pass1(path, config)
            issues = validate(result)
            report(path, result, issues, raw, args.raw)
            ok += 1
        except json.JSONDecodeError as e:
            print(f"\n🔴 {path}: JSON 파싱 실패 — {e}")
            if args.raw:
                print(raw)
            err += 1
        except Exception as e:
            print(f"\n🔴 {path}: 오류 — {e}")
            err += 1

    if len(args.images) > 1:
        print(f"\n{'='*60}")
        print(f"결과: {ok}개 성공 / {err}개 실패")


if __name__ == "__main__":
    main()
