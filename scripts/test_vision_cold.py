"""Test qwen3-vl:8b cold-load with different image sizes to isolate the 500 crash."""
import base64
import io
import time

import httpx
from PIL import Image


def unload(model):
    httpx.post("http://localhost:11434/api/generate",
               json={"model": model, "keep_alive": 0}, timeout=10)
    time.sleep(2)


def test_image(size, model="qwen3-vl:8b", num_ctx=4096):
    img = Image.new("RGB", size, color=(128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    img_b64 = base64.b64encode(buf.getvalue()).decode()
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "What color?", "images": [img_b64]}],
        "options": {"num_ctx": num_ctx},
        "stream": False,
    }
    resp = httpx.post("http://localhost:11434/api/chat", json=payload, timeout=60)
    if resp.status_code == 200:
        content = resp.json().get("message", {}).get("content", "")
        return f"OK: {content[:60]}"
    return f"ERROR {resp.status_code}: {resp.text[:150]}"


if __name__ == "__main__":
    model = "qwen3-vl:8b"

    for size, label in [((8, 8), "8x8"), ((64, 64), "64x64"), ((256, 256), "256x256")]:
        print(f"\nTest {label} cold-load...")
        unload(model)
        result = test_image(size, model)
        print(f"  {result}")

    # Warm test
    print("\nTest 8x8 warm (already loaded)...")
    result = test_image((8, 8), model)
    print(f"  {result}")
