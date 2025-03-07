#   Copyright (c) 2021 PaddlePaddle Authors. All Rights Reserved.
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

from .layer.fused_transformer import FusedMultiHeadAttention  # noqa: F401
from .layer.fused_transformer import FusedFeedForward  # noqa: F401
from .layer.fused_transformer import FusedTransformerEncoderLayer  # noqa: F401
from .layer.fused_transformer import FusedMultiTransformer  # noqa: F401
from .layer.fused_linear import FusedLinear  # noqa: F401
from .layer.fused_transformer import (
    FusedBiasDropoutResidualLayerNorm,
)  # noqa: F401

__all__ = [  # noqa
    'FusedMultiHeadAttention',
    'FusedFeedForward',
    'FusedTransformerEncoderLayer',
    'FusedMultiTransformer',
    'FusedLinear',
    'FusedBiasDropoutResidualLayerNorm',
]
