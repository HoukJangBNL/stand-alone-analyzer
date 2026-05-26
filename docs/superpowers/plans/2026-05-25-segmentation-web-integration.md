# Segmentation Web Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** SAM2.1 + LoRA inference (현재 stand-alone CLI `run_amg_v2.py`) 을 우리 FastAPI/React 스택의 정상 파이프라인 step 으로 통합한다. 이미지별 진행률 + 에러를 SSE로 웹에 노출, `runs` 테이블을 audit log로 활성화, GPU EC2(g6e.xlarge spot) 에서 실행 가능한 상태까지.

**Architecture:** Phase 1 = `HoukJangBNL/QPress-SAM-Flake` fork를 git submodule로 박고 inference-only entry point + 4 fixes + LoRA merge 스크립트 추가 (학습 코드 무손상). Phase 2 = `src/flake_analysis/pipeline/sam.py` + `src/flake_analysis/core/pipeline/sam.py` 추가, `PIPELINE_STEPS / SUBDIRS / ARTIFACTS`에 `sam` 슬롯 등록, `POST /projects/{pid}/scans/{sid}/run/sam` 라우트, 4개 step (background/sam/domain_stats/domain_proximity)에 `runs` 테이블 INSERT/UPDATE wiring. Phase 3 = 프론트엔드 SAM 패널 + `sseRun.ts` URL drift 수정 (기존 4 step도 같이). Phase 4 = GPU EC2 인스턴스 기동 (procrastinate worker가 같은 박스 / always-on g6e / S3에서 weights 다운로드) — **AWS state 변경 = owner 승인 게이트**.

**Tech Stack:**
- 인퍼런스: PyTorch 2.x, SAM2.1, peft (P1.6에서 제거 가능 옵션)
- 백엔드: FastAPI + asyncpg, 기존 `ProgressBridge` + `acquire_scan_lock(scan_id)` 재사용, `runs` 테이블 (alembic 0001부터 존재, dormant)
- 큐/워커: procrastinate (PG-backed, no Redis) — Phase 4에서 도입
- 프론트엔드: React 18 + TanStack Query + 기존 `useStepProgress` 훅 재사용
- AWS: EC2 g6e.xlarge spot (us-east-2 quota 192 vCPU 승인됨, 2026-05-20)
- 테스트: pytest + `pytest.mark.pg` (runs 테이블), vitest (프론트), procrastinate test harness

**Pre-read:**
- `src/flake_analysis/api/routes/run.py` (기존 4 step SSE 패턴 — `acquire_scan_lock`, `ProgressBridge`, `run_in_executor`)
- `src/flake_analysis/state/paths.py` (`PIPELINE_STEPS / SUBDIRS / ARTIFACTS` 레지스트리)
- `src/flake_analysis/db/models/analysis.py:173-210` (`runs` 테이블 ORM, 현재 미사용)
- `src/flake_analysis/api/sse.py` (`ProgressBridge`, `sse_stream`, 15s heartbeat)
- `src/flake_analysis/api/mutex.py` (`acquire_scan_lock(scan_id)`, 423 Locked)
- `~/QPress-SAM-Flake/run_amg_v2.py` (이번 통합 대상, 우리 fork)
- `web/src/api/sseRun.ts` (URL drift: `/projects/{pid}/run/{step}` → 실제는 `/projects/{pid}/scans/{sid}/run/{step}`)
- `web/src/hooks/useStepProgress.ts` (기존 SSE 훅 패턴)
- `docs/superpowers/plans/2026-05-21-W7-gpu-workers.md` (이 플랜이 흡수/대체)

---

## Status Markers

| Phase | AWS state 변경 | 승인 필요 | 1차 목표 (GUI 업로드 e2e) 와 관계 |
|---|---|---|---|
| Phase 1 | 없음 (fork만 수정) | 없음 | 무관 (병렬) |
| Phase 2 | 없음 (백엔드 코드만) | 없음 | 1차 목표 후 진입 OK |
| Phase 3 | 없음 (프론트만) | 없음 | URL fix 부분은 1차 목표에도 영향 |
| Phase 4 | EC2 기동 (g6e.xlarge spot) | **필요** | 1차 목표 외 |

> **⚠️ Phase 4 진입 전 owner에게 별도 승인을 받아야 한다.** Plan 자체는 작성하되, Phase 4 task를 dispatch 하기 전에 비용 견적 + spot 선점 데이터 + AMI 결정을 owner에게 보고하고 명시적 GO 받는다. (CLAUDE.md §6 에스컬레이션 트리거.)

> **W7 SKETCH plan 처리:** 이 플랜이 W7의 D1–D7을 흡수한다. `docs/superpowers/plans/2026-05-21-W7-gpu-workers.md` 헤더에 `Superseded by 2026-05-25-segmentation-web-integration.md` 라인을 Phase 1에서 추가 (P1 Task 0).

---

# Phase 1 — Inference 모듈 슬림화 (fork submodule + 4 fixes + LoRA merge)

**Phase Goal:** `HoukJangBNL/QPress-SAM-Flake` fork를 우리 레포에 git submodule로 임베드하고, inference 경로의 4개 알려진 문제(per-image progress callback / IndexError swallow / os.chdir / args.json 학습-머신 절대경로)를 fix한다. LoRA를 base 가중치에 merge해서 단일 .pt 파일로 만들고 `peft` 의존성을 제거한다. **학습 코드는 무손상.**

**Phase Owner:** algo-engineer (인퍼런스 코드 수정), devops-engineer (submodule + LoRA merge 스크립트)

**Phase Exit Criteria:**
- `vendor/QPress-SAM-Flake/` submodule 박혀 있고 `git submodule status` 깨끗
- `python -m vendor.QPress-SAM-Flake.run_amg_v2_inference --images <dir> --weights <merged.pt> --out <dir>` (또는 동등 entry point) 가 progress callback 받아 per-image 진행 출력
- LoRA merge CLI 1회 실행 → `models/sam2.1_hiera_large.merged.pt` 생성 (S3 업로드는 Phase 4)
- 학습 코드 (`train_*.py`) 무변경 — `git diff vendor/QPress-SAM-Flake/train` empty
- W7 SKETCH 플랜에 Superseded 헤더 추가됨

---

### Task P1.0: W7 SKETCH 플랜에 Superseded 헤더 추가

**Files:**
- Modify: `docs/superpowers/plans/2026-05-21-W7-gpu-workers.md:1-3`

**Owner suggestion:** PM (메타 작업 — 아주 짧음)

- [ ] **Step 1: 헤더에 Superseded 라인 추가**

`docs/superpowers/plans/2026-05-21-W7-gpu-workers.md` 1-3번째 줄을:

```markdown
# W7 — GPU Worker Trigger (background → SAM → domain_stats → domain_proximity) Implementation Plan

> **Status: SUPERSEDED by `2026-05-25-segmentation-web-integration.md` (2026-05-25).** D1–D7 결정은 신규 플랜 Phase 4에 흡수됨. 이 문서는 감사 트레일 목적 보존. 신규 작업은 신규 플랜 참조.

> **Status: SKETCH + DECISIONS-PENDING.** ...(원문 유지)
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/plans/2026-05-21-W7-gpu-workers.md
git commit -m "docs(plans): mark W7-gpu-workers as superseded by segmentation-web-integration"
```

---

### Task P1.1: `HoukJangBNL/QPress-SAM-Flake` 를 git submodule로 임베드

**Files:**
- Create: `.gitmodules`
- Create: `vendor/QPress-SAM-Flake/` (submodule pointer)
- Modify: `README.md` 또는 `docs/db-ops.md` (submodule init 절차 한 줄)

**Owner suggestion:** devops-engineer

> **사전 확인:** `~/QPress-SAM-Flake` 가 이미 우리 fork (`origin = HoukJangBNL/QPress-SAM-Flake`) 인지 `git -C ~/QPress-SAM-Flake remote -v` 로 검증. 그렇지 않으면 P1.1을 멈추고 PM에 보고.

- [ ] **Step 1: submodule 추가**

레포 루트에서:

```bash
git submodule add https://github.com/HoukJangBNL/QPress-SAM-Flake.git vendor/QPress-SAM-Flake
git submodule update --init --recursive
```

- [ ] **Step 2: `.gitmodules` 검증**

`.gitmodules` 가 다음과 같이 만들어졌는지 확인:

```ini
[submodule "vendor/QPress-SAM-Flake"]
	path = vendor/QPress-SAM-Flake
	url = https://github.com/HoukJangBNL/QPress-SAM-Flake.git
```

- [ ] **Step 3: README/db-ops 한 줄 추가**

`README.md` 의 "Setup" 섹션 또는 `docs/db-ops.md` 첫 setup 단계에:

```markdown
### Submodules

이 레포는 `vendor/QPress-SAM-Flake` 에 SAM2.1 inference fork를 submodule로 들고 있습니다. 클론 후:

```bash
git submodule update --init --recursive
```
```

- [ ] **Step 4: Commit**

```bash
git add .gitmodules vendor/QPress-SAM-Flake README.md
git commit -m "build: add HoukJangBNL/QPress-SAM-Flake as vendor submodule"
```

---

### Task P1.2: Inference-only entry point 분리 (run_amg_v2_inference.py)

**Files:**
- Create: `vendor/QPress-SAM-Flake/run_amg_v2_inference.py` (fork 안에서 작업, 별도 PR로 fork에 머지)
- Test: `vendor/QPress-SAM-Flake/tests/test_inference_smoke.py` (가능하면 fork에 있는 기존 test 패턴 따름)

**Owner suggestion:** algo-engineer

> **작업 흐름 주의:** vendor 디렉토리는 submodule이라 main 레포의 commit이 아니라 **fork repo 자체에 commit & push**. 그 후 main 레포에서 `git add vendor/QPress-SAM-Flake` 로 submodule pointer 업데이트.

- [ ] **Step 1: Fork 안에서 feature 브랜치 생성**

```bash
cd vendor/QPress-SAM-Flake
git checkout -b feat/inference-only-entry-point
```

- [ ] **Step 2: `run_amg_v2_inference.py` 작성 — failing test 먼저**

`tests/test_inference_smoke.py` (또는 fork의 기존 test layout을 따름):

```python
"""Smoke test for the inference-only entry point.

Runs against a 2-image fixture, expects:
- Progress callback called >= 2 times
- Returns dict {image_filename: {n_masks, error}}
- No process-wide os.chdir side effect
"""
from pathlib import Path
import os
from run_amg_v2_inference import infer

FIXTURE = Path(__file__).parent / "fixtures" / "2_image_smoke"

def test_inference_smoke(tmp_path):
    cwd_before = os.getcwd()
    progress_calls = []

    def on_progress(payload):
        progress_calls.append(payload)

    result = infer(
        images_dir=FIXTURE / "images",
        weights_path=FIXTURE / "tiny_merged.pt",
        out_dir=tmp_path,
        device="cpu",
        progress_callback=on_progress,
    )

    assert os.getcwd() == cwd_before, "infer() must not change cwd"
    assert len(progress_calls) >= 2
    assert all(isinstance(v, dict) for v in result.values())
```

- [ ] **Step 3: 테스트 실행 → fail 확인**

```bash
pytest tests/test_inference_smoke.py -v
```

Expected: FAIL — `run_amg_v2_inference` module 없음.

- [ ] **Step 4: `run_amg_v2_inference.py` 작성**

`run_amg_v2_inference.py` (fork 루트):

```python
"""Inference-only entry point — extracted from run_amg_v2.py main().

Differences vs run_amg_v2.py main:
1. No training code paths.
2. No multi-process (single GPU or CPU only — multi-process moved to a wrapper if needed).
3. progress_callback hook fires per-image with {idx, total, image_name, n_masks, error}.
4. IndexError from amg.generate is captured into result[image_name]['error'], NOT swallowed.
5. No os.chdir(sam2_repo) — uses absolute imports / explicit sys.path entries.
6. No args.json baked-in absolute paths — caller supplies images_dir + weights_path explicitly.
"""
from __future__ import annotations
from pathlib import Path
from typing import Callable, Optional

# (Keep all imports relative to the fork; do NOT import from flake_analysis.* here —
#  this entry point must run as a stand-alone script too.)
import torch
from sam2.build_sam import build_sam2
from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator

ProgressCallback = Callable[[dict], None]


def _resolve_device(device: Optional[str]) -> str:
    if device:
        return device
    return "cuda" if torch.cuda.is_available() else "cpu"


def _list_images(images_dir: Path) -> list[Path]:
    exts = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
    return sorted(p for p in images_dir.iterdir() if p.suffix.lower() in exts)


def infer(
    *,
    images_dir: Path,
    weights_path: Path,
    out_dir: Path,
    device: Optional[str] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> dict[str, dict]:
    """Run SAM2 AMG over every image in images_dir.

    Returns:
        {image_filename: {"n_masks": int, "error": str | None}}
    """
    device = _resolve_device(device)
    images = _list_images(images_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load merged weights — single file, no LoRA mount.
    state = torch.load(weights_path, map_location=device)
    sam = build_sam2(state["model_config"], None, device=device)
    sam.load_state_dict(state["model_state_dict"], strict=True)
    amg = SAM2AutomaticMaskGenerator(sam)

    result: dict[str, dict] = {}
    total = len(images)

    for idx, img_path in enumerate(images):
        from PIL import Image
        import numpy as np

        try:
            image = np.asarray(Image.open(img_path).convert("RGB"))
            masks = amg.generate(image)
            n_masks = len(masks)
            error = None

            # Persist masks
            np.savez_compressed(
                out_dir / f"{img_path.stem}.masks.npz",
                masks=np.stack([m["segmentation"] for m in masks]) if masks else np.zeros((0,), dtype=bool),
            )
        except IndexError as e:
            n_masks = 0
            error = f"IndexError: {e}"
        except Exception as e:
            n_masks = 0
            error = f"{type(e).__name__}: {e}"

        result[img_path.name] = {"n_masks": n_masks, "error": error}

        if progress_callback:
            progress_callback({
                "idx": idx + 1,
                "total": total,
                "image_name": img_path.name,
                "n_masks": n_masks,
                "error": error,
            })

    return result


def _cli():
    """CLI mirror — supports same flags as run_amg_v2.py minus training ones."""
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--images", required=True, type=Path)
    p.add_argument("--weights", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--device", default=None)
    args = p.parse_args()

    def _print_progress(payload: dict):
        print(f"[{payload['idx']}/{payload['total']}] {payload['image_name']}: "
              f"{payload['n_masks']} masks"
              + (f" — ERROR: {payload['error']}" if payload['error'] else ""))

    infer(
        images_dir=args.images,
        weights_path=args.weights,
        out_dir=args.out,
        device=args.device,
        progress_callback=_print_progress,
    )


if __name__ == "__main__":
    _cli()
```

- [ ] **Step 5: 테스트 실행 → pass 확인**

```bash
pytest tests/test_inference_smoke.py -v
```

Expected: PASS.

> **Note:** `tiny_merged.pt` 픽스처는 P1.5에서 만들어진 후에 이 테스트가 진짜로 통과한다. 그 전에는 모킹된 weight 파일로 통과시키거나, P1.5 완료 후에 P1.2 마지막 step을 실행한다. 의존성 → P1.5 가 P1.2 의 PASS 검증을 unlock.

- [ ] **Step 6: Fork 안에서 commit + PR**

```bash
cd vendor/QPress-SAM-Flake
git add run_amg_v2_inference.py tests/test_inference_smoke.py
git commit -m "feat: add inference-only entry point with per-image callback (4-fix bundle)"
git push origin feat/inference-only-entry-point
gh pr create --title "Inference-only entry point (per-image callback / no os.chdir / no IndexError swallow / no args.json hardcode)" --body "..."
```

> 이 PR은 fork repo에 머지된 뒤 main 레포에서 submodule pointer를 그 commit으로 업데이트 (P1.7).

---

### Task P1.3: 4-fix 검증 — `os.chdir` 제거 (test 명시)

**Files:**
- Modify: `vendor/QPress-SAM-Flake/run_amg_v2_inference.py` (이미 P1.2에서 chdir 안 하도록 작성됨 — 본 task는 회귀 방지 test 추가)
- Test: `vendor/QPress-SAM-Flake/tests/test_no_chdir.py`

**Owner suggestion:** algo-engineer

- [ ] **Step 1: Failing test 작성**

```python
"""Regression: infer() must not call os.chdir on the calling process."""
import os
from pathlib import Path
from run_amg_v2_inference import infer

def test_no_chdir(tmp_path, monkeypatch):
    cwd_before = os.getcwd()
    chdir_calls = []
    real_chdir = os.chdir
    monkeypatch.setattr(os, "chdir", lambda p: chdir_calls.append(p))

    # Use a degenerate config that fails early — we only care that no chdir was attempted.
    try:
        infer(
            images_dir=tmp_path,
            weights_path=tmp_path / "missing.pt",
            out_dir=tmp_path,
            device="cpu",
            progress_callback=None,
        )
    except Exception:
        pass

    assert chdir_calls == []
    assert os.getcwd() == cwd_before
```

- [ ] **Step 2: 테스트 실행 → P1.2 구현이 chdir을 안 하면 PASS, 부주의하게 추가하면 FAIL**

```bash
pytest tests/test_no_chdir.py -v
```

Expected: PASS (P1.2가 이미 chdir-free).

- [ ] **Step 3: Commit**

```bash
git add tests/test_no_chdir.py
git commit -m "test: regression — infer() must not call os.chdir"
```

---

### Task P1.4: 4-fix 검증 — `IndexError` no-swallow + `args.json` no-bake (test 명시)

**Files:**
- Test: `vendor/QPress-SAM-Flake/tests/test_indexerror_surfaced.py`
- Test: `vendor/QPress-SAM-Flake/tests/test_no_argsjson_lookup.py`

**Owner suggestion:** algo-engineer

- [ ] **Step 1: IndexError 노출 test**

```python
"""IndexError from amg.generate must end up in result[image]['error'], NOT silently dropped."""
from pathlib import Path
from unittest.mock import patch
from run_amg_v2_inference import infer

def test_indexerror_surfaces_in_result(tmp_path):
    images_dir = tmp_path / "imgs"
    images_dir.mkdir()
    img = images_dir / "fake.png"
    # 1x1 png
    from PIL import Image
    Image.new("RGB", (1, 1)).save(img)

    out_dir = tmp_path / "out"

    # We don't actually load weights — patch build_sam2 + AMG to force IndexError.
    with patch("run_amg_v2_inference.build_sam2") as bs, \
         patch("run_amg_v2_inference.SAM2AutomaticMaskGenerator") as gen:
        bs.return_value = object()
        gen.return_value.generate.side_effect = IndexError("synthetic")

        # We also need to skip torch.load — patch it.
        with patch("run_amg_v2_inference.torch.load") as tl:
            tl.return_value = {"model_config": {}, "model_state_dict": {}}

            result = infer(
                images_dir=images_dir,
                weights_path=tmp_path / "fake.pt",
                out_dir=out_dir,
                device="cpu",
                progress_callback=None,
            )

    assert "fake.png" in result
    assert result["fake.png"]["n_masks"] == 0
    assert "IndexError" in (result["fake.png"]["error"] or "")
```

- [ ] **Step 2: args.json 미사용 test**

```python
"""infer() must NOT read args.json from any path — config is supplied via kwargs."""
import os
from pathlib import Path
from unittest.mock import patch

def test_no_argsjson_lookup(tmp_path, monkeypatch):
    open_calls = []
    real_open = open
    def tracked_open(path, *a, **kw):
        open_calls.append(str(path))
        return real_open(path, *a, **kw)
    monkeypatch.setattr("builtins.open", tracked_open)

    from run_amg_v2_inference import infer
    try:
        infer(
            images_dir=tmp_path,
            weights_path=tmp_path / "missing.pt",
            out_dir=tmp_path,
            device="cpu",
            progress_callback=None,
        )
    except Exception:
        pass

    assert not any("args.json" in c for c in open_calls), (
        f"args.json should never be read; got: {[c for c in open_calls if 'args.json' in c]}"
    )
```

- [ ] **Step 3: 두 테스트 실행 → PASS**

```bash
pytest tests/test_indexerror_surfaced.py tests/test_no_argsjson_lookup.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit (fork repo 안에서)**

```bash
git add tests/test_indexerror_surfaced.py tests/test_no_argsjson_lookup.py
git commit -m "test: regression — IndexError surfaced + args.json never read"
```

---

### Task P1.5: LoRA → base merge CLI 스크립트

**Files:**
- Create: `vendor/QPress-SAM-Flake/scripts/merge_lora.py`
- Create: `vendor/QPress-SAM-Flake/tests/fixtures/2_image_smoke/tiny_merged.pt` (CI smoke)
- Test: `vendor/QPress-SAM-Flake/tests/test_merge_lora.py`

**Owner suggestion:** algo-engineer

> **이 task는 한 번 실행해서 prod merged.pt 를 만들고 그 다음엔 weights 변경 시에만 다시 돌린다.** prod merged.pt는 git에 안 들어가고 (S3에만 — Phase 4에서 업로드), CI smoke용 tiny fixture만 git lfs 또는 작은 stub.

- [ ] **Step 1: failing test (수학적 정합성)**

`tests/test_merge_lora.py`:

```python
"""LoRA merge: base + lora_A @ lora_B should equal direct application of LoRA at inference time
on a small synthetic checkpoint."""
import torch
from scripts.merge_lora import merge_lora_into_base

def test_merge_equivalence():
    # Synthetic weight: a single Linear(4, 4) layer
    base_weight = torch.randn(4, 4)
    lora_A = torch.randn(4, 2)
    lora_B = torch.randn(2, 4)
    alpha = 8.0
    rank = 2
    scaling = alpha / rank

    base_state = {"layer.weight": base_weight}
    lora_state = {
        "layer.lora_A.default.weight": lora_A,
        "layer.lora_B.default.weight": lora_B,
    }
    config = {"alpha": alpha, "rank": rank}

    merged = merge_lora_into_base(base_state, lora_state, config)

    # Expected merged weight: base + lora_B @ lora_A * scaling   (peft convention)
    expected = base_weight + (lora_B.T @ lora_A.T).T * scaling

    assert torch.allclose(merged["layer.weight"], expected, atol=1e-5)
```

- [ ] **Step 2: 테스트 실행 → FAIL** (`scripts/merge_lora.py` 없음)

- [ ] **Step 3: `scripts/merge_lora.py` 작성**

```python
"""Offline merge: bake LoRA adapter into base SAM2.1 weights.

Output: a single .pt with {model_config, model_state_dict} consumable by
run_amg_v2_inference.infer() without peft.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import torch


def merge_lora_into_base(
    base_state: dict[str, torch.Tensor],
    lora_state: dict[str, torch.Tensor],
    config: dict,
) -> dict[str, torch.Tensor]:
    """Return a state dict with LoRA absorbed into base weights.

    Convention follows peft 0.x: merged = W + (B @ A) * (alpha / rank)
    """
    rank = config["rank"]
    alpha = config["alpha"]
    scaling = alpha / rank

    merged = dict(base_state)
    # Find pairs of lora_A / lora_B
    for key in list(lora_state.keys()):
        if not key.endswith(".lora_A.default.weight"):
            continue
        prefix = key.removesuffix(".lora_A.default.weight")
        b_key = f"{prefix}.lora_B.default.weight"
        if b_key not in lora_state:
            raise RuntimeError(f"missing matching lora_B for {key}")
        a = lora_state[key]
        b = lora_state[b_key]
        target_key = f"{prefix}.weight"
        if target_key not in merged:
            raise RuntimeError(f"target weight {target_key} not in base")
        merged[target_key] = merged[target_key] + (b @ a) * scaling
    return merged


def _cli():
    p = argparse.ArgumentParser()
    p.add_argument("--base", required=True, type=Path)
    p.add_argument("--lora", required=True, type=Path)
    p.add_argument("--config", required=True, type=Path, help="JSON with rank+alpha")
    p.add_argument("--out", required=True, type=Path)
    args = p.parse_args()

    import json
    config = json.loads(args.config.read_text())
    base = torch.load(args.base, map_location="cpu")
    lora = torch.load(args.lora, map_location="cpu")

    base_state = base["model_state_dict"] if "model_state_dict" in base else base
    lora_state = lora["model_state_dict"] if "model_state_dict" in lora else lora

    merged_state = merge_lora_into_base(base_state, lora_state, config)
    out = {
        "model_config": base.get("model_config", {}),
        "model_state_dict": merged_state,
    }
    torch.save(out, args.out)
    print(f"Merged weight written: {args.out}")


if __name__ == "__main__":
    _cli()
```

- [ ] **Step 4: 테스트 PASS 확인**

```bash
pytest tests/test_merge_lora.py -v
```

- [ ] **Step 5: Tiny fixture 생성 (CI smoke용)**

```bash
python scripts/merge_lora.py \
    --base tests/fixtures/2_image_smoke/tiny_base.pt \
    --lora tests/fixtures/2_image_smoke/tiny_lora.pt \
    --config tests/fixtures/2_image_smoke/lora_config.json \
    --out tests/fixtures/2_image_smoke/tiny_merged.pt
```

> 이 picky하게 만든 tiny fixture는 P1.2 step 5의 smoke test가 진짜로 PASS하게 해 준다.

- [ ] **Step 6: P1.2 smoke test 재실행 (이번엔 진짜 fixture)**

```bash
pytest tests/test_inference_smoke.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add scripts/merge_lora.py tests/test_merge_lora.py tests/fixtures/2_image_smoke/
git commit -m "feat: LoRA→base merge CLI + tiny CI fixture"
```

---

### Task P1.6: `peft` 의존 제거 (P1 옵션 — owner는 'P1에서 merge' 선택했음)

**Files:**
- Modify: `vendor/QPress-SAM-Flake/run_amg_v2_inference.py` (이미 peft import 안 함 — verify)
- Modify: `vendor/QPress-SAM-Flake/requirements.txt` 또는 `pyproject.toml` (inference-only requirements 분리)
- Create: `vendor/QPress-SAM-Flake/requirements-inference.txt`

**Owner suggestion:** algo-engineer

- [ ] **Step 1: inference-only requirements 작성**

`requirements-inference.txt`:

```
torch>=2.1
torchvision
numpy
Pillow
sam2 @ git+https://github.com/facebookresearch/sam2.git
# NB: peft / bitsandbytes intentionally excluded — merged weights are used.
```

- [ ] **Step 2: README 또는 inference 섹션에 한 줄**

```markdown
## Inference (server / production)

```bash
pip install -r requirements-inference.txt
python run_amg_v2_inference.py --images <dir> --weights <merged.pt> --out <dir>
```

학습 시에만 `requirements.txt` (peft 포함). 추론 서버는 `requirements-inference.txt` 사용.
```

- [ ] **Step 3: import grep — `peft` 가 inference 경로에 없는지 확인**

```bash
grep -n "import peft\|from peft" run_amg_v2_inference.py
```

Expected: 결과 없음.

- [ ] **Step 4: Commit**

```bash
git add requirements-inference.txt README.md
git commit -m "build: split inference-only requirements (drop peft for server-side inference)"
```

---

### Task P1.7: Submodule pointer를 fork PR 머지 commit으로 업데이트 (main 레포 commit)

**Files:**
- Modify: `vendor/QPress-SAM-Flake` (submodule pointer)

**Owner suggestion:** PM (메타) 또는 devops-engineer

- [ ] **Step 1: fork PR 머지 후 submodule update**

```bash
cd vendor/QPress-SAM-Flake
git fetch origin
git checkout main  # 또는 fork의 default 브랜치
git pull
cd ../..
git add vendor/QPress-SAM-Flake
git commit -m "build: bump QPress-SAM-Flake submodule to inference-entry-point head"
```

---

# Phase 2 — 백엔드 파이프라인 통합 (sam step + runs wiring)

**Phase Goal:** `sam` step을 `PIPELINE_STEPS / SUBDIRS / ARTIFACTS`에 등록, `pipeline/sam.py` 래퍼 + `core/pipeline/sam.py` 엔진 작성, `POST /run/sam` 라우트 (기존 4 step 패턴 미러), `runs` 테이블에 4 step 모두 INSERT/UPDATE wiring. **AWS state 변경 없음.** 로컬 CPU에서 tiny fixture 기준 e2e 동작.

**Phase Owner:** api-developer (라우트 + runs wiring), algo-engineer (pipeline/sam.py 엔진 호출 어댑터)

**Phase Exit Criteria:**
- `PIPELINE_STEPS` 에 `sam` 포함, `SUBDIRS["sam"] = "07_sam"`, `ARTIFACTS["sam"] = ["per_image_results.json", "<image>.masks.npz"]`
- `POST /api/v1/projects/{pid}/scans/{sid}/run/sam` 가 SSE progress 스트림 emit (per-image: `{idx, total, image_name, n_masks, error}`)
- `runs` 테이블에 SAM run insert + 완료 시 update — 4 step 모두 wired
- `tests/api/test_run_sam_sse.py` PG-marked PASS — runs row 1 INSERT + status 전이 'pending → running → succeeded'
- 로컬 CPU에서 2-image fixture로 `/run/sam` 호출 → 200 OK + SSE stream → `analyses.steps_done["sam"] = true` 검증
- 회귀 없음: 기존 `test_run_*_sse.py` PASS (acceptance gate)

---

### Task P2.1: `PIPELINE_STEPS` 에 `sam` 슬롯 등록

**Files:**
- Modify: `src/flake_analysis/state/paths.py:21-49`
- Test: `tests/state/test_paths.py` (새 파일 또는 기존)

**Owner suggestion:** api-developer

- [ ] **Step 1: 회귀 test 작성 (sam slot 등록 expected)**

`tests/state/test_paths.py`:

```python
from flake_analysis.state.paths import PIPELINE_STEPS, SUBDIRS, ARTIFACTS

def test_sam_step_registered():
    assert "sam" in PIPELINE_STEPS
    assert SUBDIRS["sam"] == "07_sam"
    assert ARTIFACTS["sam"] == ["per_image_results.json"]

def test_step_dir_resolves_sam(tmp_path):
    from flake_analysis.state.paths import step_dir
    assert step_dir(tmp_path, "sam") == tmp_path / "07_sam"
```

- [ ] **Step 2: 실행 → FAIL**

```bash
uv run pytest tests/state/test_paths.py -v
```

Expected: KeyError on `SUBDIRS["sam"]`.

- [ ] **Step 3: paths.py 수정**

`src/flake_analysis/state/paths.py:21-49`:

```python
PIPELINE_STEPS = (
    "background",
    "thumbnails",
    "sam",
    "domain_stats",
    "selector",
    "clustering",
    "domain_proximity",
    "explorer",
)

SUBDIRS = {
    "background":       "01_background",
    "thumbnails":       "00_thumbnails",
    "sam":              "07_sam",
    "domain_stats":     "02_domain_stats",
    "selector":         "03_selector",
    "clustering":       "04_clustering",
    "domain_proximity": "05_domain_proximity",
    "explorer":         "06_explorer",
}

ARTIFACTS = {
    "background": ["background.npy"],
    "thumbnails": ["index.json"],
    "sam": ["per_image_results.json"],  # per-image .masks.npz는 동적, 별도 매니페스트로 카운트
    "domain_stats": ["stats.npz"],
    "selector": ["selection.parquet"],
    "clustering": ["seed_groups.json", "gmm_model.pkl", "assignments.parquet", "labels.json"],
    "domain_proximity": ["distances.parquet", "flake_assignments.parquet"],
    "explorer": ["explorer_state.json"],
}
```

> **번호 충돌 주의:** `07_sam` 으로 정한 이유는 thumbnails(00) → background(01) → domain_stats(02) → selector(03) → clustering(04) → domain_proximity(05) → explorer(06) 가 이미 잡혀 있고, `sam` 은 background와 domain_stats 사이 단계지만 디렉토리 번호는 추가 순서를 따른다 (재배열 시 기존 manifest 호환성 깨짐).

- [ ] **Step 4: 실행 → PASS**

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/state/paths.py tests/state/test_paths.py
git commit -m "feat(state): register 'sam' step in PIPELINE_STEPS / SUBDIRS / ARTIFACTS"
```

---

### Task P2.2: `core/pipeline/sam.py` 엔진 어댑터 작성

**Files:**
- Create: `src/flake_analysis/core/pipeline/sam.py`
- Test: `tests/core/pipeline/test_sam_engine.py`

**Owner suggestion:** algo-engineer

> **호출 경계:** `core/pipeline/sam.py` 는 vendor의 `run_amg_v2_inference.infer` 를 import해서 호출한다. `vendor/QPress-SAM-Flake` 는 `sys.path.insert(0, ...)` 또는 `[tool.uv.sources]` 로 install. 가장 간단한 길은 `pyproject.toml` 의 dev dep로 path 설치 — 별도 task에서.

- [ ] **Step 1: failing test**

`tests/core/pipeline/test_sam_engine.py`:

```python
"""Engine adapter test: run_sam dispatches to vendor.run_amg_v2_inference.infer
and translates progress events into ProgressCallback format."""
from pathlib import Path
from unittest.mock import patch
from flake_analysis.core.pipeline.sam import run_sam

def test_run_sam_calls_vendor_infer(tmp_path):
    images_dir = tmp_path / "imgs"
    images_dir.mkdir()
    out_dir = tmp_path / "out"

    progress_emits = []

    with patch("flake_analysis.core.pipeline.sam._vendor_infer") as vinfer:
        # Simulate vendor calling our progress shim with 2 events
        def fake_infer(images_dir, weights_path, out_dir, device, progress_callback):
            progress_callback({"idx": 1, "total": 2, "image_name": "a.png", "n_masks": 5, "error": None})
            progress_callback({"idx": 2, "total": 2, "image_name": "b.png", "n_masks": 0, "error": "IndexError: x"})
            return {
                "a.png": {"n_masks": 5, "error": None},
                "b.png": {"n_masks": 0, "error": "IndexError: x"},
            }
        vinfer.side_effect = fake_infer

        run_sam(
            images_dir=images_dir,
            weights_path=tmp_path / "merged.pt",
            out_dir=out_dir,
            device="cpu",
            progress_callback=lambda pct, msg: progress_emits.append((pct, msg)),
        )

    # 0.5 (1/2), 1.0 (2/2)
    assert len(progress_emits) == 2
    assert progress_emits[0][0] == 0.5
    assert "a.png" in progress_emits[0][1]
    assert progress_emits[1][0] == 1.0
    assert "b.png" in progress_emits[1][1]

    # Per-image results manifest is written
    assert (out_dir / "per_image_results.json").exists()
```

- [ ] **Step 2: FAIL 확인**

- [ ] **Step 3: 엔진 작성**

`src/flake_analysis/core/pipeline/sam.py`:

```python
"""SAM2 inference adapter — bridges vendor run_amg_v2_inference into
our ProgressCallback (pct, msg) protocol used by other pipeline steps."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Callable, Optional

# Lazy import — vendor may not be importable in unit-test environments.
def _vendor_infer(*args, **kwargs):
    import sys
    vendor_root = Path(__file__).resolve().parents[4] / "vendor" / "QPress-SAM-Flake"
    if str(vendor_root) not in sys.path:
        sys.path.insert(0, str(vendor_root))
    from run_amg_v2_inference import infer
    return infer(*args, **kwargs)


ProgressCallback = Callable[[float, str], None]


def run_sam(
    *,
    images_dir: Path,
    weights_path: Path,
    out_dir: Path,
    device: Optional[str] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> dict:
    """Run SAM2 AMG over images_dir, write per-image masks + summary manifest.

    Returns: {"images": int, "masks_total": int, "errors": int}
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    def _shim(payload: dict):
        if progress_callback is None:
            return
        pct = payload["idx"] / payload["total"] if payload["total"] else 0.0
        msg = (
            f"[{payload['idx']}/{payload['total']}] {payload['image_name']}: "
            f"{payload['n_masks']} masks"
            + (f" — ERROR: {payload['error']}" if payload['error'] else "")
        )
        progress_callback(pct, msg)

    result = _vendor_infer(
        images_dir=images_dir,
        weights_path=weights_path,
        out_dir=out_dir,
        device=device,
        progress_callback=_shim,
    )

    # Persist manifest
    summary = {
        "images": len(result),
        "masks_total": sum(r["n_masks"] for r in result.values()),
        "errors": sum(1 for r in result.values() if r["error"]),
        "per_image": result,
    }
    (out_dir / "per_image_results.json").write_text(json.dumps(summary, indent=2))
    return summary
```

- [ ] **Step 4: PASS 확인**

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/core/pipeline/sam.py tests/core/pipeline/test_sam_engine.py
git commit -m "feat(core): add SAM engine adapter wrapping vendor run_amg_v2_inference"
```

---

### Task P2.3: `pipeline/sam.py` 호출 래퍼 (다른 step과 일관성)

**Files:**
- Create: `src/flake_analysis/pipeline/sam.py`
- Test: `tests/pipeline/test_sam_step.py`

**Owner suggestion:** api-developer

- [ ] **Step 1: 다른 step 패턴 참고**

`src/flake_analysis/pipeline/background.py` 의 `run_background_step` 시그니처를 미러 (analysis_folder + raw_images_dir + progress_callback + 기타 params).

- [ ] **Step 2: failing test**

`tests/pipeline/test_sam_step.py`:

```python
from pathlib import Path
from unittest.mock import patch
from flake_analysis.pipeline.sam import run_sam_step

def test_run_sam_step_dispatches_to_engine(tmp_path):
    raw = tmp_path / "raw"; raw.mkdir()
    analysis = tmp_path / "analysis"; analysis.mkdir()
    weights = tmp_path / "merged.pt"; weights.write_bytes(b"")

    with patch("flake_analysis.pipeline.sam.run_sam") as eng:
        eng.return_value = {"images": 0, "masks_total": 0, "errors": 0}
        run_sam_step(
            raw_images_dir=raw,
            analysis_folder=analysis,
            weights_path=weights,
            device="cpu",
            progress_callback=None,
        )
    eng.assert_called_once()
    kwargs = eng.call_args.kwargs
    assert kwargs["images_dir"] == raw
    assert kwargs["out_dir"] == analysis / "07_sam"
```

- [ ] **Step 3: FAIL 확인**

- [ ] **Step 4: 래퍼 작성**

`src/flake_analysis/pipeline/sam.py`:

```python
"""SAM step wrapper — fills in subdir from PIPELINE_STEPS layout, delegates to core engine."""
from __future__ import annotations
from pathlib import Path
from typing import Callable, Optional

from flake_analysis.core.pipeline.sam import run_sam
from flake_analysis.state.paths import SUBDIRS

ProgressCallback = Callable[[float, str], None]


def run_sam_step(
    *,
    raw_images_dir: Path,
    analysis_folder: Path,
    weights_path: Path,
    device: Optional[str] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> dict:
    out_dir = Path(analysis_folder) / SUBDIRS["sam"]
    return run_sam(
        images_dir=Path(raw_images_dir),
        weights_path=Path(weights_path),
        out_dir=out_dir,
        device=device,
        progress_callback=progress_callback,
    )
```

- [ ] **Step 5: PASS 확인**

- [ ] **Step 6: Commit**

```bash
git add src/flake_analysis/pipeline/sam.py tests/pipeline/test_sam_step.py
git commit -m "feat(pipeline): add run_sam_step wrapper layered on core engine"
```

---

### Task P2.4: `runs` 테이블 wiring 헬퍼 (4 step 공유)

**Files:**
- Create: `src/flake_analysis/api/services/runs.py`
- Test: `tests/api/services/test_runs.py`

**Owner suggestion:** api-developer

> **Why a service:** 4 step 모두 같은 패턴(insert pending → update running → update succeeded/failed)을 반복한다. `runs.py` 서비스에 `record_run_start(session, analysis_id, step, instance_meta)` + `record_run_end(session, run_id, status, error, metrics)` 두 함수만 노출.

- [ ] **Step 1: failing test (PG)**

`tests/api/services/test_runs.py`:

```python
"""runs service smoke (PG required)."""
import pytest
from flake_analysis.db.models.analysis import Run
from flake_analysis.api.services.runs import record_run_start, record_run_end
from sqlalchemy import select

pytestmark = pytest.mark.pg

async def test_run_lifecycle(pg_session, active_scan):
    analysis = active_scan["analysis"]

    run_id = await record_run_start(
        pg_session,
        analysis_id=analysis.id,
        step="sam",
        instance_meta={"instance_type": "g6e.xlarge", "instance_id": "i-test", "is_spot": True},
    )
    await pg_session.flush()

    row = (await pg_session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert row.status == "running"
    assert row.is_spot is True
    assert row.started_at is not None
    assert row.completed_at is None

    await record_run_end(
        pg_session,
        run_id=run_id,
        status="succeeded",
        metrics={"images": 2, "masks_total": 7, "errors": 0},
    )
    await pg_session.flush()

    row = (await pg_session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert row.status == "succeeded"
    assert row.completed_at is not None
    assert row.metrics["images"] == 2
```

- [ ] **Step 2: FAIL 확인**

- [ ] **Step 3: 서비스 작성**

`src/flake_analysis/api/services/runs.py`:

```python
"""runs table audit-log helpers — 4 pipeline step에 공유."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from flake_analysis.db.models.analysis import Run


async def record_run_start(
    session: AsyncSession,
    *,
    analysis_id: int,
    step: str,
    instance_meta: Optional[dict] = None,
) -> int:
    """Insert a 'running' Run row, return its id."""
    instance_meta = instance_meta or {}
    row = Run(
        analysis_id=analysis_id,
        step=step,
        status="running",
        started_at=datetime.now(timezone.utc),
        instance_type=instance_meta.get("instance_type"),
        instance_id=instance_meta.get("instance_id"),
        is_spot=instance_meta.get("is_spot"),
    )
    session.add(row)
    await session.flush()
    return row.id


async def record_run_end(
    session: AsyncSession,
    *,
    run_id: int,
    status: str,           # 'succeeded' | 'failed'
    error: Optional[str] = None,
    metrics: Optional[dict] = None,
) -> None:
    """Update Run.status/completed_at/error/metrics."""
    from sqlalchemy import update
    await session.execute(
        update(Run)
        .where(Run.id == run_id)
        .values(
            status=status,
            completed_at=datetime.now(timezone.utc),
            error=error,
            metrics=metrics,
        )
    )
```

- [ ] **Step 4: PASS 확인**

```bash
env SAA_DB_NAME=saa_test SAA_DB_USER=houkjang SAA_DB_HOST=127.0.0.1 \
    uv run pytest tests/api/services/test_runs.py -v -m pg
```

- [ ] **Step 5: Commit**

```bash
git add src/flake_analysis/api/services/runs.py tests/api/services/test_runs.py
git commit -m "feat(api): runs audit-log helpers (record_run_start / record_run_end)"
```

---

### Task P2.5: `runs.step` CHECK constraint에 빠진 step 추가 (alembic 0005)

**Files:**
- Read first: `alembic/versions/0001_initial_v6.py` (찾을 step CHECK)
- Create: `alembic/versions/0005_runs_step_full_enum.py`
- Modify: `src/flake_analysis/db/models/analysis.py:177-181`

**Owner suggestion:** db-specialist

> **이유:** 현재 CHECK는 `step IN ('background', 'sam', 'domain_stats', 'domain_proximity')` 만 허용. 우리는 `thumbnails`, `selector`, `clustering`, `explorer` 도 wiring 후보 (P2.6 에서 4 step만 wiring 한다고 결정했지만, 향후 확장 대비 + ORM 모델과 schema 일관성). **결정**: thumbnails/selector/clustering/explorer는 CPU only이고 이번에 wiring 안 한다 → CHECK는 그대로 두고 ORM 코멘트만 갱신. **이 Task는 SKIP** — 별도 백로그로 등록.

- [ ] **Step 1: ORM 모델에 코멘트 추가 (CHECK enum과 PIPELINE_STEPS 격차 명시)**

`src/flake_analysis/db/models/analysis.py:177-181` 위에:

```python
__table_args__ = (
    # NB: CHECK enum is intentionally narrower than PIPELINE_STEPS — thumbnails/
    # selector/clustering/explorer are CPU-only steps that don't write `runs`
    # rows yet. If wiring those, extend this CHECK in a new migration.
    CheckConstraint(
        "step IN ('background', 'sam', 'domain_stats', 'domain_proximity')",
        name="runs_step_check",
    ),
    ...
)
```

- [ ] **Step 2: Commit**

```bash
git add src/flake_analysis/db/models/analysis.py
git commit -m "docs(db): clarify runs.step CHECK is narrower than PIPELINE_STEPS by design"
```

---

### Task P2.6: 4 step 라우트에 runs wiring (background → sam → domain_stats → domain_proximity)

**Files:**
- Modify: `src/flake_analysis/api/routes/run.py` (4 endpoint 모두)
- Test: 기존 `tests/api/test_run_*_sse.py` 4 파일에 runs row 검증 assertion 추가

**Owner suggestion:** api-developer

> **패턴 (4 endpoint 모두 동일):**
> ```python
> # before bridge, inside the endpoint:
> run_id = await record_run_start(session, analysis_id=analysis.id, step="<step>")
> await session.commit()
> 
> # in the run_pipeline coroutine:
> async def run_pipeline():
>     try:
>         result = await loop.run_in_executor(None, call_wrapper)
>         await record_run_end(session_factory(), run_id=run_id, status="succeeded",
>                              metrics={...step-specific...})
>         bridge.emit_done(result)
>     except Exception as e:
>         await record_run_end(session_factory(), run_id=run_id, status="failed", error=str(e))
>         bridge.emit_error(...)
>     finally:
>         bridge.close()
> ```
> **주의:** SSE 제너레이터 안에서 새 session 필요 (기존 session은 endpoint 종료 시 닫힘). `async_session_factory()` 헬퍼를 `api/deps.py` 에서 export.

- [ ] **Step 1: `analysis_id` 가져오기 — endpoint에서 manifest로부터 resolve 가능한지 검증**

`src/flake_analysis/api/deps.py` 의 `get_active_analysis(scan_id, session)` 가 이미 있음 — 그대로 사용.

- [ ] **Step 2: `async_session_factory` 헬퍼 export**

`src/flake_analysis/api/deps.py` 끝에:

```python
from contextlib import asynccontextmanager
from flake_analysis.db.session import AsyncSessionLocal  # 기존 SessionLocal alias

@asynccontextmanager
async def get_session_for_background():
    """For background tasks (run_in_executor wrappers) that outlive the request scope."""
    async with AsyncSessionLocal() as session:
        yield session
```

- [ ] **Step 3: `run_background` 엔드포인트 wiring (test-first)**

`tests/api/test_run_background_sse.py` (기존)에 새 test 추가:

```python
async def test_run_background_writes_runs_row(client_pg, active_scan):
    project_id = active_scan["project_id"]
    scan_id = active_scan["scan_id"]
    analysis_id = active_scan["analysis"].id

    async with client_pg.stream(
        "POST",
        f"/api/v1/projects/{project_id}/scans/{scan_id}/run/background",
        json={"seed": 0, "max_images": 1, "method": "median", "gaussian_sigma": 0},
    ) as resp:
        async for _ in resp.aiter_lines():
            pass  # drain

    # Validate runs row
    from sqlalchemy import select
    from flake_analysis.db.models.analysis import Run
    rows = (await pg_session.execute(
        select(Run).where(Run.analysis_id == analysis_id, Run.step == "background")
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].status in ("succeeded", "failed")
    assert rows[0].started_at is not None
    assert rows[0].completed_at is not None
```

- [ ] **Step 4: FAIL 확인 (현재 runs 테이블에 INSERT 없음)**

- [ ] **Step 5: `routes/run.py` 의 `run_background` 수정**

호출 직전:

```python
# After get_active_analysis(scan_id):
analysis = await get_active_analysis(scan_id, session)
run_id = await record_run_start(session, analysis_id=analysis.id, step="background")
await session.commit()
```

`run_pipeline` 안:

```python
async def run_pipeline():
    try:
        result = await loop.run_in_executor(None, call_wrapper)
        async with get_session_for_background() as bg_session:
            await record_run_end(bg_session, run_id=run_id, status="succeeded",
                                 metrics={"max_images": params.max_images, "method": params.method})
            await bg_session.commit()
        bridge.emit_done(result)
    except Exception as e:
        async with get_session_for_background() as bg_session:
            await record_run_end(bg_session, run_id=run_id, status="failed", error=str(e))
            await bg_session.commit()
        bridge.emit_error("pipeline_failed", str(e), {"exc_type": type(e).__name__})
    finally:
        bridge.close()
```

- [ ] **Step 6: PASS 확인**

```bash
env SAA_DB_NAME=saa_test SAA_DB_USER=houkjang SAA_DB_HOST=127.0.0.1 \
    uv run pytest tests/api/test_run_background_sse.py -v -m pg
```

- [ ] **Step 7: Step 3-6 을 `run_domain_stats`, `run_domain_proximity` 에 동일 적용**

각 endpoint마다:
1. `tests/api/test_run_<step>_sse.py` 에 runs row 검증 추가
2. FAIL → endpoint wiring → PASS
3. 별도 commit

- [ ] **Step 8: Commit (각 step별)**

```bash
git commit -m "feat(api): runs audit-log wiring for background step"
git commit -m "feat(api): runs audit-log wiring for domain_stats step"
git commit -m "feat(api): runs audit-log wiring for domain_proximity step"
```

> **thumbnails / selector / clustering / explorer는 P2.6 scope 아님** — runs CHECK enum에 없으니 wiring 시도하면 IntegrityError. P2.5의 결정대로 그대로 둔다.

---

### Task P2.7: `POST /run/sam` 라우트 추가

**Files:**
- Modify: `src/flake_analysis/api/routes/run.py` (`run_sam` 엔드포인트 추가)
- Create: `src/flake_analysis/api/schemas/compute.py` 에 `SamParams` 스키마 추가
- Create: `tests/api/test_run_sam_sse.py`

**Owner suggestion:** api-developer

- [ ] **Step 1: `SamParams` 스키마**

`src/flake_analysis/api/schemas/compute.py` 끝에:

```python
class SamParams(BaseModel):
    weights_path: str  # 절대경로 또는 S3 키 — Phase 4에서 S3 다운로드 로직 추가
    device: str | None = None  # None = auto-detect
```

- [ ] **Step 2: failing test**

`tests/api/test_run_sam_sse.py`:

```python
"""SAM SSE smoke (PG marked, requires tiny merged fixture)."""
import os
import pytest
from pathlib import Path

pytestmark = pytest.mark.pg

@pytest.mark.skipif(
    not Path(os.environ.get("SAM_TEST_WEIGHTS", "/nonexistent")).exists(),
    reason="SAM_TEST_WEIGHTS not set — skip; integration env supplies tiny_merged.pt"
)
async def test_run_sam_emits_progress_and_writes_runs_row(client_pg, active_scan, pg_session):
    project_id = active_scan["project_id"]
    scan_id = active_scan["scan_id"]
    analysis_id = active_scan["analysis"].id
    weights = os.environ["SAM_TEST_WEIGHTS"]

    progress_events = 0
    async with client_pg.stream(
        "POST",
        f"/api/v1/projects/{project_id}/scans/{scan_id}/run/sam",
        json={"weights_path": weights, "device": "cpu"},
    ) as resp:
        async for line in resp.aiter_lines():
            if line.startswith("event: progress"):
                progress_events += 1

    assert progress_events >= 1

    from sqlalchemy import select
    from flake_analysis.db.models.analysis import Run
    rows = (await pg_session.execute(
        select(Run).where(Run.analysis_id == analysis_id, Run.step == "sam")
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "succeeded"
```

- [ ] **Step 3: FAIL 확인 (SAM endpoint 없음)**

- [ ] **Step 4: `run_sam` 엔드포인트 작성**

`src/flake_analysis/api/routes/run.py` 끝에:

```python
from flake_analysis.api.schemas.compute import SamParams
from flake_analysis.api.services.runs import record_run_start, record_run_end
from flake_analysis.api.deps import get_session_for_background, get_active_analysis
from flake_analysis.pipeline.sam import run_sam_step


@router.post("/sam")
async def run_sam(
    project_id: str,
    scan_id: int,
    params: SamParams,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """Run SAM2 inference step with SSE progress."""
    manifest = await get_manifest(project_id=project_id, scan_id=scan_id)
    analysis = await get_active_analysis(scan_id, session)

    lock_cm = acquire_scan_lock(scan_id)
    await lock_cm.__aenter__()

    from flake_analysis.api.services.usage import emit
    await emit(
        session, user, "scan_run",
        {"step": "sam", "project_id": project_id, "scan_id": scan_id},
    )
    run_id = await record_run_start(session, analysis_id=analysis.id, step="sam")
    await session.commit()

    bridge = ProgressBridge()

    def call_wrapper():
        return run_sam_step(
            raw_images_dir=manifest.raw_images_dir,
            analysis_folder=manifest.analysis_folder,
            weights_path=params.weights_path,
            device=params.device,
            progress_callback=bridge.emit_progress,
        )

    async def generate():
        loop = asyncio.get_running_loop()

        async def run_pipeline():
            try:
                result = await loop.run_in_executor(None, call_wrapper)
                async with get_session_for_background() as bg:
                    await record_run_end(bg, run_id=run_id, status="succeeded",
                                         metrics=result)
                    await bg.commit()
                bridge.emit_done(result)
            except Exception as e:
                async with get_session_for_background() as bg:
                    await record_run_end(bg, run_id=run_id, status="failed", error=str(e))
                    await bg.commit()
                bridge.emit_error("pipeline_failed", str(e), {"exc_type": type(e).__name__})
            finally:
                bridge.close()

        task = asyncio.create_task(run_pipeline())
        try:
            async for frame in sse_stream(bridge):
                yield frame
        finally:
            try:
                await task
            finally:
                await lock_cm.__aexit__(None, None, None)

    return StreamingResponse(generate(), media_type="text/event-stream")
```

- [ ] **Step 5: PASS 확인**

```bash
SAM_TEST_WEIGHTS=/path/to/tiny_merged.pt \
env SAA_DB_NAME=saa_test SAA_DB_USER=houkjang SAA_DB_HOST=127.0.0.1 \
    uv run pytest tests/api/test_run_sam_sse.py -v -m pg
```

- [ ] **Step 6: Commit**

```bash
git add src/flake_analysis/api/routes/run.py src/flake_analysis/api/schemas/compute.py tests/api/test_run_sam_sse.py
git commit -m "feat(api): POST /projects/{pid}/scans/{sid}/run/sam endpoint with runs wiring"
```

---

### Task P2.8: Phase 2 acceptance gate

**Files:**
- Run: 기존 `scripts/dev/w10-acceptance.sh` (alembic + pytest 전체 + vitest + build)

**Owner suggestion:** PM (위임 — devops-engineer가 실행)

- [ ] **Step 1: 게이트 실행**

```bash
bash scripts/dev/w10-acceptance.sh
```

Expected: PASS — 회귀 없음. SAM endpoint test는 `SAM_TEST_WEIGHTS` 미설정 시 skip.

- [ ] **Step 2: 회귀 발견 시 P2 안에서 fix**

P2 task로 회귀 잡고, fix 후 게이트 재실행.

---

# Phase 3 — 프론트엔드 와이어링 (SAM 패널 + sseRun URL drift fix)

**Phase Goal:** `web/src/api/sseRun.ts` 의 URL drift fix (기존 4 step도 같이), ComputeTab에 SAM 진행 패널 추가, vitest fixture 갱신.

**Phase Owner:** frontend-architect

**Phase Exit Criteria:**
- `sseRun.ts` 의 URL이 `/api/v1/projects/{pid}/scans/{sid}/run/{step}` 그래머와 일치
- ComputeTab에서 SAM 버튼 클릭 → SSE progress (per-image idx/total/image_name/n_masks) 노출 → 에러는 toast + 각 이미지 옆 표시
- 모든 vitest PASS, build green
- W10-D 게이트 재실행 PASS (회귀 없음)

---

### Task P3.1: `sseRun.ts` URL drift fix

**Files:**
- Modify: `web/src/api/sseRun.ts:18`
- Modify: `web/src/hooks/useStepProgress.ts` (signature: 추가 `scanId` 파라미터)
- Modify: `web/src/hooks/useClusteringRefit.ts`, `useClusteringApplyThresholds.ts` 호출부
- Modify: `web/src/hooks/useStepProgress.ts` 사용처 (ComputeTab 등)

**Owner suggestion:** frontend-architect

> **현재 drift 상태:** `sseRun.ts:18` 은 `/api/v1/projects/${projectId}/run/${step}` — `scans/{sid}` 누락. 백엔드는 W10-C에서 이미 `/projects/{pid}/scans/{sid}/run/{step}` 으로 마이그레이션됨. 따라서 현재 프론트는 이 4 endpoint 호출 시 404 떨어진다 (1차 목표 GUI 업로드 e2e의 마지막 mile).

- [ ] **Step 1: vitest test 추가 (URL 검증)**

`web/src/api/__tests__/sseRun.test.ts`:

```ts
import { describe, expect, it, vi } from 'vitest'
import { postSseRun } from '@/api/sseRun'

describe('postSseRun', () => {
  it('hits the per-scan grammar URL', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('', { status: 200 })
    )
    await postSseRun('p-1', 42, 'background', { seed: 0 }, new AbortController().signal)
    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/v1/projects/p-1/scans/42/run/background',
      expect.anything()
    )
  })
})
```

- [ ] **Step 2: FAIL 확인**

- [ ] **Step 3: `sseRun.ts` 시그니처 수정**

```ts
export async function postSseRun(
  projectId: string,
  scanId: number,
  step: string,
  body: unknown,
  signal: AbortSignal
): Promise<Response> {
  const response = await fetch(
    `/api/v1/projects/${projectId}/scans/${scanId}/run/${step}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', ...getAuthHeaders() },
      credentials: 'include',
      body: JSON.stringify(body),
      signal,
    }
  )
  // ... rest unchanged
}
```

- [ ] **Step 4: 호출부 3개 수정 (`useStepProgress`, `useClusteringRefit`, `useClusteringApplyThresholds`)**

각 훅이 `scanId` 받게 시그니처 확장. 사용처(`ComputeTab` 등)는 이미 W10-D에서 `scanId` prop 받고 있음 — 그대로 통과.

- [ ] **Step 5: PASS 확인**

```bash
cd web && npm test -- --run src/api/__tests__/sseRun.test.ts src/hooks/__tests__/
```

- [ ] **Step 6: Commit**

```bash
git add web/src/api/sseRun.ts web/src/hooks/useStepProgress.ts web/src/hooks/useClusteringRefit.ts web/src/hooks/useClusteringApplyThresholds.ts web/src/api/__tests__/sseRun.test.ts
git commit -m "fix(web): sseRun URL drift — use /scans/{sid}/run/{step} per W10-C grammar"
```

---

### Task P3.2: ComputeTab에 SAM 패널 추가

**Files:**
- Modify: `web/src/components/run/ComputeTab.tsx` (또는 동등 위치)
- Create: `web/src/components/run/SamRunPanel.tsx`
- Test: `web/src/components/run/__tests__/SamRunPanel.test.tsx`

**Owner suggestion:** frontend-architect

> **UX 결정:** SAM 진행은 (a) 이미지별 i/N (b) 이미지별 n_masks (c) 에러 이미지는 빨간색 표시. 요건은 "이미지별 진행 + 에러" — 별도의 모달 없이 ComputeTab inline.

- [ ] **Step 1: failing test (vitest + RTL)**

`web/src/components/run/__tests__/SamRunPanel.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { SamRunPanel } from '@/components/run/SamRunPanel'

describe('SamRunPanel', () => {
  it('shows "Run SAM" button when idle', () => {
    render(<SamRunPanel projectId="p-1" scanId={1} />)
    expect(screen.getByRole('button', { name: /run sam/i })).toBeInTheDocument()
  })

  it('renders per-image progress when running', () => {
    // mock useStepProgress to return running with msg "[1/2] a.png: 5 masks"
    render(<SamRunPanel projectId="p-1" scanId={1} />)
    // (...)
  })
})
```

- [ ] **Step 2: FAIL 확인**

- [ ] **Step 3: `SamRunPanel.tsx` 작성**

```tsx
import { useStepProgress } from '@/hooks/useStepProgress'

interface Props {
  projectId: string
  scanId: number
}

export function SamRunPanel({ projectId, scanId }: Props) {
  const { status, pct, message, start } = useStepProgress<{ weights_path: string }, unknown>(
    projectId, scanId, 'sam'
  )

  // weights_path is supplied via env / future settings UI; for v1 we hard-wire dev path
  const weightsPath = import.meta.env.VITE_SAM_WEIGHTS_PATH ?? '/srv/sam/merged.pt'

  return (
    <section className="rounded border p-4">
      <header className="flex items-center justify-between">
        <h3 className="font-semibold">SAM2 Inference</h3>
        <button
          disabled={status === 'running'}
          onClick={() => start({ weights_path: weightsPath })}
          className="rounded bg-blue-600 px-3 py-1 text-white disabled:opacity-50"
        >
          Run SAM
        </button>
      </header>
      {status === 'running' && (
        <div className="mt-2">
          <progress value={pct} max={1} className="w-full" />
          <p className="text-sm text-gray-600">{message}</p>
        </div>
      )}
      {status === 'done' && <p className="mt-2 text-green-700">완료.</p>}
      {status === 'error' && <p className="mt-2 text-red-700">{message}</p>}
    </section>
  )
}
```

- [ ] **Step 4: ComputeTab에 마운트**

```tsx
// web/src/components/run/ComputeTab.tsx — 다른 step 패널 옆에 추가
<SamRunPanel projectId={projectId} scanId={scanId} />
```

- [ ] **Step 5: PASS 확인**

```bash
cd web && npm test -- --run src/components/run/__tests__/SamRunPanel.test.tsx
```

- [ ] **Step 6: Commit**

```bash
git add web/src/components/run/SamRunPanel.tsx web/src/components/run/ComputeTab.tsx web/src/components/run/__tests__/SamRunPanel.test.tsx
git commit -m "feat(web): SAM run panel in ComputeTab with per-image progress"
```

---

### Task P3.3: Phase 3 acceptance gate

**Files:**
- Run: `bash scripts/dev/w10-acceptance.sh`

**Owner suggestion:** PM (위임 — devops-engineer 실행)

- [ ] **Step 1: 게이트 실행**

회귀 없으면 PASS. 회귀 있으면 P3 안에서 fix.

---

# Phase 4 — GPU 컴퓨트 (AWS state 변경 — owner 승인 게이트)

> **⚠️ STOP — owner 승인 필요.**
> Phase 1-3가 모두 PASS한 후, owner에게 다음을 보고하고 명시적 GO를 받는다:
> 1. 비용 견적 — g6e.xlarge spot $0.30/h × 예상 사용 시간 + S3 weights 다운로드 한 달 추정
> 2. 인스턴스 lifecycle 결정 (always-on vs scale-to-zero — 신규 플랜 P4 옵션 A/B/C 중 하나)
> 3. AMI 결정 (S3 lazy download vs AMI bake — D5 동등)
> 4. spot 선점 처리 정책 (auto-retry vs surface to user — D6 동등)
> 5. Worker 격리 (procrastinate가 같은 g6e 박스 / 별도 박스)
>
> Owner GO 받기 전에 P4의 어떤 task도 dispatch 하지 않는다.

**Phase Goal:** g6e.xlarge GPU 인스턴스에서 SAM inference가 동작. procrastinate worker가 PG 큐를 polling, 큐에 들어온 작업을 잡아 실행하고 progress를 PG NOTIFY로 fan-out → API의 SSE endpoint가 forward.

**Phase Owner:** devops-engineer (AMI + EC2 + IAM + EventBridge), api-developer (procrastinate integration)

**Phase Exit Criteria:**
- g6e.xlarge spot 인스턴스 1대 기동 (always-on 또는 on-demand-launch — owner 결정)
- merged weights `s3://qpress-uploads/internal/sam/<weights-version>.pt` 업로드 완료
- procrastinate 큐 + worker entry point — `flake-analysis worker` CLI 실행 시 GPU 박스에서 SAM step 잡음
- API의 `POST /run/sam` 이 procrastinate에 enqueue → worker가 잡아 실행 → progress가 SSE로 fan-out → 프론트 패널에 표시 (e2e)
- Spot 인터럽션 시 graceful: `runs.status='failed'` + `error='spot_interrupted'`, frontend는 toast로 surface
- 비용 알람: CloudWatch alarm — 일일 GPU 사용 > $X 시 알림

---

### Task P4.0: Owner 승인 보고 + 결정 sweep

**Files:**
- Create: `docs/superpowers/decisions/2026-XX-XX-segmentation-phase4-decisions.md` (PM이 작성)

**Owner suggestion:** PM

- [ ] **Step 1: 비용 견적 + 옵션 표 작성**

decision doc에 D1 (lifecycle), D2 (AMI), D3 (spot policy), D4 (worker 격리), D5 (비용 알람) 옵션 + 권장 + 비용 추정.

- [ ] **Step 2: Owner와 AskUserQuestion 또는 직접 보고로 결정 받기**

- [ ] **Step 3: 결정 결과를 decision doc + project-status.md 에 기록**

> 이후 P4.1 ~ P4.5는 결정에 따라 task 시퀀스가 분기. Owner GO 받기 전에는 details 작성하지 않는다 — sketch만 유지.

---

### Task P4.1 (SKETCH): merged weights를 S3에 업로드

**Owner suggestion:** devops-engineer

- 산출: `s3://qpress-uploads/internal/sam/sam2.1_hiera_large.merged.<version>.pt`
- IAM: API 서버 + worker 둘 다 `s3:GetObject` on this prefix
- SHA256 체크섬 메타데이터로 부착

---

### Task P4.2 (SKETCH): procrastinate 통합

**Owner suggestion:** api-developer

- `pyproject.toml`에 procrastinate 추가
- alembic으로 procrastinate 스키마 마이그레이션 (별도 schema `procrastinate` 권장)
- `flake_analysis.worker.tasks` 모듈 — `@app.task` 로 SAM step wrap
- API의 `POST /run/sam` 을 dispatch-and-poll 패턴으로 rewrite (in-process executor → procrastinate enqueue)
- progress fan-out: PG NOTIFY (`run_progress` 채널) — worker가 `runs.metrics.progress` UPDATE + NOTIFY, API의 SSE generator가 LISTEN

---

### Task P4.3 (SKETCH): GPU AMI + 부트스트랩

**Owner suggestion:** devops-engineer

- AMI: Ubuntu 22.04 + CUDA 12.x + python 3.11 + uv + 우리 레포 + submodule + S3에서 weights lazy download
- bootstrap: systemd service 1개 — `flake-analysis worker --queue gpu` 자동 시작
- IAM role: `s3:GetObject` (weights), `rds:Connect` (procrastinate), `secretsmanager:GetSecretValue`

---

### Task P4.4 (SKETCH): EC2 기동 + EventBridge spot interrupt 핸들러

**Owner suggestion:** devops-engineer

- always-on g6e.xlarge spot launch (또는 owner 결정에 따라 scale-to-zero ASG)
- spot interrupt 2-min notice → SIGTERM → worker가 진행 중 task를 `failed/spot_interrupted` 마킹
- EventBridge rule → Lambda 또는 직접 RDS 마킹

---

### Task P4.5 (SKETCH): e2e 검증 + 비용 알람

**Owner suggestion:** PM (조율) + frontend-architect (Playwright e2e)

- Playwright: 업로드 → SAM 실행 → progress 패널 → 완료 확인
- CloudWatch alarm: 일일 GPU 사용 > $X
- runbook 추가: `docs/db-ops.md` 또는 `docs/sam-ops.md` 신설

---

## Risk Register

- **R1. Submodule import path drift.** `vendor/QPress-SAM-Flake` 의 internal import (`from sam2.build_sam import ...`) 가 fork 안의 `sys.path` 셋업에 의존. `core/pipeline/sam.py:_vendor_infer` 의 `sys.path.insert` 가 race condition 없는지 (동시 호출 시) 확인. 단일 프로세스 단일 worker면 문제 없음. multi-worker면 `pyproject.toml` 의 `[tool.uv.sources]` 또는 `pip install -e ./vendor/QPress-SAM-Flake` 로 정식 설치 필요.

- **R2. LoRA merge 수학 검증.** P1.5의 `merge_lora_into_base` 가 peft 컨벤션 정확히 따르는지 — peft 라이브러리 버전별로 (`B @ A` vs `A @ B`, scaling 위치) 차이 존재. fork 안의 학습 코드가 사용한 peft 버전 (`requirements.txt`) 확인 후 수식 fix.

- **R3. `runs` audit log 격리 transaction.** 백그라운드 SSE generator의 `record_run_end` 가 별도 session/transaction에서 실행 — `record_run_start` 의 commit과 `record_run_end` 의 commit 사이에 SSE stream 자체가 client abort로 중단되면 `runs.status='running'` 인 좀비 row 발생. 주기적 cleanup task 또는 timeout으로 mark-failed.

- **R4. 1차 목표 (GUI 업로드 e2e) 와 P3.1 의존.** sseRun URL drift는 1차 목표 직접 블로커는 아니지만 (업로드는 `/run/*` 안 거침), 업로드 성공 후 ComputeTab에서 첫 step 호출 시 404 — owner 검증 단계에서 즉시 발견. P3.1 우선순위 ↑.

- **R5. SAM weights size in dev.** 우리 dev 박스(M1 Mac 등)는 GPU 없음. `tiny_merged.pt` 픽스처가 진짜 ~900MB merged.pt 와 schema 호환되는지 — `merge_lora` CLI를 prod base+lora에 한 번 돌려보고 `torch.load` 하는 mini-script 별도 검증.

- **R6. Phase 4 비용 폭주.** g6e.xlarge always-on은 spot $0.30/h × 730h = $220/mo. 학습 안 한다고 했으니 이 비용은 over-provisioning. scale-to-zero 권장 (D2=B/C). P4.0 결정 sweep에서 owner 결정 받음.

- **R7. procrastinate vs in-process scheduler 회귀.** P4.2에서 `POST /run/sam` 을 dispatch-and-poll로 rewrite할 때, 기존 4 step의 in-process executor 패턴은 그대로 둘지 / 같이 procrastinate로 옮길지 결정. **권장**: SAM만 procrastinate (GPU step), CPU step은 in-process 유지 (cold start 비용 없음). 하지만 `runs` wiring이 일관성 깨짐 — SAM은 worker가 INSERT/UPDATE, CPU step은 API process가 INSERT/UPDATE. 검증 단계에서 discrepancy 안 생기는지 확인.

- **R8. Submodule sync 비용.** fork에 학습 쪽 변경이 들어오면 submodule pointer 업데이트 필요. CI에 submodule pinned check 추가 권장.

---

## Self-review checklist (planner — fix inline)

- [ ] **Spec coverage**: 4-fix(callback / IndexError / chdir / args.json) → P1.2-P1.4. LoRA merge → P1.5. runs wiring → P2.4-P2.6. SAM endpoint → P2.7. URL drift → P3.1. SAM panel → P3.2. AWS GPU → P4.
- [ ] **Placeholder scan**: P4 task들은 의도적으로 SKETCH (owner 승인 게이트). P1-P3 task들은 모두 코드/test 명시.
- [ ] **Type consistency**: `progress_callback` 형식 — P1 vendor에선 `(payload: dict) -> None`, P2의 `core/pipeline/sam.py` 에서 `(pct: float, msg: str) -> None` 으로 shim. ProgressBridge.emit_progress 시그니처와 일치.
- [ ] **W7 SKETCH 처리**: P1.0에서 Superseded 헤더 추가.
- [ ] **CLAUDE.md 룰 준수**: PM은 도메인 명령(pytest/alembic 등) 직접 실행 금지 — 모든 acceptance gate task는 devops-engineer 위임 표시.

---

## Execution Handoff

**Plan complete and saved to** `docs/superpowers/plans/2026-05-25-segmentation-web-integration.md`.

이 플랜은 **Phase 1-3 만 자동 실행 가능**하다 (subagent-driven-development 사용). Phase 4는 owner 승인 게이트가 박혀 있어 자동 dispatch 금지 — P4.0 task에서 owner와 직접 결정 후 P4.1+ 확장.

**다음 단계 (PM):**
1. Phase 1 → Phase 2 → Phase 3 순서로 subagent-driven-development 실행 (각 phase 끝에 acceptance gate)
2. Phase 3 완료 시 1차 목표 GUI 업로드 e2e 재검증
3. 그 후 P4.0 owner 승인 sweep

**Approach for Phase 1-3:**
- **1. Subagent-Driven (recommended)** — fresh subagent per task + 두 단계 review (spec + quality)
- **2. Inline Execution** — executing-plans skill, batch with checkpoints
