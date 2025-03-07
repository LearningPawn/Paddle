# Copyright (c) 2022 PaddlePaddle Authors. All Rights Reserved.
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

import paddle
import paddle.distributed.communication.stream as stream
import paddle.fluid.framework as framework


def recv(tensor, src=0, group=None, sync_op=True):
    """
    Receive a tensor to the sender.

    Args:
        tensor (Tensor): The tensor to receive. Its data type
            should be float16, float32, float64, int32, int64, int8, uint8, bool or bfloat16.
        src (int): The source rank id.
        group (Group, optional): The group instance return by new_group or None for global default group. Default: None.
        sync_op (bool, optional): Whether this op is a sync op. The default value is True.

    Returns:
        Return a task object.

    Examples:
        .. code-block:: python

            # required: distributed
            import paddle
            import paddle.distributed as dist

            dist.init_parallel_env()
            if dist.get_rank() == 0:
                data = paddle.to_tensor([7, 8, 9])
                dist.send(data, dst=1)
            else:
                data = paddle.to_tensor([1, 2, 3])
                dist.recv(data, src=0)
            print(data)
            # [7, 8, 9] (2 GPUs)
    """
    if not framework._in_legacy_dygraph():
        return stream.recv(
            tensor, src=src, group=group, sync_op=sync_op, use_calc_stream=False
        )

    # code below will be removed after we remove the old dygraph
    if group is not None and not group.is_member():
        return
    use_calc_stream = sync_op
    gsrc = src if group is None else group.get_group_rank(src)
    ring_id = 0 if group is None else group.id
    return paddle._legacy_C_ops.recv_v2(
        tensor,
        'use_calc_stream',
        use_calc_stream,
        'ring_id',
        ring_id,
        'peer',
        src,
        'dtype',
        tensor.dtype,
        'out_shape',
        tensor.shape,
    )


def irecv(tensor, src=None, group=None):
    """
    Receive a tensor to the sender.

    Args:
        tensor (Tensor): The Tensor to receive. Its data type
            should be float16, float32, float64, int32, int64, int8, uint8, bool or bfloat16.
        src (int): The source rank id.
        group (Group, optional): The group instance return by new_group or None for global default group. Default: None.

    Returns:
        Return a task object.

    Warning:
        This API only supports the dygraph mode.

    Examples:
        .. code-block:: python

            # required: distributed
            import paddle
            import paddle.distributed as dist

            dist.init_parallel_env()
            if dist.get_rank() == 0:
                data = paddle.to_tensor([7, 8, 9])
                task = dist.isend(data, dst=1)
            else:
                data = paddle.to_tensor([1, 2, 3])
                task = dist.irecv(data, src=0)
            task.wait()
            print(data)
            # [7, 8, 9] (2 GPUs)
    """
    return recv(tensor, src, group, sync_op=False)
