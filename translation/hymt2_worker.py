"""Hy-MT2 runtime entrypoint for the isolated Transformers environment."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path


class Runtime:
    def __init__(self, model_path: Path, device: str) -> None:
        self.model_path = model_path
        self.requested_device = device
        self.model = None
        self.tokenizer = None
        self.device = None

    def load(self) -> None:
        if self.model is not None:
            return
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        device = self.requested_device
        if device == "auto":
            if torch.cuda.is_available():
                device = "cuda"
            elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                device = "mps"
            else:
                device = "cpu"
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but is unavailable in the Hy-MT2 runtime.")
        if device == "mps" and not (
            getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()
        ):
            raise RuntimeError("MPS was requested but is unavailable in the Hy-MT2 runtime.")

        if device == "mps" or device.startswith("cuda"):
            dtype = torch.float16
        else:
            dtype = torch.bfloat16
        self.tokenizer = AutoTokenizer.from_pretrained(
            self.model_path, local_files_only=True, trust_remote_code=True
        )
        self.model = AutoModelForCausalLM.from_pretrained(
            self.model_path,
            local_files_only=True,
            trust_remote_code=True,
            dtype=dtype,
            low_cpu_mem_usage=True,
        )
        self.model.to(torch.device(device))
        self.model.eval()
        self.device = torch.device(device)

    def _max_new_tokens(self, prompt: str) -> int:
        """Choose a bounded generation budget from the spoken-length target.

        Hy-MT2 occasionally continues past a complete translation when EOS is
        not emitted.  A duration-derived ceiling leaves room for natural
        phrasing while preventing an unbounded tail; it is not a one-size-fits-
        all token cap.
        """
        match = re.search(r"Target spoken-length budget:\s*approximately\s*(\d+)", prompt)
        units = int(match.group(1)) if match else 24
        # Roughly 2.5 subword tokens per spoken unit plus instruction margin.
        return max(64, min(192, int(units * 2.5 + 32)))

    def generate(self, prompt: str, *, deterministic: bool = False) -> tuple[str, dict[str, object]]:
        import torch

        self.load()
        messages = [{"role": "user", "content": prompt}]
        inputs = self.tokenizer.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        )
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        input_length = inputs["input_ids"].shape[-1]
        max_new_tokens = self._max_new_tokens(prompt)
        if os.environ.get("HYMT2_SEED") is not None:
            torch.manual_seed(int(os.environ["HYMT2_SEED"]))
        started = time.perf_counter()
        generation_options = {
            "do_sample": not deterministic,
            "repetition_penalty": 1.05,
            # Dubbing groups are short utterances.  Bounding generation
            # prevents pathological chain-of-thought tails (hundreds of
            # unused tokens) while retaining ample room for translation
            # and duration rewrites.
            "max_new_tokens": max_new_tokens,
        }
        if not deterministic:
            generation_options.update(temperature=0.7, top_p=0.6, top_k=20)
        with torch.inference_mode():
            outputs = self.model.generate(**inputs, **generation_options)
        generated_ids = outputs[0][input_length:]
        eos_id = self.tokenizer.eos_token_id
        eos_seen = bool(eos_id is not None and (generated_ids == eos_id).any().item())
        generated_count = int(generated_ids.shape[-1])
        generation_wall_seconds = time.perf_counter() - started
        stop_reason = "eos" if eos_seen else ("length" if generated_count >= max_new_tokens else "unknown")
        stats = {
            "prompt_tokens": int(input_length),
            "generated_tokens": generated_count,
            "max_new_tokens": max_new_tokens,
            "eos_token_id": eos_id,
            "eos_seen": eos_seen,
            "stop_reason": stop_reason,
            "generation_wall_seconds": generation_wall_seconds,
            "deterministic": deterministic,
        }
        return self.tokenizer.decode(
            outputs[0][input_length:], skip_special_tokens=True
        ).strip(), stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--device", default="auto")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runtime = Runtime(args.model_path.resolve(), args.device)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if request.get("operation") == "shutdown":
                return 0
            request_id = request.get("id")
            if request.get("operation") == "prepare":
                started = time.perf_counter()
                runtime.load()
                text, stats = "", {"prepared": True, "model_load_seconds": time.perf_counter() - started}
            elif request.get("operation") in {"translate", "rewrite_for_duration", "simultaneous_proposal"}:
                text, stats = runtime.generate(
                    str(request.get("prompt", "")),
                    deterministic=(request.get("deterministic") is True and
                                   request.get("operation") in {
                                       "translate", "simultaneous_proposal"}),
                )
            else:
                raise ValueError(f"Unsupported operation: {request.get('operation')}")
            response = {"id": request_id, "ok": True, "text": text, "stats": stats}
        except Exception as exc:
            request_id = locals().get("request", {}).get("id")
            response = {
                "id": request_id,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
            traceback.print_exc(file=sys.stderr)
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
