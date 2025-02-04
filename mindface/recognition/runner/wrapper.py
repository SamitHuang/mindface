import numpy as np

import mindspore as ms
from mindspore import nn

from mindspore.ops import functional as F
from mindspore.ops import GradOperation
from mindspore.ops.composite import MultitypeFuncGraph, HyperMap

from mindspore import context, Tensor
from mindspore.parallel._auto_parallel_context import auto_parallel_context
from mindspore.communication.management import get_group_size

__all__ = ["NetWithLoss", "TrainingWrapper", "lr_generator"]


def lr_generator(lr_init, schedule, gamma, total_epochs, steps_per_epoch):
    """lr_generator
    """
    lr_each_step = []
    for i in range(total_epochs):
        if i in schedule:
            lr_init *= gamma
        for _ in range(steps_per_epoch):
            lr_each_step.append(lr_init)
    lr_each_step = np.array(lr_each_step).astype(np.float32)
    return Tensor(lr_each_step)


class NetWithLoss(nn.Cell):
    """
    WithLossCell
    """
    def __init__(self, backbone, head, loss_func):
        super(NetWithLoss, self).__init__(auto_prefix=False)
        self._backbone = backbone
        self.fc = head
        self.loss_func = loss_func

    def construct(self, data, label):
        out = self._backbone(data)
        out_fc = self.fc(out)
        loss = self.loss_func(out_fc, label)

        return loss


clip_grad = MultitypeFuncGraph("clip_grad")
@clip_grad.register("Number", "Number", "Tensor")
def _clip_grad(clip_type, clip_value, grad):
    if clip_type not in (0, 1):
        return grad
    dt = F.dtype(grad)
    if clip_type == 0:
        new_grad = F.clip_by_value(grad, F.cast(F.tuple_to_array((-clip_value,)), dt),
                                   F.cast(F.tuple_to_array((clip_value,)), dt))
    else:
        new_grad = nn.ClipByNorm()(grad, F.cast(F.tuple_to_array((clip_value,)), dt))
    return new_grad


class TrainingWrapper(nn.Cell):
    def __init__(self, network, optimizer, sens=1.0, GRADIENT_CLIP_TYPE = 1, GRADIENT_CLIP_VALUE = 1.0):
        super(TrainingWrapper, self).__init__(auto_prefix=False)
        self.network = network
        self.weights = ms.ParameterTuple(network.trainable_params())
        self.optimizer = optimizer
        self.grad = GradOperation(get_by_list=True, sens_param=True)
        self.hyper_map = HyperMap()
        self.sens = sens
        self.reducer_flag = False
        self.grad_reducer = None
        self.parallel_mode = context.get_auto_parallel_context("parallel_mode")
        class_list = [context.ParallelMode.DATA_PARALLEL,
                      context.ParallelMode.HYBRID_PARALLEL]
        if self.parallel_mode in class_list:
            self.reducer_flag = True
        if self.reducer_flag:
            mean = context.get_auto_parallel_context("gradients_mean")
            if auto_parallel_context().get_device_num_is_set():
                degree = context.get_auto_parallel_context("device_num")
            else:
                degree = get_group_size()
            self.grad_reducer = nn.DistributedGradReducer(
                optimizer.parameters, mean, degree)

        self.GRADIENT_CLIP_TYPE = GRADIENT_CLIP_TYPE
        self.GRADIENT_CLIP_VALUE = GRADIENT_CLIP_VALUE

    def construct(self, *args):
        weights = self.weights
        loss = self.network(*args)
        sens = F.fill(F.dtype(loss), F.shape(loss), self.sens)
        grads = self.grad(self.network, weights)(*args, sens)

        grads = self.hyper_map(
            F.partial(clip_grad, self.GRADIENT_CLIP_TYPE, self.GRADIENT_CLIP_VALUE), grads)
        if self.reducer_flag:
            grads = self.grad_reducer(grads)
        return F.depend(loss, self.optimizer(grads))




