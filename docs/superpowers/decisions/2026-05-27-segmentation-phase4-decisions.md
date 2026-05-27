# Segmentation Web Integration — Phase 4 Decisions

**Date locked**: 2026-05-27
**Plan**: [`docs/superpowers/plans/2026-05-25-segmentation-web-integration.md`](../plans/2026-05-25-segmentation-web-integration.md) §Phase 4
**Owner sweep status**: GO. Phase 4 dispatch authorized.
**AWS region**: `us-east-2` (matches existing `qpressdb` RDS + `qpress-uploads` S3 + bastion stack — see `docs/db-ops.md`).

**Existing AWS GPU quotas (verified 2026-05-27 via `aws service-quotas list-service-quotas --service-code ec2`)**:
- `Running On-Demand G and VT instances` = **192 vCPU** (covers ~48 g6e.xlarge concurrently — no quota request needed for Phase 4).
- `All G and VT Spot Instance Requests` = **192 vCPU** (covers ~48 g6e.xlarge spot concurrently).
- `Running On-Demand P instances` = 0 (P-family not used; SAM2 runs on G-family L40S/L4).

No additional AWS quota approvals required to launch g6e.xlarge spot in us-east-2.

---

## D1. GPU instance lifecycle — **Scale-to-zero on-demand spot launch (Option B)**

**Locked at brainstorm time** (Plan §Decisions table, decision 4: "온디맨드 spot 런칭 (idle ~$0, cold start 3–5분 허용)"). Re-confirmed in P4.0 sweep.

- API receives `POST /run/sam` (or `/run/pipeline` w/ sam step) → enqueue procrastinate job → if no GPU box online, EventBridge / API trigger boots a spot g6e.xlarge → worker auto-starts → drains queue → idle timer (e.g., 10 min) → terminate.
- Cost when idle: ~$0 (no running EC2). Cost while running: ~$0.30/h (g6e.xlarge spot, us-east-2 estimate).
- Cold-start tolerance: 3-5 min acceptable per brainstorm decision 4.

**Why not always-on?** R6 risk register — always-on g6e.xlarge spot ≈ $220/mo over-provisioning for current dev throughput. ASG with min=0/max=1 deferred to v2.

## D2. Instance type — **g6e.xlarge spot**

Plan default. SAM2.1 hiera_large + AMG inference fits comfortably in L40S 48GB. Spot price ~$0.30/h in us-east-2 (subject to live verification at launch).

**Why not g6.xlarge (L4 24GB)?** SAM2 hiera_large + AMG memory headroom not empirically validated; cheaper-per-hour but risks OOM at first prod batch. Defer to v2 if cost optimization needed.

**Why not g5.xlarge (A10G)?** Older generation, similar price, no upside.

## D3. AMI / weights distribution — **S3 lazy download (Option A)**

- AMI: stock Ubuntu 22.04 + CUDA 12.x + cuDNN + python 3.11 + uv (DLAMI base or custom-baked once-and-cached).
- Weights `s3://qpress-uploads/internal/sam/sam2.1_hiera_large.merged.<version>.pt` downloaded by worker on first task after boot, cached to instance store / EBS for the lifetime of the spot instance.
- IAM: instance role with `s3:GetObject` on the prefix.

**Why not AMI bake?** D1=B (scale-to-zero) implies most boots are cold; weights download (~few hundred MB) is amortized across the queue drain that triggered the boot. AMI bake adds CI complexity for marginal cold-start improvement.

## D4. Spot interrupt policy — **Auto-retry once + surface on second failure**

- Worker registers SIGTERM handler. On 2-min spot interrupt notice → mark current run `failed / spot_interrupted` in `runs` table → re-enqueue job onto procrastinate (idempotent path: tasks must be safe to re-run; SAM step is by design — overwrites per_image_results.json).
- After 1 retry, if still failing or interrupted again, surface to user as "GPU capacity reclaimed — retry later" toast.

## D5. Worker isolation — **SAM-only on procrastinate (Option B in R7)**

- Only SAM step uses procrastinate (the GPU step). CPU steps (background / domain_stats / domain_proximity / thumbnails) stay in-process per existing pattern.
- Trade-off (logged in R7): `runs` row INSERT/UPDATE responsibility splits — SAM = worker process, CPU = API process. Verify e2e at P4.5 that no row-update race / missing-on-failure case slips through.

## D6. Cost alarm — **$20/day CloudWatch alarm**

Owner-set threshold at P4.0 sweep (2026-05-27).
- CloudWatch alarm on EC2 spot spend (cost allocation tag `Project=qpress-sam`) > $20/day → SNS → email owner.
- Monthly ceiling: ~$600. Comfortable headroom for batch runs without runaway risk.
- Reviewed at end of Phase 4; reduce to $10/day or $5/day after first month if real usage stays under.

## D7. P1.5b execution order — **Before P4.1 (sequential, code-only)**

- P1.5b (prod LoRA prefix-strip + Conv2d Linear path support) runs as the first Phase 4 task — it's code-only, AWS-cost zero, and produces the artifact P4.1 needs (a real prod-mergeable LoRA path).
- Synthetic-data merge already validated at P1.5; P1.5b removes the two known production blockers identified during P1.5:
  - (a) prod LoRA prefixes nest under `image_encoder.base_model.model.trunk.*` — current `_strip_peft_prefix` only handles top-level `base_model.model.*`. Need recursive strip OR direct `base_layer.weight` slot population.
  - (b) `patch_embed.proj` is `Conv2d`, not `Linear`. Current merge does `B @ A`; needs `(B.flatten(1) @ A.flatten(1)).reshape(target)` for the 1 Conv2d LoRA pair out of 100.
- Validation: synthetic CI fixture stays the only auto-tested path. Real prod merge happens at P4.3 (on the GPU EC2 box) where weights cache is local — no need to merge on the dev laptop.

---

## Phase 4 task sequence (locked)

1. **P1.5b** ✅ done (`6f7fc2e` fork + `6c82775` main) — recursive PEFT prefix strip + Conv2d LoRA path.
2. **P4.2** ✅ done (`596f498..14f12b4`, gate cross-check PASS) — procrastinate integration.
3. **P4.1** **absorbed into P4.3** per owner decision 2026-05-27: prod merge happens once on the GPU EC2 box during bootstrap (D7 비고와 정합 — 데브 머신 깨끗 유지, GPU 박스가 어차피 디스크/네트워크/CUDA 더 좋음). Output of merge → uploaded to `s3://qpress-uploads/internal/sam/sam2.1_hiera_large.merged.<sha>.pt` + SHA256 from the GPU box.
4. **P4.3** — GPU AMI choice + bootstrap (Ubuntu 22.04 + CUDA + uv + repo + submodule + base SAM2 + LoRA download + **prod merge via `vendor/QPress-SAM-Flake/scripts/merge_lora.py`** + S3 PUT merged.pt + systemd `flake-analysis worker --queue gpu`).
5. **P4.4** — EC2 launch flow (scale-to-zero spot via API trigger / EventBridge) + spot-interrupt EventBridge → SIGTERM → mark-failed pipeline.
6. **P4.5** — Playwright e2e (upload → run pipeline → SAM step on real GPU → progress → completion) + CloudWatch $20/day alarm + runbook (`docs/sam-ops.md`).

**Owner GO 2026-05-27**: bulk approval for AWS-touching steps (P4.3, P4.4, P4.5). PM dispatches sequentially; per-task results reported up. Final live e2e (P4.5) is the owner verification surface.

---

## Cost summary (locked)

| Item | Estimate | Trigger |
|---|---|---|
| g6e.xlarge spot | ~$0.30/h (us-east-2 — verify at P4.4) | only while draining queue |
| S3 GET (weights) | ~$0.01/cold-boot (single 500MB download, post-cache amortized) | per spot boot |
| S3 storage (weights) | ~$0.02/mo per 1GB | one-time |
| CloudWatch alarm | ~$0 (1 alarm in free tier) | always-on |
| Monthly hard cap (alarm) | $600 ($20/day × 30) | scaling event |
| Expected monthly cost (light dev use, ~30h GPU) | ~$10-15 | actual workload |

Owner approval threshold: anything > $20/day rings the alarm; investigation triggers immediate.
