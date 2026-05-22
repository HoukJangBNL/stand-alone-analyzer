# S3 Uploads Bucket — `qpress-uploads`

Provisioning artifacts for the W5 upload flow. See [`docs/db-ops.md` §7](../../docs/db-ops.md) for the full runbook.

## Apply order (state changes — PM executes, user pre-approves)

1. `s3api create-bucket` (Task 1)
2. `s3api put-public-access-block` (Task 1)
3. `s3api put-bucket-encryption` (Task 1)
4. `s3api put-bucket-ownership-controls` (Task 1)
5. `s3api put-bucket-cors --cors-configuration file://cors.json` (Task 2)
6. `s3api put-bucket-lifecycle-configuration --lifecycle-configuration file://lifecycle.json` (Task 3)
7. `iam create-policy` × 2 (Task 4) → `iam create-user`/`create-role` × N → `iam attach-*-policy` + `iam tag-*`
8. `s3api put-bucket-policy --policy file://bucket-policy.json` (Task 5 — must come AFTER IAM principals exist + are tagged, otherwise the deny is evaluated against an untagged principal)

## Dry-run / audit

```
bash scripts/s3/dryrun.sh
```

Read-only. Prints PASS/FAIL per resource, diffs against the JSON in this directory.

## Rollback

Per-task rollback in `docs/db-ops.md` §7. Bucket-level rollback (delete entire bucket) requires emptying it first — see §7 "Emergency rollback".
