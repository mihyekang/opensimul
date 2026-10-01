"""
Usage examples for AzureOpenAIClient.
Run: python example.py
Requires .env file with AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY.
"""

import logging

from azure_openai_client import AzureOpenAIClient, ClientConfig

logging.basicConfig(level=logging.INFO)


def example_multi_turn():
    """Reproduces the original test script as a multi-turn conversation."""
    config = ClientConfig(verify_ssl=False)  # set verify_ssl=True in production
    client = AzureOpenAIClient(config=config, system_prompt="You are a helpful assistant.")

    reply = client.chat("I am going to Paris, what should I see?")
    print("[User] I am going to Paris, what should I see?")
    print(f"[Assistant] {reply}\n")

    reply = client.chat("What is so great about #1?")
    print("[User] What is so great about #1?")
    print(f"[Assistant] {reply}\n")


def example_streaming():
    """Stream the response token by token."""
    config = ClientConfig(verify_ssl=False)
    client = AzureOpenAIClient(config=config)

    print("[User] Give me a one-sentence fun fact about Paris.")
    print("[Assistant] ", end="", flush=True)
    for token in client.stream_chat("Give me a one-sentence fun fact about Paris."):
        print(token, end="", flush=True)
    print()


if __name__ == "__main__":
    example_multi_turn()
    example_streaming()
