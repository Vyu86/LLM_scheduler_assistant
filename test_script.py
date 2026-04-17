import time
import os
import re
from kubernetes import client, config
from llm_scheduler_local import ClusterSnapshot, WorkloadDescriptor, NodeState, WorkloadType, load_model, query_llm

# Data Normalization Helpers

def parse_cpu(cpu_str: str) -> float:
    """Convert K8s CPU strings (500m, 1, 1.5) to float cores."""
    if cpu_str.endswith('m'):
        return float(cpu_str[:-1]) / 1000.0
    return float(cpu_str)


def parse_mem(mem_str: str) -> float:
    """Convert K8s memory strings (Ki, Mi, Gi) to float GB."""
    factors = {'Ki': 1024, 'Mi': 1024 ** 2, 'Gi': 1024 ** 3, 'Ti': 1024 ** 4}
    suffix = mem_str[-2:]
    if suffix in factors:
        bytes_val = float(mem_str[:-2]) * factors[suffix]
    else:
        bytes_val = float(mem_str)  # Assume raw bytes
    return bytes_val / (1024 ** 3)

# Kubernetes Control Loop

class RawK8sConfigurator:
    def __init__(self):
        try:
            config.load_kube_config()
        except:
            config.load_incluster_config()

        self.v1 = client.CoreV1Api()

    def get_cluster_snapshot(self) -> ClusterSnapshot:
        """Pulls raw data from Node/Pod statuses to build the LLM snapshot."""
        nodes = self.v1.list_node().items
        node_states = []

        for n in nodes:
            # Capacity vs Allocatable
            cpu_cap = parse_cpu(n.status.capacity['cpu'])
            mem_cap = parse_mem(n.status.capacity['memory'])

            # Assuming no Metrics Server, we calculate
            # the ratio of requested resources vs total capacity.
            field_selector = f"spec.nodeName={n.metadata.name}"
            pods_on_node = self.v1.list_pod_for_all_namespaces(field_selector=field_selector).items

            req_cpu = 0.0
            req_mem = 0.0
            for p in pods_on_node:
                if p.status.phase == "Running":
                    for container in p.spec.containers:
                        res = container.resources.requests or {}
                        req_cpu += parse_cpu(res.get('cpu', '0m'))
                        req_mem += parse_mem(res.get('memory', '0Mi'))

            node_states.append(NodeState(
                node_id=n.metadata.name,
                cpu_utilization=min(req_cpu / cpu_cap, 1.0) if cpu_cap > 0 else 0,
                memory_utilization=min(req_mem / mem_cap, 1.0) if mem_cap > 0 else 0,
                pod_count=len(pods_on_node),
                available_cpu_cores=max(cpu_cap - req_cpu, 0),
                available_memory_gb=max(mem_cap - req_mem, 0)
            ))

        # Determine Workload context from Pending pods
        pending_pods = self.v1.list_pod_for_all_namespaces(field_selector="status.phase=Pending").items

        # Simple heuristic: if many pods are pending, it's compute-heavy/batch
        w_type = WorkloadType.COMPUTE_HEAVY if len(pending_pods) > 5 else WorkloadType.LATENCY_SENSITIVE

        return ClusterSnapshot(
            workload=WorkloadDescriptor(
                workload_type=w_type,
                queue_depth=len(pending_pods),
                avg_cpu_request=1.0,
                avg_memory_request_gb=2.0,
                has_deadline=False
            ),
            nodes=node_states
        )

    def update_config(self, policy: str):
        """Applies the LLM decision to a ConfigMap."""
        cm_name = "llm-scheduler-config"
        namespace = "default"

        meta = client.V1ObjectMeta(name=cm_name)
        body = client.V1ConfigMap(
            api_version="v1",
            kind="ConfigMap",
            metadata=meta,
            data={"active_policy": policy, "last_updated": str(time.ctime())}
        )

        try:
            # Try to update; if it doesn't exist, create it
            self.v1.replace_namespaced_config_map(name=cm_name, namespace=namespace, body=body)
            print(f"[*] Cluster Policy Updated to: {policy}")
        except client.exceptions.ApiException as e:
            if e.status == 404:
                self.v1.create_namespaced_config_map(namespace=namespace, body=body)
                print(f"[*] Created new ConfigMap with Policy: {policy}")
            else:
                raise


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_loop():
    MODEL_PATH = "./models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf"
    if not os.path.exists(MODEL_PATH):
        print(f"Model missing at {MODEL_PATH}")
        return

    llm = load_model(MODEL_PATH)
    configurator = RawK8sConfigurator()

    print("--- LLM K8s Configurator Active ---")
    while True:
        try:
            snapshot = configurator.get_cluster_snapshot()
            print(snapshot)
            result = query_llm(snapshot, llm)

            print(result)

            policy = result.decision.recommended_policy.value

            configurator.update_config(policy)

        except Exception as e:
            print(f"Error in control loop: {e}")

        time.sleep(10) #change depending on allocation/testing preferences


if __name__ == "__main__":
    run_loop()