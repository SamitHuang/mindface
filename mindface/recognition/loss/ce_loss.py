# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ============================================================================

from mindspore import Tensor
import mindspore.nn as nn
import mindspore.ops as ops
from mindspore import dtype as mstype
from mindspore.ops import functional as F


class SoftMaxCE(nn.Cell):
    '''
    softmax cross entrophy
    '''
    def __init__(self, world_size):
        super(SoftMaxCE, self).__init__()
        self.max = ops.ReduceMax(keep_dims=True)
        self.sum = ops.ReduceSum(keep_dims=True)
        self.mean = ops.ReduceMean(keep_dims=False)
        self.exp = ops.Exp()
        self.div = ops.Div()
        self.onehot = ops.OneHot().shard(((1, world_size), (), ()))
        self.mul = ops.Mul()
        self.log = ops.Log()
        self.onvalue = Tensor(1.0, mstype.float32)
        self.offvalue = Tensor(0.0, mstype.float32)
        self.eps = Tensor(1e-30, mstype.float32)

    def construct(self, logits, total_label):
        '''construct
        '''
        max_fc = self.max(logits, 1)

        logits_exp = self.exp(logits - max_fc)
        logits_sum_exp = self.sum(logits_exp, 1)

        logits_exp = self.div(logits_exp, logits_sum_exp)

        label = self.onehot(total_label, F.shape(
            logits)[1], self.onvalue, self.offvalue)

        softmax_result_log = self.log(logits_exp + self.eps)
        loss = self.sum((self.mul(softmax_result_log, label)), -1)
        loss = self.mul(ops.scalar_to_array(-1.0), loss)
        loss_v = self.mean(loss, 0)

        return loss_v