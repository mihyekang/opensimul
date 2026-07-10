"""
Tool definitions and executor for the spending analysis agent.
"""

import json
import logging
from datetime import date, timedelta
from typing import Any

import db

logger = logging.getLogger(__name__)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "query_spending_by_period",
            "description": "특정 기간의 지출 이력을 조회합니다. 영수증 목록과 총 지출액을 반환합니다. 이번 달, 지난주 등 날짜 범위 질문에 사용합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "조회 시작일 (YYYY-MM-DD 형식)"
                    },
                    "end_date": {
                        "type": "string",
                        "description": "조회 종료일 (YYYY-MM-DD 형식)"
                    }
                },
                "required": ["start_date", "end_date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_spending_by_merchant",
            "description": "특정 업체(마트/쇼핑몰)의 구매 이력과 총 지출액을 조회합니다. 예: 이마트에서 얼마 썼어?",
            "parameters": {
                "type": "object",
                "properties": {
                    "merchant": {
                        "type": "string",
                        "description": "업체명 (예: 이마트, 쿠팡, 네이버쇼핑)"
                    },
                    "days": {
                        "type": "integer",
                        "description": "최근 N일 이내 조회 (기본값: 90)"
                    }
                },
                "required": ["merchant"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_items",
            "description": "품목명 키워드로 구매 이력을 검색합니다. 특정 상품을 언제 얼마에 샀는지 찾을 때 사용합니다. 예: 우유 언제 마지막으로 샀어?",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "검색할 품목명 키워드 (예: 우유, 계란, 세제)"
                    },
                    "days": {
                        "type": "integer",
                        "description": "최근 N일 이내 검색 (기본값: 90)"
                    }
                },
                "required": ["keyword"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_spending_summary",
            "description": "기간별·업체별 지출 통계를 집계합니다. 어디서 얼마나 썼는지 요약할 때 사용합니다. 예: 이번 달 지출 요약해줘, 어디서 제일 많이 썼어?",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "조회 시작일 (YYYY-MM-DD). 미입력 시 30일 전."
                    },
                    "end_date": {
                        "type": "string",
                        "description": "조회 종료일 (YYYY-MM-DD). 미입력 시 오늘."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_pantry_current_qty",
            "description": "재료를 사용/소비했을 때 팬트리 현재 재고를 차감합니다. '계란 3개 썼어', '우유 다 마셨어', '라면 2개 먹었어' 같은 말을 들으면 사용합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_name": {
                        "type": "string",
                        "description": "차감할 품목명 (예: 계란, 우유)"
                    },
                    "qty_used": {
                        "type": "integer",
                        "description": "사용한 수량"
                    }
                },
                "required": ["item_name", "qty_used"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_pantry_items",
            "description": "냉장고/팬트리 재고 현황을 조회합니다. 특정 재료가 얼마나 남았는지, 재고 현황을 확인할 때 사용합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "조회할 품목 키워드 (예: 계란, 우유). 비워두면 전체 재고."
                    }
                },
                "required": []
            }
        }
    },
]


def execute_tool(name: str, args: dict, user_id: str) -> Any:
    """Execute a named tool and return JSON-serializable result."""
    try:
        if name == "query_spending_by_period":
            return _query_spending_by_period(args["start_date"], args["end_date"], user_id)
        if name == "query_spending_by_merchant":
            return _query_spending_by_merchant(args["merchant"], args.get("days", 90), user_id)
        if name == "search_items":
            return _search_items(args["keyword"], args.get("days", 90), user_id)
        if name == "get_spending_summary":
            return _get_spending_summary(
                args.get("start_date") or (date.today() - timedelta(days=30)).isoformat(),
                args.get("end_date") or date.today().isoformat(),
                user_id,
            )
        if name == "update_pantry_current_qty":
            return db.deduct_pantry_qty(user_id, args["item_name"], args["qty_used"])
        if name == "get_pantry_items":
            return _get_pantry_items(args.get("keyword", ""), user_id)
        return {"error": f"Unknown tool: {name}"}
    except Exception as e:
        logger.error("Tool execution error [%s]: %s", name, e)
        return {"error": str(e)}


def _query_spending_by_period(start_date: str, end_date: str, user_id: str) -> dict:
    rows = db.list_grocery_receipts(start_date=start_date, end_date=end_date, user_id=user_id)
    total = sum((r.get("total") or 0) for r in rows)
    return {
        "period": f"{start_date} ~ {end_date}",
        "receipt_count": len(rows),
        "total_spent": total,
        "receipts": [
            {
                "id": r["id"],
                "date": str(r.get("purchase_date") or ""),
                "merchant": r.get("merchant"),
                "total": r.get("total"),
                "item_count": r.get("item_count", 0),
            }
            for r in rows
        ],
    }


def _query_spending_by_merchant(merchant: str, days: int, user_id: str) -> dict:
    rows = db.search_receipts_by_merchant(merchant, days, user_id)
    total = sum((r.get("total") or 0) for r in rows)
    return {
        "merchant_query": merchant,
        "receipt_count": len(rows),
        "total_spent": total,
        "receipts": [
            {
                "id": r["id"],
                "date": str(r.get("purchase_date") or ""),
                "merchant": r.get("merchant"),
                "total": r.get("total"),
            }
            for r in rows
        ],
    }


def _search_items(keyword: str, days: int, user_id: str) -> dict:
    rows = db.search_grocery_items(keyword, days, user_id)
    return {
        "keyword": keyword,
        "match_count": len(rows),
        "items": [
            {
                "name": r.get("raw_name"),
                "qty": r.get("qty"),
                "amount": r.get("amount"),
                "merchant": r.get("merchant"),
                "date": str(r.get("purchase_date") or ""),
            }
            for r in rows
        ],
    }


def _get_spending_summary(start_date: str, end_date: str, user_id: str) -> dict:
    rows = db.list_grocery_receipts(start_date=start_date, end_date=end_date, user_id=user_id)
    by_merchant: dict[str, int] = {}
    total = 0
    for r in rows:
        m = r.get("merchant") or "기타"
        amt = r.get("total") or 0
        by_merchant[m] = by_merchant.get(m, 0) + amt
        total += amt
    sorted_merchants = sorted(by_merchant.items(), key=lambda x: x[1], reverse=True)
    return {
        "period": f"{start_date} ~ {end_date}",
        "total_spent": total,
        "receipt_count": len(rows),
        "by_merchant": [{"merchant": m, "total": t} for m, t in sorted_merchants],
    }


def _get_pantry_items(keyword: str, user_id: str) -> dict:
    rows = db.list_pantry(user_id)
    if keyword:
        rows = [r for r in rows if keyword.lower() in r["raw_name"].lower()]
    return {
        "keyword": keyword or "(전체)",
        "item_count": len(rows),
        "items": [
            {
                "name": r["raw_name"],
                "total_qty": r["total_qty"],
                "current_qty": r["current_qty"],
                "unit": r["unit"] or "개",
                "last_updated": r.get("updated_at", ""),
            }
            for r in rows
        ],
    }
