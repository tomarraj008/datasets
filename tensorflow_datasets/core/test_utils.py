# coding=utf-8
# Copyright 2018 The TensorFlow Datasets Authors.
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

"""Test utilities."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import contextlib
import os
import tempfile

import tensorflow as tf

from tensorflow_datasets.core import dataset_builder
from tensorflow_datasets.core import dataset_info
from tensorflow_datasets.core import dataset_utils
from tensorflow_datasets.core import features
from tensorflow_datasets.core import file_format_adapter
from tensorflow_datasets.core import splits
from tensorflow_datasets.core import utils


@contextlib.contextmanager
def tmp_dir(dirname=None):
  tmp = make_tmp_dir(dirname)
  yield tmp
  rm_tmp_dir(tmp)


def make_tmp_dir(dirname=None):
  if dirname and not tf.io.gfile.exists(dirname):
    tf.io.gfile.makedirs(dirname)
  return tempfile.mkdtemp(dir=dirname)


def rm_tmp_dir(dirname):
  tf.io.gfile.rmtree(dirname)


def remake_dir(d):
  """Possibly deletes and recreates directory."""
  if tf.io.gfile.exists(d):
    tf.io.gfile.rmtree(d)
  tf.io.gfile.makedirs(d)


class FeatureExpectationItem(object):
  """Test item of a FeatureExpectation."""

  def __init__(
      self,
      value,
      expected=None,
      expected_serialized=None,
      raise_cls=None,
      raise_msg=None):
    self.value = value
    self.expected = expected
    self.expected_serialized = expected_serialized
    self.raise_cls = raise_cls
    self.raise_msg = raise_msg


class FeatureExpectation(object):
  """Object defining a featureConnector test."""

  def __init__(self, name, feature, shape, dtype, tests, serialized_info=None):
    self.name = name
    self.feature = feature
    self.shape = shape
    self.dtype = dtype
    self.tests = tests
    self.serialized_info = serialized_info


class SubTestCase(tf.test.TestCase):
  """Adds subTest() context manager to the TestCase if supported.

  Note: To use this feature, make sure you call super() in setUpClass to
  initialize the sub stack.
  """

  @classmethod
  def setUpClass(cls):
    cls._sub_test_stack = []

  @contextlib.contextmanager
  def _subTest(self, test_str):
    sub_test_not_implemented = True
    if sub_test_not_implemented:
      yield
    else:
      self._sub_test_stack.append(test_str)
      sub_test_str = "/".join(self._sub_test_stack)
      with self.subTest(sub_test_str):
        yield
      self._sub_test_stack.pop()


def run_in_graph_and_eager_modes(func=None,
                                 config=None,
                                 use_gpu=True):
  """Execute the decorated test with and without enabling eager execution.

  This function returns a decorator intended to be applied to test methods in
  a `tf.test.TestCase` class. Doing so will cause the contents of the test
  method to be executed twice - once in graph mode, and once with eager
  execution enabled. This allows unittests to confirm the equivalence between
  eager and graph execution.

  NOTE: This decorator can only be used when executing eagerly in the
  outer scope.

  For example, consider the following unittest:

  ```python
  tf.compat.v1.enable_eager_execution()

  class SomeTest(tf.test.TestCase):

    @test_utils.run_in_graph_and_eager_modes
    def test_foo(self):
      x = tf.constant([1, 2])
      y = tf.constant([3, 4])
      z = tf.add(x, y)
      self.assertAllEqual([4, 6], self.evaluate(z))

  if __name__ == "__main__":
    tf.test.main()
  ```

  This test validates that `tf.add()` has the same behavior when computed with
  eager execution enabled as it does when constructing a TensorFlow graph and
  executing the `z` tensor with a session.

  Args:
    func: function to be annotated. If `func` is None, this method returns a
      decorator the can be applied to a function. If `func` is not None this
      returns the decorator applied to `func`.
    config: An optional config_pb2.ConfigProto to use to configure the session
      when executing graphs.
    use_gpu: If True, attempt to run as many operations as possible on GPU.

  Returns:
    Returns a decorator that will run the decorated test method twice:
    once by constructing and executing a graph in a session and once with
    eager execution enabled.
  """

  def decorator(f):
    """Decorator for a method."""
    def decorated(self, *args, **kwargs):
      """Run the decorated test method."""
      if not tf.executing_eagerly():
        raise ValueError("Must be executing eagerly when using the "
                         "run_in_graph_and_eager_modes decorator.")

      # Run eager block
      f(self, *args, **kwargs)
      self.tearDown()

      # Run in graph mode block
      with tf.Graph().as_default():
        self.setUp()
        with self.test_session(use_gpu=use_gpu, config=config):
          f(self, *args, **kwargs)

    return decorated

  if func is not None:
    return decorator(func)

  return decorator


class FeatureExpectationsTestCase(SubTestCase):
  """Tests FeatureExpectations with full encode-decode."""

  @property
  def expectations(self):
    raise NotImplementedError

  @run_in_graph_and_eager_modes()
  def test_encode_decode(self):
    for exp in self.expectations:
      with self._subTest(exp.name):
        self._process_exp(exp)

  def _process_exp(self, exp):

    # Check the shape/dtype
    with self._subTest("shape"):
      self.assertEqual(exp.feature.shape, exp.shape)
    with self._subTest("dtype"):
      self.assertEqual(exp.feature.dtype, exp.dtype)

    # Check the serialized features
    if exp.serialized_info is not None:
      with self._subTest("serialized_info"):
        self.assertEqual(
            exp.serialized_info,
            exp.feature.get_serialized_info(),
        )

    # Create the feature dict
    fdict = features.FeaturesDict({exp.name: exp.feature})
    for i, test in enumerate(exp.tests):
      with self._subTest(str(i)):
        # self._process_subtest_exp(e)
        input_value = {exp.name: test.value}

        if test.raise_cls is not None:
          with self._subTest("raise"):
            if not test.raise_msg:
              raise ValueError(
                  "test.raise_msg should be set with {}for test {}".format(
                      test.raise_cls, exp.name))
            with self.assertRaisesWithPredicateMatch(
                test.raise_cls, test.raise_msg):
              features_encode_decode(fdict, input_value)
        else:
          # Test the serialization only
          if test.expected_serialized is not None:
            with self._subTest("out_serialize"):
              self.assertEqual(
                  test.expected_serialized,
                  exp.feature.encode_example(test.value),
              )

          # Assert the returned type match the expected one
          with self._subTest("out"):
            out = features_encode_decode(fdict, input_value, as_tensor=True)
            out = out[exp.name]
            with self._subTest("dtype"):
              out_dtypes = utils.map_nested(lambda s: s.dtype, out)
              self.assertEqual(out_dtypes, exp.feature.dtype)
            with self._subTest("shape"):
              # For shape, because (None, 3) match with (5, 3), we use
              # tf.TensorShape.assert_is_compatible_with on each of the elements
              out_shapes = utils.zip_nested(out, exp.feature.shape)
              utils.map_nested(
                  lambda x: x[0].shape.assert_is_compatible_with(x[1]),
                  out_shapes
              )

          # Test serialization + decoding from disk
          with self._subTest("out_value"):
            decoded_examples = features_encode_decode(fdict, input_value)
            decoded_examples = decoded_examples[exp.name]
            if isinstance(decoded_examples, dict):
              # assertAllEqual do not works well with dictionaries so assert
              # on each individual elements instead
              zipped_examples = utils.zip_nested(
                  test.expected,
                  decoded_examples,
                  dict_only=True,
              )
              utils.map_nested(
                  lambda x: self.assertAllEqual(x[0], x[1]),
                  zipped_examples,
                  dict_only=True,
              )
            else:
              self.assertAllEqual(test.expected, decoded_examples)


def features_encode_decode(features_dict, example, as_tensor=False):
  """Runs the full pipeline: encode > write > tmp files > read > decode."""
  # Encode example
  encoded_example = features_dict.encode_example(example)

  with tmp_dir() as tmp_dir_:
    tmp_filename = os.path.join(tmp_dir_, "tmp.tfrecord")

    # Read/write the file
    file_adapter = file_format_adapter.TFRecordExampleAdapter(
        features_dict.get_serialized_info())
    file_adapter.write_from_generator(
        generator_fn=lambda: [encoded_example],
        output_files=[tmp_filename],
    )
    dataset = file_adapter.dataset_from_filename(tmp_filename)

    # Decode the example
    dataset = dataset.map(features_dict.decode_example)

    if not as_tensor:  # Evaluate to numpy array
      for el in dataset_utils.as_numpy(dataset):
        return el
    else:
      if tf.executing_eagerly():
        return next(iter(dataset))
      else:
        return tf.compat.v1.data.make_one_shot_iterator(dataset).get_next()


class DummyDatasetSharedGenerator(dataset_builder.GeneratorBasedBuilder):
  """Test DatasetBuilder."""

  VERSION = utils.Version("1.0.0")

  def _info(self):
    return dataset_info.DatasetInfo(
        builder=self,
        features=features.FeaturesDict({"x": tf.int64}),
        supervised_keys=("x", "x"),
    )

  def _split_generators(self, dl_manager):
    # Split the 30 examples from the generator into 2 train shards and 1 test
    # shard.
    del dl_manager
    return [splits.SplitGenerator(
        name=[splits.Split.TRAIN, splits.Split.TEST],
        num_shards=[2, 1],
    )]

  def _generate_examples(self):
    for i in range(30):
      yield {"x": i}
