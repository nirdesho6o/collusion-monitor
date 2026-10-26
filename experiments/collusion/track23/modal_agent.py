"""Deploy an open-weight agent model on Modal L4 via vLLM for feasibility testing.

Model: NousResearch/Hermes-3-Llama-3.1-8B (Llama family, ungated, hermes tool-calling parser).
Different family from the Qwen3-8B monitor to avoid single-family confounds.

Based on Modal's official vLLM example (modal.com/docs/examples/llm_inference, Oct 2026).
"""

from __future__ import annotations

import json
import subprocess

import modal

VLLM_VERSION = "0.21.0"
MODEL_NAME = "NousResearch/Hermes-3-Llama-3.1-8B"

vllm_image = (
    modal.Image.from_registry("nvidia/cuda:12.9.0-devel-ubuntu22.04", add_python="3.12")
    .entrypoint([])
    .uv_pip_install(f"vllm=={VLLM_VERSION}")
    .env({"HF_XET_HIGH_PERFORMANCE": "1"})
)

hf_cache_vol = modal.Volume.from_name("huggingface-cache", create_if_missing=True)
vllm_cache_vol = modal.Volume.from_name("vllm-cache", create_if_missing=True)

app = modal.App("track23-agent")

VLLM_PORT = 8000
MINUTES = 60


@app.server(
    image=vllm_image,
    gpu="L4",
    scaledown_window=15 * MINUTES,
    startup_timeout=15 * MINUTES,
    volumes={
        "/root/.cache/huggingface": hf_cache_vol,
        "/root/.cache/vllm": vllm_cache_vol,
    },
    port=VLLM_PORT,
    target_concurrency=8,
    unauthenticated=True,
)
class Server:
    @modal.enter()
    def start(self):
        cmd = [
            "vllm", "serve", MODEL_NAME,
            "--served-model-name", MODEL_NAME,
            "--host", "0.0.0.0",
            "--port", str(VLLM_PORT),
            "--enforce-eager",
            "--tensor-parallel-size", "1",
            "--max-model-len", "8192",
            "--enable-auto-tool-choice",
            "--tool-call-parser", "hermes",
        ]
        print(" ".join(cmd))
        self.process = subprocess.Popen(cmd)

    @modal.exit()
    def stop(self):
        self.process.terminate()


@app.local_entrypoint()
async def test():
    """Print the server URL for use in experiment configs."""
    url = await Server.get_url.aio()
    print(f"Agent server URL: {url}")
    print(f"Use this as base_url in the vLLM config (append /v1):")
    print(f"  base_url: {url}/v1")

    import aiohttp
    import asyncio

    print(f"\nWaiting for server health at {url}...")
    async with aiohttp.ClientSession(base_url=url) as session:
        for _ in range(180):
            try:
                async with session.get("/health", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        break
            except Exception:
                pass
            await asyncio.sleep(5)
        else:
            print("Server did not become healthy in 15 minutes")
            return

    print("Server healthy!")

    # Quick tool-calling test
    async with aiohttp.ClientSession(base_url=url) as session:
        payload = {
            "model": MODEL_NAME,
            "messages": [
                {"role": "system", "content": "You are an assistant that uses tools."},
                {"role": "user", "content": "What's the weather in Paris?"},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get the current weather for a city",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "city": {"type": "string", "description": "City name"},
                            },
                            "required": ["city"],
                        },
                    },
                }
            ],
            "temperature": 0.7,
            "max_tokens": 256,
        }
        async with session.post(
            "/v1/chat/completions",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            data = await resp.json()
            msg = data["choices"][0]["message"]
            print(f"\nTool calling test:")
            print(f"  Content: {msg.get('content')}")
            print(f"  Tool calls: {msg.get('tool_calls')}")
            print(f"  Usage: {data.get('usage')}")
