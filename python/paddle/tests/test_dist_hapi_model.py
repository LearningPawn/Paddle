# Copyright (c) 2020 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import copy
import os
import subprocess
import time
import unittest

import paddle.fluid as fluid
from paddle.distributed.utils.launch_utils import (
    TrainerProc,
    find_free_ports,
    get_cluster,
    watch_local_trainers,
)


def get_cluster_from_args(selected_gpus):
    cluster_node_ips = '127.0.0.1'
    node_ip = '127.0.0.1'

    node_ips = [x.strip() for x in cluster_node_ips.split(',')]

    node_ips.index(node_ip)

    free_ports = None

    free_ports = find_free_ports(len(selected_gpus))
    if free_ports is not None:
        free_ports = list(free_ports)

    trainer_endpoints = []
    for ip in node_ips:
        trainer_endpoints.append(["%s:%d" % (ip, port) for port in free_ports])
    return get_cluster(node_ips, node_ip, trainer_endpoints, selected_gpus)


def get_gpus(selected_gpus):
    selected_gpus = [x.strip() for x in selected_gpus.split(',')]
    return selected_gpus


def start_local_trainers(
    cluster,
    pod,
    training_script,
    eager_mode,
    training_script_args,
    log_dir=None,
):
    current_env = copy.copy(os.environ.copy())
    # paddle broadcast ncclUniqueId use socket, and
    # proxy maybe make trainers unreachable, so delete them.
    # if we set them to "", grpc will log error message "bad uri"
    # so just delete them.
    current_env.pop("http_proxy", None)
    current_env.pop("https_proxy", None)

    procs = []
    for t in pod.trainers:
        proc_env = {
            "FLAGS_selected_gpus": "%s" % ",".join([str(g) for g in t.gpus]),
            "PADDLE_TRAINER_ID": "%d" % t.rank,
            "PADDLE_CURRENT_ENDPOINT": "%s" % t.endpoint,
            "PADDLE_TRAINERS_NUM": "%d" % cluster.trainers_nranks(),
            "PADDLE_TRAINER_ENDPOINTS": ",".join(cluster.trainers_endpoints()),
        }

        if not eager_mode:
            proc_env["FLAGS_enable_eager_mode"] = "%d" % 0

        current_env.update(proc_env)

        print("trainer proc env:{}".format(current_env))

        if os.getenv('WITH_COVERAGE', 'OFF') == 'ON':
            cmd = "python -m coverage run --branch -p " + training_script
        else:
            cmd = "python -u " + training_script

        print("start trainer proc:{} env:{}".format(cmd, proc_env))

        fn = None

        proc = subprocess.Popen(cmd.split(" "), env=current_env)

        tp = TrainerProc()
        tp.proc = proc
        tp.rank = t.rank
        tp.log_fn = fn
        tp.cmd = cmd

        procs.append(tp)

    return procs


class TestMultipleGpus(unittest.TestCase):
    def run_mnist_2gpu(self, target_file_name, eager_mode=True):
        if fluid.core.get_cuda_device_count() == 0:
            return

        selected_gpus = get_gpus('0,1')
        cluster = None
        pod = None

        cluster, pod = get_cluster_from_args(selected_gpus)

        procs = start_local_trainers(
            cluster,
            pod,
            eager_mode=eager_mode,
            training_script=target_file_name,
            training_script_args=[],
        )

        while True:
            alive = watch_local_trainers(procs, cluster.trainers_nranks())

            if not alive:
                print("Local procs complete, POD info:{}".format(pod))
                break
            time.sleep(3)

    def test_hapi_multiple_gpus_static(self):
        self.run_mnist_2gpu('dist_hapi_mnist_static.py')
        self.run_mnist_2gpu('dist_hapi_mnist_static.py', eager_mode=False)

    def test_hapi_multiple_gpus_dynamic(self):
        self.run_mnist_2gpu('dist_hapi_mnist_dynamic.py')
        self.run_mnist_2gpu('dist_hapi_mnist_dynamic.py', eager_mode=False)

    def test_hapi_amp_static(self):
        self.run_mnist_2gpu('dist_hapi_pure_fp16_static.py')
        self.run_mnist_2gpu('dist_hapi_pure_fp16_static.py', eager_mode=False)


if __name__ == "__main__":
    os.environ["FLAGS_enable_eager_mode"] = "1"
    unittest.main()
