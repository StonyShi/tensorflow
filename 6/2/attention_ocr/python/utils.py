# Copyright 2017 The TensorFlow Authors All Rights Reserved.
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
# ==============================================================================

"""Functions to support building models for StreetView text transcription."""

import tensorflow as tf
from tensorflow.contrib import slim

# 输入 ligits [32 37 134]
def logits_to_log_prob(logits):
  """Computes log probabilities using numerically stable trick.

  This uses two numerical stability tricks:
  1) softmax(x) = softmax(x - c) where c is a constant applied to all
  arguments. If we set c = max(x) then the softmax is more numerically
  stable.
  2) log softmax(x) is not numerically stable, but we can stabilize it
  by using the identity log softmax(x) = x - log sum exp(x)

  Args:
    logits: Tensor of arbitrary shape whose last dimension contains logits.

  Returns:
    A tensor of the same shape as the input, but with corresponding log
    probabilities.
  """

  with tf.variable_scope('log_probabilities'):
    # 将所有的值都降到0和以下
    reduction_indices = len(logits.shape.as_list()) - 1
    # 取最大值 max_logits [32 37 1]
    max_logits = tf.reduce_max(
        logits, reduction_indices=reduction_indices, keep_dims=True)
    
    # 都降到 0 以下 [32 37 134]
    safe_logits = tf.subtract(logits, max_logits)
    # exp(-x) => (0 ~ 1) 求和最后一个维度
    sum_exp = tf.reduce_sum(
        tf.exp(safe_logits),
        reduction_indices=reduction_indices,
        keep_dims=True)
    # 再将 log(sum) => (0 ~ 1)  
    log_probs = tf.subtract(safe_logits, tf.log(sum_exp))
    
    # c = x - max(x)         ==> (-1, 0)
    # c - log(sum ( exp(c) ))  ==> (-1.74, -0.74)
    # 会提高小差异的敏感度
    # 返回 [32 27 134]
  return log_probs


def variables_to_restore(scope=None, strip_scope=False):
  """Returns a list of variables to restore for the specified list of methods.

  It is supposed that variable name starts with the method's scope (a prefix
  returned by _method_scope function).

  Args:
    methods_names: a list of names of configurable methods.
    strip_scope: if True will return variable names without method's scope.
      If methods_names is None will return names unchanged.
    model_scope: a scope for a whole model.

  Returns:
    a dictionary mapping variable names to variables for restore.
  """
  if scope:
    variable_map = {}
    method_variables = slim.get_variables_to_restore(include=[scope])
    for var in method_variables:
      if strip_scope:
        var_name = var.op.name[len(scope) + 1:]
      else:
        var_name = var.op.name
      variable_map[var_name] = var

    return variable_map
  else:
    return {v.op.name: v for v in slim.get_variables_to_restore()}
