# Copyright (c) 2021 PaddlePaddle Authors. All Rights Reserved.
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

import os
import tempfile
import unittest

import numpy as np

import paddle


class GradLayer(paddle.nn.Layer):
    def __init__(self):
        super().__init__()

    @paddle.jit.to_static
    def forward(self, x):
        x.stop_gradient = False
        y = x * x
        dx = paddle.grad(outputs=[y], inputs=[x])[0]
        return dx


class GradLinearLayer(paddle.nn.Layer):
    def __init__(self):
        super().__init__()
        self.linear = paddle.nn.Linear(5, 5, bias_attr=False)

    @paddle.jit.to_static
    def forward(self, x):
        x.stop_gradient = False
        tmp = x + x
        for i in range(10):
            tmp = self.linear(tmp)
        out = tmp
        dx = paddle.grad(
            [out], [x], None, create_graph=True, allow_unused=False
        )[0]
        return dx


class NoGradLinearLayer(paddle.nn.Layer):
    def __init__(self):
        super().__init__()
        self.linear = paddle.nn.Linear(5, 5, bias_attr=False)

    @paddle.jit.to_static
    def forward(self, x):
        x.stop_gradient = False

        with paddle.no_grad():
            y = self.linear(x)

        out = y + x
        return out


class TestGrad(unittest.TestCase):
    def setUp(self):
        self.func = GradLayer()
        self.x = paddle.ones(shape=[10, 2, 5], dtype='float32')
        self.x.stop_gradient = False

    def _run(self, func, to_static):
        prog_trans = paddle.jit.ProgramTranslator()
        prog_trans.enable(to_static)
        ret = func(self.x).numpy()
        prog_trans.enable(True)
        return ret

    def test_forward(self):
        dygraph_res = self._run(self.func, to_static=False)
        static_res = self._run(self.func, to_static=True)
        np.testing.assert_allclose(static_res, dygraph_res, rtol=1e-05)


class TestGradLinear(TestGrad):
    def setUp(self):
        self.func = GradLinearLayer()
        self.x = paddle.ones(shape=[10, 2, 5], dtype='float32')
        self.x.stop_gradient = False

        self.temp_dir = tempfile.TemporaryDirectory()
        self.infer_model_path = os.path.join(
            self.temp_dir.name, 'double_grad_infer_model'
        )
        self.train_model_path = os.path.join(
            self.temp_dir.name, 'double_grad_train_model'
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_save_infer_program(self):
        input_spec = [
            paddle.static.InputSpec(shape=[10, 2, 5], dtype='float32')
        ]
        paddle.jit.save(self.func, self.infer_model_path, input_spec=input_spec)
        load_func = paddle.jit.load(self.infer_model_path)

        origin_res = self.func(self.x).numpy()
        load_res = load_func(self.x).numpy()
        np.testing.assert_allclose(origin_res, load_res, rtol=1e-05)

    def test_save_train_program(self):
        grad_clip = paddle.nn.ClipGradByGlobalNorm(2.0)
        optimizer = paddle.optimizer.SGD(
            learning_rate=0.01,
            grad_clip=grad_clip,
            parameters=self.func.parameters(),
        )
        for i in range(10):
            out = self.func(self.x)
            avg_loss = paddle.mean(paddle.abs(out - 1))
            avg_loss.backward()
            optimizer.minimize(avg_loss)

            self.func.clear_gradients()

        paddle.jit.save(self.func, self.train_model_path)
        load_func = paddle.jit.load(self.train_model_path)

        origin_res = self.func(self.x).numpy()
        load_res = load_func(self.x).numpy()
        np.testing.assert_allclose(origin_res, load_res, rtol=1e-05)


class TestNoGradLinear(TestGradLinear):
    def setUp(self):
        self.func = NoGradLinearLayer()
        self.x = paddle.ones(shape=[10, 2, 5], dtype='float32')
        self.x.stop_gradient = False

        self.temp_dir = tempfile.TemporaryDirectory()
        self.infer_model_path = os.path.join(
            self.temp_dir.name, 'no_grad_infer_model'
        )
        self.train_model_path = os.path.join(
            self.temp_dir.name, 'no_grad_train_model'
        )

    def tearDown(self):
        self.temp_dir.cleanup()


if __name__ == '__main__':
    unittest.main()
