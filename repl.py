"""
Interactive REPL for Azure OpenAI chat with token/cost tracking.
Run: python repl.py
"""

import sys

from azure_openai_client import AzureOpenAIClient, ClientConfig

HELP = """
Commands:
  /reset [prompt]  — clear conversation (optional: new system prompt)
  /history         — show full conversation so far
  /cost            — show token usage and estimated cost
  /quit            — exit
  /help            — show this message
""".strip()


def fmt_cost(dollars: float) -> str:
    if dollars < 0.000001:
        return "$0.000000"
    return f"${dollars:.6f}"


def show_usage(client: AzureOpenAIClient) -> None:
    last = client.last_usage
    if last:
        turn_cost = last.cost(client.config.input_price_per_m, client.config.output_price_per_m)
        print(
            f"  tokens: {last.prompt_tokens} in / {last.completion_tokens} out"
            f"  |  turn cost: {fmt_cost(turn_cost)}"
            f"  |  session total: {fmt_cost(client.total_cost)} ({client.total_tokens} tokens)"
        )


def run() -> None:
    config = ClientConfig()
    system_prompt = "You are a helpful assistant."
    client = AzureOpenAIClient(config=config, system_prompt=system_prompt)

    print(f"Azure OpenAI REPL  |  deployment: {config.deployment}")
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
                print(f"\nSession ended.  Total cost: {fmt_cost(client.total_cost)}  ({client.total_tokens} tokens)")
                break
            elif cmd == "/help":
                print(HELP)
            elif cmd == "/cost":
                if not client.last_usage:
                    print("  No messages sent yet.")
                else:
                    print(f"  Total tokens : {client.total_tokens}")
                    print(f"  Estimated cost: {fmt_cost(client.total_cost)}")
                    print(f"  Pricing: ${client.config.input_price_per_m}/M input, ${client.config.output_price_per_m}/M output")
            elif cmd == "/history":
                for msg in client.history:
                    role = msg["role"].upper()
                    print(f"  [{role}] {msg['content'][:120]}{'...' if len(msg['content']) > 120 else ''}")
            elif cmd == "/reset":
                new_prompt = arg.strip() or None
                client.reset(system_prompt=new_prompt)
                print(f"  Conversation reset.  System prompt: \"{new_prompt or system_prompt}\"")
            else:
                print(f"  Unknown command: {cmd}. Type /help.")
            continue

        print("Assistant: ", end="", flush=True)
        try:
            for token in client.stream_chat(user_input):
                print(token, end="", flush=True)
            print()
            show_usage(client)
            print()
        except Exception as e:
            print(f"\n[Error] {e}", file=sys.stderr)


if __name__ == "__main__":
    run()
