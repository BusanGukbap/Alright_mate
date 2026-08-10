# Remote state contract

Remote state is opt-in and must be bootstrapped by an authorised operator before the root module is initialised. The state bucket is separate from the application backup bucket and must have versioning, server-side encryption, public-access blocking and retention protection enabled.

1. Create the state bucket through a separately reviewed bootstrap process. Do not use this root module to manage the backend that stores its own state.
2. Copy `backend.tf.example` to `backend.tf` only in the authorised deployment workspace.
3. Pass `bucket`, `key`, `region`, `encrypt=true` and `use_lockfile=true` through a local backend configuration file excluded from Git, or through explicit `-backend-config` arguments.
4. Use the AWS SDK credential chain. Never put account identifiers or credentials in a committed backend file.
5. Confirm recovery and locking behaviour before moving any existing state. This repository does not contain or migrate live state.

Validation in this task uses `tofu init -backend=false`; no remote backend or AWS resource is created.
