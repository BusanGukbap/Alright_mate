# OpenTofu infrastructure

This root module describes the approved Seoul MVP: one Ubuntu LTS Lightsail 2 GB host, static IP, restricted firewall, optional Lightsail DNS, encrypted backup storage and monthly budget alerts.

The backup bucket uses a customer-managed symmetric AWS KMS key with automatic annual rotation and an S3 Bucket Key. KMS key-material storage starts at roughly USD 1 per month, rises by roughly USD 1 after the first rotation and another USD 1 after the second rotation, then remains capped at roughly USD 3 per month for this key; KMS request charges are additional. The S3 Bucket Key reduces request volume and cost. The six-week MVP should therefore remain in the initial USD 1 stage, while a retained year-one/year-two deployment must budget for the later USD 2/USD 3 stages. The project-wide USD 12 and USD 15 Budget notifications provide drift warnings, not a spending cap. Confirm current Seoul pricing before an approved apply. The backup operator still needs explicit S3 object permissions and KMS data-key permissions in its IAM policy.

Copy `terraform.tfvars.example` to an ignored `.tfvars` file and replace the documentation-only values. Review every value before use. In particular, keep SSH restricted to operator networks; `allow_world_ssh` is an emergency override that requires an explicit `true` value.

Static validation:

```text
tofu fmt -check
tofu init -backend=false
tofu validate
tofu providers lock -platform=linux_amd64 -platform=windows_amd64
python verify_provider_lock.py --tofu tofu
```

`provider-lock-policy.json` fixes the required deployment platforms to `linux_amd64` and `windows_amd64`. The verifier regenerates the AWS provider lock for both platforms in a disposable directory and fails if the committed lock lacks any resulting package hash. It does not initialise a backend, read AWS credentials or create resources.

Do not run `plan` or `apply` without separate approval, configured AWS credentials and a review of the expected paid resources. DNS records do not change registrar delegation; an operator must approve and perform that external step.
