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
4. purchase_date → YYYY-MM-DD.
   • "2026. 5. 26" or "2026.05.28" → "2026-05-28"
   • "5.25. 22:39 주문" or "5/26(화)" → use the most plausible year from context → "2026-05-25"
   • Order history screens with multiple dates: use the most recent date.
   • Partial or unreadable date → null.
5. merchant → scan the ENTIRE image (header, footer, watermark, stamp, badge, nav bar).
   Korean store heuristics — map ANY matching signal to canonical name:
   ── 오프라인 대형마트 ──
   • "E-MART" / "이마트" / "EMART" / "트레이더스" → "이마트" (include branch "○○점" if visible)
   • "피코크" brand item present → "이마트"
   • "(*)면세물품" AND "과세물품" AND "부가세" lines all present → "이마트"
   • Item lines formatted as "NN" or "NN*" (two-digit number + optional asterisk) followed by
     a 13-digit barcode on the next line → "이마트"
   • "결제대상금액" label → "이마트"
   • "LOTTE MART" / "롯데마트" → "롯데마트"
   • "HOMEPLUS" / "홈플러스" → "홈플러스"
   • "COSTCO" / "코스트코" → "코스트코"
   • "GS THE FRESH" / "GS슈퍼마켓" → "GS더프레시"
   ── 편의점 ──
   • "GS25" / "CU" / "세븐일레븐" / "미니스톱" / "이마트24" → use as-is
   ── 온라인 ──
   • "로켓" / "판매자로켓" / "로켓배송" / "로켓프레시" badge visible → "쿠팡"
   • "N pay" / "Npay+" / "N pay 내역" / "네이버페이" visible → "네이버쇼핑"
   • "마켓컬리" → "마켓컬리"
   • "오아시스" → "오아시스"
   • "펫프렌즈" / "PETFRIENDS" / "petfriends" → "펫프렌즈"
   If no signal found → null.
6. raw_name → copy item name character-by-character.
   For app order history screens: product name only (strip "옵션:", "[무료배송]", status labels, etc.)
7. qty → integer. Default 1 if not shown.
8. unit_price → null if not separately printed.
9. Discounts: every discount / coupon / event-price line → SEPARATE item, NEGATIVE amount.
   raw_name: exact label (e.g. "피코크 행사", "회원할인", "즉시할인", "쿠폰할인").
   amount: negative integer (e.g. -440). Never modify the original item price.
10. Cancelled items: if an item shows "주문취소" / "취소" status, add "is_cancelled": true on that item.
    Still extract the name and amount. Do NOT include cancelled amounts in the total check.
11. total → prefer "결제대상금액" if present (이마트). Otherwise "합계" / "총결제금액" / "주문금액".
    For order history screens: sum of non-cancelled, non-discount items.
12. If sum(active items) ≠ total: add "needs_review": true.
13. is_refund → true only when the whole receipt/order is a refund or cancellation.
14. Return ONLY the JSON object. No markdown fences, no explanation."""

PASS1_USER = """Extract all purchase data from this receipt image.

Return exactly this JSON structure:
{
  "merchant": "string or null",
  "purchase_date": "YYYY-MM-DD or null",
  "currency": "KRW",
  "items": [
    {"raw_name": "상품명", "qty": 1, "unit_price": null, "amount": 9900, "is_cancelled": false},
    {"raw_name": "피코크 행사", "qty": 1, "unit_price": null, "amount": -440, "is_cancelled": false},
    {"raw_name": "취소된상품", "qty": 1, "unit_price": null, "amount": 5000, "is_cancelled": true}
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
    if result.get("_date_inferred"):
        issues.append("ℹ  purchase_date: 영수증에 날짜 없음 → 오늘 날짜로 대체")
    elif not result.get("purchase_date"):
        issues.append("⚠  purchase_date: null")

    items = result.get("items", [])
    if not items:
        issues.append("🔴 items: 비어있음")
    else:
        null_count = sum(1 for i in items if i.get("amount") is None and not i.get("is_cancelled"))
        zero_names = [i["raw_name"] for i in items if i.get("amount") == 0 and not i.get("is_cancelled")]
        discount_items = [i for i in items if (i.get("amount") or 0) < 0 and not i.get("is_cancelled")]
        cancelled_items = [i for i in items if i.get("is_cancelled")]
        if null_count:
            issues.append(f"ℹ  {null_count}개 항목 금액 미인식 (합산 제외)")
        if zero_names:
            issues.append(f"⚠  amount=0 항목: {zero_names}")
        if discount_items:
            discount_total = sum(i["amount"] for i in discount_items)
            issues.append(f"ℹ  할인 {len(discount_items)}건 포함 ({discount_total:,}원)")
        if cancelled_items:
            issues.append(f"ℹ  주문취소 {len(cancelled_items)}건 (합산 제외)")

    total = result.get("total", 0) or 0
    active_items = [i for i in items if i.get("amount") is not None and not i.get("is_cancelled")]
    items_sum = sum(i["amount"] for i in active_items)
    if active_items and total and abs(items_sum - total) > 1:
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
