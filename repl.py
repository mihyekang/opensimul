"""
Interactive REPL for Azure OpenAI chat with token/cost tracking.
Run: python repl.py
"""

import sys
from datetime import date

import httpx

from azure_openai_client import AzureOpenAIClient, ClientConfig

FALLBACK_USD_TO_KRW = 1_503.0  # 2026-05-27 기준 fallback


def fetch_usd_to_krw(verify_ssl: bool = True) -> tuple[float, str]:
    """실시간 USD/KRW 환율 조회. 실패 시 fallback 값 반환."""
    try:
        resp = httpx.get(
            "https://api.frankfurter.dev/v1/latest",
            params={"from": "USD", "to": "KRW"},
            timeout=5.0,
            verify=verify_ssl,
        )
        resp.raise_for_status()
        data = resp.json()
        rate = data["rates"]["KRW"]
        fetched_date = data.get("date", str(date.today()))
        return float(rate), fetched_date
    except Exception as e:
        print(f"  [환율 조회 실패: {e}] fallback 값 사용 (₩{FALLBACK_USD_TO_KRW:,.0f})", file=sys.stderr)
        return FALLBACK_USD_TO_KRW, "fallback"


HELP = """
Commands:
  /reset [prompt]  — clear conversation (optional: new system prompt)
  /history         — show full conversation so far
  /cost            — show token usage and estimated cost
  /quit            — exit
  /help            — show this message
""".strip()


def fmt_usd(dollars: float) -> str:
    return f"${dollars:.6f}"


def fmt_krw(dollars: float, usd_to_krw: float) -> str:
    won = dollars * usd_to_krw
    if won < 0.01:
        return "₩0.00"
    return f"₩{won:,.2f}"


def fmt_cost(dollars: float, usd_to_krw: float) -> str:
    return f"{fmt_usd(dollars)}  ({fmt_krw(dollars, usd_to_krw)})"


def show_usage(client: AzureOpenAIClient, usd_to_krw: float) -> None:
    last = client.last_usage
    if last:
        turn_cost = last.cost(client.config.input_price_per_m, client.config.output_price_per_m)
        print(
            f"  tokens: {last.prompt_tokens} in / {last.completion_tokens} out"
            f"  |  turn: {fmt_cost(turn_cost, usd_to_krw)}"
            f"  |  누적: {fmt_cost(client.total_cost, usd_to_krw)} ({client.total_tokens} tokens)"
        )


def run() -> None:
    config = ClientConfig()
    usd_to_krw, rate_date = fetch_usd_to_krw(verify_ssl=config.verify_ssl)

    client = AzureOpenAIClient(config=config, base_system_prompt="You are a helpful assistant.")

    print(f"Azure OpenAI REPL  |  deployment: {config.deployment}")
    print(f"환율: 1 USD = ₩{usd_to_krw:,.1f}  ({rate_date} 기준, frankfurter.app)")
    print("Type /help for commands, /quit to exit.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            cmd, _, arg = user_input.partition(" ")
            cmd = cmd.lower()

            if cmd == "/quit":
                print("\nSession ended.")
                print(f"  총 토큰  : {client.total_tokens}")
                print(f"  총 비용  : {fmt_cost(client.total_cost, usd_to_krw)}")
                break
            elif cmd == "/help":
                print(HELP)
            elif cmd == "/cost":
                if not client.last_usage:
                    print("  No messages sent yet.")
                else:
                    print(f"  총 토큰  : {client.total_tokens}")
                    print(f"  총 비용  : {fmt_cost(client.total_cost, usd_to_krw)}")
                    print(f"  단가     : ${client.config.input_price_per_m}/M input, ${client.config.output_price_per_m}/M output")
                    print(f"  환율     : 1 USD = ₩{usd_to_krw:,.1f}  ({rate_date})")
            elif cmd == "/history":
                for msg in client.history:
                    role = msg["role"].upper()
                    print(f"  [{role}] {msg['content'][:120]}{'...' if len(msg['content']) > 120 else ''}")
            elif cmd == "/reset":
                new_prompt = arg.strip() or None
                client.reset(system_prompt=new_prompt)
                print(f"  Conversation reset.  System prompt: \"{new_prompt or 'You are a helpful assistant.'}\"")
            else:
                print(f"  Unknown command: {cmd}. Type /help.")
            continue

        print("Assistant: ", end="", flush=True)
        try:
            for token in client.stream_chat(user_input):
                print(token, end="", flush=True)
            print()
            show_usage(client, usd_to_krw)
            print()
        except Exception as e:
            print(f"\n[Error] {e}", file=sys.stderr)


if __name__ == "__main__":
    run()
