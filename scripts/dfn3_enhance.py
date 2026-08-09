"""Standalone DeepFilterNet3 enhancement runner (sidecar).

Invoked by ``orivellum.capabilities.enhancement`` as a subprocess when
DeepFilterNet cannot be imported into the server's own interpreter (no
prebuilt wheels exist for Python >= 3.12).  Runs under Python 3.11 via
``uv run`` with pinned packages — see ``_SIDECAR_WITH`` in enhancement.py.

Usage:
    python dfn3_enhance.py <input_audio> <output_wav>

Exit codes:
    0 — enhanced audio written to <output_wav>
    1 — failure (message on stderr)
"""
from __future__ import annotations

import sys
from pathlib import Path

NATIVE_SR = 48_000  # DeepFilterNet3 full-band native sample rate


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: dfn3_enhance.py <input_audio> <output_wav>", file=sys.stderr)
        return 1
    in_path, out_path = Path(sys.argv[1]), Path(sys.argv[2])

    import torch          # noqa: PLC0415
    import torchaudio     # noqa: PLC0415
    from df import enhance, init_df  # noqa: PLC0415

    # init_df() returns 3 values on 0.5.6 and 4 on 0.5.7+.
    result = init_df()
    model, df_state = result[0], result[1]

    audio, sr = torchaudio.load(str(in_path))
    if audio.shape[0] > 1:
        audio = torch.mean(audio, dim=0, keepdim=True)
    if sr != NATIVE_SR:
        audio = torchaudio.transforms.Resample(sr, NATIVE_SR)(audio)

    enhanced = enhance(model, df_state, audio)
    torchaudio.save(str(out_path), enhanced, NATIVE_SR)
    print(f"enhanced {enhanced.shape[-1] / NATIVE_SR:.1f}s -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
