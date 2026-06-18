# Third-Party Notices — OpenMW

## TurboQuant MSE quantizer (rotation + Lloyd-Max scalar codebook)

**Source repository:** https://github.com/scos-lab/turboquant  
**Vendored commit:** `34a10b639247dce1aa5f20e31428568586e6f52a`  
**Vendored files (under `openmw/vendor/turboquant/`):**

- `rotation.py` — random orthogonal rotation
- `scalar_quantizer.py` — Lloyd's algorithm on Beta((d−1)/2, (d−1)/2)
- `utils.py` — normalization helpers
- `mse_quantizer.py` — extracted `TurboQuantMSE` + `QuantizedMSE` from upstream `core.py`

**Paper:** Zandieh et al., *TurboQuant: Online Vector Quantization with Near-optimal Distortion Rate* (ICLR 2026, [arXiv:2504.19874](https://arxiv.org/abs/2504.19874)).

This is a **third-party reproduction** via scos-lab's reference implementation — **not** an official Google Research release (Google never published one). Upstream README also notes that full PolarQuant is not implemented; what is vendored is random orthogonal rotation plus Beta-distribution Lloyd–Max scalar quantization.

### Why TurboQuantMSE only (not TurboQuantProd / qjl.py)

Upstream `core.py` defines two quantizers: `TurboQuantMSE` (reconstruction-optimal, intended for **Value cache**) and `TurboQuantProd` (inner-product-optimal for **Key cache**, adds QJL residual correction). OpenMW uses **TurboQuantMSE** for Value-cache reconstruction.

We omit `TurboQuantProd` and `qjl.py` because independent reproduction ([SaschaOnTour/turboquant](https://github.com/SaschaOnTour/turboquant)) found the QJL-inclusive mode **increases variance and degrades attention quality** in practice — an engineering quality decision, not a convenience cut. TurboQuantMSE is used for **Value cache** only; Key cache uses a separate clean-room path (see below).

### MIT License

```
MIT License

Copyright (c) 2026 scos-lab (Syn-claude & wuko)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## Algorithm attribution (not vendored code)

### KIVI per-channel Key cache quantization

**Paper:** Liu et al., *KIVI: Plug-and-play 2-Bit KV Cache Quantization with Streaming Asymmetric Quantization* (ICML 2024, [arXiv:2402.02750](https://arxiv.org/abs/2402.02750)).

**Reference repository:** https://github.com/jy-yuan/KIVI

**OpenMW implementation:** `openmw/key_channel_quant.py` — clean-room NumPy reproduction of the published per-channel grouped quantization plus a full-precision residual window. **No source code was copied.** The upstream reference couples CUDA/Triton kernels into specific HuggingFace model classes; that integration does not extract cleanly the way TurboQuant's isolated math module did. OpenMW implements the algorithm from the paper description only, attributed here.

This section is intentionally separate from the TurboQuant vendoring block above: TurboQuant files are third-party MIT code checked in verbatim; KIVI is an independent algorithm reproduction with paper attribution only.

---

## Unsloth (optional training dependency)

**Source repository:** https://github.com/unslothai/unsloth  
**Documentation:** https://unsloth.ai/docs  

Unsloth is **not vendored** and **not a required OpenMW dependency. It is loaded optionally at runtime via `openmw/unsloth_bridge.py` when users install Unsloth on a CUDA-capable host for LoRA fine-tuning and GGUF export.

OpenMW calls Unsloth APIs (`FastLanguageModel.from_pretrained`, `get_peft_model`, `save_pretrained_gguf`, `save_pretrained_merged`, `for_training`, `for_inference`) as documented in `docs/research/PART5_findings.md`.

### License

Unsloth is distributed under the **Apache License 2.0**. See the upstream repository for the full license text and copyright notices.

