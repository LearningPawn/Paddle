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

import math
import os
import tempfile
import time
import unittest

import numpy as np
from predictor_utils import PredictorTools

import paddle
import paddle.fluid as fluid
from paddle.fluid.dygraph.io import INFER_MODEL_SUFFIX, INFER_PARAMS_SUFFIX
from paddle.jit import ProgramTranslator
from paddle.nn import BatchNorm

SEED = 2020
IMAGENET1000 = 1281167
base_lr = 0.001
momentum_rate = 0.9
l2_decay = 1e-4
# NOTE: Reduce batch_size from 8 to 2 to avoid unittest timeout.
batch_size = 2
epoch_num = 1
place = (
    fluid.CUDAPlace(0) if fluid.is_compiled_with_cuda() else fluid.CPUPlace()
)

program_translator = ProgramTranslator()

if fluid.is_compiled_with_cuda():
    fluid.set_flags({'FLAGS_cudnn_deterministic': True})


def optimizer_setting(parameter_list=None):
    optimizer = fluid.optimizer.Momentum(
        learning_rate=base_lr,
        momentum=momentum_rate,
        regularization=fluid.regularizer.L2Decay(l2_decay),
        parameter_list=parameter_list,
    )

    return optimizer


class ConvBNLayer(fluid.dygraph.Layer):
    def __init__(
        self,
        num_channels,
        num_filters,
        filter_size,
        stride=1,
        groups=1,
        act=None,
    ):
        super().__init__()

        self._conv = paddle.nn.Conv2D(
            in_channels=num_channels,
            out_channels=num_filters,
            kernel_size=filter_size,
            stride=stride,
            padding=(filter_size - 1) // 2,
            groups=groups,
            bias_attr=False,
        )

        self._batch_norm = BatchNorm(num_filters, act=act)

    def forward(self, inputs):
        y = self._conv(inputs)
        y = self._batch_norm(y)

        return y


class BottleneckBlock(fluid.dygraph.Layer):
    def __init__(self, num_channels, num_filters, stride, shortcut=True):
        super().__init__()

        self.conv0 = ConvBNLayer(
            num_channels=num_channels,
            num_filters=num_filters,
            filter_size=1,
            act='relu',
        )
        self.conv1 = ConvBNLayer(
            num_channels=num_filters,
            num_filters=num_filters,
            filter_size=3,
            stride=stride,
            act='relu',
        )
        self.conv2 = ConvBNLayer(
            num_channels=num_filters,
            num_filters=num_filters * 4,
            filter_size=1,
            act=None,
        )

        if not shortcut:
            self.short = ConvBNLayer(
                num_channels=num_channels,
                num_filters=num_filters * 4,
                filter_size=1,
                stride=stride,
            )

        self.shortcut = shortcut

        self._num_channels_out = num_filters * 4

    def forward(self, inputs):
        y = self.conv0(inputs)
        conv1 = self.conv1(y)
        conv2 = self.conv2(conv1)

        if self.shortcut:
            short = inputs
        else:
            short = self.short(inputs)

        y = paddle.add(x=short, y=conv2)

        layer_helper = fluid.layer_helper.LayerHelper(
            self.full_name(), act='relu'
        )
        return layer_helper.append_activation(y)


class ResNet(fluid.dygraph.Layer):
    def __init__(self, layers=50, class_dim=102):
        super().__init__()

        self.layers = layers
        supported_layers = [50, 101, 152]
        assert (
            layers in supported_layers
        ), "supported layers are {} but input layer is {}".format(
            supported_layers, layers
        )

        if layers == 50:
            depth = [3, 4, 6, 3]
        elif layers == 101:
            depth = [3, 4, 23, 3]
        elif layers == 152:
            depth = [3, 8, 36, 3]
        num_channels = [64, 256, 512, 1024]
        num_filters = [64, 128, 256, 512]

        self.conv = ConvBNLayer(
            num_channels=3, num_filters=64, filter_size=7, stride=2, act='relu'
        )
        self.pool2d_max = paddle.nn.MaxPool2D(kernel_size=3, stride=2)

        self.bottleneck_block_list = []
        for block in range(len(depth)):
            shortcut = False
            for i in range(depth[block]):
                bottleneck_block = self.add_sublayer(
                    'bb_%d_%d' % (block, i),
                    BottleneckBlock(
                        num_channels=num_channels[block]
                        if i == 0
                        else num_filters[block] * 4,
                        num_filters=num_filters[block],
                        stride=2 if i == 0 and block != 0 else 1,
                        shortcut=shortcut,
                    ),
                )
                self.bottleneck_block_list.append(bottleneck_block)
                shortcut = True
        self.pool2d_avg = paddle.nn.AdaptiveAvgPool2D(1)

        self.pool2d_avg_output = num_filters[len(num_filters) - 1] * 4 * 1 * 1

        stdv = 1.0 / math.sqrt(2048 * 1.0)

        self.out = paddle.nn.Linear(
            self.pool2d_avg_output,
            class_dim,
            weight_attr=fluid.param_attr.ParamAttr(
                initializer=fluid.initializer.Uniform(-stdv, stdv)
            ),
        )

    def forward(self, inputs):
        y = self.conv(inputs)
        y = self.pool2d_max(y)
        for bottleneck_block in self.bottleneck_block_list:
            y = bottleneck_block(y)
        y = self.pool2d_avg(y)
        y = paddle.reshape(y, shape=[-1, self.pool2d_avg_output])
        pred = self.out(y)
        pred = paddle.nn.functional.softmax(pred)

        return pred


def reader_decorator(reader):
    def __reader__():
        for item in reader():
            img = np.array(item[0]).astype('float32').reshape(3, 224, 224)
            label = np.array(item[1]).astype('int64').reshape(1)
            yield img, label

    return __reader__


class ResNetHelper:
    def __init__(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.model_save_dir = os.path.join(self.temp_dir.name, 'inference')
        self.model_save_prefix = os.path.join(self.model_save_dir, 'resnet')
        self.model_filename = 'resnet' + INFER_MODEL_SUFFIX
        self.params_filename = 'resnet' + INFER_PARAMS_SUFFIX
        self.dy_state_dict_save_path = os.path.join(
            self.temp_dir.name, 'resnet.dygraph'
        )

    def __del__(self):
        self.temp_dir.cleanup()

    def train(self, to_static, build_strategy=None):
        """
        Tests model decorated by `dygraph_to_static_output` in static mode. For users, the model is defined in dygraph mode and trained in static mode.
        """
        with fluid.dygraph.guard(place):
            np.random.seed(SEED)
            paddle.seed(SEED)
            paddle.framework.random._manual_program_seed(SEED)

            train_reader = paddle.batch(
                reader_decorator(paddle.dataset.flowers.train(use_xmap=False)),
                batch_size=batch_size,
                drop_last=True,
            )
            data_loader = fluid.io.DataLoader.from_generator(
                capacity=5, iterable=True
            )
            data_loader.set_sample_list_generator(train_reader)

            resnet = ResNet()
            if to_static:
                resnet = paddle.jit.to_static(
                    resnet, build_strategy=build_strategy
                )
            optimizer = optimizer_setting(parameter_list=resnet.parameters())

            for epoch in range(epoch_num):
                total_loss = 0.0
                total_acc1 = 0.0
                total_acc5 = 0.0
                total_sample = 0

                for batch_id, data in enumerate(data_loader()):
                    start_time = time.time()
                    img, label = data

                    pred = resnet(img)
                    loss = paddle.nn.functional.cross_entropy(
                        input=pred,
                        label=label,
                        reduction='none',
                        use_softmax=False,
                    )
                    avg_loss = paddle.mean(x=loss)
                    acc_top1 = paddle.static.accuracy(
                        input=pred, label=label, k=1
                    )
                    acc_top5 = paddle.static.accuracy(
                        input=pred, label=label, k=5
                    )

                    avg_loss.backward()
                    optimizer.minimize(avg_loss)
                    resnet.clear_gradients()

                    total_loss += avg_loss
                    total_acc1 += acc_top1
                    total_acc5 += acc_top5
                    total_sample += 1

                    end_time = time.time()
                    if batch_id % 2 == 0:
                        print(
                            "epoch %d | batch step %d, loss %0.3f, acc1 %0.3f, acc5 %0.3f, time %f"
                            % (
                                epoch,
                                batch_id,
                                total_loss.numpy() / total_sample,
                                total_acc1.numpy() / total_sample,
                                total_acc5.numpy() / total_sample,
                                end_time - start_time,
                            )
                        )
                    if batch_id == 10:
                        if to_static:
                            paddle.jit.save(resnet, self.model_save_prefix)
                        else:
                            fluid.dygraph.save_dygraph(
                                resnet.state_dict(),
                                self.dy_state_dict_save_path,
                            )
                        # avoid dataloader throw abort signaal
                        data_loader._reset()
                        break

        return total_loss.numpy()

    def predict_dygraph(self, data):
        program_translator.enable(False)
        with fluid.dygraph.guard(place):
            resnet = ResNet()

            model_dict, _ = fluid.dygraph.load_dygraph(
                self.dy_state_dict_save_path
            )
            resnet.set_dict(model_dict)
            resnet.eval()

            pred_res = resnet(fluid.dygraph.to_variable(data))

            return pred_res.numpy()

    def predict_static(self, data):
        paddle.enable_static()
        exe = fluid.Executor(place)
        [
            inference_program,
            feed_target_names,
            fetch_targets,
        ] = fluid.io.load_inference_model(
            self.model_save_dir,
            executor=exe,
            model_filename=self.model_filename,
            params_filename=self.params_filename,
        )

        pred_res = exe.run(
            inference_program,
            feed={feed_target_names[0]: data},
            fetch_list=fetch_targets,
        )

        return pred_res[0]

    def predict_dygraph_jit(self, data):
        with fluid.dygraph.guard(place):
            resnet = paddle.jit.load(self.model_save_prefix)
            resnet.eval()

            pred_res = resnet(data)

            return pred_res.numpy()

    def predict_analysis_inference(self, data):
        output = PredictorTools(
            self.model_save_dir,
            self.model_filename,
            self.params_filename,
            [data],
        )
        (out,) = output()
        return out


class TestResnet(unittest.TestCase):
    def setUp(self):
        self.resnet_helper = ResNetHelper()

    def train(self, to_static):
        program_translator.enable(to_static)
        return self.resnet_helper.train(to_static)

    def verify_predict(self):
        image = np.random.random([1, 3, 224, 224]).astype('float32')
        dy_pre = self.resnet_helper.predict_dygraph(image)
        st_pre = self.resnet_helper.predict_static(image)
        dy_jit_pre = self.resnet_helper.predict_dygraph_jit(image)
        predictor_pre = self.resnet_helper.predict_analysis_inference(image)
        np.testing.assert_allclose(
            dy_pre,
            st_pre,
            rtol=1e-05,
            err_msg='dy_pre:\n {}\n, st_pre: \n{}.'.format(dy_pre, st_pre),
        )
        np.testing.assert_allclose(
            dy_jit_pre,
            st_pre,
            rtol=1e-05,
            err_msg='dy_jit_pre:\n {}\n, st_pre: \n{}.'.format(
                dy_jit_pre, st_pre
            ),
        )
        np.testing.assert_allclose(
            predictor_pre,
            st_pre,
            rtol=1e-05,
            err_msg='predictor_pre:\n {}\n, st_pre: \n{}.'.format(
                predictor_pre, st_pre
            ),
        )

    def test_resnet(self):
        static_loss = self.train(to_static=True)
        dygraph_loss = self.train(to_static=False)
        np.testing.assert_allclose(
            static_loss,
            dygraph_loss,
            rtol=1e-05,
            err_msg='static_loss: {} \n dygraph_loss: {}'.format(
                static_loss, dygraph_loss
            ),
        )
        self.verify_predict()

    def test_in_static_mode_mkldnn(self):
        fluid.set_flags({'FLAGS_use_mkldnn': True})
        try:
            if paddle.fluid.core.is_compiled_with_mkldnn():
                self.resnet_helper.train(to_static=True)
        finally:
            fluid.set_flags({'FLAGS_use_mkldnn': False})


if __name__ == '__main__':
    unittest.main()
