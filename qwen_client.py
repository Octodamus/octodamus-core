from openai import OpenAI
import re


def get_qwen_client(api_key: str) -> OpenAI:
    return OpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1"
    )


def qwen_complete(
    client: OpenAI,
    prompt: str,
    model: str = "qwen/qwen3.5-flash-02-23",
    system: str = None,
    max_tokens: int = 1024,
    thinking: bool = False
) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    extra = {}
    if thinking:
        extra["extra_body"] = {"reasoning": {"effort": "high"}}

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        **extra
    )

    content = response.choices[0].message.content or ""
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    return content
