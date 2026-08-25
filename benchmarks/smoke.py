#!/usr/bin/env python3
"""Repeatable OpenAI-compatible local-model smoke and throughput suite."""

import argparse
import base64
import json
import mimetypes
import time
import urllib.request


def post(url, payload, timeout=900):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.load(response)
    return round(time.monotonic() - started, 3), result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8002/v1")
    parser.add_argument("--model", required=True)
    parser.add_argument("--image")
    parser.add_argument("--output")
    parser.add_argument("--reasoning-effort", default="medium", choices=["low", "medium", "high", "xhigh"])
    args = parser.parse_args()
    tests = [
        ("chinese", [{"role": "user", "content": "用恰好三句话解释 MoE 路由、负载均衡和 CPU 卸载的关系。"}], None),
        ("code", [{"role": "user", "content": "写 Python 函数 merge_intervals，并给出恰好三个 assert；只输出代码。"}], None),
        ("tool", [{"role": "user", "content": "查 Toronto 当前天气。必须调用工具，不要猜。"}], [{"type": "function", "function": {"name": "get_weather", "description": "Get current weather", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}]),
        ("reasoning", [{"role": "user", "content": "A shop discounts an item by 20%, then raises the discounted price by 25%. Is the final price above, below, or equal to the original? Explain briefly."}], None),
    ]
    if args.image:
        mime = mimetypes.guess_type(args.image)[0] or "image/png"
        with open(args.image, "rb") as image_file:
            encoded = base64.b64encode(image_file.read()).decode("ascii")
        tests.append(("vision", [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}, {"type": "text", "text": "客观描述图片，不要猜测看不见的内容。"}]}], None))

    elapsed, completion = post(args.base.removesuffix("/v1") + "/completion", {
        "prompt": "Explain why explicit GPU ownership matters when multiple AI services share one workstation.",
        "n_predict": 256,
        "temperature": 0,
        "cache_prompt": False,
    })
    output = {"model": args.model, "native_completion": {
        "elapsed_s": elapsed,
        "prompt_tok_s": completion.get("timings", {}).get("prompt_per_second"),
        "decode_tok_s": completion.get("timings", {}).get("predicted_per_second"),
        "predicted_n": completion.get("tokens_predicted"),
        "draft_n": completion.get("timings", {}).get("draft_n"),
        "draft_n_accepted": completion.get("timings", {}).get("draft_n_accepted"),
    }}
    for name, messages, tools in tests:
        payload = {"model": args.model, "messages": messages, "max_tokens": 768, "temperature": 0.2, "reasoning_effort": args.reasoning_effort}
        if tools:
            payload.update({"tools": tools, "tool_choice": "auto"})
        elapsed, response = post(args.base + "/chat/completions", payload)
        output[name] = {"elapsed_s": elapsed, "response": response}
        message = response.get("choices", [{}])[0].get("message", {})
        timings = response.get("timings", {})
        print(json.dumps({
            "test": name,
            "elapsed_s": elapsed,
            "content": message.get("content"),
            "tool_calls": message.get("tool_calls"),
            "decode_tok_s": timings.get("predicted_per_second"),
            "draft_n": timings.get("draft_n"),
            "draft_n_accepted": timings.get("draft_n_accepted"),
        }, ensure_ascii=False))
    if args.output:
        with open(args.output, "w", encoding="utf-8") as output_file:
            json.dump(output, output_file, ensure_ascii=False, indent=2)
            output_file.write("\n")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
