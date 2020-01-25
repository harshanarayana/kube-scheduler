from os import getenv
from json import loads as json_loads
import random

from kubernetes import config, watch
from kubernetes.client import ApiClient, CoreV1Api, V1ObjectReference, V1ObjectMeta, V1Binding, Configuration
from kubernetes.client.rest import ApiException, RESTClientObject

from logging import basicConfig, getLogger, INFO

formatter = " %(asctime)s | %(levelname)-6s | %(process)d | %(threadName)-12s |" \
            " %(thread)-15d | %(name)-30s | %(filename)s:%(lineno)d | %(message)s |"
basicConfig(level=INFO, format=formatter)
logger = getLogger("meetup-scheduler")

V1_CLIENT = None  # type: CoreV1Api
SCHEDULE_STRATEGY = "schedulingStrategy=meetup"
_NOSCHEDULE_TAINT = "NoSchedule"


def _get_ready_nodes(v1_client, filtered=True):
    ready_nodes = []
    try:
        for n in v1_client.list_node().items:
            if n.metadata.labels.get("noCustomScheduler") == "yes":
                logger.info(f"Skipping Node {n.metadata.name} since it has noCustomScheduler label")
                continue
            if filtered:
                if not n.spec.unschedulable:
                    no_schedule_taint = False
                    if n.spec.taints:
                        for taint in n.spec.taints:
                            if _NOSCHEDULE_TAINT == taint.to_dict().get("effect", None):
                                no_schedule_taint = True
                                break
                    if not no_schedule_taint:
                        for status in n.status.conditions:
                            if status.status == "True" and status.type == "Ready" and n.metadata.name:
                                ready_nodes.append(n.metadata.name)
                    else:
                        logger.error("NoSchedule taint effect on node %s", n.metadata.name)
                else:
                    logger.error("Scheduling disabled on %s ", n.metadata.name)
            else:
                if n.metadata.name:
                    ready_nodes.append(n.metadata.name)
        logger.info("Nodes : %s, Filtered: %s", ready_nodes, filtered)
    except ApiException as e:
        logger.error(json_loads(e.body)["message"])
        ready_nodes = []
    return ready_nodes


def _get_schedulable_node(v1_client):
    node_list = _get_ready_nodes(v1_client)
    if not node_list:
        return None
    available_nodes = list(set(node_list))
    return random.choice(available_nodes)


def schedule_pod(v1_client, name, node, namespace="default"):
    target = V1ObjectReference()
    target.kind = "Node"
    target.apiVersion = "v1"
    target.name = node
    meta = V1ObjectMeta()
    meta.name = name
    body = V1Binding(api_version=None, kind=None, metadata=meta, target=target)
    logger.info("Binding Pod: %s  to  Node: %s", name, node)
    return v1_client.create_namespaced_pod_binding(name, namespace, body)

def watch_pod_events():
    V1_CLIENT = CoreV1Api()
    while True:
        try:
            logger.info("Checking for pod events....")
            try:
                watcher = watch.Watch()
                for event in watcher.stream(V1_CLIENT.list_pod_for_all_namespaces, label_selector=SCHEDULE_STRATEGY, timeout_seconds=20):
                    logger.info(f"Event: {event['type']} {event['object'].kind}, {event['object'].metadata.namespace}, {event['object'].metadata.name}, {event['object'].status.phase}")
                    if event["object"].status.phase == "Pending":
                        try:
                            logger.info(f'{event["object"].metadata.name} needs scheduling...')
                            pod_namespace = event["object"].metadata.namespace
                            pod_name = event["object"].metadata.name
                            service_name = event["object"].metadata.labels["serviceName"]
                            logger.info("Processing for Pod: %s/%s", pod_namespace, pod_name)
                            node_name = _get_schedulable_node(V1_CLIENT)
                            if node_name:
                                logger.info("Namespace %s, PodName %s , Node Name: %s  Service Name: %s",
                                            pod_namespace, pod_name, node_name, service_name)
                                res = schedule_pod(V1_CLIENT, pod_name, node_name, pod_namespace)
                                logger.info("Response %s ", res)
                            else:
                                logger.error(f"Found no valid node to schedule {pod_name} in {pod_namespace}")
                        except ApiException as e:
                            logger.error(json_loads(e.body)["message"])
                        except ValueError as e:
                            logger.error("Value Error %s", e)
                        except:
                            logger.exception("Ignoring Exception")
                logger.info("Resetting k8s watcher...")
            except:
                logger.exception("Ignoring Exception")
            finally:
                del watcher
        except:
            logger.exception("Ignoring Exception & listening for pod events")

def main():
    logger.info("Initializing the meetup scheduler...")
    logger.info("Watching for pod events...")
    watch_pod_events()


if __name__ == "__main__":
    config.load_incluster_config()
    main()
