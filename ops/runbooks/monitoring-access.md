# Private monitoring access runbook

Prometheus and node exporter share the internal `observability` network. The Compose overlay exposes their container ports for service discovery but publishes none to the VM host. The default deployment therefore never exposes Prometheus publicly. Prometheus continues to collect API signals, node exporter host resources and backup metrics from the node exporter textfile collector.

Grafana 13.1.3 is temporarily absent from the runtime because its latest official immutable image reported 14 HIGH findings in Grafana and bundled plugin Go binaries. The checked-in dashboard and provisioning source files are inactive future assets and are not mounted by Compose. Re-enable Grafana only when an official immutable release reports zero unwaived HIGH/CRITICAL findings under the unchanged focused scan. This MVP accepts neither a waiver nor a custom Grafana build.

Container-resource metrics are pending an official cAdvisor release built with grpc-go >= 1.82.1 and passing the unchanged HIGH/CRITICAL security gate. The current official release remains affected by [GHSA-hrxh-6v49-42gf](https://github.com/advisories/GHSA-hrxh-6v49-42gf), so this MVP does not run cAdvisor, accept a waiver or maintain a custom build. The sole-purpose Docker socket proxy was removed with cAdvisor, leaving no standing Docker socket access in the monitoring profile. The separately invoked restore drill retains its bounded Docker access and is not part of telemetry.

Do not reintroduce cAdvisor or its proxy until an official release satisfies both the grpc-go version floor and a focused vulnerability scan. Until then, use the preserved API, host and backup signals for operational decisions and record the missing per-container CPU and memory views as degraded observability.

## Operator access through an SSH tunnel

1. Confirm the operator has time-limited SSH access to the VM. Prometheus has no application authentication in this MVP, so the loopback-only tunnel and short access lifetime are mandatory.
2. On the VM, resolve Prometheus's current address on the private Docker network:

   ```sh
   docker inspect "$(docker compose -f compose.yaml -f compose.production.yaml -f compose.observability.yaml ps -q prometheus)" \
     --format '{{with index .NetworkSettings.Networks "alright-mate_observability"}}{{.IPAddress}}{{end}}'
   ```

3. On the operator workstation, substitute that private address and establish `ssh -N -L 127.0.0.1:9090:PRIVATE_CONTAINER_IP:9090 operator@VM_ADDRESS`.
4. Open `http://127.0.0.1:9090` for the private Prometheus UI/API, complete the approved investigation and close the SSH process immediately afterwards.

The VM firewall remains unchanged and no public listener is created. Container addresses can change after recreation, so resolve the address for each session. An authenticated gateway route may be designed after MVP, but must receive a separate threat review and must never bypass OAuth.

## Alert delivery

The checked-in rules cover API availability, 5xx ratio, p95 latency, host disk pressure and backup failure/staleness. Connecting Prometheus to an external alert receiver requires separately approved receiver credentials and routing; this repository intentionally does not contain them. Until then, operators must inspect the alerts view through the private session during routine checks.
